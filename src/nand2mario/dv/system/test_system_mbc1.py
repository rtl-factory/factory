"""n2m_system MBC1: a locally built 1 MiB / 32 KiB-RAM MBC1+RAM+BATTERY image
runs out of the cartridge backing through the mapper. Every retirement
matches the independent CPU oracle over the Python memory map whose cartridge
is the MBC1 model; the program switches ROM banks in both modes (including
code executing from switched banks and the mode-1 low-window remap from a
WRAM-resident routine), banks and enables/disables the cartridge RAM, and
leaves an exact WRAM and cartridge RAM signature with pinned values."""
import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge

from ..core.cpu_model import CPU
from ..core.test_cpu import unpack
from ..memory.memory_model import GameBoyMemory
from .peripheral_model import Peripherals
from .test_system import (idle_host, CLOCK_PERIOD_NS, MACHINE_CYCLE_EDGES, compare_signature, defined_signature,
                          power_up, store_bytes)

IMAGE_BYTES = 1 << 20
INSTRUCTION_BOUND = 2000
TABLE_BANKS = (1, 2, 1, 31, 33, 63, 33, 5, 1, 35)


def bank_pattern(bank, offset):
    """Per-bank data byte the image expander wrote at bank offset `offset`."""
    return (bank * 0x25 + (offset - 0x3F00)) & 0xFF


def load_image():
    from pathlib import Path
    root = Path(__file__).resolve().parents[4]
    image_arg = os.environ.get('N2M_SYSTEM_IMAGE')
    assert image_arg, 'set N2M_SYSTEM_IMAGE to the locally built MBC1 cartridge image'
    image = (root / image_arg).resolve()
    assert image.is_relative_to(root / 'workdir') and image.is_file(), 'image must exist below repository workdir'
    data = image.read_bytes()
    assert len(data) == IMAGE_BYTES, 'the expanded MBC1 image is exactly 1 MiB'
    assert data[0x147:0x14A] == bytes([0x03, 0x05, 0x03]), 'MBC1+RAM+BATTERY, 1 MiB, 32 KiB RAM'
    return data


@cocotb.test(timeout_time=120, timeout_unit='ms')
async def system_mbc1(dut):
    seed = int(os.environ['DV_SEED'])
    bound = int(os.environ['DV_MAX_CYCLES'])
    assert int(os.environ['DV_CLOCK_HZ']) == 25_000_000
    cocotb.log.info('n2m_system MBC1 seed=%d (directed) bound=%d', seed, bound)
    image = load_image()
    cycles = 0
    # Why: the 1 MiB ROM store is defined entirely by the load below, so its
    # (costly) simulator handle is not touched; RAM and the map stores get
    # garbage.
    power_up(dut, random.Random(seed), rom_bytes=0)

    dut.reset_i.value = 1
    dut.buttons_i.value = 0
    dut.rom_load_write_i.value = 0
    dut.rom_load_address_i.value = 0
    dut.rom_load_data_i.value = 0
    idle_host(dut)
    cocotb.start_soon(Clock(dut.clk_i, CLOCK_PERIOD_NS, unit='ns').start())
    await RisingEdge(dut.clk_i)

    # Why: load the whole cartridge through the load port while in reset; the
    # mapper captures the header bytes as they pass.
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
    load_edges = cycles

    peripherals = Peripherals()
    memory = GameBoyMemory(image, io_read=peripherals.read, io_write=peripherals.write)
    cartridge = memory.cartridge
    model = CPU(mem=memory)
    retirements = 0
    anchor_edge = anchor_cycles = None
    banks_executed = set()
    halted = False
    while not halted:
        await RisingEdge(dut.clk_i)
        await ReadOnly()
        cycles += 1
        assert cycles <= bound, f'finite bound exceeded after {retirements} retirements'
        assert not int(dut.fault_o.value), 'unexpected bus fault'
        assert not int(dut.locked_o.value), 'unexpected illegal-opcode lock'
        assert not int(dut.stopped_o.value), 'unexpected STOP'
        record = unpack(int(dut.retirement_o.value))
        if not record['valid']:
            continue
        assert not record['is_interrupt'], 'the program enables no interrupt'
        pc = model.pc
        if pc < 0x8000:
            banks_executed.add(cartridge.rom_bank(pc))
        expected, instruction_cycles, _ = model.step(pending=0)
        mismatch = {k: (record[k], v) for k, v in expected.items() if record[k] != v}
        assert not mismatch, (f'retirement {retirements} pc={expected["pc_before"]:04x}', mismatch)
        retirements += 1
        assert retirements <= INSTRUCTION_BOUND, 'program did not halt within the instruction bound'
        # Why: composed timing: the 23/24-edge machine cadence with no stall, so
        # the one-edge on-chip backing never stretches an instruction.
        if anchor_edge is not None:
            anchor_cycles += instruction_cycles
            drift = (cycles - anchor_edge) - anchor_cycles * MACHINE_CYCLE_EDGES
            assert abs(drift) < MACHINE_CYCLE_EDGES, (f'retirement {retirements}: edges since anchor '
                                                      f'{cycles - anchor_edge} expected about {anchor_cycles * MACHINE_CYCLE_EDGES:.1f}')
        else:
            anchor_edge, anchor_cycles = cycles, 0
        halted = bool(record['halted'])
    for _ in range(4):
        await RisingEdge(dut.clk_i)
        await ReadOnly()
    assert int(dut.halted_o.value) == 1, 'CPU must report HALT after the halting retirement'

    wram_observed, wram_expected = defined_signature(store_bytes(dut.u_memory.u_wram.memory), memory, 'wram')
    compare_signature(wram_observed, wram_expected, 'WRAM')
    ram_store = store_bytes(dut.g_onchip.u_backing.u_ram.memory)
    ram_pairs = cartridge.defined_ram_bytes()
    compare_signature(bytes(ram_store[offset] for offset, _ in ram_pairs), bytes(value for _, value in ram_pairs), 'CART-RAM')
    assert len(ram_pairs) == 8 and [offset for offset, _ in ram_pairs] == [0x0000, 0x1FFF, 0x2000, 0x3FFF, 0x4000, 0x5FFF, 0x6000, 0x7FFF], (
        'four RAM banks with two bytes each', ram_pairs)
    assert [value for _, value in ram_pairs] == [0x50, 0x60, 0x51, 0x61, 0x52, 0x62, 0x53, 0x63]
    assert len(wram_observed) >= 0x20, ('the program defines at least $C000-$C01F', len(wram_observed))

    # Why: pinned evidence independent of the oracle.
    wram = store_bytes(dut.u_memory.u_wram.memory)
    pinned = {0x00: 0x52, 0x01: bank_pattern(32, 0x3F80), 0x02: 0x20, 0x03: 0x21, 0x04: 0xC3, 0x05: 0x01,
              0x06: 0x50, 0x07: 0x60, 0x08: 0xFF, 0x09: 0x50, 0x0A: 0xFF, 0x1F: len(TABLE_BANKS)}
    for index, bank in enumerate(TABLE_BANKS):
        pinned[0x0B + 2 * index] = bank
        pinned[0x0C + 2 * index] = bank_pattern(bank, 0x3F80)
    for offset, value in sorted(pinned.items()):
        assert wram[offset] == value, f'pinned WRAM[{offset:02x}] observed {wram[offset]:02x} expected {value:02x}'
    assert banks_executed >= {0} | set(TABLE_BANKS), ('code must execute from bank 0 and every table bank', sorted(banks_executed))
    assert cartridge.registers['mode'] == 0 and cartridge.registers['ramg'] is True

    # Why: proves the check can fail: the signature comparison must reject a single flipped byte.
    corrupted = bytearray(wram_expected)
    corrupted[0] ^= 0x01
    try:
        compare_signature(wram_observed, bytes(corrupted), 'WRAM-negative')
    except AssertionError:
        pass
    else:
        raise AssertionError('signature comparison accepted a corrupted expectation')
    cocotb.log.info('n2m_system MBC1 passed: retirements=%d load_edges=%d total_cycles=%d banks_executed=%s wram_bytes=%d',
                    retirements, load_edges, cycles, sorted(banks_executed), len(wram_observed))
