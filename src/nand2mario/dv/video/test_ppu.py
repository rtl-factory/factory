"""Focused checks of the background PPU on the public fixture ports against the
independent Python timing and rendering model: register file, raster/mode
timing per dot, access blocking, pixel stream and markers, STAT/VBlank
interrupt edges, and LCD off/on behavior."""
import os
import random
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge
from cocotb.utils import get_sim_time

from . import ppu_model as m

CLOCK_PERIOD_NS = 40
DATA = 1
UNCLAIMED = (0xFF46, 0xFF4C, 0xFF4F, 0xFF00, 0xFF7F)


def request_word(valid, write, kind, address, data):
    return (valid << 27) | (write << 26) | (kind << 24) | (address << 8) | data


def response_fields(word):
    return (word >> 9) & 1, (word >> 8) & 1, word & 0xFF


class Bench:
    def __init__(self, dut, seed):
        self.dut = dut
        self.rng = random.Random(seed)
        self.bound = int(os.environ['DV_MAX_CYCLES'])
        self.cycles = 0
        self.stat_events = []
        self.stat_cursor = 0
        self.vblank_events = []
        self.vram = bytearray(8192)
        self.oam = bytearray(160)
        self.regs = {address: 0 for address in m.STORED_REGISTERS}
        self.regs['stat'] = 0

    def idle(self):
        d = self.dut
        d.io_request_i.value = 0
        d.vram_write_i.value = 0
        d.vram_address_i.value = 0
        d.vram_data_i.value = 0
        d.oam_write_i.value = 0
        d.oam_address_i.value = 0
        d.oam_data_i.value = 0

    async def edge(self):
        self.cycles += 1
        assert self.cycles <= self.bound, 'finite simulation bound exceeded'
        await RisingEdge(self.dut.clk_i)

    async def reset(self):
        d = self.dut
        await FallingEdge(d.clk_i)
        d.reset_i.value = 1
        self.idle()
        for _ in range(3):
            await self.edge()
        await FallingEdge(d.clk_i)
        d.reset_i.value = 0
        # Ref: Pan Docs "Power Up Sequence": post-boot LCDC is $91 and BGP $FC.
        # The benches then turn the LCD off first, as DMG programs do, so
        # every phase starts the raster from its origin with no retained flag.
        assert await self.read(m.LCDC) == 0x91 and await self.read(m.BGP) == 0xFC, 'post-boot LCDC/BGP'
        # Why: LYC $FF first clears the coincidence flag (LY 0 = LYC 0 at boot)
        # so it is not retained across the LCD-off write.
        await self.write(m.LYC, 0xFF)
        for _ in range(8):
            await self.edge()
        await self.write(m.LCDC, 0x00)
        await self.write(m.LYC, 0x00)
        await self.write(m.BGP, 0x00)

    async def load_vram(self, image):
        """Fill the VRAM store through its load port, one byte per edge."""
        d = self.dut
        assert len(image) == 8192
        for offset, byte in enumerate(image):
            await FallingEdge(d.clk_i)
            d.vram_write_i.value = 1
            d.vram_address_i.value = offset
            d.vram_data_i.value = byte
            await self.edge()
        await FallingEdge(d.clk_i)
        d.vram_write_i.value = 0
        self.vram[:] = image

    async def load_oam(self, image):
        """Fill the OAM store through its load port, one byte per edge."""
        d = self.dut
        assert len(image) == 160
        for offset, byte in enumerate(image):
            await FallingEdge(d.clk_i)
            d.oam_write_i.value = 1
            d.oam_address_i.value = offset
            d.oam_data_i.value = byte
            await self.edge()
        await FallingEdge(d.clk_i)
        d.oam_write_i.value = 0
        self.oam[:] = image

    async def io(self, address, write=False, data=0):
        """Present one request for one cycle (the memory's peripheral contract)
        and return the combinational read data; checks selection and validity."""
        d = self.dut
        await FallingEdge(d.clk_i)
        d.io_request_i.value = request_word(1, int(write), DATA, address, data)
        await ReadOnly()
        valid, fault, read_data = response_fields(int(d.io_response_o.value))
        selected = int(d.io_selected_o.value)
        claimed = address in m.PPU_REGISTERS
        assert selected == int(claimed), f'io_selected {selected} for {address:04x}'
        assert valid == int(claimed) and fault == 0, f'response valid={valid} fault={fault} for {address:04x}'
        await self.edge()
        await FallingEdge(d.clk_i)
        d.io_request_i.value = 0
        if write and claimed:
            if address == m.STAT:
                self.regs['stat'] = data & 0x78
            elif address != m.LY:
                self.regs[address] = data
        return read_data

    async def read(self, address):
        return await self.io(address)

    async def write(self, address, data):
        await self.io(address, write=True, data=data)

    def expected_stat(self, model):
        coincidence = (model.ly == self.regs[m.LYC]) if model.lcd_on else self.retained
        return 0x80 | self.regs['stat'] | (int(coincidence) << 2) | model.mode

    @property
    def enables(self):
        return self.regs['stat']

    def lyc_line(self, coincidence):
        return bool(self.enables & m.STAT_LYC) and bool(coincidence)

    async def monitor(self):
        """Record every STAT and VBlank pulse with the raster position it shows."""
        d = self.dut
        while True:
            await RisingEdge(d.clk_i)
            await ReadOnly()
            if int(d.stat_irq_o.value):
                self.stat_events.append((int(d.ly_o.value), int(d.dot_o.value)))
            if int(d.vblank_irq_o.value):
                self.vblank_events.append((int(d.ly_o.value), int(d.dot_o.value)))

    async def run_dots(self, model, count, expect_frame, probes=()):
        """Walk ``count`` dots from the current raster position, comparing the
        pre-edge mode/LY/blocking with the model, the post-edge pixel, marker
        and VBlank pulses, and the LY/STAT register reads at the probe dots.
        Returns the pixels seen as {(x, y): shade}."""
        d = self.dut
        pixels = {}
        probes = set(probes)
        for _ in range(count):
            if int(d.dot_enable_o.value) == 0:
                await RisingEdge(d.dot_enable_o)
            position = (model.ly, model.dot)
            probe = position in probes
            if probe:
                await FallingEdge(d.clk_i)
                register = m.LY if self.rng.random() < 0.5 else m.STAT
                d.io_request_i.value = request_word(1, 0, DATA, register, 0)
            await ReadOnly()
            assert int(d.ly_o.value) == model.ly, (position, 'LY', int(d.ly_o.value))
            assert int(d.mode_o.value) == model.mode, (position, 'mode', int(d.mode_o.value))
            assert int(d.dot_o.value) == model.dot, (position, 'dot', int(d.dot_o.value))
            assert int(d.vram_blocked_o.value) == int(model.vram_blocked), (position, 'vram_blocked')
            assert int(d.oam_blocked_o.value) == int(model.oam_blocked), (position, 'oam_blocked')
            if probe:
                _, _, read_data = response_fields(int(d.io_response_o.value))
                expected = m.ly_read(model.ly, model.dot) if register == m.LY else self.expected_stat(model)
                assert read_data == expected, (position, f'{register:04x} read {read_data:02x} expected {expected:02x}')
            pixel, vblank = model.end_dot(self.regs[m.SCX])
            await RisingEdge(d.clk_i)
            self.cycles += 1
            assert self.cycles <= self.bound, 'finite simulation bound exceeded'
            await ReadOnly()
            valid = int(d.pixel_valid_o.value)
            assert valid == int(pixel is not None), (position, 'pixel_valid', valid, pixel)
            if valid:
                x, y, shade = int(d.pixel_x_o.value), int(d.pixel_y_o.value), int(d.pixel_shade_o.value)
                assert (x, y) == pixel, (position, 'pixel position', (x, y), pixel)
                assert shade == expect_frame[y][x], (position, f'pixel ({x},{y}) shade {shade} expected {expect_frame[y][x]}')
                assert int(d.line_start_o.value) == int(x == 0), (position, 'line_start')
                assert int(d.frame_start_o.value) == int(x == 0 and y == 0), (position, 'frame_start')
                pixels[(x, y)] = shade
            else:
                assert int(d.line_start_o.value) == 0 and int(d.frame_start_o.value) == 0, (position, 'marker without pixel')
            assert int(d.vblank_irq_o.value) == int(vblank), (position, 'vblank pulse')
            if probe:
                await FallingEdge(d.clk_i)
                d.io_request_i.value = 0
        return pixels

    def random_lcdc(self, on=True, bg=True):
        value = self.rng.randrange(256) & 0x18
        value |= 0x80 if on else 0
        value |= 0x01 if bg else 0
        return value

    def random_bgp(self):
        """A palette whose color-0 entry is nonzero, so the enabled and
        disabled background paths give different shades for raw color 0."""
        return (self.rng.randrange(256) & 0xFC) | self.rng.randrange(1, 4)


def compare_edges(observed, expected, label):
    """STAT edge lists compared with the first difference reported."""
    for index, (got, want) in enumerate(zip(observed, expected)):
        assert got == want, f'{label}: edge {index} observed {got} expected {want}'
    assert len(observed) == len(expected), (
        f'{label}: {len(observed)} edges observed, {len(expected)} expected; '
        f'tail observed {observed[len(expected):]} expected {expected[len(observed):]}')


def check_edges(bench, expected, label, wrap=None):
    """Consume the next STAT edges from the cumulative record. A frame walk
    ends on the edge that starts the next frame; the monitor may record the
    STAT rise of that edge (``wrap``, when the model predicts one) before or
    after the walk returns, so a trailing wrap edge is tolerated here and
    left for the next phase to consume."""
    observed = bench.stat_events[bench.stat_cursor:]
    if wrap is not None and observed == expected + [wrap]:
        observed = expected
    compare_edges(observed, expected, label)
    bench.stat_cursor += len(expected)


def compare_frame(pixels, expected):
    assert len(pixels) == m.VISIBLE_PIXELS * m.VISIBLE_LINES, f'{len(pixels)} pixels in the frame'
    for (x, y), shade in sorted(pixels.items(), key=lambda item: (item[0][1], item[0][0])):
        assert shade == expected[y][x], f'pixel ({x},{y}) shade {shade} expected {expected[y][x]}'


@cocotb.test(timeout_time=400, timeout_unit='ms')
async def ppu_background_contract(dut):
    seed = int(os.environ['DV_SEED'])
    assert int(os.environ['DV_CLOCK_HZ']) == 25_000_000
    cocotb.log.info('nand2mario PPU background seed=%d', seed)
    b = Bench(dut, seed)
    b.idle()
    # Why: VRAM and OAM have no power-up value: fill them with seeded garbage
    # first; the background frames run with LCDC.1 and LCDC.5 clear, so the
    # scanned garbage objects and the window never reach the pixel stream.
    for index in range(8192):
        dut.u_vram.memory[index].value = b.rng.randrange(1, 256)
    for index in range(256):
        dut.u_oam.memory[index].value = b.rng.randrange(1, 256)
    cocotb.start_soon(Clock(dut.clk_i, CLOCK_PERIOD_NS, unit='ns').start())
    await b.reset()
    cocotb.start_soon(b.monitor())
    model = m.PpuTiming()
    b.retained = False

    # Ref: Pan Docs "LCD Status Registers": with the LCD off every register reads
    # back, STAT keeps only bits 6:3 (bit 7 reads 1, mode 0, no coincidence
    # retained), LY is read-only, unclaimed addresses are not selected.
    assert await b.read(m.LCDC) == 0 and await b.read(m.LY) == 0 and await b.read(m.STAT) == 0x80
    assert int(dut.vram_blocked_o.value) == 0 and int(dut.oam_blocked_o.value) == 0
    for address in (m.SCY, m.SCX, m.LYC, m.BGP, m.OBP0, m.OBP1, m.WY, m.WX):
        for value in (0xFF, 0x00, b.rng.randrange(256)):
            await b.write(address, value)
            assert await b.read(address) == value, f'{address:04x} readback'
    for address in (m.OBP0, m.OBP1, m.WY, m.WX):
        await b.write(address, 0)
    await b.write(m.LCDC, 0x7F)
    assert await b.read(m.LCDC) == 0x7F, 'LCDC readback with the LCD off'
    await b.write(m.STAT, 0xFF)
    assert await b.read(m.STAT) == 0xF8, 'STAT keeps bits 6:3 only'
    await b.write(m.STAT, 0x00)
    await b.write(m.LY, 0x5A)
    assert await b.read(m.LY) == 0, 'LY writes are ignored'
    for address in UNCLAIMED:
        await b.read(address)
    assert b.stat_events == [] and b.vblank_events == [], 'no interrupt with the LCD off'
    for _ in range(2000):
        await b.edge()
    await ReadOnly()
    assert int(dut.ly_o.value) == 0 and int(dut.mode_o.value) == 0 and int(dut.pixel_valid_o.value) == 0
    await FallingEdge(dut.clk_i)

    image = bytes(b.rng.randrange(256) for _ in range(8192))
    await b.load_vram(image)
    await b.write(m.SCY, b.rng.randrange(256))
    await b.write(m.SCX, b.rng.randrange(256))
    await b.write(m.BGP, b.random_bgp())
    await b.write(m.LYC, b.rng.randrange(m.LINES_PER_FRAME))
    await b.write(m.STAT, 0x78)
    lcdc = b.random_lcdc()
    expected = m.render_background(image, lcdc, b.regs[m.SCY], b.regs[m.SCX], b.regs[m.BGP])
    b.stat_events.clear()
    await b.write(m.LCDC, lcdc)
    model.enable()
    probes = {(b.rng.randrange(m.LINES_PER_FRAME), b.rng.randrange(m.DOTS_PER_LINE)) for _ in range(40)}
    probes |= {(b.regs[m.LYC], 0), (b.regs[m.LYC], 455), (143, 455), (144, 0), (153, 455), (0, 80), (0, 100)}
    pixels = await b.run_dots(model, m.DOTS_PER_FRAME, expected, probes)
    compare_frame(pixels, expected)
    assert b.vblank_events == [(144, 0)], b.vblank_events
    expected_edges = m.stat_edges(b.enables, b.regs[m.LYC], b.regs[m.SCX])
    wrap_low = not m.stat_line(m.LINES_PER_FRAME - 1, m.DOTS_PER_LINE - 1, model.fine, b.enables, b.regs[m.LYC])
    wrap_edge = (0, 0) if wrap_low and m.stat_line(0, 0, model.fine, b.enables, b.regs[m.LYC]) else None
    check_edges(b, expected_edges, 'STAT edges frame 1', wrap_edge)
    cocotb.log.info('frame 1: lcdc=%02x scx=%d scy=%d bgp=%02x lyc=%d stat edges=%d',
                    lcdc, b.regs[m.SCX], b.regs[m.SCY], b.regs[m.BGP], b.regs[m.LYC], len(expected_edges))
    m.write_png(Path.cwd() / 'frame1.png', [[pixels[(x, y)] for x in range(160)] for y in range(144)])

    # Why: proves the check can fail: the frame comparison must reject one changed pixel.
    corrupted = [row[:] for row in expected]
    corrupted[17][23] ^= 1
    try:
        compare_frame(pixels, corrupted)
    except AssertionError:
        pass
    else:
        raise AssertionError('frame comparison accepted a corrupted expectation')

    b.vblank_events.clear()
    stop_line, stop_dot = b.rng.randrange(1, m.VISIBLE_LINES), b.rng.randrange(m.DOTS_PER_LINE)
    partial = stop_line * m.DOTS_PER_LINE + stop_dot
    await b.run_dots(model, partial, expected)
    before_off = m.stat_line(model.ly, model.dot, model.fine, b.enables, b.regs[m.LYC])
    b.retained = model.ly == b.regs[m.LYC]
    await b.write(m.LCDC, lcdc & 0x7F)
    model.disable()
    # Why: frame 2 continues frame 1: its first edge depends on the line level at
    # the last dot of frame 1 (the wrap edge, when predicted, is consumed here).
    expected_partial = [edge for edge in m.stat_edges(b.enables, b.regs[m.LYC], b.regs[m.SCX], start_line_low=wrap_low)
                        if edge < (stop_line, stop_dot)]
    off_line = b.lyc_line(b.retained)
    if off_line and not before_off:
        expected_partial.append((0, 0))
    check_edges(b, expected_partial, 'STAT edges before/at LCD off')
    assert b.vblank_events == [], 'no VBlank in the visible lines'
    await b.edge()
    await ReadOnly()
    assert int(dut.ly_o.value) == 0 and int(dut.mode_o.value) == 0
    assert int(dut.vram_blocked_o.value) == 0 and int(dut.oam_blocked_o.value) == 0
    for _ in range(3000):
        await b.edge()
        await ReadOnly()
        assert int(dut.pixel_valid_o.value) == 0 and int(dut.vblank_irq_o.value) == 0
        assert int(dut.ly_o.value) == 0 and int(dut.mode_o.value) == 0
    await FallingEdge(dut.clk_i)
    assert await b.read(m.LY) == 0
    assert await b.read(m.STAT) == 0x80 | b.enables | (int(b.retained) << 2), 'coincidence retained while off'
    await b.write(m.LYC, (b.regs[m.LYC] + 1) % 256)
    assert await b.read(m.STAT) == 0x80 | b.enables | (int(b.retained) << 2)
    assert b.stat_events[b.stat_cursor:] == [], 'no STAT edge from a LYC write while off'

    # Ref: Pan Docs, DMG LCDC bit 0: frame 3 sets new VRAM and registers while off, then a complete enabled
    # frame with the background disabled: every pixel is white (shade 0)
    # although BGP maps color 0 to a nonzero shade (DMG LCDC.0 behavior).
    image = bytes(b.rng.randrange(256) for _ in range(8192))
    await b.load_vram(image)
    await b.write(m.SCY, b.rng.randrange(256))
    await b.write(m.SCX, b.rng.randrange(256))
    await b.write(m.BGP, b.random_bgp())
    await b.write(m.LYC, b.rng.randrange(m.LINES_PER_FRAME))
    enables = b.rng.choice((0x08, 0x10, 0x20, 0x40, 0x48, 0x30, 0x78))
    line_before = b.lyc_line(b.retained)
    await b.write(m.STAT, enables)
    line_after = b.lyc_line(b.retained)
    off_edges = [(0, 0)] if line_after and not line_before else []
    check_edges(b, off_edges, 'STAT write while off')
    lcdc = b.random_lcdc(bg=False)
    expected = m.render_background(image, lcdc, b.regs[m.SCY], b.regs[m.SCX], b.regs[m.BGP])
    assert all(shade == 0 for row in expected for shade in row) and b.regs[m.BGP] & 3
    await b.write(m.LCDC, lcdc)
    model.enable()
    pixels = await b.run_dots(model, m.DOTS_PER_FRAME, expected,
                              {(b.regs[m.LYC], 3), (b.regs[m.LYC] + 1, 3), (144, 2)})
    compare_frame(pixels, expected)
    assert b.vblank_events == [(144, 0)], b.vblank_events
    expected_edges = m.stat_edges(enables, b.regs[m.LYC], b.regs[m.SCX], start_line_low=not line_after)
    wrap_low = not m.stat_line(m.LINES_PER_FRAME - 1, m.DOTS_PER_LINE - 1, model.fine, enables, b.regs[m.LYC])
    wrap_edge = (0, 0) if wrap_low and m.stat_line(0, 0, model.fine, enables, b.regs[m.LYC]) else None
    check_edges(b, expected_edges, 'STAT edges frame 3', wrap_edge)
    cocotb.log.info('frame 3: lcdc=%02x scx=%d scy=%d bgp=%02x lyc=%d enables=%02x stat edges=%d',
                    lcdc, b.regs[m.SCX], b.regs[m.SCY], b.regs[m.BGP], b.regs[m.LYC], enables, len(expected_edges))
    m.write_png(Path.cwd() / 'frame3.png', [[pixels[(x, y)] for x in range(160)] for y in range(144)])
    edges = int(get_sim_time(unit='ns')) // CLOCK_PERIOD_NS
    assert edges <= b.bound, f'finite simulation bound exceeded ({edges} edges)'
    cocotb.log.info('PPU background checks passed seed=%d edges=%d', seed, edges)
