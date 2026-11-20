"""Double-buffered n2d framebuffer, palette and VGA scanout contract.

The full-width (320x200 banks) and half-width (160x200 banks) geometries
share every test body; each target selects its geometry's testcases. A
Python model holds each bank as the displayed 320x200 index image.
"""

import os
import zlib

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge


CLOCK_HZ = 25_000_000
CLOCK_PERIOD_NS = 40
H_VISIBLE = 640
H_TOTAL = 800
V_VISIBLE = 480
V_TOTAL = 525
FRAME_CYCLES = H_TOTAL * V_TOTAL
ACTIVE_PIXEL_COUNT = 320 * 200
TOP_BORDER_LINES = 40
# Raster CRC of the known pattern: RGB444 as two big-endian bytes per clock.
KNOWN_FRAME_CRC32 = {True: 0xC3AEAAEF, False: 0x39E9750F}


class BankModel:
    """Two banks of the displayed 320x200 image and the front-bank index."""

    def __init__(self, full: bool) -> None:
        self.full = full
        self.banks = [bytearray(ACTIVE_PIXEL_COUNT), bytearray(ACTIVE_PIXEL_COUNT)]
        self.known = [bytearray(ACTIVE_PIXEL_COUNT), bytearray(ACTIVE_PIXEL_COUNT)]
        self.front = 0

    def write(self, address: int, data: int, strobe: int) -> None:
        bank = 1 - self.front
        for lane in range(4):
            target = address + lane
            if not strobe & (1 << lane) or target >= ACTIVE_PIXEL_COUNT:
                continue
            if not self.full and lane & 1:
                continue  # odd-x bytes are not stored at half width
            for pixel in range(1 if self.full else 2):
                self.banks[bank][target + pixel] = (data >> (8 * lane)) & 0xFF
                self.known[bank][target + pixel] = 1

    def read(self, address: int, front: bool = False) -> int | None:
        """Expected word, or None when a lane holds never-written RAM."""
        bank = self.front if front else 1 - self.front
        value = 0
        for lane in range(4):
            target = address + lane
            if target < ACTIVE_PIXEL_COUNT:
                if not self.known[bank][target]:
                    return None
                value |= self.banks[bank][target] << (8 * lane)
        return value


def _drive_idle(dut) -> None:
    dut.framebuffer_request_valid_i.value = 0
    dut.framebuffer_request_write_i.value = 0
    dut.framebuffer_request_front_i.value = 0
    dut.framebuffer_request_address_i.value = 0
    dut.framebuffer_request_write_data_i.value = 0
    dut.framebuffer_request_write_strobe_i.value = 0
    dut.swap_request_i.value = 0
    dut.palette_request_valid_i.value = 0
    dut.palette_request_write_i.value = 0
    dut.palette_request_index_i.value = 0
    dut.palette_request_write_data_i.value = 0
    dut.palette_request_write_strobe_i.value = 0
    dut.palette_commit_i.value = 0


async def _start(dut) -> None:
    assert int(os.environ["DV_CLOCK_HZ"]) == CLOCK_HZ
    _drive_idle(dut)
    dut.reset_i.value = 1
    cocotb.start_soon(Clock(dut.clk_i, CLOCK_PERIOD_NS, unit="ns").start())
    for _ in range(2):
        await RisingEdge(dut.clk_i)
    await FallingEdge(dut.clk_i)
    dut.reset_i.value = 0


async def _framebuffer_request(
    dut, *, address: int, write_data: int = 0, write_strobe: int = 0,
    front: bool = False, swap: bool = False,
) -> int:
    """One request issued on a falling edge; returns its response data."""
    dut.framebuffer_request_valid_i.value = 1
    dut.framebuffer_request_write_i.value = int(write_strobe != 0)
    dut.framebuffer_request_front_i.value = int(front)
    dut.framebuffer_request_address_i.value = address
    dut.framebuffer_request_write_data_i.value = write_data
    dut.framebuffer_request_write_strobe_i.value = write_strobe
    dut.swap_request_i.value = int(swap)
    await RisingEdge(dut.clk_i)
    await ReadOnly()
    assert int(dut.framebuffer_request_ready_o.value) == 1
    assert int(dut.framebuffer_response_valid_o.value) == 1
    result = int(dut.framebuffer_response_read_data_o.value)
    await FallingEdge(dut.clk_i)
    dut.framebuffer_request_valid_i.value = 0
    dut.framebuffer_request_write_i.value = 0
    dut.framebuffer_request_front_i.value = 0
    dut.framebuffer_request_write_strobe_i.value = 0
    dut.swap_request_i.value = 0
    return result


async def _write(dut, model: BankModel, address: int, data: int, strobe: int = 0xF,
                 swap: bool = False) -> None:
    """Write and check the read-first response against the model."""
    expected = model.read(address)
    old = await _framebuffer_request(dut, address=address, write_data=data,
                                     write_strobe=strobe, swap=swap)
    if expected is not None:
        assert old == expected, f"address={address} old=0x{old:08x} expected=0x{expected:08x}"
    model.write(address, data, strobe)


async def _check(dut, model: BankModel, address: int, front: bool = False) -> None:
    expected = model.read(address, front)
    observed = await _framebuffer_request(dut, address=address, front=front)
    assert expected is not None
    assert observed == expected, (
        f"address={address} front={front} observed=0x{observed:08x} expected=0x{expected:08x}")


async def _palette_request(
    dut, *, index: int, write_data: int = 0, write_strobe: int = 0
) -> int:
    dut.palette_request_valid_i.value = 1
    dut.palette_request_write_i.value = int(write_strobe != 0)
    dut.palette_request_index_i.value = index
    dut.palette_request_write_data_i.value = write_data
    dut.palette_request_write_strobe_i.value = write_strobe
    await RisingEdge(dut.clk_i)
    await ReadOnly()
    assert int(dut.palette_request_ready_o.value) == 1
    assert int(dut.palette_response_valid_o.value) == 1
    result = int(dut.palette_response_read_data_o.value)
    await FallingEdge(dut.clk_i)
    dut.palette_request_valid_i.value = 0
    dut.palette_request_write_i.value = 0
    dut.palette_request_write_strobe_i.value = 0
    return result


def _index_for_source(source_x: int, source_y: int) -> int:
    return ((source_x * 3) + (source_y * 5)) & 0xFF


def _palette_rgb(index: int) -> int:
    red = (index >> 4) & 0xF
    green = index & 0xF
    blue = red ^ green
    return (red << 8) | (green << 4) | blue


def _expected_rgb(image: bytearray, x: int, y: int) -> int:
    if x >= H_VISIBLE or not 40 <= y < 440:
        return 0
    return _palette_rgb(image[((y - 40) // 2) * 320 + x // 2])


def _known_word(address: int) -> int:
    word = 0
    for lane in range(4):
        source_y, source_x = divmod(address + lane, 320)
        word |= _index_for_source(source_x, source_y) << (8 * lane)
    return word


async def _cpu_palette_contract(dut, full: bool) -> None:
    seed = int(os.environ["DV_SEED"])
    maximum = int(os.environ["DV_MAX_CYCLES"])
    assert maximum >= 32
    cocotb.log.info("n2d framebuffer focused full_width=%d seed=%d max_cycles=%d",
                    full, seed, maximum)
    model = BankModel(full)
    await _start(dut)

    await FallingEdge(dut.clk_i)
    assert int(dut.active_palette_bank_o.value) == 0
    assert int(dut.palette_commit_pending_o.value) == 0
    assert int(dut.front_bank_o.value) == 0
    assert int(dut.swap_pending_o.value) == 0
    assert int(dut.vga_red_o.value) == 0
    assert int(dut.vga_green_o.value) == 0
    assert int(dut.vga_blue_o.value) == 0

    # Full words, individual byte strobes and read-first old data. At half
    # width each even byte is returned for both of its source pixels and odd
    # bytes are dropped.
    await _write(dut, model, 0, 0x44332211)
    await _check(dut, model, 0)
    await _write(dut, model, 0, 0x0000AA00, 0x2)
    await _check(dut, model, 0)
    await _write(dut, model, 4, 0)
    for lane, value in enumerate((0x12, 0x34, 0x56, 0x78)):
        await _write(dut, model, 4, value << (lane * 8), 1 << lane)
        await _check(dut, model, 4)
    await _write(dut, model, 8, 0x11223344)
    await _write(dut, model, 8, 0xAABBCCDD)
    await _check(dut, model, 8)

    # Last active word, then the zero-reading reserved tail.
    await _write(dut, model, 63_996, 0xA5C35A7E)
    await _check(dut, model, 63_996)
    await _framebuffer_request(dut, address=64_000, write_data=0xFFFFFFFF, write_strobe=0xF)
    assert await _framebuffer_request(dut, address=64_000) == 0
    assert await _framebuffer_request(dut, address=0xFFFC, write_data=0xFFFFFFFF,
                                      write_strobe=0xF) == 0
    await _check(dut, model, 63_996)

    # The front view is read-only: a write presented on it changes nothing.
    await _framebuffer_request(dut, address=0, write_data=0xDEADBEEF,
                               write_strobe=0xF, front=True)
    await _check(dut, model, 0)

    await _palette_request(dut, index=5, write_data=0xFFFFFABC, write_strobe=0xF)
    assert await _palette_request(dut, index=5) == 0xABC
    await _palette_request(dut, index=6, write_data=0x05A, write_strobe=0x1)
    assert await _palette_request(dut, index=6) == 0x05A
    await _palette_request(dut, index=6, write_data=0xC00, write_strobe=0x2)
    assert await _palette_request(dut, index=6) == 0xC5A

    # Palette commit and bank swap requests stay pending until vertical blank.
    await FallingEdge(dut.clk_i)
    dut.palette_commit_i.value = 1
    dut.swap_request_i.value = 1
    await RisingEdge(dut.clk_i)
    await ReadOnly()
    assert int(dut.active_palette_bank_o.value) == 0
    assert int(dut.palette_commit_pending_o.value) == 1
    assert int(dut.front_bank_o.value) == 0
    assert int(dut.swap_pending_o.value) == 1
    await FallingEdge(dut.clk_i)
    dut.palette_commit_i.value = 0
    dut.swap_request_i.value = 0
    # A write while the swap is pending still targets the current back bank.
    await _write(dut, model, 12, 0x01020304)
    await _check(dut, model, 12)
    assert int(dut.front_bank_o.value) == 0


@cocotb.test(timeout_time=200, timeout_unit="us")
async def framebuffer_cpu_palette_contract(dut) -> None:
    await _cpu_palette_contract(dut, full=True)


@cocotb.test(timeout_time=200, timeout_unit="us")
async def framebuffer_half_cpu_palette_contract(dut) -> None:
    await _cpu_palette_contract(dut, full=False)


async def _wait_for_asserted(signal, clk, *, limit: int, name: str) -> None:
    for _ in range(limit):
        await RisingEdge(clk)
        await ReadOnly()
        if int(signal.value):
            return
    raise AssertionError(f"timeout waiting for {name} after {limit} cycles")


async def _wait_for_swap(dut, model: BankModel) -> None:
    """The front bank flips exactly at the first vertical-blank line."""
    previous = int(dut.front_bank_o.value)
    for _ in range(FRAME_CYCLES + 4):
        await RisingEdge(dut.clk_i)
        await ReadOnly()
        if int(dut.front_bank_o.value) != previous:
            # Public coordinates lag the raster by two clocks: the flip edge
            # sampled raster position (0, 480).
            position = (int(dut.pixel_x_o.value), int(dut.pixel_y_o.value))
            assert position == (H_TOTAL - 1, V_VISIBLE - 1), position
            assert int(dut.swap_pending_o.value) == 0
            model.front ^= 1
            await FallingEdge(dut.clk_i)
            return
        assert int(dut.swap_pending_o.value) == 1, "swap request lost before vertical blank"
    raise AssertionError("timeout waiting for the framebuffer swap")


async def _check_frame(dut, image: bytearray, expected_crc: int) -> None:
    """Observe one complete 800x525 raster from its aligned frame start."""
    await _wait_for_asserted(dut.frame_start_o, dut.clk_i, limit=FRAME_CYCLES,
                             name="aligned frame start")
    crc = 0
    starts = 0
    ends = 0
    for cycle in range(FRAME_CYCLES):
        x = cycle % H_TOTAL
        y = cycle // H_TOTAL
        observed_x = int(dut.pixel_x_o.value)
        observed_y = int(dut.pixel_y_o.value)
        assert (observed_x, observed_y) == (x, y), (
            f"cycle={cycle} coordinate expected={(x, y)} observed={(observed_x, observed_y)}")
        assert int(dut.visible_o.value) == int(x < H_VISIBLE and y < V_VISIBLE)
        assert int(dut.vga_hsync_o.value) == int(not (656 <= x < 752))
        assert int(dut.vga_vsync_o.value) == int(not (490 <= y < 492))
        observed_rgb = ((int(dut.vga_red_o.value) << 8)
                        | (int(dut.vga_green_o.value) << 4)
                        | int(dut.vga_blue_o.value))
        expected_rgb = _expected_rgb(image, x, y)
        assert observed_rgb == expected_rgb, (
            f"cycle={cycle} coordinate={(x, y)} expected_rgb=0x{expected_rgb:03x} "
            f"observed_rgb=0x{observed_rgb:03x}")
        crc = zlib.crc32(observed_rgb.to_bytes(2, "big"), crc)
        starts += int(dut.frame_start_o.value)
        ends += int(dut.frame_end_o.value)
        if cycle + 1 < FRAME_CYCLES:
            await RisingEdge(dut.clk_i)
            await ReadOnly()
    assert starts == 1
    assert ends == 1
    assert crc == expected_crc, f"frame CRC expected=0x{expected_crc:08x} observed=0x{crc:08x}"
    await RisingEdge(dut.clk_i)
    await ReadOnly()
    assert int(dut.frame_start_o.value) == 1
    assert (int(dut.pixel_x_o.value), int(dut.pixel_y_o.value)) == (0, 0)
    await FallingEdge(dut.clk_i)


async def _check_reset_black(dut) -> None:
    """After a warm reset, image rows stay black until a palette commit."""
    await FallingEdge(dut.clk_i)
    dut.reset_i.value = 1
    await FallingEdge(dut.clk_i)
    dut.reset_i.value = 0
    assert int(dut.active_palette_bank_o.value) == 0
    assert int(dut.front_bank_o.value) == 0
    # Through the first two image lines (one source row), visible included.
    image_pixels = 0
    for _ in range((TOP_BORDER_LINES + 2) * H_TOTAL + 4):
        await RisingEdge(dut.clk_i)
        await ReadOnly()
        y = int(dut.pixel_y_o.value)
        if y == TOP_BORDER_LINES + 2:
            break
        image_pixels += int(y >= TOP_BORDER_LINES and int(dut.visible_o.value))
        assert (int(dut.vga_red_o.value), int(dut.vga_green_o.value),
                int(dut.vga_blue_o.value)) == (0, 0, 0), (int(dut.pixel_x_o.value), y)
    assert image_pixels == 2 * H_VISIBLE
    await FallingEdge(dut.clk_i)


async def _actual_frame_contract(dut, full: bool) -> None:
    seed = int(os.environ["DV_SEED"])
    maximum = int(os.environ["DV_MAX_CYCLES"])
    assert maximum >= (5 * FRAME_CYCLES) + 80_000
    cocotb.log.info(
        "n2d framebuffer frame full_width=%d seed=%d max_cycles=%d expected_crc=%08x",
        full, seed, maximum, KNOWN_FRAME_CRC32[full])
    model = BankModel(full)
    await _start(dut)

    # Draw the known image into the back bank and its palette into the
    # inactive palette bank.
    for address in range(0, ACTIVE_PIXEL_COUNT, 4):
        await _write(dut, model, address, _known_word(address))
    for index in range(256):
        await _palette_request(dut, index=index, write_data=_palette_rgb(index),
                               write_strobe=0x3)

    # Posted-write ordering: the last image word is written on the same clock
    # as the swap request and one more on the next clock; both precede the
    # flip, so both belong to the image that becomes visible.
    known = _known_word(63_996)
    await _write(dut, model, 63_996, known ^ 0xFFFFFFFF)
    await FallingEdge(dut.clk_i)
    dut.palette_commit_i.value = 1
    await _write(dut, model, 63_996, known, swap=True)
    dut.palette_commit_i.value = 0
    assert int(dut.swap_pending_o.value) == 1
    assert int(dut.palette_commit_pending_o.value) == 1
    await _write(dut, model, 0, _known_word(0) ^ 0x00FF00FF)
    await _write(dut, model, 0, _known_word(0))
    await _wait_for_swap(dut, model)
    assert int(dut.palette_commit_pending_o.value) == 0
    assert int(dut.active_palette_bank_o.value) == 1
    image = bytes(model.banks[model.front])
    await _check(dut, model, 63_996, front=True)
    await _check(dut, model, 0, front=True)

    # The palette bank that became inactive is independently writable. Its
    # never-written entries are undefined after reset (video/HAS.md).
    await _palette_request(dut, index=17, write_data=0x123, write_strobe=0x3)
    assert await _palette_request(dut, index=17) == 0x123

    await _check_frame(dut, bytearray(image), KNOWN_FRAME_CRC32[full])

    # Back-bank invisibility: draw a different image into the new back bank
    # at row, lane and end boundaries while the front frame scans unchanged.
    for address in (0, 316, 320, 4092, 32764, 63_996):
        await _write(dut, model, address, 0x5AA5C33C ^ address)
        await _check(dut, model, address)
        await _check(dut, model, address, front=True)
    await _check_frame(dut, bytearray(image), KNOWN_FRAME_CRC32[full])

    # A swap shows the back image; the other bank keeps the known image for
    # the front view after a second swap.
    await FallingEdge(dut.clk_i)
    dut.swap_request_i.value = 1
    await FallingEdge(dut.clk_i)
    dut.swap_request_i.value = 0
    await _wait_for_swap(dut, model)
    for address in (0, 316, 32764):
        await _check(dut, model, address, front=True)
    assert model.banks[model.front] != image

    # Palette storage has no reset. Fill palette bank 0 (inactive) with
    # white, then warm-reset: bank 0 becomes active again over the retained
    # front image, yet no commit has taken effect, so the image stays black.
    for index in range(256):
        await _palette_request(dut, index=index, write_data=0xFFF, write_strobe=0x3)
    await _check_reset_black(dut)


@cocotb.test(timeout_time=100, timeout_unit="ms")
async def framebuffer_actual_frame_crc_contract(dut) -> None:
    await _actual_frame_contract(dut, full=True)


@cocotb.test(timeout_time=100, timeout_unit="ms")
async def framebuffer_half_actual_frame_crc_contract(dut) -> None:
    await _actual_frame_contract(dut, full=False)


async def _swap_palette_vblank_contract(dut, full: bool) -> None:
    """A swap and a palette commit armed on the same clock flip together.

    The platform arms both from one video-control write. Arming them one
    clock before, on, and one clock after the first vertical-blank clock
    (raster (0, 480)) flips both on the same edge: at that vblank in the first
    two cases and one raster later in the third.
    """
    seed = int(os.environ["DV_SEED"])
    maximum = int(os.environ["DV_MAX_CYCLES"])
    assert maximum >= 4 * FRAME_CYCLES
    cocotb.log.info("n2d framebuffer swap/palette vblank full_width=%d seed=%d", full, seed)
    await _start(dut)
    vblank_offset = 480 * H_TOTAL
    for delta, expected_wait in ((-1, 1), (0, 0), (1, FRAME_CYCLES - 1)):
        await FallingEdge(dut.clk_i)
        while True:
            raster = (int(dut.pixel_y_o.value) * H_TOTAL + int(dut.pixel_x_o.value) + 2) % FRAME_CYCLES
            if raster == (vblank_offset + delta) % FRAME_CYCLES:
                break
            await FallingEdge(dut.clk_i)
        # The timing raster is the public coordinate plus two; the request is
        # sampled on the next rising edge, at raster vblank_offset + delta.
        front = int(dut.front_bank_o.value)
        palette = int(dut.active_palette_bank_o.value)
        dut.swap_request_i.value = 1
        dut.palette_commit_i.value = 1
        await RisingEdge(dut.clk_i)
        await ReadOnly()
        waited = 0
        while int(dut.front_bank_o.value) == front:
            assert int(dut.active_palette_bank_o.value) == palette, "palette flipped before the swap"
            assert int(dut.swap_pending_o.value) and int(dut.palette_commit_pending_o.value)
            await FallingEdge(dut.clk_i)
            dut.swap_request_i.value = 0
            dut.palette_commit_i.value = 0
            await RisingEdge(dut.clk_i)
            await ReadOnly()
            waited += 1
            assert waited <= FRAME_CYCLES, "swap never taken"
        assert int(dut.active_palette_bank_o.value) != palette, "swap without its palette"
        assert not int(dut.swap_pending_o.value) and not int(dut.palette_commit_pending_o.value)
        assert waited == expected_wait, (delta, waited, expected_wait)
        await FallingEdge(dut.clk_i)
        dut.swap_request_i.value = 0
        dut.palette_commit_i.value = 0


@cocotb.test(timeout_time=100, timeout_unit="ms")
async def framebuffer_swap_palette_vblank_contract(dut) -> None:
    await _swap_palette_vblank_contract(dut, full=True)


@cocotb.test(timeout_time=100, timeout_unit="ms")
async def framebuffer_half_swap_palette_vblank_contract(dut) -> None:
    await _swap_palette_vblank_contract(dut, full=False)
