"""Focused checks of the PPU window and object layers on the public fixture
ports against the independent Python renderer: three complete frames with
seeded VRAM, OAM and registers are walked dot by dot (mode, LY and blocking
per dot; every pixel's shade, coordinates and markers), each frame asserting
its own coverage of the rules it exercises: OAM scan with the ten-per-line
limit, 8x8 and 8x16 objects, X/Y flips, OBP0/OBP1, the priority attribute,
smallest-X then lowest-index ordering, hidden X positions, the window from
WX/WY with its line counter, and the disabled background and object
layers. The window's edge geometry and its stateful hide/resume behavior
are the window target's (test_ppu_window)."""
import os
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly
from cocotb.utils import get_sim_time

from . import ppu_model as m
from .test_ppu import CLOCK_PERIOD_NS, Bench, compare_frame


def random_palette(rng):
    """A palette whose entries 1-3 are nonzero and not all equal, so object
    and tile colors stay distinguishable through it."""
    while True:
        value = rng.randrange(256)
        entries = [(value >> (2 * c)) & 3 for c in range(4)]
        if all(entries[1:]) and len(set(entries[1:])) > 1:
            return value


def random_oam(rng, tall, overflow_line, hidden=2):
    """40 OAM entries: twelve share the Y of ``overflow_line`` (so the line
    holds more than ten objects), ``hidden`` sit at X 0 or beyond 167, two
    pairs share an X on a common line (ties), the rest are random with every
    attribute bit random. Objects are 8x16 when ``tall``."""
    height = 16 if tall else 8
    entries = []
    for index in range(40):
        y = rng.randrange(0, 160)
        x = rng.randrange(1, 168)
        if index < 12:
            y = overflow_line + 16 - rng.randrange(height)
        elif index < 12 + hidden:
            x = rng.choice((0, 168, 200, 255))
        elif index in (14, 15):
            y, x = 100 + 16, 40
        elif index in (16, 17):
            y, x = 120 + 16, 90
        entries.append((y, x, rng.randrange(256), rng.randrange(256)))
    return bytes(byte for entry in entries for byte in entry)


async def walk_frame(b, model, regs, label, probes=()):
    """Enable the LCD on the current registers, walk one frame against the
    renderer and return the renderer's coverage counts."""
    stats = {}
    expected = m.render_frame(bytes(b.vram), bytes(b.oam), regs, stats)
    await b.write(m.LCDC, regs[m.LCDC])
    model.enable()
    pixels = await b.run_dots(model, m.DOTS_PER_FRAME, expected, probes)
    compare_frame(pixels, expected)
    assert b.vblank_events[-1] == (144, 0), b.vblank_events
    m.write_png(Path.cwd() / f'{label}.png', [[pixels[(x, y)] for x in range(160)] for y in range(144)])
    cocotb.log.info('%s: lcdc=%02x scx=%d scy=%d wx=%d wy=%d bgp=%02x obp0=%02x obp1=%02x stats=%s',
                    label, regs[m.LCDC], regs[m.SCX], regs[m.SCY], regs[m.WX], regs[m.WY],
                    regs[m.BGP], regs[m.OBP0], regs[m.OBP1], stats)
    return expected, stats


async def set_registers(b, regs):
    for address, value in regs.items():
        if address != m.LCDC:
            await b.write(address, value)


@cocotb.test(timeout_time=500, timeout_unit='ms')
async def ppu_objects_contract(dut):
    seed = int(os.environ['DV_SEED'])
    assert int(os.environ['DV_CLOCK_HZ']) == 25_000_000
    cocotb.log.info('nand2mario PPU objects/window seed=%d', seed)
    b = Bench(dut, seed)
    b.idle()
    for index in range(8192):
        dut.u_vram.u_ram.memory[index].value = b.rng.randrange(1, 256)
    for index in range(256):
        dut.u_oam.u_ram.memory[index].value = b.rng.randrange(1, 256)
    cocotb.start_soon(Clock(dut.clk_i, CLOCK_PERIOD_NS, unit='ns').start())
    await b.reset()
    cocotb.start_soon(b.monitor())
    model = m.PpuTiming()
    b.retained = False
    rng = b.rng

    # Why: frame A: 8x8 objects over background and window, every attribute in
    # play, window at a random WX 8..165 and WY in the upper half so lines
    # above WY show only background, a random LYC with STAT fully enabled.
    await b.load_vram(bytes(rng.randrange(256) for _ in range(8192)))
    overflow_line = rng.randrange(8, 96)
    await b.load_oam(random_oam(rng, tall=False, overflow_line=overflow_line))
    regs = {m.LCDC: 0x80 | 0x20 | 0x02 | 0x01 | (rng.randrange(256) & 0x58),
            m.SCY: rng.randrange(256), m.SCX: rng.randrange(256), m.BGP: random_palette(rng),
            m.OBP0: random_palette(rng), m.OBP1: random_palette(rng),
            m.WY: rng.randrange(0, 72), m.WX: rng.randrange(8, 166), m.LYC: rng.randrange(154)}
    await set_registers(b, regs)
    await b.write(m.STAT, 0x78)
    probes = {(rng.randrange(m.LINES_PER_FRAME), rng.randrange(m.DOTS_PER_LINE)) for _ in range(20)}
    expected, stats = await walk_frame(b, model, regs, 'frame_a', probes)
    assert stats['overflow_lines'] >= 1, stats
    assert stats['object_pixels'] >= 200 and stats['window_pixels'] >= 160, stats
    assert stats['priority_covered'] >= 1 and stats['ties'] >= 1 and stats['lower_x_wins'] >= 1, stats
    assert stats['flip_x'] >= 1 and stats['flip_y'] >= 1 and stats['obp1'] >= 1, stats
    lines = m.window_lines(regs[m.LCDC], regs[m.WY], regs[m.WX])
    assert min(lines) == regs[m.WY] and lines[regs[m.WY]] == 0 and len(lines) == 144 - regs[m.WY]

    # Why: proves the check can fail: one changed pixel inside the window and one inside
    # an object must both be rejected by the frame comparison.
    pixels = {(x, y): expected[y][x] for y in range(144) for x in range(160)}
    for (x, y) in ((regs[m.WX] - 7, regs[m.WY]), (80, overflow_line)):
        corrupted = [row[:] for row in expected]
        corrupted[y][x] ^= 1
        try:
            compare_frame(pixels, corrupted)
        except AssertionError:
            pass
        else:
            raise AssertionError(f'frame comparison accepted a corrupted pixel at {(x, y)}')

    await b.write(m.LCDC, 0x00)
    model.disable()
    await b.load_vram(bytes(rng.randrange(256) for _ in range(8192)))
    overflow_line = rng.randrange(8, 128)
    await b.load_oam(random_oam(rng, tall=True, overflow_line=overflow_line))
    regs = {m.LCDC: 0x80 | 0x20 | 0x04 | 0x02 | (rng.randrange(256) & 0x58),
            m.SCY: rng.randrange(256), m.SCX: rng.randrange(256), m.BGP: random_palette(rng),
            m.OBP0: random_palette(rng), m.OBP1: random_palette(rng),
            m.WY: rng.randrange(0, 144), m.WX: rng.randrange(8, 166), m.LYC: rng.randrange(154)}
    await set_registers(b, regs)
    expected, stats = await walk_frame(b, model, regs, 'frame_b')
    assert stats['overflow_lines'] >= 1 and stats['object_pixels'] >= 200, stats
    assert stats['priority_covered'] == 0, stats
    tile_shades = {expected[y][x] for y in range(144) for x in range(160)
                   if m.object_pixel(bytes(b.vram), regs[m.LCDC], m.line_objects(bytes(b.oam), regs[m.LCDC], y)[0], x) is None}
    assert tile_shades == {0}, f'disabled background must be white under the window too, saw {tile_shades}'

    await b.write(m.LCDC, 0x00)
    model.disable()
    regs = {**regs, m.LCDC: 0x80 | 0x01 | (rng.randrange(256) & 0x58), m.WX: rng.randrange(7, 160)}
    await set_registers(b, regs)
    expected, stats = await walk_frame(b, model, regs, 'frame_c')
    assert stats['object_pixels'] == 0 and stats['window_pixels'] == 0, stats
    assert expected == m.render_background(bytes(b.vram), regs[m.LCDC], regs[m.SCY], regs[m.SCX], regs[m.BGP])

    await b.write(m.LCDC, 0x00)
    model.disable()
    regs = {**regs, m.LCDC: 0x80 | 0x20 | 0x02 | 0x01 | (rng.randrange(256) & 0x58), m.WX: 7, m.WY: 0}
    await set_registers(b, regs)
    expected, stats = await walk_frame(b, model, regs, 'frame_d')
    assert stats['window_pixels'] == 160 * 144 and stats['object_pixels'] >= 100, stats
    assert stats['ties'] >= 1, stats

    edges = int(get_sim_time(unit='ns')) // CLOCK_PERIOD_NS
    assert edges <= b.bound, f'finite simulation bound exceeded ({edges} edges)'
    cocotb.log.info('PPU objects/window checks passed seed=%d edges=%d', seed, edges)
