"""n2m_system background frame: a locally built program draws a known scrolled
background through the CPU bus, every retirement matches the CPU oracle
(including the VBlank interrupt entry predicted from the public frame start),
the registers it reads back land in the WRAM signature, and after the final
HALT one full frame of the PPU pixel stream matches the independent Python
renderer of the oracle's VRAM pixel for pixel."""
import os
import random
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge
from cocotb.utils import get_sim_time

from ..core.cpu_model import CPU
from ..core.test_cpu import unpack
from ..interrupts.interrupts_model import VBLANK
from ..memory.memory_model import GameBoyMemory
from ..video import ppu_model as m
from .peripheral_model import Peripherals
from .test_system import (idle_host, HALT_OPCODE, compare_signature, defined_signature, interrupt_entry,
                          load_image, power_up, store_bytes, ticks_at, store_array)

CLOCK_PERIOD_NS = 40
INSTRUCTION_BOUND = 30000
DOTS_PER_FRAME = m.DOTS_PER_FRAME
DOT_EDGES = 390625 / 65536      # Why: the exact 25 MHz / 4,194,304 Hz ratio, not a rounded one.
# Why: 5 because dispatch starts on the boundary at which the VBlank flag is visible,
# with no discarded opcode read before the five entry cycles of a CPU
# sleeping in HALT.
HALT_WAKE_CYCLES = 5


def dots_at(edges):
    """Dot enables sampled in the first ``edges`` active edges after reset
    release (core HAS timebase contract)."""
    return edges * 65536 // 390625


def dot_edge(pulse):
    """Active edge carrying dot pulse number ``pulse`` (1-based)."""
    return (pulse * 390625 + 65535) // 65536


def tick_edge(tick):
    """Active edge carrying machine pulse number ``tick`` (1-based)."""
    return (tick * 390625 + 16383) // 16384


class VideoPeripherals(Peripherals):
    """The merged timer/IF oracle plus the PPU's static LCD registers and a
    VBlank flag raised at the tick the test predicts from the frame start."""

    def __init__(self):
        super().__init__()
        self.lcd = {m.LCDC: 0, m.SCY: 0, m.SCX: 0, m.LYC: 0, m.BGP: 0}
        self.vblank_tick = None
        self.enable_dots = None
        self.lcdc_writes = 0

    def advance(self, ticks):
        previous = self.ticks
        super().advance(ticks)
        if self.vblank_tick is not None and previous < self.vblank_tick <= self.ticks:
            self.flags.edge(sources=VBLANK)

    def read(self, address):
        if address in self.lcd:
            return self.lcd[address]
        if address == m.LY:
            assert self.enable_dots is not None, 'LY read before the frame start fixed the raster phase'
            lcd_dot = 4 * self.ticks - self.enable_dots - 1
            return (lcd_dot // m.DOTS_PER_LINE) % m.LINES_PER_FRAME
        return super().read(address)

    def write(self, address, value):
        if address in self.lcd:
            self.lcd[address] = value
            self.lcdc_writes += int(address == m.LCDC and value & 0x80 != 0)
        else:
            super().write(address, value)


def compare_frame(observed, expected):
    """Exact pixel comparison with the first mismatch reported."""
    assert len(observed) == len(expected) == m.VISIBLE_LINES
    for y, (row, want) in enumerate(zip(observed, expected)):
        assert len(row) == len(want) == m.VISIBLE_PIXELS, (y, len(row))
        for x, (got, shade) in enumerate(zip(row, want)):
            assert got == shade, f'pixel ({x},{y}) observed {got} expected {shade}'


@cocotb.test(timeout_time=60, timeout_unit='ms')
async def system_background_frame(dut):
    seed = int(os.environ['DV_SEED'])
    bound = int(os.environ['DV_MAX_CYCLES'])
    assert int(os.environ['DV_CLOCK_HZ']) == 25_000_000
    cocotb.log.info('n2m_system background seed=%d (directed) bound=%d', seed, bound)
    image = load_image()
    cycles = 0
    power_up(dut, random.Random(seed))

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

    peripherals = VideoPeripherals()
    memory = GameBoyMemory(image, io_read=peripherals.read, io_write=peripherals.write)
    model = CPU(mem=memory)
    retirements, interrupts = 0, []
    pixels_before_lcd = 0
    halted = False
    while not halted:
        await RisingEdge(dut.clk_i)
        await ReadOnly()
        cycles += 1
        assert cycles <= bound, f'finite bound exceeded after {retirements} retirements'
        assert not int(dut.fault_o.value), 'unexpected bus fault'
        assert not int(dut.locked_o.value), 'unexpected illegal-opcode lock'
        assert not int(dut.stopped_o.value), 'unexpected STOP'
        if not peripherals.lcd[m.LCDC] & 0x80:
            pixels_before_lcd += int(dut.pixel_valid_o.value)
        if frame_starts and peripherals.enable_dots is None:
            # Why: pixel (0, 0) is emitted by the pulse ending LCD dot 92 + fine,
            # the 93 + fine'th dot pulse after the enable; the VBlank entry
            # pulse ends LCD dot 144 * 456 - 1. Both fix the raster phase and
            # the tick at which the IF flag becomes visible to the CPU.
            fine = peripherals.lcd[m.SCX] & 7
            peripherals.enable_dots = dots_at(frame_starts[0]) - (m.PIXEL_DOT + fine + 1)
            vblank_edge = dot_edge(peripherals.enable_dots + m.VISIBLE_LINES * m.DOTS_PER_LINE)
            visible_tick = (vblank_edge + 2) * 16384 // 390625 + 1   # Why: the first tick edge at or after vblank_edge + 2.
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
    assert pixels_before_lcd == 0, 'pixels appeared while the LCD was off'
    for _ in range(4):
        await RisingEdge(dut.clk_i)
        await ReadOnly()
    assert int(dut.halted_o.value) == 1, 'CPU must report HALT after the halting retirement'
    cocotb.log.info('program halted after %d retirements and %d edges', retirements, cycles)

    assert peripherals.lcdc_writes == 1 and len(frame_starts) >= 1
    assert len(interrupts) == 1 and interrupts[0][1] == 0, ('VBlank interrupt entries', interrupts)
    entry_tick, visible_tick = interrupts[0][0], peripherals.vblank_tick + 1
    assert entry_tick == visible_tick + HALT_WAKE_CYCLES, (
        f'interrupt entry at tick {entry_tick}, flag visible at tick {visible_tick}')

    vram_observed, vram_expected = defined_signature(store_bytes(store_array(dut.u_memory.u_vram)), memory, 'vram')
    compare_signature(vram_observed, vram_expected, 'VRAM')
    wram_observed, wram_expected = defined_signature(store_bytes(store_array(dut.u_memory.u_wram)), memory, 'wram')
    compare_signature(wram_observed, wram_expected, 'WRAM')
    lcdc, scy, scx, bgp = (peripherals.lcd[r] for r in (m.LCDC, m.SCY, m.SCX, m.BGP))
    wram = store_bytes(store_array(dut.u_memory.u_wram))
    assert wram[:4] == bytes((lcdc, scy, scx, bgp)), 'register readback signature'
    assert wram[4:7] == bytes((1, 144, 0xE0)), ('VBlank count, LY in the handler, IF after acknowledge', wram[4:7].hex())
    assert lcdc & 0x80 and len(vram_observed) == 48 + 16 + 1024, (lcdc, len(vram_observed))
    assert int(dut.u_memory.interrupt_enable_o.value) == memory.interrupt_enable() == 0

    expected_frame = m.render_background(bytes(memory.vram), lcdc, scy, scx, bgp)
    observed = [[None] * m.VISIBLE_PIXELS for _ in range(m.VISIBLE_LINES)]
    await RisingEdge(dut.frame_start_o)
    start_ns = get_sim_time(unit='ns')
    for index in range(m.VISIBLE_PIXELS * m.VISIBLE_LINES):
        if index:
            await RisingEdge(dut.pixel_valid_o)
        await ReadOnly()
        x, y = int(dut.pixel_x_o.value), int(dut.pixel_y_o.value)
        assert (x, y) == (index % m.VISIBLE_PIXELS, index // m.VISIBLE_PIXELS), ('pixel order', index, x, y)
        assert int(dut.line_start_o.value) == int(x == 0) and int(dut.frame_start_o.value) == int(index == 0)
        observed[y][x] = int(dut.pixel_shade_o.value)
    await RisingEdge(dut.frame_start_o)
    frame_ns = get_sim_time(unit='ns') - start_ns
    frame_edges = frame_ns / CLOCK_PERIOD_NS
    assert abs(frame_edges - DOTS_PER_FRAME * DOT_EDGES) <= DOT_EDGES, (
        f'frame period {frame_edges} edges, expected {DOTS_PER_FRAME * DOT_EDGES:.1f}')
    cycles = int(get_sim_time(unit='ns')) // CLOCK_PERIOD_NS
    assert cycles <= bound, f'finite bound exceeded during the frame capture ({cycles})'
    compare_frame(observed, expected_frame)
    m.write_png(Path.cwd() / 'frame.png', observed)
    await ReadOnly()
    assert int(dut.u_interrupts.interrupt_flags_o.value) & 0x01 and int(dut.halted_o.value) == 1

    # Why: proves the check can fail: the frame comparison must reject a single changed pixel.
    corrupted = [row[:] for row in expected_frame]
    corrupted[70][80] ^= 2
    try:
        compare_frame(observed, corrupted)
    except AssertionError:
        pass
    else:
        raise AssertionError('frame comparison accepted a corrupted expectation')
    shades = sorted({shade for row in observed for shade in row})
    assert shades == [0, 1, 2, 3], f'the frame must use all four shades, saw {shades}'
    cocotb.log.info('n2m_system background frame passed: retirements=%d vblank_entry_tick=%d frame_edges=%.0f total_cycles=%d',
                    retirements, entry_tick, frame_edges, cycles)
