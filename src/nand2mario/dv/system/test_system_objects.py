"""n2m_system objects frame: a locally built program draws a background, a
window and sprites whose OAM is copied by OAM DMA from inside the VBlank
handler through an HRAM routine, every retirement matches the CPU oracle
(whose DMA copies OAM when the register is written), the DMA restriction
window is observed for exactly 160 machine cycles starting at the routine's
register write while the CPU runs only HRAM code, the OAM store equals the
shadow, and after the final HALT one full frame of the pixel stream matches
the independent Python renderer of the oracle's VRAM, OAM and registers."""
import os
import random
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge
from cocotb.utils import get_sim_time

from ..core.cpu_model import CPU
from ..core.test_cpu import unpack
from ..dma import dma_model as dm
from ..memory.memory_model import GameBoyMemory
from ..video import ppu_model as m
from .test_system import (await_core_release, idle_host, HALT_OPCODE, compare_signature, defined_signature, interrupt_entry,
                          load_image, power_up, store_bytes, ticks_at, store_array)
from .test_system_background import (DOTS_PER_FRAME, DOT_EDGES, HALT_WAKE_CYCLES, VideoPeripherals,
                                     compare_frame, dot_edge, dots_at)

CLOCK_PERIOD_NS = 40
INSTRUCTION_BOUND = 30000
LDH_WRITE_OPCODE = 0xE0
HRAM_START, HRAM_END = 0xFF80, 0xFFFE


class ObjectsPeripherals(VideoPeripherals):
    """The video oracle plus the object/window registers and a DMA register
    whose write copies the source page into the memory model's OAM at once
    (the program never touches OAM while the transfer runs)."""

    def __init__(self):
        super().__init__()
        self.lcd.update({m.OBP0: 0, m.OBP1: 0, m.WY: 0, m.WX: 0})
        self.dma = dm.DmaModel()
        self.memory = None
        self.dma_pages = []

    def read(self, address):
        if address == dm.DMA:
            return self.dma.read()
        return super().read(address)

    def write(self, address, value):
        if address == dm.DMA:
            self.dma.write(value)
            self.dma_pages.append(value)
            for offset, byte in enumerate(dm.transfer_image(self.memory, value)):
                self.memory[0xFE00 + offset] = byte
        else:
            super().write(address, value)


@cocotb.test(timeout_time=120, timeout_unit='ms')
async def system_objects_frame(dut):
    seed = int(os.environ['DV_SEED'])
    bound = int(os.environ['DV_MAX_CYCLES'])
    assert int(os.environ['DV_CLOCK_HZ']) == 25_000_000
    cocotb.log.info('n2m_system objects seed=%d (directed) bound=%d', seed, bound)
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
    cycles += await await_core_release(dut)
    release_edge = cycles
    release_ns = get_sim_time(unit='ns')

    frame_starts = []

    async def watch_frame_starts():
        while True:
            await RisingEdge(dut.frame_start_o)
            frame_starts.append(round((get_sim_time(unit='ns') - release_ns + 20) / CLOCK_PERIOD_NS))

    cocotb.start_soon(watch_frame_starts())

    peripherals = ObjectsPeripherals()
    memory = GameBoyMemory(image, io_read=peripherals.read, io_write=peripherals.write)
    peripherals.memory = memory
    model = CPU(mem=memory)
    retirements, interrupts = 0, []
    pixels_before_lcd = 0
    dma_edges, dma_write_ticks, pcs_during_dma = [], [], []
    dma_active = 0
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
        level = int(dut.u_dma.active_o.value)
        if level != dma_active:
            dma_edges.append(ticks_at(cycles - release_edge))
            dma_active = level
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
        if dma_active:
            pcs_during_dma.append(record['pc_before'])
        if record['is_interrupt']:
            peripherals.advance(ticks - 2)
            expected, bit = interrupt_entry(model, memory, peripherals)
            interrupts.append((ticks, bit))
        else:
            opcode = memory[model.pc]
            peripherals.advance(ticks - (1 if opcode == HALT_OPCODE else 2))
            pending = memory.interrupt_enable() & peripherals.flags.flags
            expected, _, _ = model.step(pending=pending)
            if opcode == LDH_WRITE_OPCODE and memory[(expected['pc_before'] + 1) & 0xFFFF] == dm.DMA & 0xFF:
                dma_write_ticks.append(ticks)
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

    # Why: one DMA transfer: the level rose at the retirement tick of the HRAM
    # routine's register write (the write lands in the machine cycle before
    # the retiring one, so the retiring cycle is the armed cycle and the
    # transfer starts at the tick that ends it), lasted exactly 160 machine
    # cycles, and every instruction retired while it ran executed from HRAM.
    assert peripherals.dma_pages == [0xC1], peripherals.dma_pages
    assert len(dma_write_ticks) == 1 and len(dma_edges) == 2, (dma_write_ticks, dma_edges)
    rise, fall = dma_edges
    assert rise == dma_write_ticks[0] and fall - rise == dm.TRANSFER_BYTES, (
        f'DMA level ticks {dma_edges}, register write retired at tick {dma_write_ticks[0]}')
    assert pcs_during_dma and all(HRAM_START <= pc <= HRAM_END for pc in pcs_during_dma), (
        'instructions outside HRAM during the transfer', [f'{pc:04x}' for pc in pcs_during_dma][:5])
    assert len(pcs_during_dma) >= 80, len(pcs_during_dma)

    for name, store in (('vram', dut.u_memory.u_vram), ('wram', dut.u_memory.u_wram),
                        ('hram', dut.u_memory.u_hram), ('oam', dut.u_memory.u_oam)):
        observed, expected = defined_signature(store_bytes(store_array(store)), memory, name)
        compare_signature(observed, expected, name.upper())
    oam = store_bytes(store_array(dut.u_memory.u_oam))[:160]
    shadow = bytes(memory.wram[0x100:0x1A0])
    assert len(memory.defined['oam']) == 160 and oam == shadow, 'OAM must equal the WRAM shadow after the DMA'
    lcd = peripherals.lcd
    wram = store_bytes(store_array(dut.u_memory.u_wram))
    assert wram[:9] == bytes((lcd[m.LCDC], lcd[m.SCY], lcd[m.SCX], lcd[m.BGP], lcd[m.OBP0], lcd[m.OBP1],
                              lcd[m.WY], lcd[m.WX], 0x00)), ('register readback signature', wram[:9].hex())
    assert wram[9:13] == bytes((1, 144, 0xC1, 0xE0)), ('VBlank count, LY, DMA readback, IF', wram[9:13].hex())
    assert lcd[m.LCDC] == 0xF3 and len(memory.defined['vram']) == 128 + 2048
    assert int(dut.u_memory.interrupt_enable_o.value) == memory.interrupt_enable() == 0

    stats = {}
    expected_frame = m.render_frame(bytes(memory.vram), shadow, lcd, stats)
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
    frame_edges = (get_sim_time(unit='ns') - start_ns) / CLOCK_PERIOD_NS
    assert abs(frame_edges - DOTS_PER_FRAME * DOT_EDGES) <= DOT_EDGES, (
        f'frame period {frame_edges} edges, expected {DOTS_PER_FRAME * DOT_EDGES:.1f}')
    cycles = int(get_sim_time(unit='ns')) // CLOCK_PERIOD_NS
    assert cycles <= bound, f'finite bound exceeded during the frame capture ({cycles})'
    compare_frame(observed, expected_frame)
    m.write_png(Path.cwd() / 'frame.png', observed)
    await ReadOnly()
    assert int(dut.u_interrupts.interrupt_flags_o.value) & 0x01 and int(dut.halted_o.value) == 1

    assert stats['window_pixels'] == 64 * 67 and stats['overflow_lines'] == 8, stats
    assert stats['object_pixels'] >= 300 and stats['priority_covered'] >= 20, stats
    assert stats['ties'] >= 1 and stats['lower_x_wins'] >= 1, stats
    assert stats['flip_x'] >= 1 and stats['flip_y'] >= 1 and stats['obp1'] >= 1, stats
    shades = sorted({shade for row in observed for shade in row})
    assert shades == [0, 1, 2, 3], f'the frame must use all four shades, saw {shades}'

    # Why: proves the check can fail: a changed window pixel and a changed sprite pixel
    # must both be rejected by the frame comparison.
    for (x, y) in ((100, 100), (55, 102)):
        corrupted = [row[:] for row in expected_frame]
        corrupted[y][x] ^= 1
        try:
            compare_frame(observed, corrupted)
        except AssertionError:
            pass
        else:
            raise AssertionError(f'frame comparison accepted a corrupted pixel at {(x, y)}')
    cocotb.log.info('n2m_system objects frame passed: retirements=%d vblank_entry_tick=%d dma_ticks=%s '
                    'frame_edges=%.0f total_cycles=%d stats=%s',
                    retirements, entry_tick, dma_edges, frame_edges, cycles, stats)
