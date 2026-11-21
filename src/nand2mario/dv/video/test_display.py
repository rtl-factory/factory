"""nand2mario display tier: a synthetic PPU pixel stream drives n2m_display
and the VGA output is checked clock by clock against the independent Python
display model: blank (white image, black border) before the first complete
frame, the swap of a complete frame at the next raster origin, the newest of
two frames completing in one VGA frame (the other dropped), a repeated frame
when nothing new completes, a completion in the raster-origin cycle versus
one cycle later, the LCD-off blank and recovery, the 3x scaled centered
image with the grey palette, and the 640x480 sync timing of the common
raster contract measured on the output pins. Frame contents and the pixel
cadence are seeded random; every phase asserts its own bank-policy pulses."""
import os
import random
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge, Timer
from cocotb.utils import get_sim_time

from . import display_model as m

CLOCK_PERIOD_NS = 40
HALF_PERIOD_NS = CLOCK_PERIOD_NS // 2
PULSE_LATENCY = 1
PULSES = ('frame_complete_o', 'frame_swapped_o', 'frame_dropped_o', 'frame_repeated_o')


def random_frame(rng):
    return [[rng.randrange(4) for _ in range(m.FRAME_WIDTH)] for _ in range(m.FRAME_HEIGHT)]


class Bench:
    def __init__(self, dut, seed):
        self.dut = dut
        self.rng = random.Random(seed)
        self.bound = int(os.environ['DV_MAX_CYCLES'])
        self.events = []
        self.starts = []

    @staticmethod
    def now_edge():
        """Index of the rising edge at or before the current simulation time."""
        return int(get_sim_time(unit='ns')) // CLOCK_PERIOD_NS

    def check_bound(self):
        assert self.now_edge() <= self.bound, f'finite simulation bound exceeded ({self.now_edge()} edges)'

    async def watch(self, name):
        signal = getattr(self.dut, name)
        while True:
            await RisingEdge(signal)
            if name == 'pixel_start_o':
                self.starts.append(self.now_edge())
            else:
                self.events.append((self.now_edge(), name))

    def events_since(self, edge):
        return [(when, kind) for when, kind in self.events if when >= edge]

    async def wait_pulse(self, name, since):
        """Edge of the first ``name`` pulse recorded at or after ``since``,
        waiting for it if it has not happened yet (a pulse that rose while
        the bench was still driving the completing pixel is already recorded)."""
        for when, kind in self.events:
            if kind == name and when >= since:
                return when
        await RisingEdge(getattr(self.dut, name))
        return self.now_edge()

    def origin_cycle(self, cycle):
        """The raster origin cycle at or after ``cycle``: pixel_start_o rises
        OUTPUT_LATENCY edges after the origin, and origins repeat every frame."""
        first = self.starts[0] - m.OUTPUT_LATENCY
        return first + ((cycle - first + m.FRAME_CLOCKS - 1) // m.FRAME_CLOCKS) * m.FRAME_CLOCKS

    def swap_edge_for(self, complete_edge):
        """Edge of the swap pulse for a completion recorded at ``complete_edge``:
        the origin cycle at or after the cycle the pixel was presented in."""
        return self.origin_cycle(complete_edge - PULSE_LATENCY) + PULSE_LATENCY

    def power_up(self):
        """The frame store has no power-up value: fill every bank with seeded
        garbage so only written pixels can match, and blank never leaks it."""
        banks = self.dut.u_frame_ram.g_banks
        for bank in range(3):
            memory = banks[bank].u_ram.u_enabled.memory
            for index in range(m.FRAME_PIXELS):
                memory[index].value = self.rng.randrange(4)

    async def reset(self):
        d = self.dut
        d.reset_i.value = 1
        d.lcd_enable_i.value = 0
        d.pixel_valid_i.value = 0
        d.pixel_shade_i.value = 0
        d.pixel_x_i.value = 0
        d.pixel_y_i.value = 0
        # Why: the snapshot ports stay idle here (the snapshot target owns them).
        d.snapshot_pin_i.value = 0
        d.snapshot_release_i.value = 0
        d.snapshot_request_i.value = 0
        for _ in range(3):
            await RisingEdge(d.clk_i)
        await FallingEdge(d.clk_i)
        d.reset_i.value = 0

    async def set_lcd(self, on):
        await FallingEdge(self.dut.clk_i)
        self.dut.lcd_enable_i.value = int(on)

    async def drive_frame(self, frame, gap_max=3, last_pixel_edge=None, lcd_after_last=None):
        """Emit every pixel of ``frame`` in raster order as one-cycle pulses
        separated by 0..gap_max idle cycles. With ``last_pixel_edge`` the
        final pixel (159, 143) is sampled exactly at that rising edge; with
        ``lcd_after_last`` the LCD enable takes that value on the falling
        edge that withdraws the final pixel, so it is sampled at the very
        next rising edge after the pixel's."""
        d = self.dut
        for y in range(m.FRAME_HEIGHT):
            for x in range(m.FRAME_WIDTH):
                last = (x, y) == (m.FRAME_WIDTH - 1, m.FRAME_HEIGHT - 1)
                if last and last_pixel_edge is not None:
                    # Why: present the pixel at the falling edge before the target
                    # rising edge: land 10 ns earlier so that falling edge is
                    # unambiguously the next one awaited.
                    target_ns = last_pixel_edge * CLOCK_PERIOD_NS - HALF_PERIOD_NS
                    assert target_ns > get_sim_time(unit='ns') + CLOCK_PERIOD_NS, 'last pixel scheduled in the past'
                    await Timer(target_ns - 10 - get_sim_time(unit='ns'), unit='ns')
                await FallingEdge(d.clk_i)
                d.pixel_valid_i.value = 1
                d.pixel_shade_i.value = frame[y][x]
                d.pixel_x_i.value = x
                d.pixel_y_i.value = y
                await FallingEdge(d.clk_i)
                d.pixel_valid_i.value = 0
                if last and lcd_after_last is not None:
                    d.lcd_enable_i.value = int(lcd_after_last)
                gap = 0 if last else self.rng.randrange(gap_max + 1)
                if gap:
                    # Why: idle cycles; the next falling edge awaited is unambiguous.
                    await Timer(gap * CLOCK_PERIOD_NS - 10, unit='ns')
        self.check_bound()

    def read_output(self):
        d = self.dut
        return (int(d.vga_hsync_o.value), int(d.vga_vsync_o.value),
                (int(d.vga_red_o.value), int(d.vga_green_o.value), int(d.vga_blue_o.value)))

    async def next_start(self):
        """Wait for the next pixel_start_o pulse; returns its edge index."""
        await RisingEdge(self.dut.pixel_start_o)
        return self.now_edge()

    async def begin_phase(self):
        """Align a phase with an output frame start, so its stimulus has a
        whole raster period before the next origin; returns the phase edge."""
        await self.next_start()
        return self.now_edge()

    async def capture_frame(self):
        """From the next pixel_start_o pulse, every output clock of one
        frame sampled on the falling edges; returns (first edge, clocks)."""
        d = self.dut
        start = await self.next_start()
        clocks = []
        for _ in range(m.FRAME_CLOCKS):
            await FallingEdge(d.clk_i)
            clocks.append(self.read_output())
        self.check_bound()
        return start, clocks

    async def capture_lines(self, lines):
        """From the next pixel_start_o pulse, the output clocks of the given
        raster lines of that frame; returns (first edge, {line: clocks})."""
        d = self.dut
        start = await self.next_start()
        start_ns = start * CLOCK_PERIOD_NS
        captured = {}
        for line in sorted(lines):
            # Why: land inside the high phase of the line's first clock, so the
            # falling edge awaited next is unambiguously that clock's.
            line_ns = start_ns + line * m.H_TOTAL * CLOCK_PERIOD_NS + HALF_PERIOD_NS // 2
            await Timer(line_ns - get_sim_time(unit='ns'), unit='ns')
            clocks = []
            for _ in range(m.H_TOTAL):
                await FallingEdge(d.clk_i)
                clocks.append(self.read_output())
            captured[line] = clocks
        self.check_bound()
        return start, captured

    def sample_lines(self):
        """A seeded choice of raster lines: image edges, border, blanking and
        random image lines."""
        lines = {0, m.IMAGE_TOP - 1, m.IMAGE_TOP, m.IMAGE_TOP + 1, m.IMAGE_TOP + m.IMAGE_HEIGHT - 1,
                 m.IMAGE_TOP + m.IMAGE_HEIGHT, m.V_VISIBLE - 1, m.V_VISIBLE, m.V_VISIBLE + m.V_FRONT_PORCH,
                 m.V_TOTAL - 2}   # Why: the capture ends before the next origin's pulses.
        lines |= {self.rng.randrange(m.IMAGE_TOP, m.IMAGE_TOP + m.IMAGE_HEIGHT) for _ in range(12)}
        return lines


def check_lines(captured, frame, label):
    for line, clocks in captured.items():
        m.compare_clocks(clocks, m.expected_line(frame, line), first_clock=line * m.H_TOTAL, label=f'{label} line {line}')


def check_pulses(events, expected, label):
    """Phase-local pulse record: exactly the expected pulses (two pulses in
    one edge are recorded in arbitrary order, so the multiset is compared;
    the phases assert the edges that matter)."""
    observed = sorted(kind for _, kind in events)
    assert observed == sorted(expected), f'{label}: pulses {events}, expected {list(expected)}'


@cocotb.test(timeout_time=800, timeout_unit='ms')
async def display_contract(dut):
    seed = int(os.environ['DV_SEED'])
    assert int(os.environ['DV_CLOCK_HZ']) == 25_000_000
    cocotb.log.info('nand2mario display seed=%d', seed)
    b = Bench(dut, seed)
    b.power_up()
    cocotb.start_soon(Clock(dut.clk_i, CLOCK_PERIOD_NS, unit='ns').start())
    await b.reset()
    for name in (*PULSES, 'pixel_start_o'):
        cocotb.start_soon(b.watch(name))

    phase_edge = b.now_edge()
    start, clocks = await b.capture_frame()
    m.compare_clocks(clocks, m.expected_frame(None), label='blank frame')
    assert int(dut.display_valid_o.value) == 0
    next_start = await b.next_start()
    assert next_start - start == m.FRAME_CLOCKS, (start, next_start)
    check_pulses(b.events_since(phase_edge), (), 'phase 1')
    cocotb.log.info('phase 1: blank frame and sync timing verified from edge %d', start)

    await b.set_lcd(True)
    phase_edge = b.now_edge()
    frame1 = random_frame(b.rng)
    await b.drive_frame(frame1)
    complete_edge = b.events[-1][0]
    swap_edge = await b.wait_pulse('frame_swapped_o', phase_edge)
    assert swap_edge == b.swap_edge_for(complete_edge), (swap_edge, complete_edge, b.starts)
    check_pulses(b.events_since(phase_edge), ('frame_complete_o', 'frame_swapped_o'), 'phase 2')
    start, clocks = await b.capture_frame()
    assert start == swap_edge - PULSE_LATENCY + m.OUTPUT_LATENCY, (start, swap_edge)
    m.compare_clocks(clocks, m.expected_frame(frame1), label='frame 1')
    assert int(dut.display_valid_o.value) == 1
    m.write_rgb_png(Path.cwd() / 'vga_frame1.png', m.visible_rows(clocks))
    cocotb.log.info('phase 2: frame 1 swapped at edge %d and displayed', swap_edge)

    # Why: proves the check can fail: the clock comparison must reject one changed pixel.
    corrupted = [row[:] for row in frame1]
    corrupted[101][37] ^= 1
    try:
        m.compare_clocks(clocks, m.expected_frame(corrupted), label='corrupted')
    except AssertionError:
        pass
    else:
        raise AssertionError('frame comparison accepted a corrupted expectation')

    phase_edge = await b.begin_phase()
    frame2, frame3 = random_frame(b.rng), random_frame(b.rng)
    await b.drive_frame(frame2, gap_max=1)
    await b.drive_frame(frame3, gap_max=1)
    complete_edge = b.events[-1][0]
    assert complete_edge - PULSE_LATENCY < b.origin_cycle(phase_edge), 'both frames must complete before the next origin'
    swap_edge = await b.wait_pulse('frame_swapped_o', phase_edge)
    assert swap_edge == b.origin_cycle(phase_edge) + PULSE_LATENCY
    check_pulses(b.events_since(phase_edge),
                 ('frame_complete_o', 'frame_complete_o', 'frame_dropped_o', 'frame_swapped_o'), 'phase 3')
    _, captured = await b.capture_lines(b.sample_lines())
    check_lines(captured, frame3, 'frame 3')
    cocotb.log.info('phase 3: frame 2 dropped, frame 3 displayed')

    phase_edge = await b.begin_phase()
    repeat_edge = await b.wait_pulse('frame_repeated_o', phase_edge)
    assert repeat_edge == b.origin_cycle(phase_edge) + PULSE_LATENCY
    check_pulses(b.events_since(phase_edge), ('frame_repeated_o',), 'phase 4')
    _, captured = await b.capture_lines(b.sample_lines())
    check_lines(captured, frame3, 'repeated frame 3')
    cocotb.log.info('phase 4: frame 3 repeated')

    for offset, expected in ((0, ('frame_complete_o', 'frame_swapped_o')),
                             (1, ('frame_repeated_o', 'frame_complete_o', 'frame_swapped_o'))):
        phase_edge = await b.begin_phase()
        frame = random_frame(b.rng)
        origin = b.origin_cycle(phase_edge + 150_000)
        # Why: the pixel presented in cycle origin + offset is sampled at the next edge.
        await b.drive_frame(frame, gap_max=1, last_pixel_edge=origin + offset + 1)
        swap_edge = await b.wait_pulse('frame_swapped_o', phase_edge)
        assert swap_edge == origin + offset * m.FRAME_CLOCKS + PULSE_LATENCY, (offset, swap_edge, origin)
        check_pulses(b.events_since(phase_edge), expected, f'phase 5 offset {offset}')
        _, captured = await b.capture_lines(b.sample_lines())
        check_lines(captured, frame, f'origin offset {offset} frame')
        cocotb.log.info('phase 5: last pixel at origin + %d swapped at edge %d', offset, swap_edge)

    phase_edge = b.now_edge()
    await b.set_lcd(False)
    await b.drive_frame(random_frame(b.rng), gap_max=1)
    _, captured = await b.capture_lines(b.sample_lines())
    check_pulses(b.events_since(phase_edge), ('frame_complete_o',), 'phase 6 off')
    check_lines(captured, None, 'LCD off frame')
    assert int(dut.display_valid_o.value) == 0
    phase_edge = b.now_edge()
    await b.set_lcd(True)
    _, captured = await b.capture_lines(b.sample_lines())
    check_pulses(b.events_since(phase_edge), (), 'phase 6 on, nothing complete')
    check_lines(captured, None, 'LCD on blank frame')
    frame6 = random_frame(b.rng)
    phase_edge = await b.begin_phase()
    await b.drive_frame(frame6, gap_max=1)
    await b.wait_pulse('frame_swapped_o', phase_edge)
    check_pulses(b.events_since(phase_edge), ('frame_complete_o', 'frame_swapped_o'), 'phase 6 recovery')
    _, captured = await b.capture_lines(b.sample_lines())
    check_lines(captured, frame6, 'frame 6')
    phase_edge = await b.begin_phase()
    await b.drive_frame(random_frame(b.rng), gap_max=1)
    await b.set_lcd(False)
    await Timer(5 * CLOCK_PERIOD_NS, unit='ns')
    await b.set_lcd(True)
    assert b.now_edge() < b.origin_cycle(phase_edge)
    _, captured = await b.capture_lines(b.sample_lines())
    check_pulses(b.events_since(phase_edge), ('frame_complete_o', 'frame_repeated_o'), 'phase 6 brief off')
    check_lines(captured, frame6, 'frame 6 kept across a brief LCD off')

    for coincident in (True, False):
        await b.set_lcd(False)
        phase_edge = await b.begin_phase()
        assert int(dut.display_valid_o.value) == 0
        origin = b.origin_cycle(phase_edge + 150_000)
        # Why: the last pixel is sampled at edge `sample`; the LCD enable is set in
        # the cycle after that edge and is sampled together with the origin
        # (coincident) or long before it.
        sample = origin if coincident else origin - 100_000
        await b.drive_frame(random_frame(b.rng), gap_max=1, last_pixel_edge=sample, lcd_after_last=True)
        assert b.now_edge() == sample, (b.now_edge(), sample)
        _, captured = await b.capture_lines(b.sample_lines())
        check_pulses(b.events_since(phase_edge), ('frame_complete_o',), f'phase 7 coincident={coincident}')
        check_lines(captured, None, f'LCD-off completion discarded (coincident origin {coincident})')
        assert int(dut.display_valid_o.value) == 0
        frame7 = random_frame(b.rng)
        phase_edge = await b.begin_phase()
        await b.drive_frame(frame7, gap_max=1)
        await b.wait_pulse('frame_swapped_o', phase_edge)
        check_pulses(b.events_since(phase_edge), ('frame_complete_o', 'frame_swapped_o'), f'phase 7 recovery {coincident}')
        _, captured = await b.capture_lines(b.sample_lines())
        check_lines(captured, frame7, f'frame 7 after recovery (coincident origin {coincident})')
    cocotb.log.info('phase 6/7: LCD-off blank, recovery, brief-off retention and off-completion priority verified')

    assert all(later - earlier == m.FRAME_CLOCKS for earlier, later in zip(b.starts, b.starts[1:])), b.starts
    b.check_bound()
    cocotb.log.info('display checks passed seed=%d edges=%d starts=%d events=%d',
                    seed, b.now_edge(), len(b.starts), len(b.events))
