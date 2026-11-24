"""n2m_system loaded and booted by the common UART memory host library
(wiki/src/common/scripts/uart_mem/spec.md#load-transaction).

The bench is the host on the system's serial pins at exactly 2 Mbaud (500 ns
bits, time form), while the system's UART runs the board configuration: 12
1/2 clocks a bit at 25 MHz through the common PHY's fractional accumulator.
The host side is the library itself: uart_mem.load (identify and project
check, one hold, write and read back each segment, entry, one release) runs
in a cocotb bridge thread over the common simulated transport
(src/common/dv/uart_mem/sim_host.py). The segments are a cartridge header
(ROM only, no RAM) and a short program at the entry point, 0x0100, over a
ROM filled with garbage: the program can only run if the load landed. It
clears IE, stores a marker in WRAM and halts; the bench requires the CPU to
retire the program's instructions to that final HALT and the marker in WRAM.
"""
import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ReadOnly, RisingEdge
from cocotb.utils import get_sim_time

from src.common.dv.uart.uart_wire import UartWire
from src.common.dv.uart_mem import sim_host
from src.common.scripts.uart_mem import uart_mem as um

from ..core.test_cpu import unpack
from .test_system import CLOCK_PERIOD_NS, power_up, store_array

BAUD = 2_000_000              # Why: the factory board rate; the registry's UART_CLKS_PER_BIT 12 + 1/2 must match it at 25 MHz.
BIT_PS = 10**12 // BAUD
# Why: each received bit must hold over its middle 60%; the fractional period is
# within 20 ns of the ideal edges, while 12 whole clocks drift 160 ns by bit 8.
SAMPLE_WINDOW_PS = 150_000
PROJECT = int.from_bytes(b'n2m ', 'little')
ENTRY = 0x0100
MARKER, MARKER_ADDRESS = 0x5A, 0xC000
# Ref: Pan Docs "The Cartridge Header": 0x147 type 0x00 (ROM only), 0x148 ROM size 32 KiB, 0x149 no RAM.
HEADER = um.Segment(0x0144, bytes([0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]))
# Ref: Pan Docs "CPU Instruction Set": the SM83 opcodes the program uses.
DI, XOR_A, LDH_N_A, LD_A_N, LD_NN_A, HALT, NOP = 0xF3, 0xAF, 0xE0, 0x3E, 0xEA, 0x76, 0x00
IE_LOW = 0xFF
# Why: IE is cleared first so nothing can wake the final HALT; two NOPs pad to whole words.
PROGRAM = um.Segment(ENTRY, bytes([
    DI,
    XOR_A,
    LDH_N_A, IE_LOW,
    LD_A_N, MARKER,
    LD_NN_A, MARKER_ADDRESS & 0xFF, MARKER_ADDRESS >> 8,
    HALT,
    NOP, NOP,
]))
PROGRAM_PCS = [0x0101, 0x0102, 0x0104, 0x0106, 0x0109, 0x010A]


@cocotb.test(timeout_time=60, timeout_unit='ms')
async def system_uart_mem_load(dut):
    seed = int(os.environ['DV_SEED'])
    bound = int(os.environ['DV_MAX_CYCLES'])
    assert int(os.environ['DV_CLOCK_HZ']) == 25_000_000
    rng = random.Random(seed)
    cocotb.log.info('n2m_system uart_mem load seed=%d bound=%d', seed, bound)
    power_up(dut, rng)
    dut.reset_i.value = 1
    dut.buttons_i.value = 0
    dut.rom_load_write_i.value = 0
    dut.rom_load_address_i.value = 0
    dut.rom_load_data_i.value = 0
    dut.uart_rx_i.value = 1
    dut.dram_dq_i.value = 0
    cocotb.start_soon(Clock(dut.clk_i, CLOCK_PERIOD_NS, unit='ns').start())
    for _ in range(4):
        await RisingEdge(dut.clk_i)
    dut.reset_i.value = 0

    await load_and_boot(dut, dut.uart_rx_i, dut.uart_tx_o, bound)


async def load_and_boot(system, rx, tx, bound):
    """Load HEADER and PROGRAM into `system` (an n2m_system) with uart_mem.load
    over its UART pins `rx`/`tx` at exactly 2 Mbaud, then require the core to
    retire the program to its final HALT and store its marker."""
    # Why: the core runs garbage ROM until the hold and the loaded program
    # finishes within the release response, so retirements are recorded from
    # the last core reset on, in the background.
    pcs = []
    cocotb.start_soon(record_retirements(system, pcs))
    wire = UartWire(rx=rx, tx=tx, bit_ps=BIT_PS, sample_window_ps=SAMPLE_WINDOW_PS)
    client = sim_host.client(wire, poll_ps=10 * BIT_PS, timeout_s=0.005)
    identity = await sim_host.run(client.identify)
    assert identity.project == 'n2m ' and identity.entry == ENTRY, identity
    await sim_host.run(um.load, client, [HEADER, PROGRAM], ENTRY, PROJECT)
    cocotb.log.info('loaded through uart_mem.load by edge %d', edge_now())
    assert not int(system.core_reset_o.value), 'release must start the core'
    while not (pcs and int(system.halted_o.value) and int(system.u_memory.interrupt_enable_o.value) == 0):
        assert edge_now() <= bound, f'finite bound exceeded after retiring {pcs}'
        await RisingEdge(system.clk_i)
        await ReadOnly()
    assert pcs == PROGRAM_PCS, f'retired {[hex(pc) for pc in pcs]}, expected the loaded program'
    wram = store_array(system.u_memory.u_wram)
    assert int(wram[MARKER_ADDRESS - 0xC000].value) == MARKER, 'the program stored its marker'
    cocotb.log.info('booted the loaded program to its HALT by edge %d', edge_now())


def edge_now():
    return int(get_sim_time(unit='ns')) // CLOCK_PERIOD_NS


async def record_retirements(dut, pcs):
    while True:
        await RisingEdge(dut.clk_i)
        await ReadOnly()
        if int(dut.core_reset_o.value):
            pcs.clear()
            continue
        assert not (pcs and int(dut.fault_o.value)), 'bus fault in the loaded program'
        record = unpack(int(dut.retirement_o.value))
        if record['valid']:
            pcs.append(record['pc_after'])
