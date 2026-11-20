"""Optional long-running real Doom system test for nand2doom."""

from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import time
import zlib

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, Event, FallingEdge, First, ReadOnly, RisingEdge, Timer, with_timeout

from src.nand2doom.scripts.host.n2d_uart_debug_host import (
    CMD_HALT, CMD_RESET_PC, CMD_RESUME, ST_OK, input_request, request,
)
from src.nand2doom.dv.io.test_n2d_ps2_keyboard import send_frame
from src.nand2doom.dv.io.test_n2d_uart_debug import _exchange
from src.nand2doom.dv.platform.event_monitors import (
    FramebufferBanks, observe_completion_events, observe_framebuffer_events,
    observe_trap_events, observe_gameplay_retirements, next_writable_input_cycle,
    stop_monitors,
)
from src.nand2doom.dv.software.doom_gameplay import (
    GameplayTrace, elf_symbols, frame_statistics,
)
from src.nand2doom.dv.software.input_witness import InputABI, InputWitness


REPO_ROOT = Path(__file__).resolve().parents[4]
from src.nand2doom.scripts.sw_build.n2d_doom import build as build_doom

CLOCK_PERIOD_NS = 40
FRAME_BYTES = 320 * 200
BOOT_MARKER = 0xD00D3701
FRAME_MARKER = 0xD00D3702
SCENE_MARKER = 0xD00D3703
RUNTIME_FAILURE_MARKER = 0xD00D37EE
RASTER_CYCLES = 800 * 525
POST_INPUT_MAX_CYCLES = 50_000_000
EXPECTED_SCENE_CRC = 0x9DA80229
EXPECTED_SCENE_VGA_CRC = 0x516470A8
# Gameplay after the injected turns is real-time play, so its exact pixels
# depend on CPU timing and are not pinned. The settled frame after both turns
# must instead be a sane rendered scene. Observed values on passing runs: 129
# to 136 palette indices, about 58,000 of 64,000 non-black pixels on all 200
# rows; the VGA raster of that frame showed 96 RGB444 colors, 210,228 non-black
# visible pixels on all 400 rows. Each bound is at most 80% of the lowest
# observed value.
GAMEPLAY_FRAME_MIN_COLORS = 64
GAMEPLAY_FRAME_MIN_NONBLACK_PIXELS = 32_000
GAMEPLAY_FRAME_MIN_NONBLACK_ROWS = 160
GAMEPLAY_VGA_MIN_COLORS = 48
GAMEPLAY_VGA_MIN_NONBLACK_PIXELS = 640 * 160
GAMEPLAY_VGA_MIN_NONBLACK_ROWS = 320


async def _preload_segment(dut, address: int, payload: bytes) -> None:
    started = time.monotonic()
    next_report = 4 * 1024 * 1024
    for offset in range(0, len(payload), 256):
        chunk = payload[offset:offset + 256]
        dut.preload_valid_i.value = 1
        dut.preload_address_i.value = address + offset
        dut.preload_data_i.value = int.from_bytes(chunk.ljust(256, b"\0"), "little")
        dut.preload_byte_enable_i.value = (1 << len(chunk)) - 1
        await RisingEdge(dut.clk_i)
        assert int(dut.preload_ready_o.value)
        assert int(dut.preload_response_valid_o.value)
        assert not int(dut.preload_response_error_o.value)
        dut.preload_valid_i.value = 0
        if offset + len(chunk) >= next_report:
            cocotb.log.info("preload progress bytes=%d elapsed=%.1fs",
                            offset + len(chunk), time.monotonic() - started)
            next_report += 4 * 1024 * 1024
    cocotb.log.info("preload segment address=0x%08x bytes=%d elapsed=%.1fs",
                    address, len(payload), time.monotonic() - started)


async def _capture_vga_crc(dut, artifact_name: str) -> tuple[int, int, int, int]:
    while True:
        await RisingEdge(dut.clk_i)
        await ReadOnly()
        if int(dut.frame_start_o.value):
            break
    crc = 0
    colors: set[int] = set()
    pixels = bytearray()
    nonblack_pixels = 0
    nonblack_rows: set[int] = set()
    for cycle in range(RASTER_CYCLES):
        if cycle:
            await RisingEdge(dut.clk_i)
            await ReadOnly()
        rgb = ((int(dut.vga_red_o.value) << 8)
               | (int(dut.vga_green_o.value) << 4)
               | int(dut.vga_blue_o.value))
        crc = zlib.crc32(rgb.to_bytes(2, "big"), crc)
        if int(dut.visible_o.value):
            colors.add(rgb)
            if rgb != 0:
                visible_index = len(pixels) // 3
                nonblack_pixels += 1
                nonblack_rows.add(visible_index // 640)
            pixels.extend((((rgb >> 8) & 0xf) * 17,
                           ((rgb >> 4) & 0xf) * 17,
                           (rgb & 0xf) * 17))
    assert len(pixels) == 640 * 480 * 3
    artifact = REPO_ROOT / f"workdir/cocotb/n2d_doom_system/{artifact_name}"
    artifact.write_bytes(b"P6\n640 480\n255\n" + pixels)
    return crc & 0xffffffff, len(colors), nonblack_pixels, len(nonblack_rows)


async def _observe_activity(signal, state: dict[str, int], key: str) -> None:
    await RisingEdge(signal)
    state[key] = 1


async def _observe_gameplay_frames(dut, trace: GameplayTrace) -> None:
    try:
        await _gameplay_frame_loop(dut, trace)
    except Exception as error:
        trace.failure = repr(error)
        trace.save(REPO_ROOT / "workdir/cocotb/n2d_doom_system/gameplay-trace.json")
        raise


async def _gameplay_frame_loop(dut, trace: GameplayTrace) -> None:
    """A gameplay frame completes at a bank swap; its start is the last
    back-bank write before the swap, the end of that frame's drawing."""
    framebuffer = bytearray(FRAME_BYTES)
    written = bytearray(FRAME_BYTES)
    banks = FramebufferBanks(dut, framebuffer, written)
    last_write = None
    while True:
        await First(RisingEdge(dut.framebuffer_write_valid_o),
                    dut.framebuffer_front_bank_o.value_change)
        while True:
            await ReadOnly()
            writing = int(dut.framebuffer_write_valid_o.value)
            if banks.sample(dut):
                assert last_write is not None and all(written), "incomplete gameplay frame"
                crc = zlib.crc32(framebuffer) & 0xffffffff
                trace.frame(last_write, int(dut.system_tick_o.value), crc)
                artifact = REPO_ROOT / "workdir/cocotb/n2d_doom_system"
                (artifact / f"gameplay-frame-{len(trace.frames):03d}.bin").write_bytes(framebuffer)
                cocotb.log.info("gameplay frame=%s angles=%s barriers=%s",
                                trace.frames[-1], trace.angles, trace.barriers)
            if not writing:
                break
            last_write = int(dut.system_tick_o.value)
            await RisingEdge(dut.clk_i)


async def _wait_gameplay(dut, trace: GameplayTrace, key: str, phase: int,
                        health: dict[str, object],
                        limit: int = POST_INPUT_MAX_CYCLES) -> None:
    start = int(dut.system_tick_o.value)
    while ((int(dut.system_tick_o.value) - start) & 0xffffffff) < limit:
        await Timer(100_000, unit="ns")
        await ReadOnly()
        assert not health["traps"], f"Doom trapped pc/cause={health['traps']}"
        assert not any(
            marker == RUNTIME_FAILURE_MARKER
            or marker & 0xfffff000 == 0xd00de000
            for marker in health["markers"]
        )
        complete = (trace.settled_frame(phase) is not None if key == "frame"
                    else len(trace.events[key]) > phase)
        if complete:
            cocotb.log.info("gameplay phase=%d reached=%s counters=%s angles=%s barriers=%s",
                            phase, key, trace.counters, trace.angles, trace.barriers)
            return
    raise AssertionError(f"timeout waiting for Doom phase={phase} {key}: {trace.evidence()}")


async def _shadow_cycle_monitor(dut, state: dict[str, object],
                                framebuffer: bytearray,
                                written: bytearray) -> None:
    banks = FramebufferBanks(dut, framebuffer, written)
    previous_marker = int(dut.system_completion_o.value)
    state["baseline_retirements"] = int(dut.observation_order_o.value) + 1
    while True:
        await RisingEdge(dut.clk_i)
        await ReadOnly()
        if int(dut.observation_valid_o.value):
            state["retirements"] = int(state["retirements"]) + 1
        if int(dut.observation_trap_o.value):
            state["trap"] = (
                int(dut.observation_pc_o.value), int(dut.observation_cause_o.value)
            )
        marker = int(dut.system_completion_o.value)
        if marker != previous_marker:
            state["markers"].add(marker)
        previous_marker = marker
        if int(dut.framebuffer_write_valid_o.value):
            state["framebuffer_transactions"] = (
                int(state["framebuffer_transactions"]) + 1
            )
        banks.sample(dut)


@cocotb.test(timeout_time=3_600_000, timeout_unit="ms")
async def doom_boot_render_contract(dut) -> None:
    evidence = {}
    artifact = REPO_ROOT / "workdir/cocotb/n2d_doom_system/gameplay-trace.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    try:
        await _run_doom_contract(dut, evidence, artifact)
    except BaseException as error:
        if "gameplay" in evidence and evidence["gameplay"].failure is None:
            evidence["gameplay"].failure = repr(error)
        raise
    finally:
        if "gameplay" in evidence:
            evidence["gameplay"].save(artifact)


async def _run_doom_contract(dut, evidence, artifact) -> None:
    seed = int(os.environ["DV_SEED"])
    maximum = int(os.environ["DV_MAX_CYCLES"])
    output = REPO_ROOT / "workdir/software/n2d_doom"
    manifest = build_doom(output, REPO_ROOT, None)
    assert json.loads((output / "manifest.json").read_text()) == manifest
    gameplay = GameplayTrace(elf_symbols(output / "n2d_doom.elf"))
    input_armed, input_ready, input_boundary = Event(), Event(), Event()
    input_witness = InputWitness(
        InputABI(output / "n2d_doom.elf", output / "n2d_doom.bin"),
        gameplay, input_ready.set, input_boundary.set,
    )
    gameplay.input_witness = input_witness
    evidence["gameplay"] = gameplay
    cocotb.log.info("Doom seed=%d max_cycles=%d", seed, maximum)

    dut.reset_i.value = 1
    dut.uart_rx_i.value = 1
    dut.sdram_force_error_i.value = 0
    dut.ps2_clk_i.value = 1
    dut.ps2_data_i.value = 1
    dut.preload_mode_i.value = 1
    dut.preload_valid_i.value = 0
    dut.preload_address_i.value = 0
    dut.preload_data_i.value = 0
    dut.preload_byte_enable_i.value = 0
    cocotb.start_soon(
        Clock(dut.clk_i, CLOCK_PERIOD_NS, unit="ns", impl="gpi").start()
    )
    await ClockCycles(dut.clk_i, 4)
    for segment in manifest["segments"]:
        payload = (output / segment["path"]).read_bytes()
        assert len(payload) == int(segment["byte_length"])
        assert hashlib.sha256(payload).hexdigest() == segment["sha256"]
        await _preload_segment(dut, int(segment["address"]), payload)
    dut.preload_valid_i.value = 0
    dut.preload_byte_enable_i.value = 0
    dut.preload_mode_i.value = 0
    await RisingEdge(dut.clk_i)

    dut.reset_i.value = 0
    await ClockCycles(dut.clk_i, 4)
    sequence = 1
    response = await _exchange(dut, request(CMD_RESET_PC, sequence, address=0))
    assert response.status == ST_OK
    sequence += 1

    framebuffer = bytearray(FRAME_BYTES)
    written = bytearray(FRAME_BYTES)
    health = {"traps": [], "markers": set(), "framebuffer_transactions": 0}
    trap_monitor = cocotb.start_soon(observe_trap_events(dut, health))
    # Attach before the first CPU resume: startup clears these BSS words using
    # byte stores, and no counter value may be guessed at scene completion.
    retirement_monitor = cocotb.start_soon(observe_gameplay_retirements(
        dut, gameplay, input_witness, input_armed, artifact))
    completion_monitor = cocotb.start_soon(observe_completion_events(dut, health))
    framebuffer_monitor = cocotb.start_soon(
        observe_framebuffer_events(dut, framebuffer, written, health)
    )
    cache_activity = {"instruction_hits": 0, "instruction_misses": 0,
                      "data_hits": 0, "data_misses": 0}
    cache_monitors = [
        cocotb.start_soon(_observe_activity(signal, cache_activity, key))
        for signal, key in (
            (dut.instruction_hit_event_o, "instruction_hits"),
            (dut.instruction_miss_event_o, "instruction_misses"),
            (dut.data_hit_event_o, "data_hits"),
            (dut.data_miss_event_o, "data_misses"),
        )
    ]
    assert not any(cache_activity.values()), "cache activity latched before CPU resume"
    shadow = None
    shadow_state: dict[str, object] | None = None
    shadow_framebuffer = bytearray(FRAME_BYTES)
    shadow_written = bytearray(FRAME_BYTES)
    if os.environ.get("N2D_DOOM_SHADOW_MONITOR") == "1":
        shadow_state = {"trap": None, "retirements": 0, "markers": set(),
                        "framebuffer_transactions": 0,
                        "baseline_retirements": None}
        shadow = cocotb.start_soon(
            _shadow_cycle_monitor(dut, shadow_state, shadow_framebuffer,
                                  shadow_written)
        )
    start_cycle = int(dut.system_tick_o.value)
    response = await _exchange(dut, request(CMD_RESUME, sequence))
    assert response.status == ST_OK
    sequence = (sequence + 1) & 0xff
    cocotb.log.info("Doom CPU resumed after sparse preload")
    last_report = 0
    while True:
        await Timer(1_000_000, unit="ns")
        await ReadOnly()
        cycle = (int(dut.system_tick_o.value) - start_cycle) & 0xffffffff
        assert not health["traps"], f"Doom trapped pc/cause={health['traps']}"
        failures = [
            marker for marker in health["markers"]
            if marker == RUNTIME_FAILURE_MARKER
            or marker & 0xfffff000 == 0xd00de000
        ]
        assert not failures, f"Doom runtime failure/exit markers={failures}"
        # The scene marker follows the scene's swap request; once that swap
        # completes, the front bank holds the scene until the next request.
        if (SCENE_MARKER in health["markers"] and all(written)
                and not int(dut.framebuffer_swap_pending_o.value)):
            break
        if cycle >= maximum:
            if shadow_state is not None:
                assert not health["traps"] and shadow_state["trap"] is None
                assert health["markers"] == shadow_state["markers"]
                assert (health["framebuffer_transactions"]
                        == shadow_state["framebuffer_transactions"])
                assert framebuffer == shadow_framebuffer
                assert written == shadow_written
                assert ((int(dut.observation_order_o.value) + 1
                         - int(shadow_state["baseline_retirements"]))
                        == shadow_state["retirements"])
                cocotb.log.info(
                    "monitor equivalence PASS retirements=%d transactions=%d",
                    shadow_state["retirements"], health["framebuffer_transactions"],
                )
            raise AssertionError(
                f"Doom frame timeout retirements={int(dut.observation_order_o.value) + 1} "
                f"boot_seen={BOOT_MARKER in health['markers']} "
                f"framebuffer_bytes={sum(written)}"
            )
        if cycle - last_report >= 5_000_000:
            last_report = cycle
            cocotb.log.info(
                "execution progress cycles=%d retirements=%d pc=0x%08x "
                "marker=0x%08x framebuffer_bytes=%d",
                cycle, int(dut.observation_order_o.value) + 1,
                int(dut.observation_pc_o.value), int(dut.system_completion_o.value),
                sum(written),
            )
    retirements = int(dut.observation_order_o.value) + 1
    await stop_monitors(framebuffer_monitor, *([shadow] if shadow is not None else []))

    crc = zlib.crc32(framebuffer) & 0xffffffff
    assert BOOT_MARKER in health["markers"]
    assert all(cache_activity.values()), f"missing cache activity: {cache_activity}"
    assert len(set(framebuffer)) >= 8, "Doom frame is blank or palette-index trivial"
    assert crc == EXPECTED_SCENE_CRC, f"unexpected Doom scene CRC {crc:08x}"
    await FallingEdge(dut.clk_i)
    response = await _exchange(dut, request(CMD_HALT, sequence))
    assert response.status == ST_OK
    sequence = (sequence + 1) & 0xff
    vga_crc, vga_colors, nonblack_pixels, nonblack_rows = await _capture_vga_crc(
        dut, "doom-scene-vga.ppm"
    )
    assert vga_colors >= 8, "committed Doom palette did not reach VGA RGB"
    assert vga_crc == EXPECTED_SCENE_VGA_CRC, f"unexpected VGA scene CRC {vga_crc:08x}"
    assert nonblack_pixels >= 640 * 160, "completed Doom scene is mostly black"
    assert nonblack_rows >= 320, "completed Doom scene lacks recognizable height"
    await FallingEdge(dut.clk_i)
    response = await _exchange(dut, request(CMD_RESUME, sequence))
    assert response.status == ST_OK
    sequence = (sequence + 1) & 0xff
    cocotb.log.info(
        "DOOM FIRST FRAME crc=%08x vga_crc=%08x vga_colors=%d "
        "nonblack_pixels=%d nonblack_rows=%d retirements=%d image=%s wad=%s",
        crc, vga_crc, vga_colors, nonblack_pixels, nonblack_rows,
        retirements, manifest["segments"][0]["sha256"], manifest["segments"][1]["sha256"],
    )

    frame_observer = cocotb.start_soon(_observe_gameplay_frames(dut, gameplay))

    await FallingEdge(dut.clk_i)
    gameplay.begin_phase(0, int(dut.system_tick_o.value))
    await send_frame(dut, 0xE0)
    await send_frame(dut, 0x74)
    await _wait_gameplay(dut, gameplay, "make", 0, health)
    await _wait_gameplay(dut, gameplay, "turn", 0, health)
    # Release after command construction, before waiting for the slow renderer
    # and player update. Otherwise held input accumulates timing-dependent tics.
    await FallingEdge(dut.clk_i)
    await send_frame(dut, 0xE0)
    await send_frame(dut, 0xF0)
    await send_frame(dut, 0x74)
    await _wait_gameplay(dut, gameplay, "break", 0, health)
    await _wait_gameplay(dut, gameplay, "angle", 0, health)
    await _wait_gameplay(dut, gameplay, "frame", 0, health)
    before_uart_crc = gameplay.settled_frame(0)["crc"]
    await FallingEdge(dut.clk_i)
    gameplay.begin_phase(1, int(dut.system_tick_o.value))
    input_witness.arm(int(dut.system_tick_o.value))
    input_armed.set()
    # Phase preparation may occur mid-BuildNewTic. Synchronize to a fresh
    # architectural entry before sending MAKE; partial seams remain evidence.
    await with_timeout(input_boundary.wait(), POST_INPUT_MAX_CYCLES * CLOCK_PERIOD_NS, "ns")
    input_witness.start_make(await next_writable_input_cycle(dut, input_witness.boundary_tick))
    response = await _exchange(dut, input_request(sequence, 0x274))
    assert response.status == ST_OK
    input_witness.ack_make(int(dut.system_tick_o.value))
    sequence = (sequence + 1) & 0xff
    # The latched event covers the same MAKE invocation's successful EMPTY
    # read/zero/returns, before command construction. ACK completion serializes
    # parser transactions. Neither ACK nor retired EMPTY is a device margin.
    await with_timeout(input_ready.wait(), POST_INPUT_MAX_CYCLES * CLOCK_PERIOD_NS, "ns")
    input_witness.start_break(await next_writable_input_cycle(
        dut, max(input_witness.ready_tick, input_witness.make_ack_tick)))
    response = await _exchange(dut, input_request(sequence, 0x374))
    assert response.status == ST_OK
    input_witness.break_ack_tick = int(dut.system_tick_o.value)
    sequence = (sequence + 1) & 0xff
    await _wait_gameplay(dut, gameplay, "break", 1, health)
    await _wait_gameplay(dut, gameplay, "angle", 1, health)
    await _wait_gameplay(dut, gameplay, "frame", 1, health)
    gameplay.check_final()
    input_witness.check_final()
    settled = gameplay.settled_frame(1)
    later_crc = settled["crc"]
    assert later_crc != before_uart_crc, "UART gameplay turn did not change the rendered state"
    # The settled frame is the first one drawn after the UART turn, its break
    # and the neutral barrier; every earlier completed frame predates it.
    earlier_crcs = [frame["crc"] for frame in gameplay.frames[:gameplay.frames.index(settled)]]
    assert before_uart_crc in earlier_crcs
    assert later_crc not in earlier_crcs, "UART gameplay turn repeated an earlier rendered frame"
    settled_image = (REPO_ROOT / "workdir/cocotb/n2d_doom_system"
                     / f"gameplay-frame-{len(earlier_crcs) + 1:03d}.bin").read_bytes()
    assert len(settled_image) == FRAME_BYTES
    assert zlib.crc32(settled_image) & 0xffffffff == later_crc, "settled frame artifact mismatch"
    frame_colors, frame_nonblack, frame_rows = frame_statistics(settled_image)
    await FallingEdge(dut.clk_i)
    response = await _exchange(dut, request(CMD_HALT, sequence))
    assert response.status == ST_OK
    gameplay_vga_crc, gameplay_colors, gameplay_nonblack, gameplay_rows = (
        await _capture_vga_crc(dut, "doom-gameplay-vga.ppm")
    )
    assert gameplay_vga_crc != vga_crc, "input did not change the committed VGA scene"
    gameplay.check_final()
    gameplay.save(artifact)
    # Report the actual images before the sanity assertions, so a failing run
    # has inspectable evidence rather than only counts. The CRCs are evidence
    # of what was rendered, not expectations: real-time play after injected
    # input depends on CPU timing, so correct speed changes move them.
    cocotb.log.info(
        "DOOM GAMEPLAY OBSERVED indexed=%08x vga=%08x frame_colors=%d "
        "frame_pixels=%d frame_rows=%d vga_colors=%d vga_pixels=%d vga_rows=%d "
        "angles=%s barriers=%s", later_crc, gameplay_vga_crc, frame_colors,
        frame_nonblack, frame_rows, gameplay_colors, gameplay_nonblack,
        gameplay_rows, gameplay.angles, gameplay.barriers,
    )
    assert frame_colors >= GAMEPLAY_FRAME_MIN_COLORS, f"gameplay frame colors {frame_colors}"
    assert frame_nonblack >= GAMEPLAY_FRAME_MIN_NONBLACK_PIXELS, (
        f"gameplay frame is mostly black: {frame_nonblack} non-black pixels"
    )
    assert frame_rows >= GAMEPLAY_FRAME_MIN_NONBLACK_ROWS, f"gameplay frame rows {frame_rows}"
    assert gameplay_colors >= GAMEPLAY_VGA_MIN_COLORS, f"gameplay VGA colors {gameplay_colors}"
    assert gameplay_nonblack >= GAMEPLAY_VGA_MIN_NONBLACK_PIXELS, (
        f"gameplay VGA raster is mostly black: {gameplay_nonblack} non-black pixels"
    )
    assert gameplay_rows >= GAMEPLAY_VGA_MIN_NONBLACK_ROWS, f"gameplay VGA rows {gameplay_rows}"
    # Finish cancelling every monitor before the test returns; see stop_monitors.
    await stop_monitors(retirement_monitor, frame_observer, trap_monitor,
                        completion_monitor, *cache_monitors)
    cocotb.log.info(
        "DOOM GAMEPLAY PASS first_crc=%08x later_crc=%08x "
        "physical_and_uart_turns=%d vga_crc=%08x gameplay_vga_crc=%08x "
        "cache_activity=%s",
        crc, later_crc, len(gameplay.events["turn"]), vga_crc, gameplay_vga_crc,
        cache_activity,
    )
