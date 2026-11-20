"""n2m_system frame snapshot: the owned animated program (snapshot.asm)
scrolls its background every frame while the bench, as the UART host on the
system's serial pins, reads back displayed frames through the loader's
snapshot commands with the capture tool's exact request sequence, and
decodes each record with the tool's decoder.

The PPU frame oracle is the system's public pixel stream: every completed
frame is recorded under its completion number, which is the frame ID the
display assigns (1 for the first completion after the core reset). Checked:
SNAP_PIN before the first frame answers NO_FRAME and SNAP_READ NOT_PINNED;
three snapshots with strictly increasing IDs and pairwise different
pictures, each decoded record equal pixel for pixel to the PPU frame of its
ID and on screen at the pin and at the end; the VGA output captured over one
whole output frame during the first pin equals that record scaled 3x and
centred (the display is frozen on the pinned frame: no swap pulse while
pinned); and a HOLD (core reset) clears the pin, after which SNAP_READ
answers NOT_PINNED. A corrupted expectation is rejected (negative control).
"""
import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge, Timer
from cocotb.utils import get_sim_time

from ...scripts import frame_snapshot as fs
from ...scripts.cart_loader import (CMD_HOLD, CMD_RELEASE, CMD_SNAP_PIN, CMD_SNAP_READ, STATUS_NO_FRAME,
                                  STATUS_NOT_PINNED, decode_response, encode_request, response_total_length)
from ..video import display_model as dm
from .test_system import idle_host, load_image, power_up
from .test_system_preload import UART_CLKS_PER_BIT, LineHost

CLOCK_PERIOD_NS = 40
SNAPSHOTS = 3
FRAMES_BETWEEN = 1      # PPU completions the bench lets pass between snapshots
# A SNAP_READ pixel byte waits for clocks the scanout leaves free (up to
# about 500), several times the 80-clock simulated byte time.
EXCHANGE_LIMIT = 300_000


def edge_now():
    return int(get_sim_time(unit='ns')) // CLOCK_PERIOD_NS


class FastLineHost(LineHost):
    """The preload test's line host with an edge-triggered receiver and no
    per-cycle ticker: this test runs millions of clocks."""

    @property
    def clock(self):
        return edge_now()

    @clock.setter
    def clock(self, value):
        pass

    async def ticker(self):
        return

    async def receiver(self):
        d = self.dut
        bit_ns = UART_CLKS_PER_BIT * CLOCK_PERIOD_NS
        while True:
            await FallingEdge(d.uart_tx_o)
            await Timer(bit_ns // 2, unit='ns')
            assert not int(d.uart_tx_o.value), 'start bit glitch on the loader line'
            byte = 0
            for bit in range(8):
                await Timer(bit_ns, unit='ns')
                byte |= int(d.uart_tx_o.value) << bit
            await Timer(bit_ns, unit='ns')
            assert int(d.uart_tx_o.value), 'loader stop bit low'
            assert edge_now() <= self.bound, 'finite simulation bound exceeded'
            self.received.append(byte)

    async def exchange(self, frame, limit_clocks=None):
        """Send a request and collect its whole response, polling once per
        simulated byte time instead of every clock."""
        start = len(self.received)
        await self.send(frame)
        deadline = edge_now() + (limit_clocks or 10 * UART_CLKS_PER_BIT * (len(frame) + 300) + 20000)
        total = None
        while total is None or len(self.received) - start < total:
            assert edge_now() < deadline, f'response timeout after {len(self.received) - start} bytes'
            await Timer(10 * UART_CLKS_PER_BIT * CLOCK_PERIOD_NS, unit='ns')
            if total is None and len(self.received) - start >= 7:
                total = response_total_length(bytes(self.received[start:start + 7]))
        response = decode_response(bytes(self.received[start:start + total]))
        assert response.crc_ok, 'response CRC'
        return response


class Observer:
    """The PPU frame oracle and the bank-policy pulses, timestamped."""

    def __init__(self, dut):
        self.dut = dut
        self.frames = {}          # completion number -> 23040 shades
        self.completions = 0
        self.swaps = []           # edges of frame_swapped_o pulses

    async def pixels(self):
        d = self.dut
        shades = [None] * dm.FRAME_PIXELS
        while True:
            await RisingEdge(d.pixel_valid_o)
            await ReadOnly()
            x, y = int(d.pixel_x_o.value), int(d.pixel_y_o.value)
            shades[y * dm.FRAME_WIDTH + x] = int(d.pixel_shade_o.value)
            if (x, y) == (dm.FRAME_WIDTH - 1, dm.FRAME_HEIGHT - 1):
                self.completions += 1
                assert None not in shades, 'a completed PPU frame left pixels unwritten'
                self.frames[self.completions] = tuple(shades)
                shades = [None] * dm.FRAME_PIXELS

    async def swap_watch(self):
        while True:
            await RisingEdge(self.dut.frame_swapped_o)
            self.swaps.append(edge_now())

    async def wait_completions(self, count):
        target = self.completions + count
        while self.completions < target:
            await RisingEdge(self.dut.pixel_valid_o)


async def capture_vga_frame(dut):
    """Every output clock of the next complete VGA frame (from pixel_start_o)."""
    await RisingEdge(dut.pixel_start_o)
    clocks = []
    for _ in range(dm.FRAME_CLOCKS):
        await FallingEdge(dut.clk_i)
        clocks.append((int(dut.vga_hsync_o.value), int(dut.vga_vsync_o.value),
                       (int(dut.vga_red_o.value), int(dut.vga_green_o.value), int(dut.vga_blue_o.value))))
    return clocks


async def snapshot(host, after_pin=None):
    """The capture tool's request sequence over the line; returns the record,
    the closing header and the edges at which the pin and the closing release
    were answered, and the result of `after_pin`, awaited while pinned before
    the first read."""
    frames = fs.plan_frames()
    responses = []
    pinned_edge = released_edge = None
    result = None
    for index, frame in enumerate(frames):
        if index == len(frames) - 1:
            released_edge = edge_now()          # the release is decided after its request arrives
        response = await host.exchange(frame, EXCHANGE_LIMIT)
        assert response.ok, (index, response.describe())
        responses.append(response)
        if index == 1:
            pinned_edge = edge_now()
            if after_pin is not None:
                result = await after_pin()
    record = b''.join(r.payload for r in responses[2:-2])
    closing = responses[-2].payload
    assert len(record) == fs.RECORD_BYTES and closing[:8] == record[:8], (len(record), closing.hex(), record[:8].hex())
    return record, closing, pinned_edge, released_edge, result


@cocotb.test(timeout_time=400, timeout_unit='ms')
async def system_frame_snapshot(dut):
    seed = int(os.environ['DV_SEED'])
    assert int(os.environ['DV_CLOCK_HZ']) == 25_000_000
    cocotb.log.info('n2m_system frame snapshot seed=%d', seed)
    image = load_image()
    rng = random.Random(seed)
    power_up(dut, rng)
    for bank in range(3):
        memory = dut.u_display.u_frame_ram.g_banks[bank].u_ram.u_enabled.memory
        for index in range(dm.FRAME_PIXELS):
            memory[index].value = rng.randrange(4)

    dut.reset_i.value = 1
    dut.buttons_i.value = 0
    dut.rom_load_write_i.value = 0
    dut.rom_load_address_i.value = 0
    dut.rom_load_data_i.value = 0
    idle_host(dut)
    cocotb.start_soon(Clock(dut.clk_i, CLOCK_PERIOD_NS, unit='ns').start())
    await RisingEdge(dut.clk_i)
    for address, byte in enumerate(image):
        await FallingEdge(dut.clk_i)
        dut.rom_load_write_i.value = 1
        dut.rom_load_address_i.value = address
        dut.rom_load_data_i.value = byte
        await RisingEdge(dut.clk_i)
    await FallingEdge(dut.clk_i)
    dut.rom_load_write_i.value = 0
    await RisingEdge(dut.clk_i)
    await FallingEdge(dut.clk_i)
    dut.reset_i.value = 0

    host = FastLineHost(dut)
    observer = Observer(dut)
    cocotb.start_soon(host.ticker())
    cocotb.start_soon(host.receiver())
    cocotb.start_soon(observer.pixels())
    cocotb.start_soon(observer.swap_watch())

    # First frame: nothing is displayed yet.
    assert (await host.exchange(encode_request(CMD_SNAP_PIN))).code == STATUS_NO_FRAME
    assert (await host.exchange(encode_request(CMD_SNAP_READ, 0, length=8))).code == STATUS_NOT_PINNED
    assert observer.completions == 0 and not int(dut.display_valid_o.value)
    while not int(dut.display_valid_o.value):
        await RisingEdge(dut.clk_i)
    cocotb.log.info('first frame displayed at edge %d', edge_now())

    snapshots = []
    for number in range(SNAPSHOTS):
        if number:
            await observer.wait_completions(FRAMES_BETWEEN)
        record, closing, pinned_edge, released_edge, vga = await snapshot(
            host, after_pin=(lambda: capture_vga_frame(dut)) if number == 0 else None)
        decoded = fs.parse_record(record)
        assert decoded.on_screen and closing[3] >> 7, 'the pinned frame stayed on screen'
        assert decoded.frame_id in observer.frames, (decoded.frame_id, sorted(observer.frames))
        expected = observer.frames[decoded.frame_id]
        if decoded.shades != expected:
            first = next(i for i, (a, b) in enumerate(zip(decoded.shades, expected)) if a != b)
            raise AssertionError(f'snapshot {number} frame {decoded.frame_id}: pixel ({first % 160}, {first // 160}) '
                                 f'is {decoded.shades[first]}, PPU frame has {expected[first]}')
        swaps_while_pinned = [edge for edge in observer.swaps if pinned_edge - 200 < edge < released_edge]
        assert not swaps_while_pinned, ('swap while pinned', swaps_while_pinned, pinned_edge, released_edge)
        rows = fs.raw_indices(decoded)
        if vga is not None:
            clocks = vga
            dm.compare_clocks(clocks, dm.expected_frame(rows), label='VGA frame during the pin')
            corrupted = [row[:] for row in rows]
            corrupted[71][33] ^= 1
            try:
                dm.compare_clocks(clocks, dm.expected_frame(corrupted), label='corrupted')
            except AssertionError:
                pass
            else:
                raise AssertionError('the VGA comparison accepted a corrupted snapshot')
        snapshots.append(decoded)
        cocotb.log.info('snapshot %d: frame ID %d bank %d, pinned edges %d..%d, PPU completions so far %d',
                        number, decoded.frame_id, decoded.bank, pinned_edge, released_edge, observer.completions)

    ids = [s.frame_id for s in snapshots]
    assert all(later > earlier for earlier, later in zip(ids, ids[1:])), ids
    assert len({s.shades for s in snapshots}) == SNAPSHOTS, 'three different pictures'

    # A HOLD resets the core and the display: the pin is gone.
    assert (await host.exchange(encode_request(CMD_SNAP_PIN))).ok
    assert (await host.exchange(encode_request(CMD_HOLD))).held
    assert (await host.exchange(encode_request(CMD_SNAP_READ, 0, length=8))).code == STATUS_NOT_PINNED
    assert (await host.exchange(encode_request(CMD_SNAP_PIN))).code == STATUS_NO_FRAME
    assert (await host.exchange(encode_request(CMD_RELEASE))).ok
    cocotb.log.info('n2m_system frame snapshot passed: ids=%s completions=%d edges=%d',
                    ids, observer.completions, edge_now())
