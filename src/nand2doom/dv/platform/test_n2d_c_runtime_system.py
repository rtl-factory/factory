from __future__ import annotations

import json
import os
from pathlib import Path
import zlib

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, ReadOnly, RisingEdge

from src.nand2doom.scripts.host.n2d_uart_debug_host import (
    CMD_HALT, CMD_LOAD, CMD_PEEK, CMD_POKE, CMD_RESET_PC, CMD_RESUME,
    ST_NOT_HALTED, ST_OK, ST_TIMEOUT, input_request, request,
)
from src.nand2doom.dv.io.test_n2d_ps2_keyboard import send_frame
from src.nand2doom.dv.io.test_n2d_uart_debug import _exchange
from src.nand2doom.dv.platform.event_monitors import (
    observe_completion_events, observe_framebuffer_events, observe_trap_events,
)
from src.nand2doom.scripts.sw_build.n2d_image import build_image, scratch_path


REPO_ROOT = Path(__file__).resolve().parents[4]

CLOCK_HZ = 25_000_000
CLOCK_PERIOD_NS = 40
FRAME_WIDTH = 320
FRAME_HEIGHT = 200
FRAME_BYTES = FRAME_WIDTH * FRAME_HEIGHT
RASTER_CYCLES = 800 * 525
SIGNATURE_ADDRESS = 0x00004000
COMPLETION_MARKER = 0xD00D0035
INPUT_EVENTS = (0x01C, 0x11C, 0x01C, 0x11C)


def _input_digest() -> int:
    value = 0x811C9DC5
    for event in INPUT_EVENTS:
        value = ((value * 33) ^ event) & 0xFFFFFFFF
    return value


def _stack_call_expected() -> int:
    local0 = (0x2468ACE0 + 3) & 0xFFFFFFFF
    local1 = local0 ^ 0x13579BDF
    local2 = (local1 + 7) & 0xFFFFFFFF
    return local2 ^ local0


def _expected_signature() -> tuple[int, ...]:
    stack = _stack_call_expected()
    return (
        COMPLETION_MARKER, stack, 12345 * 37, 12345 // 37, 12345 % 37,
        0xA7332211, 0x5A000000, stack ^ 0x5A5A5A5A, _input_digest(),
        0x51A70001,
    )


def _expected_frame() -> bytes:
    frame = bytearray(
        ((x * 3 + y * 5) & 0xFF)
        for y in range(FRAME_HEIGHT)
        for x in range(FRAME_WIDTH)
    )
    frame[0] = INPUT_EVENTS[0] & 0xFF
    return bytes(frame)


def _expected_vga_crc(frame: bytes) -> int:
    crc = 0
    for y in range(525):
        for x in range(800):
            rgb = 0
            if x < 640 and 40 <= y < 440:
                index = frame[((y - 40) // 2) * FRAME_WIDTH + (x // 2)]
                red = (index >> 4) & 0xF
                green = index & 0xF
                rgb = (red << 8) | (green << 4) | (red ^ green)
            crc = zlib.crc32(rgb.to_bytes(2, "big"), crc)
    return crc & 0xFFFFFFFF


async def _observe_execution(dut, framebuffer, framebuffer_written, state) -> None:
    while True:
        await RisingEdge(dut.clk_i)
        await ReadOnly()
        state["instruction_hits"] += int(dut.instruction_hit_event_o.value)
        state["instruction_misses"] += int(dut.instruction_miss_event_o.value)
        state["data_hits"] += int(dut.data_hit_event_o.value)
        state["data_misses"] += int(dut.data_miss_event_o.value)
        state["debug_requests"] += int(dut.debug_memory_request_valid_o.value)
        state["ps2_events"] += int(dut.ps2_event_consumed_o.value)
        if int(dut.framebuffer_write_valid_o.value):
            state["framebuffer_transactions"] += 1
            address = int(dut.framebuffer_write_address_o.value)
            data = int(dut.framebuffer_write_data_o.value)
            strobes = int(dut.framebuffer_write_strobe_o.value)
            for lane in range(4):
                if strobes & (1 << lane):
                    byte_address = address + lane
                    assert byte_address < FRAME_BYTES
                    framebuffer[byte_address] = (data >> (8 * lane)) & 0xFF
                    framebuffer_written[byte_address] = 1
        if int(dut.observation_valid_o.value):
            state["retirements"] += 1
            state["last_pc"] = int(dut.observation_pc_o.value)
            assert not int(dut.observation_trap_o.value), (
                f"unexpected trap pc=0x{state['last_pc']:08x} "
                f"cause={int(dut.observation_cause_o.value)}"
            )
        if int(dut.system_completion_o.value) == COMPLETION_MARKER:
            state["completion_seen"] = True


async def _observe_trap_cycles(dut, state) -> None:
    while state["trap"] is None:
        await RisingEdge(dut.clk_i)
        await ReadOnly()
        if int(dut.observation_trap_o.value):
            state["trap"] = (
                int(dut.observation_pc_o.value), int(dut.observation_cause_o.value)
            )


def _build_validation_image() -> tuple[dict[str, object], bytes]:
    output = scratch_path("software/n2d_validation", REPO_ROOT)
    manifest = build_image(output, REPO_ROOT)
    recorded = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert recorded == manifest
    image = (output / "n2d_validation.bin").read_bytes()
    segment = manifest["segments"][0]
    assert len(image) == segment["byte_length"]
    assert segment["address"] == 0
    return manifest, image


async def _initialize(dut) -> None:
    dut.reset_i.value = 1
    dut.uart_rx_i.value = 1
    dut.sdram_force_error_i.value = 0
    dut.ps2_clk_i.value = 1
    dut.ps2_data_i.value = 1
    if hasattr(dut, "preload_mode_i"):
        dut.preload_mode_i.value = 0
        dut.preload_valid_i.value = 0
        dut.preload_address_i.value = 0
        dut.preload_data_i.value = 0
        dut.preload_byte_enable_i.value = 0
    cocotb.start_soon(Clock(dut.clk_i, CLOCK_PERIOD_NS, unit="ns").start())
    await ClockCycles(dut.clk_i, 4)


async def _preload_manifest(dut, image: bytes) -> None:
    dut.preload_mode_i.value = 1
    await RisingEdge(dut.clk_i)
    for address in range(0, len(image), 256):
        chunk = image[address : address + 256]
        dut.preload_valid_i.value = 1
        dut.preload_address_i.value = address
        dut.preload_data_i.value = int.from_bytes(chunk.ljust(256, b"\0"), "little")
        dut.preload_byte_enable_i.value = (1 << len(chunk)) - 1
        await RisingEdge(dut.clk_i)
        assert int(dut.preload_ready_o.value)
        assert int(dut.preload_response_valid_o.value)
        assert not int(dut.preload_response_error_o.value)
        dut.preload_valid_i.value = 0
    dut.preload_valid_i.value = 0
    dut.preload_byte_enable_i.value = 0
    dut.preload_mode_i.value = 0
    await RisingEdge(dut.clk_i)


async def _serial_load(dut, image: bytes, sequence: int) -> int:
    padded = image + bytes((-len(image)) % 4)
    words = tuple(
        int.from_bytes(padded[offset : offset + 4], "little")
        for offset in range(0, len(padded), 4)
    )
    for word_index in range(0, len(words), 8):
        response = await _exchange(
            dut,
            request(CMD_LOAD, sequence, address=word_index * 4,
                    words=words[word_index : word_index + 8], byte_enable=0xF),
        )
        assert response.status == ST_OK, (
            f"LOAD failed sequence={sequence} word={word_index} "
            f"status={response.status}"
        )
        sequence = (sequence + 1) & 0xFF
    return sequence


async def _capture_actual_frame_crc(dut) -> int:
    while True:
        await RisingEdge(dut.clk_i)
        await ReadOnly()
        if int(dut.frame_start_o.value):
            break
    crc = 0
    for cycle in range(RASTER_CYCLES):
        if cycle:
            await RisingEdge(dut.clk_i)
            await ReadOnly()
        rgb = ((int(dut.vga_red_o.value) << 8)
               | (int(dut.vga_green_o.value) << 4)
               | int(dut.vga_blue_o.value))
        crc = zlib.crc32(rgb.to_bytes(2, "big"), crc)
    return crc & 0xFFFFFFFF


async def _settle_palette_commit(dut) -> None:
    """Wait past the next frame end and the vertical-blank line start that
    applies a pending palette commit (the swap happens at line 480)."""
    for _ in range(800 * 525 + 1600):
        await RisingEdge(dut.clk_i)
        if int(dut.frame_end_o.value):
            break
    else:
        raise AssertionError("no frame end while halted")
    for _ in range(1600):
        await RisingEdge(dut.clk_i)
    await FallingEdge(dut.clk_i)


async def _run_system(dut, *, preload: bool, capture_vga: bool) -> None:
    seed = int(os.environ["DV_SEED"])
    maximum = int(os.environ["DV_MAX_CYCLES"])
    assert int(os.environ["DV_CLOCK_HZ"]) == CLOCK_HZ
    manifest, image = _build_validation_image()
    cocotb.log.info("n2d integrated system seed=%d max_cycles=%d preload=%s",
                    seed, maximum, preload)

    await _initialize(dut)
    if preload:
        await _preload_manifest(dut, image)
    dut.reset_i.value = 0
    await ClockCycles(dut.clk_i, 4)
    assert int(dut.halt_ack_o.value) == 1

    sequence = 1
    if not preload:
        sequence = await _serial_load(dut, image, sequence)
    reset_pc = await _exchange(dut, request(CMD_RESET_PC, sequence, address=0))
    assert reset_pc.status == ST_OK
    sequence = (sequence + 1) & 0xFF

    framebuffer = bytearray(FRAME_BYTES)
    framebuffer_written = bytearray(FRAME_BYTES)
    state = {
        "retirements": 0, "instruction_hits": 0, "instruction_misses": 0,
        "data_hits": 0, "data_misses": 0, "completion_seen": False,
        "last_pc": 0, "debug_requests": 0, "ps2_events": 0,
        "framebuffer_transactions": 0,
    }
    event_framebuffer = bytearray(FRAME_BYTES)
    event_framebuffer_written = bytearray(FRAME_BYTES)
    event_state = {"framebuffer_transactions": 0, "markers": set(), "traps": []}
    observer = cocotb.start_soon(
        _observe_execution(dut, framebuffer, framebuffer_written, state)
    )
    framebuffer_event_observer = cocotb.start_soon(
        observe_framebuffer_events(
            dut, event_framebuffer, event_framebuffer_written, event_state
        )
    )
    completion_event_observer = cocotb.start_soon(
        observe_completion_events(dut, event_state)
    )
    resumed = await _exchange(dut, request(CMD_RESUME, sequence))
    assert resumed.status == ST_OK
    sequence = (sequence + 1) & 0xFF
    async def send_physical_make_break() -> None:
        await send_frame(dut, 0x1C)
        await send_frame(dut, 0xF0)
        await send_frame(dut, 0x1C)

    keyboard = cocotb.start_soon(send_physical_make_break())
    await keyboard
    debug_before_input = state["debug_requests"]
    uart_make = await _exchange(dut, input_request(sequence, 0x01C))
    assert uart_make.status == ST_OK
    sequence = (sequence + 1) & 0xFF
    uart_break = await _exchange(dut, input_request(sequence, 0x11C))
    assert uart_break.status == ST_OK
    sequence = (sequence + 1) & 0xFF
    assert state["debug_requests"] == debug_before_input

    for _ in range(maximum):
        await RisingEdge(dut.clk_i)
        await ReadOnly()
        if state["completion_seen"]:
            break
    assert state["completion_seen"], (
        f"completion timeout retirements={state['retirements']} "
        f"last_pc=0x{state['last_pc']:08x} bytes={sum(framebuffer_written)}"
    )
    assert all(framebuffer_written)
    assert event_framebuffer_written == framebuffer_written
    assert event_framebuffer == framebuffer
    assert event_state["framebuffer_transactions"] == state["framebuffer_transactions"]
    assert state["framebuffer_transactions"] >= FRAME_BYTES // 4
    assert COMPLETION_MARKER in event_state["markers"]
    cocotb.log.info(
        "event/cycle monitor equivalence transactions=%d bytes=%d markers=%d",
        state["framebuffer_transactions"], sum(framebuffer_written),
        len(event_state["markers"]),
    )
    assert state["ps2_events"] == 4
    expected_frame = _expected_frame()
    assert framebuffer == expected_frame

    await FallingEdge(dut.clk_i)
    if not preload:
        dut.sdram_force_error_i.value = 1
        failed_halt = await _exchange(dut, request(CMD_HALT, sequence))
        assert failed_halt.status == ST_TIMEOUT
        sequence = (sequence + 1) & 0xFF
        debug_before = state["debug_requests"]
        rejected = await _exchange(
            dut, request(CMD_PEEK, sequence, address=SIGNATURE_ADDRESS, count=1)
        )
        assert rejected.status == ST_NOT_HALTED
        assert state["debug_requests"] == debug_before
        sequence = (sequence + 1) & 0xFF
        dut.sdram_force_error_i.value = 0

    halted = await _exchange(dut, request(CMD_HALT, sequence))
    assert halted.status == ST_OK
    sequence = (sequence + 1) & 0xFF

    # Why: the firmware's palette commit swaps banks at the next vertical
    # blank, which may still be pending when a fast core halts. Pass one
    # vertical blank so the inactive (readback) bank no longer depends on core
    # speed.
    await _settle_palette_commit(dut)

    framebuffer_before = await _exchange(
        dut, request(CMD_PEEK, sequence, address=0x10000000, count=2)
    )
    assert framebuffer_before.status == ST_OK
    assert framebuffer_before.words == (0x0906031C, 0x15120F0C)
    sequence = (sequence + 1) & 0xFF
    framebuffer_poke = await _exchange(
        dut, request(CMD_POKE, sequence, address=0x10000000,
                     words=(0x0000AA00, 0x0000BB00), byte_enable=0x2)
    )
    assert framebuffer_poke.status == ST_OK
    sequence = (sequence + 1) & 0xFF
    framebuffer_after = await _exchange(
        dut, request(CMD_PEEK, sequence, address=0x10000000, count=2)
    )
    assert framebuffer_after.status == ST_OK
    assert framebuffer_after.words == (0x0906AA1C, 0x1512BB0C)
    sequence = (sequence + 1) & 0xFF

    palette_before = await _exchange(
        dut, request(CMD_PEEK, sequence, address=0x1001001C, count=1)
    )
    assert palette_before.status == ST_OK and palette_before.words == (0,)
    sequence = (sequence + 1) & 0xFF
    palette_poke = await _exchange(
        dut, request(CMD_POKE, sequence, address=0x1001001C,
                     words=(0x00000ABC,), byte_enable=0x3)
    )
    assert palette_poke.status == ST_OK
    sequence = (sequence + 1) & 0xFF
    palette_after = await _exchange(
        dut, request(CMD_PEEK, sequence, address=0x1001001C, count=1)
    )
    assert palette_after.status == ST_OK and palette_after.words == (0xABC,)
    sequence = (sequence + 1) & 0xFF

    signature = await _exchange(
        dut, request(CMD_PEEK, sequence, address=SIGNATURE_ADDRESS, count=8)
    )
    assert signature.status == ST_OK
    sequence = (sequence + 1) & 0xFF
    input_signature = await _exchange(
        dut, request(CMD_PEEK, sequence, address=SIGNATURE_ADDRESS + 32, count=2)
    )
    assert input_signature.status == ST_OK
    assert signature.words + input_signature.words == _expected_signature()
    sequence = (sequence + 1) & 0xFF

    replacement = 0xC0DEC0DE
    poked = await _exchange(
        dut, request(CMD_POKE, sequence, address=SIGNATURE_ADDRESS + 32,
                     words=(replacement,), byte_enable=0xF),
    )
    assert poked.status == ST_OK
    sequence = (sequence + 1) & 0xFF
    readback = await _exchange(
        dut, request(CMD_PEEK, sequence, address=SIGNATURE_ADDRESS + 32, count=1)
    )
    assert readback.status == ST_OK and readback.words == (replacement,)
    sequence = (sequence + 1) & 0xFF

    framebuffer_crc = zlib.crc32(framebuffer) & 0xFFFFFFFF
    vga_crc = None
    if capture_vga:
        # Why: the host pokes landed in the back bank, so the displayed frame
        # must be unchanged.
        vga_crc = await _capture_actual_frame_crc(dut)
        assert vga_crc == _expected_vga_crc(expected_frame)
    assert state["retirements"] > 64_000
    assert state["instruction_hits"] and state["instruction_misses"]
    assert state["data_hits"] and state["data_misses"]
    observer.cancel()
    framebuffer_event_observer.cancel()
    completion_event_observer.cancel()

    await FallingEdge(dut.clk_i)
    cycle_trap = {"trap": None}
    event_trap = {"traps": []}
    cycle_trap_observer = cocotb.start_soon(_observe_trap_cycles(dut, cycle_trap))
    event_trap_observer = cocotb.start_soon(observe_trap_events(dut, event_trap))
    trap_address = 0x00000000
    illegal = await _exchange(
        dut, request(CMD_POKE, sequence, address=trap_address,
                     words=(0xFFFFFFFF,), byte_enable=0xF)
    )
    assert illegal.status == ST_OK
    sequence = (sequence + 1) & 0xFF
    invalid_pc = await _exchange(
        dut, request(CMD_RESET_PC, sequence, address=trap_address)
    )
    assert invalid_pc.status == ST_OK
    sequence = (sequence + 1) & 0xFF
    resumed_invalid = await _exchange(dut, request(CMD_RESUME, sequence))
    assert resumed_invalid.status == ST_OK
    for _ in range(4096):
        await RisingEdge(dut.clk_i)
        if cycle_trap["trap"] is not None and event_trap["traps"]:
            break
    assert cycle_trap["trap"] is not None
    assert event_trap["traps"]
    assert cycle_trap["trap"] == event_trap["traps"][0]
    cocotb.log.info("event/cycle trap equivalence trap=%s", cycle_trap["trap"])
    cycle_trap_observer.cancel()
    event_trap_observer.cancel()
    cocotb.log.info(
        "PASS image_bytes=%d sha256=%s retirements=%d framebuffer_crc=%08x "
        "vga_crc=%s ps2_events=%d i_hit/miss=%d/%d d_hit/miss=%d/%d gcc=%s",
        len(image), manifest["segments"][0]["sha256"], state["retirements"],
        framebuffer_crc, "none" if vga_crc is None else f"{vga_crc:08x}",
        state["ps2_events"], state["instruction_hits"],
        state["instruction_misses"], state["data_hits"], state["data_misses"],
        manifest["toolchain"]["compiler"],
    )


@cocotb.test(timeout_time=240_000, timeout_unit="ms")
async def preloaded_integrated_system_contract(dut) -> None:
    await _run_system(dut, preload=True, capture_vga=True)


@cocotb.test(timeout_time=240_000, timeout_unit="ms")
async def uart_loaded_c_runtime_contract(dut) -> None:
    await _run_system(dut, preload=False, capture_vga=False)
