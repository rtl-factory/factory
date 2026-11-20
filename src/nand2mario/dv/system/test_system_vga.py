"""n2m_system VGA frame: the background program draws a known scrolled
background through the CPU bus with every retirement matching the CPU
oracle, one PPU frame captured on the public pixel stream matches the
independent Python renderer, and one complete 640x480 VGA frame captured on
the system's output pins after that frame was swapped in equals the PPU
frame scaled 3x and centered with the documented palette, black border and
the common sync timing."""
import os
import random
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge
from cocotb.utils import get_sim_time

from ..core.cpu_model import CPU
from ..core.test_cpu import unpack
from ..memory.memory_model import GameBoyMemory
from ..video import display_model as dm
from ..video import ppu_model as m
from .test_system import HALT_OPCODE, idle_host, interrupt_entry, load_image, power_up, ticks_at
from .test_system_background import VideoPeripherals, compare_frame, dot_edge, dots_at

CLOCK_PERIOD_NS = 40
INSTRUCTION_BOUND = 30000


def read_vga(dut):
    return (int(dut.vga_hsync_o.value), int(dut.vga_vsync_o.value),
            (int(dut.vga_red_o.value), int(dut.vga_green_o.value), int(dut.vga_blue_o.value)))


@cocotb.test(timeout_time=120, timeout_unit='ms')
async def system_vga_frame(dut):
    seed = int(os.environ['DV_SEED'])
    bound = int(os.environ['DV_MAX_CYCLES'])
    assert int(os.environ['DV_CLOCK_HZ']) == 25_000_000
    cocotb.log.info('n2m_system VGA seed=%d (directed) bound=%d', seed, bound)
    image = load_image()
    rng = random.Random(seed)
    cycles = 0
    power_up(dut, rng)
    # The frame store has no power-up value either.
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
        cycles += 1
    await FallingEdge(dut.clk_i)
    dut.rom_load_write_i.value = 0
    await RisingEdge(dut.clk_i)
    await FallingEdge(dut.clk_i)
    dut.reset_i.value = 0
    release_edge = cycles
    release_ns = get_sim_time(unit='ns')

    frame_starts = []

    async def watch_frame_starts():
        while True:
            await RisingEdge(dut.frame_start_o)
            frame_starts.append(round((get_sim_time(unit='ns') - release_ns + 20) / CLOCK_PERIOD_NS))

    cocotb.start_soon(watch_frame_starts())

    # Run the program against the oracle exactly as the background frame test
    # does (system DV plan), so the VRAM and LCD registers are known.
    peripherals = VideoPeripherals()
    memory = GameBoyMemory(image, io_read=peripherals.read, io_write=peripherals.write)   # strict
    model = CPU(mem=memory)
    retirements, interrupts = 0, []
    halted = False
    while not halted:
        await RisingEdge(dut.clk_i)
        await ReadOnly()
        cycles += 1
        assert cycles <= bound, f'finite bound exceeded after {retirements} retirements'
        assert not int(dut.fault_o.value) and not int(dut.locked_o.value) and not int(dut.stopped_o.value)
        if frame_starts and peripherals.enable_dots is None:
            fine = peripherals.lcd[m.SCX] & 7
            peripherals.enable_dots = dots_at(frame_starts[0]) - (m.PIXEL_DOT + fine + 1)
            vblank_edge = dot_edge(peripherals.enable_dots + m.VISIBLE_LINES * m.DOTS_PER_LINE)
            visible_tick = (vblank_edge + 2) * 16384 // 390625 + 1
            peripherals.vblank_tick = visible_tick - 1
        record = unpack(int(dut.retirement_o.value))
        if not record['valid']:
            continue
        ticks = ticks_at(cycles - release_edge)
        if record['is_interrupt']:
            peripherals.advance(ticks - 2)
            expected, bit = interrupt_entry(model, memory, peripherals)
            interrupts.append((ticks, bit))
        else:
            peripherals.advance(ticks - (1 if memory[model.pc] == HALT_OPCODE else 2))
            pending = memory.interrupt_enable() & peripherals.flags.flags
            expected, _, _ = model.step(pending=pending)
        mismatch = {k: (record[k], v) for k, v in expected.items() if record[k] != v}
        assert not mismatch, (f'retirement {retirements} pc={expected["pc_before"]:04x}', mismatch)
        retirements += 1
        assert retirements <= INSTRUCTION_BOUND, 'program did not halt within the instruction bound'
        halted = bool(record['halted']) and not (memory.interrupt_enable() & 0x1F and model.ime)
    assert len(interrupts) == 1 and interrupts[0][1] == 0, ('VBlank interrupt entries', interrupts)
    lcdc, scy, scx, bgp = (peripherals.lcd[r] for r in (m.LCDC, m.SCY, m.SCX, m.BGP))
    assert lcdc & 0x80, 'the program halts with the LCD on'
    cocotb.log.info('program halted after %d retirements and %d edges', retirements, cycles)

    # One PPU frame from the public pixel stream, compared with the renderer.
    expected_frame = m.render_background(bytes(memory.vram), lcdc, scy, scx, bgp)
    observed = [[None] * m.VISIBLE_PIXELS for _ in range(m.VISIBLE_LINES)]
    await RisingEdge(dut.frame_start_o)
    for index in range(m.VISIBLE_PIXELS * m.VISIBLE_LINES):
        if index:
            await RisingEdge(dut.pixel_valid_o)
        await ReadOnly()
        x, y = int(dut.pixel_x_o.value), int(dut.pixel_y_o.value)
        assert (x, y) == (index % m.VISIBLE_PIXELS, index // m.VISIBLE_PIXELS), ('pixel order', index, x, y)
        observed[y][x] = int(dut.pixel_shade_o.value)
    complete_edge = int(get_sim_time(unit='ns')) // CLOCK_PERIOD_NS
    compare_frame(observed, expected_frame)
    m.write_png(Path.cwd() / 'frame.png', observed)

    # The next raster origin after the completion swaps that frame in (a
    # later identical frame may replace it first; the content is static).
    # Capture the output frame that starts after the swap, every clock.
    await RisingEdge(dut.pixel_start_o)
    if int(get_sim_time(unit='ns')) // CLOCK_PERIOD_NS < complete_edge + dm.OUTPUT_LATENCY + 1:
        await RisingEdge(dut.pixel_start_o)
    start_edge = int(get_sim_time(unit='ns')) // CLOCK_PERIOD_NS
    await ReadOnly()
    assert int(dut.display_valid_o.value) == 1, 'a complete frame must be displayed'
    clocks = []
    for _ in range(dm.FRAME_CLOCKS):
        await FallingEdge(dut.clk_i)
        clocks.append(read_vga(dut))
    cycles = int(get_sim_time(unit='ns')) // CLOCK_PERIOD_NS
    assert cycles <= bound, f'finite bound exceeded during the VGA capture ({cycles})'
    dm.compare_clocks(clocks, dm.expected_frame(observed), label='VGA frame')
    dm.write_rgb_png(Path.cwd() / 'vga.png', dm.visible_rows(clocks))
    await RisingEdge(dut.pixel_start_o)
    next_start = int(get_sim_time(unit='ns')) // CLOCK_PERIOD_NS
    assert next_start - start_edge == dm.FRAME_CLOCKS, (start_edge, next_start)

    # Negative control: the VGA comparison rejects a single changed source pixel.
    corrupted = [row[:] for row in observed]
    corrupted[70][80] ^= 2
    try:
        dm.compare_clocks(clocks, dm.expected_frame(corrupted), label='corrupted')
    except AssertionError:
        pass
    else:
        raise AssertionError('VGA comparison accepted a corrupted expectation')
    shades = sorted({shade for row in observed for shade in row})
    assert shades == [0, 1, 2, 3], f'the frame must use all four shades, saw {shades}'
    cocotb.log.info('n2m_system VGA frame passed: retirements=%d vga_start_edge=%d total_cycles=%d',
                    retirements, start_edge, cycles)
