"""One pinned Mooneye acceptance ROM on the unmodified system (conformance DV).

The ROM signals its verdict with the Mooneye breakpoint `LD B,B` ($40) and
then loops: B,C,D,E,H,L = 3,5,8,13,21,34 passes, all $42 fails. The registers
are sampled from the CPU's public retirement record every 100k cycles (the
cycle count is that resolution); reaching DV_MAX_CYCLES is a timeout. A
timeout records the PC sampled at every poll (the last few and the number
of distinct ones) and then counts retirements over one extra diagnostic
window, with the PCs retired in it, so a hung CPU (a tight loop, no
progress) can be told from a bound that is merely too short.
"""
import json
import os
import random
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, Timer, ValueChange

from ..core.test_cpu import unpack
from ..system.test_system import idle_host, power_up

CLOCK_PERIOD_NS = 40
POLL_CYCLES = 100_000
DIAGNOSTIC_CYCLES = 20_000
PASS = (3, 5, 8, 13, 21, 34)
FAIL = (0x42,) * 6


def verdict(registers):
    """Mooneye verdict of the B,C,D,E,H,L tuple at the LD B,B breakpoint."""
    registers = tuple(registers)
    if registers == PASS:
        return 'PASS'
    if registers == FAIL:
        return 'FAIL'
    return 'UNKNOWN'


@cocotb.test()
async def mooneye_rom(dut):
    image = Path(os.environ['N2M_SYSTEM_IMAGE']).read_bytes()
    result_path = Path(os.environ['N2M_MOONEYE_RESULT'])
    assert len(image) == 32768 and int(os.environ['DV_CLOCK_HZ']) == 25_000_000
    bound = int(os.environ['DV_MAX_CYCLES'])
    power_up(dut, random.Random(int(os.environ['DV_SEED'])))
    dut.reset_i.value = 1
    dut.buttons_i.value = 0
    dut.rom_load_write_i.value = 0
    dut.rom_load_address_i.value = 0
    dut.rom_load_data_i.value = 0
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

    # Why: the suite ends in an endless loop after its LD B,B breakpoint with the
    # verdict registers held, so the bench samples the latest retirement
    # record every POLL_CYCLES instead of waking per instruction.
    result = {'verdict': 'TIMEOUT', 'cycles': bound}
    elapsed = 0
    sampled = []
    while elapsed < bound - DIAGNOSTIC_CYCLES:
        await Timer(POLL_CYCLES * CLOCK_PERIOD_NS, unit='ns')
        elapsed += POLL_CYCLES
        record = unpack(int(dut.retirement_o.value))
        registers = [record[r] for r in 'bcdehl']
        sampled.append(record['pc_before'])
        if verdict(registers) != 'UNKNOWN':
            result.update(verdict=verdict(registers), registers=registers,
                          pc=record['pc_before'], cycles=elapsed)
            break
    if result['verdict'] == 'TIMEOUT':
        retired, pcs = 0, set()

        async def count():
            nonlocal retired
            while True:
                await ValueChange(dut.retirement_o)
                record = unpack(int(dut.retirement_o.value))
                if record['valid']:
                    retired += 1
                    pcs.add(record['pc_before'])
        counter = cocotb.start_soon(count())
        await Timer(DIAGNOSTIC_CYCLES * CLOCK_PERIOD_NS, unit='ns')
        counter.cancel()
        result.update(cycles=elapsed + DIAGNOSTIC_CYCLES, last_pcs=sampled[-8:],
                      distinct_sampled_pcs=len(set(sampled)),
                      window_cycles=DIAGNOSTIC_CYCLES, window_retirements=retired,
                      window_pcs=sorted(pcs)[:32], window_distinct_pcs=len(pcs))
    result_path.write_text(json.dumps(result, indent=2) + '\n')
    cocotb.log.info('mooneye verdict %s', result)
    # Why: the run itself succeeds whenever a verdict or timeout is recorded; the
    # conformance table, not the simulator status, carries pass/fail.
