"""Literal public-port STAT source boundaries, independent of the timing model."""
import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly
from cocotb.utils import get_sim_time

from .test_ppu import DATA, Bench, request_word, response_fields


@cocotb.test(timeout_time=50, timeout_unit='ms')
async def ppu_stat_contract(dut):
    seed = int(os.environ['DV_SEED'])
    assert int(os.environ['DV_CLOCK_HZ']) == 25_000_000
    b = Bench(dut, seed)
    b.idle()
    for index in range(8192):
        dut.u_vram.memory[index].value = b.rng.randrange(1, 256)
    for index in range(256):
        dut.u_oam.memory[index].value = b.rng.randrange(1, 256)
    Clock(dut.clk_i, 40, unit='ns', impl='gpi').start()
    cocotb.start_soon(b.monitor())

    async def origin(enables, lyc=255, scx=0):
        await b.reset()
        await b.write(0xFF45, lyc)
        await b.write(0xFF43, scx)
        await b.write(0xFF41, enables)
        b.stat_events.clear()
        b.vblank_events.clear()
        for _ in range(12):
            await b.edge()
        await FallingEdge(dut.clk_i)
        assert b.stat_events == [] and b.vblank_events == []
        assert (int(dut.ly_o.value), int(dut.dot_o.value),
                int(dut.mode_o.value)) == (0, 0, 0)
        await b.write(0xFF40, 0x80)

    async def until(line, dot):
        # Why: a full frame is <420k system edges. The final aggregate check also
        # counts reset/register traffic and preserves this target's 1M bound.
        for _ in range(420_000):
            if (int(dut.ly_o.value), int(dut.dot_o.value)) == (line, dot):
                await FallingEdge(dut.clk_i)
                return
            await b.edge()
            await ReadOnly()
        raise AssertionError(f'raster did not reach {(line, dot)}')

    async def scan(address, line, dot):
        seen = {}
        await FallingEdge(dut.clk_i)
        dut.io_request_i.value = request_word(1, 0, DATA, address, 0)
        for _ in range(420_000):
            await ReadOnly()
            position = (int(dut.ly_o.value), int(dut.dot_o.value))
            sample = (int(dut.mode_o.value), int(dut.vram_blocked_o.value),
                      int(dut.oam_blocked_o.value),
                      response_fields(int(dut.io_response_o.value))[2])
            assert seen.setdefault(position, sample) == sample, (position, 'unstable within a dot')
            if position == (line, dot):
                await FallingEdge(dut.clk_i)
                dut.io_request_i.value = 0
                return seen
            await b.edge()
        raise AssertionError(f'raster did not reach {(line, dot)}')

    # Why: lists are literal consequences of the modeled source and OR-line
    # contract. Never call stat_line/stat_edges or read the RTL's constants.
    # The monitor counts EVERY high system edge, so widened pulses duplicate
    # an entry and fail these exact lists.
    await origin(0x20)
    assert b.stat_events == [(0, 0)], 'mode2 LCD-enable origin'
    await until(1, 8)
    assert b.stat_events == [(0, 0), (1, 0)], 'line0/line1 mode2 boundaries'
    await until(143, 440)
    ly = await scan(0xFF44, 144, 8)
    assert [ly[(143, d)][3] for d in (451, 452, 455)] == [143, 144, 144], 'LY 143->144 read'
    assert ly[(144, 0)][3] == 144
    visible_and_entry = [(line, 0) for line in range(145)]
    assert b.stat_events == visible_and_entry, 'isolated mode2 and VBlank entry'
    assert b.vblank_events == [(144, 0)]
    await until(153, 440)
    ly = await scan(0xFF44, 0, 8)
    assert [ly[(153, d)][3] for d in (451, 452, 455)] == [153, 0, 0], 'LY 153->0 read'
    assert [ly[(0, d)][3] for d in (0, 3, 4)] == [0, 0, 0]
    assert b.stat_events == visible_and_entry + [(0, 0)], 'isolated frame-wrap edge'
    assert b.vblank_events == [(144, 0)]

    # Why: mode1 holds the shared line high throughout VBlank; mode2 takes over
    # at wrap without a falling edge, so a second IRQ would be spurious.
    await origin(0x30)
    await until(144, 8)
    assert b.stat_events == visible_and_entry
    assert b.vblank_events == [(144, 0)]
    await until(0, 8)
    assert b.stat_events == visible_and_entry, 'shared STAT blocks frame-wrap'
    await until(1, 8)
    assert b.stat_events == visible_and_entry + [(1, 0)], 'mode2 rearms after line0'

    await origin(0x60, lyc=0)
    await until(1, 8)
    assert b.stat_events == [(0, 0)], 'LYC0 blocks adjacent line1 mode2'
    await until(2, 8)
    assert b.stat_events == [(0, 0), (2, 0)], 'LYC/mode2 rearm'
    assert b.vblank_events == []

    for fine in range(8):
        await origin(0x08, scx=0xF8 | fine)
        stat = await scan(0xFF41, 1, 300)
        assert b.stat_events == [(0, 255 + fine), (1, 255 + fine)], ('mode0 source', fine)
        for line in (0, 1):
            assert stat[(line, 79)] == (2, 0, 1, 0x80 | 0x08 | 2), (line, fine, 'dot 79')
            assert stat[(line, 80)] == (3, 1, 1, 0x80 | 0x08 | 3), (line, fine, 'dot 80')
            assert stat[(line, 251 + fine)] == (3, 1, 1, 0x80 | 0x08 | 3), (line, fine, 'last transfer dot')
            assert stat[(line, 252 + fine)] == (0, 0, 0, 0x80 | 0x08 | 0), (line, fine, 'transfer end')
        ly = await scan(0xFF44, 2, 8)
        assert [ly[(1, d)][3] for d in (451, 452, 455)] == [1, 2, 2], ('LY 1->2 read', fine)
        assert [ly[(2, d)][3] for d in (0, 3, 4)] == [2, 2, 2], ('LY line 2 read', fine)
        assert b.stat_events == [(0, 255 + fine), (1, 255 + fine)], ('no extra mode0 edge', fine)
        assert b.vblank_events == []

    await origin(0)
    await until(0, 12)
    assert b.stat_events == []
    await b.write(0xFF41, 0x20)
    assert b.stat_events == [(0, 12)], 'STAT enable inside active mode2'
    for _ in range(20):
        await b.edge()
    await b.write(0xFF40, 0)
    for _ in range(20):
        await b.edge()
    await FallingEdge(dut.clk_i)
    assert b.stat_events == [(0, 12)] and b.vblank_events == []
    assert (int(dut.ly_o.value), int(dut.dot_o.value),
            int(dut.mode_o.value), int(dut.vram_blocked_o.value),
            int(dut.oam_blocked_o.value)) == (0, 0, 0, 0, 0)
    edges = int(get_sim_time(unit='ns')) // 40
    assert edges <= b.bound, f'finite simulation bound exceeded ({edges} edges)'
    cocotb.log.info('literal STAT contract passed seed=%d edges=%d', seed, edges)
