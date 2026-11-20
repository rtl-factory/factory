"""PPU timing trace of one Mooneye ROM (conformance debug aid, #483).

Loads the ROM like test_mooneye, then samples every system cycle and writes
one JSON line per event while LY is inside N2M_TRACE_LINES (default 66,67):
a CPU retirement (PC, interrupt flag), a PPU mode change, a STAT or VBlank
interrupt pulse, with the cycle, dot and LY. It stops after
N2M_TRACE_FRAMES passes through the window or at DV_MAX_CYCLES. Output goes
to N2M_TRACE_OUT. Not part of any regression level.
"""
import json
import os
import random
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge

from ..core.test_cpu import unpack
from ..system.test_system import idle_host, power_up

CLOCK_PERIOD_NS = 40


@cocotb.test()
async def ppu_trace(dut):
    image = Path(os.environ['N2M_SYSTEM_IMAGE']).read_bytes()
    out = Path(os.environ['N2M_TRACE_OUT'])
    lines = {int(x) for x in os.environ.get('N2M_TRACE_LINES', '66,67').split(',')}
    frames = int(os.environ.get('N2M_TRACE_FRAMES', '4'))
    bound = int(os.environ['DV_MAX_CYCLES'])
    power_up(dut, random.Random(int(os.environ['DV_SEED'])))
    dut.reset_i.value = 1
    dut.buttons_i.value = 0
    dut.rom_load_write_i.value = 0
    idle_host(dut)
    Clock(dut.clk_i, CLOCK_PERIOD_NS, unit='ns', impl='gpi').start()
    await RisingEdge(dut.clk_i)
    for address, byte in enumerate(image):
        await FallingEdge(dut.clk_i)
        dut.rom_load_write_i.value = 1
        dut.rom_load_address_i.value = address
        dut.rom_load_data_i.value = byte
        await RisingEdge(dut.clk_i)
    await FallingEdge(dut.clk_i)
    dut.rom_load_write_i.value = 0
    await RisingEdge(dut.clk_i)
    await FallingEdge(dut.clk_i)
    dut.reset_i.value = 0
    t = dut.u_ppu.u_timing
    cycle, passes, inside, last_mode, last_ret = 0, 0, False, None, None
    with out.open('w') as handle:
        while cycle < bound and passes < frames:
            await RisingEdge(dut.clk_i)
            await ReadOnly()
            cycle += 1
            ly = int(t.ly_q.value)
            now = ly in lines and int(t.lcd_enable_i.value)
            if inside and not now:
                passes += 1
            inside = now
            if not now:
                last_mode = None
                continue
            ev = {}
            mode = int(t.mode_o.value)
            if mode != last_mode:
                ev['mode'] = mode
                last_mode = mode
            if int(t.stat_irq_o.value):
                ev['stat_irq'] = 1
            raw = int(dut.retirement_o.value)
            if raw != last_ret:
                last_ret = raw
                r = unpack(raw)
                if r['valid']:
                    ev['pc'] = r['pc_before']
                    if r['is_interrupt']:
                        ev['int'] = 1
            if ev:
                ev.update(c=cycle, ly=ly, dot=int(t.dot_q.value))
                handle.write(json.dumps(ev) + '\n')
