"""Window geometry and line-counter checks on the public fixture ports, with
visible window pixels in every phase. Static phases walk the first lines of
a frame against the renderer for WX 0, a random WX 1-6 (the window starts at
pixel 0 with its leftmost 7 - WX columns cut off), WX 7, WX 166 (one window
pixel per line, at pixel 159), WX 167 and 255 (no window pixel) and a WY
above 0 (no window before line WY, window line 0 at line WY). A stateful
phase hides the window with LCDC.0 for one line and with LCDC.5 for
another, each time restoring it in the next HBlank, and checks every pixel
of the surrounding lines against hand-built expected lines: a hidden line
draws no window pixel and does not consume a window row, so the window
resumes with the row that follows the last drawn one."""
import os
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge
from cocotb.utils import get_sim_time

from . import ppu_model as m
from .test_ppu import CLOCK_PERIOD_NS, Bench

LINES = 3


def window_line_pixels(vram, regs, wline, y):
    """The 160 shades of screen line ``y`` drawing window line ``wline`` from
    the window's first pixel and the background before it."""
    lcdc, wx = regs[m.LCDC], regs[m.WX]
    first = m.window_first_pixel(wx)
    row = []
    for x in range(m.VISIBLE_PIXELS):
        if x >= first:
            color = m.window_color(vram, lcdc, x + 7 - wx, wline)
        else:
            color = m.background_color(vram, lcdc, regs[m.SCX] + x, regs[m.SCY] + y)
        row.append(m.palette_shade(regs[m.BGP], color))
    return row


def background_line_pixels(vram, regs, y):
    lcdc = regs[m.LCDC]
    return [m.palette_shade(regs[m.BGP], m.background_color(vram, lcdc, regs[m.SCX] + x, regs[m.SCY] + y))
            for x in range(m.VISIBLE_PIXELS)]


async def static_phase(b, model, regs, label, expected_window_pixels):
    """Enable the LCD on ``regs``, walk LINES lines against the renderer and
    require exactly ``expected_window_pixels`` window pixels in them."""
    for address, value in regs.items():
        if address != m.LCDC:
            await b.write(address, value)
    stats = {}
    frame = m.render_frame(bytes(b.vram), bytes(b.oam), regs, stats)
    lines = m.window_lines(regs[m.LCDC], regs[m.WY], regs[m.WX])
    first = m.window_first_pixel(regs[m.WX])
    walked = sum(m.VISIBLE_PIXELS - first for y in range(LINES) if y in lines)
    assert walked == expected_window_pixels, (label, walked, expected_window_pixels)
    await b.write(m.LCDC, regs[m.LCDC])
    model.enable()
    pixels = await b.run_dots(model, LINES * m.DOTS_PER_LINE, frame)
    assert len(pixels) == LINES * m.VISIBLE_PIXELS, (label, len(pixels))
    # Why: every window pixel of these lines differs from the background it covers
    # for at least one position, so the comparison observed the window.
    if expected_window_pixels:
        differs = any(frame[y][x] != background_line_pixels(bytes(b.vram), regs, y)[x]
                      for y in range(LINES) if y in lines for x in range(first, m.VISIBLE_PIXELS))
        assert differs, f'{label}: the window must be distinguishable from the background'
    await b.write(m.LCDC, 0x00)
    model.disable()
    cocotb.log.info('%s: wx=%d wy=%d lcdc=%02x window pixels in %d lines=%d',
                    label, regs[m.WX], regs[m.WY], regs[m.LCDC], LINES, expected_window_pixels)


async def collect_lines(b, count, writes):
    """Enable the LCD (already written) and collect the pixels of the first
    ``count`` lines while applying ``writes`` ({(line, dot): (address,
    value)}) at the named raster positions. Returns [[shade]*160]*count."""
    d = b.dut
    rows = [[None] * m.VISIBLE_PIXELS for _ in range(count)]
    pending = dict(writes)
    while True:
        await RisingEdge(d.clk_i)
        b.cycles += 1
        assert b.cycles <= b.bound, 'finite simulation bound exceeded'
        await ReadOnly()
        if int(d.pixel_valid_o.value):
            x, y = int(d.pixel_x_o.value), int(d.pixel_y_o.value)
            if y >= count:
                return rows
            assert rows[y][x] is None, ('pixel repeated', x, y)
            rows[y][x] = int(d.pixel_shade_o.value)
        position = (int(d.ly_o.value), int(d.dot_o.value))
        if position in pending:
            address, value = pending.pop(position)
            await b.write(address, value)


def compare_rows(rows, expected, label):
    for y, (row, want) in enumerate(zip(rows, expected)):
        for x, (got, shade) in enumerate(zip(row, want)):
            assert got == shade, f'{label}: pixel ({x},{y}) observed {got} expected {shade}'


@cocotb.test(timeout_time=20, timeout_unit='ms')
async def ppu_window_contract(dut):
    seed = int(os.environ['DV_SEED'])
    assert int(os.environ['DV_CLOCK_HZ']) == 25_000_000
    cocotb.log.info('nand2mario PPU window seed=%d', seed)
    b = Bench(dut, seed)
    b.idle()
    for index in range(8192):
        dut.u_vram.memory[index].value = b.rng.randrange(1, 256)
    for index in range(256):
        dut.u_oam.memory[index].value = b.rng.randrange(1, 256)
    cocotb.start_soon(Clock(dut.clk_i, CLOCK_PERIOD_NS, unit='ns').start())
    await b.reset()
    model = m.PpuTiming()
    rng = b.rng

    # Why: seeded VRAM for the geometry phases; OAM entries off every line
    # (Y 0) so objects, even when enabled, never draw.
    await b.load_vram(bytes(rng.randrange(256) for _ in range(8192)))
    await b.load_oam(bytes(160))
    base = {m.SCY: rng.randrange(256), m.SCX: rng.randrange(256), m.BGP: 0xE4,
            m.OBP0: 0, m.OBP1: 0, m.LYC: 0, m.WY: 0}

    def regs(wx, wy=0, lcdc_bits=None):
        bits = rng.randrange(256) & 0x58 if lcdc_bits is None else lcdc_bits
        return {**base, m.LCDC: 0x80 | 0x20 | 0x01 | bits, m.WY: wy, m.WX: wx}

    await static_phase(b, model, regs(0), 'wx0', LINES * 160)
    await static_phase(b, model, regs(rng.randrange(1, 7)), 'wx1-6', LINES * 160)
    await static_phase(b, model, regs(7), 'wx7', LINES * 160)
    await static_phase(b, model, regs(166), 'wx166', LINES * 1)
    await static_phase(b, model, regs(167), 'wx167', 0)
    await static_phase(b, model, regs(255), 'wx255', 0)
    wx = rng.randrange(8, 160)
    await static_phase(b, model, regs(wx, wy=1), 'wy1', (LINES - 1) * (160 - (wx - 7)))

    vram = bytearray(8192)
    for row in range(8):
        color = row % 3 + 1
        vram[16 + 2 * row] = 0xFF if color & 1 else 0x00
        vram[16 + 2 * row + 1] = 0xFF if color & 2 else 0x00
    for row in range(8):
        vram[32 + 2 * row] = 0xAA
        vram[32 + 2 * row + 1] = 0xAA
    vram[0x1C00:0x2000] = bytes([1] * 1024)
    vram[0x1800:0x1C00] = bytes([2] * 1024)
    await b.load_vram(bytes(vram))
    hide = {**base, m.SCY: 0, m.SCX: 0, m.LCDC: 0xF1, m.WY: 0, m.WX: 7}   # Ref: Pan Docs, LCDC bits: $F1 is LCD, window map $9C00, window, $8000 tiles, BG.
    for address, value in hide.items():
        if address != m.LCDC:
            await b.write(address, value)
    await b.write(m.LCDC, hide[m.LCDC])
    # Why: line 0 is window row 0. HBlank of line 0: LCDC.0 off -> line 1 white, no
    # row consumed. HBlank of line 1: LCDC.0 on -> line 2 window row 1.
    # HBlank of line 2: LCDC.5 off -> line 3 background, no row consumed.
    # HBlank of line 3: LCDC.5 on -> line 4 window row 2; line 5 row 3.
    writes = {(0, 300): (m.LCDC, 0xF0), (1, 300): (m.LCDC, 0xF1),
              (2, 300): (m.LCDC, 0xD1), (3, 300): (m.LCDC, 0xF1)}
    rows = await collect_lines(b, 6, writes)
    expected = [window_line_pixels(bytes(vram), hide, 0, 0),
                [0] * m.VISIBLE_PIXELS,
                window_line_pixels(bytes(vram), hide, 1, 2),
                background_line_pixels(bytes(vram), hide, 3),
                window_line_pixels(bytes(vram), hide, 2, 4),
                window_line_pixels(bytes(vram), hide, 3, 5)]
    assert expected[0][0] == 1 and expected[2][0] == 2 and expected[4][0] == 3 and expected[5][0] == 1
    assert expected[3][:2] == [3, 0]
    compare_rows(rows, expected, 'hide/resume')
    # Why: proves the check can fail: the stale accounting (line 2 drawing row 2) must be rejected.
    stale = [row[:] for row in expected]
    stale[2] = window_line_pixels(bytes(vram), hide, 2, 2)
    try:
        compare_rows(rows, stale, 'hide/resume-negative')
    except AssertionError:
        pass
    else:
        raise AssertionError('comparison accepted the stale window row')
    await b.write(m.LCDC, 0x00)
    edges = int(get_sim_time(unit='ns')) // CLOCK_PERIOD_NS
    assert edges <= b.bound, f'finite simulation bound exceeded ({edges} edges)'
    cocotb.log.info('PPU window checks passed seed=%d edges=%d', seed, edges)
