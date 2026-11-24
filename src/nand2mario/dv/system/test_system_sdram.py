"""n2m_system SDRAM boot: the bench is the host on the loader's UART. It
watches the console stay in reset until the SDRAM initialized, holds the
core, writes a locally built 128 KiB / 32 KiB-RAM MBC1 image into the SDRAM
backing frame by frame, proves a corrupted frame changes nothing, reads part
of the image back, releases the core and compares every retirement with the
independent CPU oracle while the program runs from SDRAM through the mapper
(switched banks, both modes, cartridge RAM) at the unstretched machine
cadence, then holds the core again and reads the cartridge RAM back over the
UART. WRAM and cartridge RAM end in an exact, pinned signature."""
import os
import random
import time

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, ReadOnly, RisingEdge

from ...scripts.board.cart_loader import (CMD_HOLD, CMD_PING, CMD_READ, CMD_RELEASE, CMD_WRITE, STATUS_BAD_CRC, address_field,
                                  chunks, decode_response, encode_request, response_total_length)
from ...scripts.sw_build.mbc1_image import bank_pattern
from ..core.cpu_model import CPU
from ..core.test_cpu import unpack
from ..memory.memory_model import GameBoyMemory
from .peripheral_model import Peripherals
from .test_system import CLOCK_PERIOD_NS, MACHINE_CYCLE_EDGES, VRAM_BYTES, compare_signature, defined_signature, power_up, store_bytes

IMAGE_BYTES, BANKS = 1 << 17, 8
INSTRUCTION_BOUND = 1500
UART_CLKS_PER_BIT = 8                 # Why: must match the registry's parameter for this target.
BYTE_CLOCKS = 10 * UART_CLKS_PER_BIT
SDRAM_INITIALIZED_CLOCK = 5036  # Ref: wiki/src/common/rtl/sdram/mas.md#initialization-and-refresh-timeline
VERIFY_EVERY = 16


def load_image():
    from pathlib import Path
    root = Path(__file__).resolve().parents[4]
    image_arg = os.environ.get('N2M_SYSTEM_IMAGE')
    assert image_arg, 'set N2M_SYSTEM_IMAGE to the locally built SDRAM boot cartridge image'
    image = (root / image_arg).resolve()
    assert image.is_relative_to(root / 'workdir') and image.is_file(), 'image must exist below repository workdir'
    data = image.read_bytes()
    assert len(data) == IMAGE_BYTES, 'the expanded SDRAM boot image is exactly 128 KiB'
    assert data[0x147:0x14A] == bytes([0x03, 0x02, 0x03]), 'MBC1+RAM+BATTERY, 128 KiB, 32 KiB RAM'
    return data


class Host:
    """Byte-level host on the fixture's UART pair, with a clock counter."""

    def __init__(self, dut):
        self.dut = dut
        self.clock = 0
        self.received = []
        self.bound = int(os.environ['DV_MAX_CYCLES'])

    async def ticker(self):
        while True:
            await RisingEdge(self.dut.clk_i)
            self.clock += 1
            assert self.clock <= self.bound, 'finite simulation bound exceeded'

    async def receiver(self):
        d = self.dut
        while True:
            await RisingEdge(d.host_rx_valid_o)
            await ReadOnly()
            self.received.append(int(d.host_rx_data_o.value))

    async def send(self, data):
        d = self.dut
        for byte in data:
            if not int(d.host_tx_ready_o.value):
                await RisingEdge(d.host_tx_ready_o)
            await FallingEdge(d.clk_i)
            d.host_tx_valid_i.value = 1
            d.host_tx_data_i.value = byte
            await RisingEdge(d.clk_i)
            await FallingEdge(d.clk_i)
            d.host_tx_valid_i.value = 0

    async def exchange(self, frame, limit_clocks=None):
        """Send one frame and return its decoded, CRC-checked response."""
        start = len(self.received)
        await self.send(frame)
        limit = limit_clocks or BYTE_CLOCKS * (len(frame) + 300) + 20000
        deadline = self.clock + limit
        total = None
        while total is None or len(self.received) - start < total:
            assert self.clock < deadline, f'response timeout after {len(self.received) - start} bytes'
            await RisingEdge(self.dut.clk_i)
            if total is None and len(self.received) - start >= 7:
                total = response_total_length(bytes(self.received[start:start + 7]))
        response = decode_response(bytes(self.received[start:start + total]))
        assert response.crc_ok, 'response CRC'
        return response


@cocotb.test(timeout_time=1500, timeout_unit='ms')
async def system_sdram_boot(dut):
    seed = int(os.environ['DV_SEED'])
    assert int(os.environ['DV_CLOCK_HZ']) == 25_000_000
    cocotb.log.info('n2m_system SDRAM boot seed=%d (directed)', seed)
    image = load_image()
    system = dut.u_system
    power_up(system, random.Random(seed), onchip=False)
    host = Host(dut)

    dut.reset_i.value = 1
    dut.buttons_i.value = 0
    dut.host_tx_valid_i.value = 0
    dut.host_tx_data_i.value = 0
    dut.rom_load_write_i.value = 0
    dut.rom_load_address_i.value = 0
    dut.rom_load_data_i.value = 0
    cocotb.start_soon(Clock(dut.clk_i, CLOCK_PERIOD_NS, unit='ns').start())
    for _ in range(3):
        await RisingEdge(dut.clk_i)
    await FallingEdge(dut.clk_i)
    dut.reset_i.value = 0
    cocotb.start_soon(host.ticker())
    cocotb.start_soon(host.receiver())
    await ClockCycles(dut.clk_i, 2)

    assert int(dut.core_reset_o.value) == 1
    response = await host.exchange(encode_request(CMD_PING))
    assert response.ok and not response.initialized and not response.held, response.describe()
    while host.clock < SDRAM_INITIALIZED_CLOCK + 2:
        await RisingEdge(dut.clk_i)
    await ReadOnly()
    assert int(dut.core_reset_o.value) == 1, 'the VRAM sweep outlasts the SDRAM initialization'
    while host.clock < VRAM_BYTES + 2:
        await RisingEdge(dut.clk_i)
    await ReadOnly()
    assert int(dut.core_reset_o.value) == 0, 'the core runs once the backing is initialized and VRAM swept'
    response = await host.exchange(encode_request(CMD_PING))
    assert response.ok and response.initialized and not response.held, response.describe()

    started = time.monotonic()
    response = await host.exchange(encode_request(CMD_HOLD))
    assert response.ok and response.held
    await ReadOnly()
    assert int(dut.core_reset_o.value) == 1, 'HOLD resets the core'
    load_start = host.clock
    pieces = chunks(image)
    for offset, data in pieces:
        response = await host.exchange(encode_request(CMD_WRITE, address_field(offset), data))
        assert response.ok and response.offset == offset and response.length == len(data), response.describe()
    load_clocks = host.clock - load_start
    cocotb.log.info('image loaded: %d frames, %d clocks, %.1f s wall', len(pieces), load_clocks, time.monotonic() - started)

    # Why: proves the check can fail: a corrupted rewrite of the first chunk must be rejected
    # and changes nothing; the readback still shows the image.
    frame = encode_request(CMD_WRITE, address_field(0), bytes(b ^ 0xFF for b in pieces[0][1]))
    frame = frame[:-1] + bytes([frame[-1] ^ 0x01])
    response = await host.exchange(frame)
    assert response.code == STATUS_BAD_CRC, response.describe()
    verified = 0
    for index, (offset, data) in enumerate(pieces):
        if index % VERIFY_EVERY and index != len(pieces) - 1:
            continue
        response = await host.exchange(encode_request(CMD_READ, address_field(offset), length=len(data)))
        assert response.ok and response.payload == data, (offset, response.describe())
        verified += len(data)
    header_word = int(dut.u_sdram.memory[0x147 >> 1].value)
    assert (header_word >> 8) & 0xFF == 0x03, 'cartridge type byte in the device model'

    retirements, run_clocks, banks_executed, wram_bytes = await release_and_verify(dut, system, host, image)
    cocotb.log.info('n2m_system SDRAM boot passed: retirements=%d load_clocks=%d run_clocks=%d total_clocks=%d '
                    'verified_bytes=%d banks_executed=%s wram_bytes=%d', retirements, load_clocks, run_clocks,
                    host.clock, verified, sorted(banks_executed), wram_bytes)


async def release_and_verify(dut, system, host, image):
    """RELEASE the held core, replay every retirement against the oracle at
    the unstretched cadence until the HALT, check the exact and pinned WRAM
    signature, then HOLD again and read the cartridge RAM back over the UART.
    `dut` carries the CPU observation ports and `core_reset_o`, `system` the
    n2m_system hierarchy, `host` the UART host. Shared by the SDRAM boot and
    the direct-preload tests; returns (retirements, run clocks, banks, WRAM
    bytes)."""
    # Why: release: the core leaves reset when the loader executes RELEASE, before
    # the response is on the wire, so retirements are recorded from here on
    # and replayed against the oracle. The program runs from SDRAM; every
    # retirement matches the oracle at the unstretched machine cadence.
    retired = []

    async def record_retirements():
        while True:
            await RisingEdge(dut.clk_i)
            await ReadOnly()
            assert not int(dut.fault_o.value), 'unexpected bus fault'
            assert not int(dut.locked_o.value), 'unexpected illegal-opcode lock'
            assert not int(dut.stopped_o.value), 'unexpected STOP'
            record = unpack(int(dut.retirement_o.value))
            if record['valid']:
                retired.append((host.clock, record))

    recorder = cocotb.start_soon(record_retirements())
    release_clock = host.clock
    response = await host.exchange(encode_request(CMD_RELEASE))
    assert response.ok and not response.held
    await ReadOnly()
    assert int(dut.core_reset_o.value) == 0
    peripherals = Peripherals()
    memory = GameBoyMemory(image, io_read=peripherals.read, io_write=peripherals.write)
    cartridge = memory.cartridge
    model = CPU(mem=memory)
    retirements = 0
    anchor_edge = anchor_cycles = None
    banks_executed = set()
    halted = False
    consumed = 0
    while not halted:
        if consumed == len(retired):
            await RisingEdge(dut.clk_i)
            assert host.clock - release_clock < 2_000_000, 'the program did not halt'
            continue
        clock, record = retired[consumed]
        consumed += 1
        assert not record['is_interrupt'], 'the program enables no interrupt'
        pc = model.pc
        if pc < 0x8000:
            banks_executed.add(cartridge.rom_bank(pc))
        expected, instruction_cycles, _ = model.step(pending=0)
        mismatch = {k: (record[k], v) for k, v in expected.items() if record[k] != v}
        assert not mismatch, (f'retirement {retirements} pc={expected["pc_before"]:04x}', mismatch)
        retirements += 1
        assert retirements <= INSTRUCTION_BOUND, 'program did not halt within the instruction bound'
        # Why: composed timing: the SDRAM backing answers inside the machine cycle,
        # so the 23/24-edge cadence holds and no instruction is stretched.
        if anchor_edge is not None:
            anchor_cycles += instruction_cycles
            drift = (clock - anchor_edge) - anchor_cycles * MACHINE_CYCLE_EDGES
            assert abs(drift) < MACHINE_CYCLE_EDGES, (f'retirement {retirements}: edges since anchor '
                                                      f'{clock - anchor_edge} expected about {anchor_cycles * MACHINE_CYCLE_EDGES:.1f}')
        else:
            anchor_edge, anchor_cycles = clock, 0
        halted = bool(record['halted'])
    run_clocks = clock - release_clock
    recorder.cancel()
    while host.clock < clock + 4:
        await RisingEdge(dut.clk_i)
    await ReadOnly()
    assert int(dut.halted_o.value) == 1, 'CPU must report HALT after the halting retirement'
    assert banks_executed == set(range(BANKS)), ('code executes from bank 0 and every switched bank', sorted(banks_executed))

    wram_observed, wram_expected = defined_signature(store_bytes(system.u_memory.u_wram.u_ram.memory), memory, 'wram')
    compare_signature(wram_observed, wram_expected, 'WRAM')
    wram = store_bytes(system.u_memory.u_wram.u_ram.memory)
    # Why: the mode-1 low window selects BANK2 << 5 = 32, which the 8-bank mask
    # reduces to bank 0: both low-window reads see bank 0's unused $FF bytes.
    pinned = {0x00: 0x53, 0x01: 0xFF, 0x02: 0xFF, 0x03: 0x60, 0x04: 0xFF, 0x13: 0x01, 0x14: BANKS - 1}
    for bank in range(1, BANKS):
        pinned[0x05 + 2 * (bank - 1)] = bank
        pinned[0x06 + 2 * (bank - 1)] = bank_pattern(bank, 0x3F80)
    for offset, value in sorted(pinned.items()):
        assert wram[offset] == value, f'pinned WRAM[{offset:02x}] observed {wram[offset]:02x} expected {value:02x}'
    assert len(wram_observed) >= 0x15

    response = await host.exchange(encode_request(CMD_HOLD))
    assert response.ok and response.held
    ram_pairs = cartridge.defined_ram_bytes()
    assert [offset for offset, _ in ram_pairs] == [0x0000, 0x1FFF, 0x2000, 0x3FFF, 0x4000, 0x5FFF, 0x6000, 0x7FFF], ram_pairs
    assert [value for _, value in ram_pairs] == [0x50, 0x60, 0x51, 0x61, 0x52, 0x62, 0x53, 0x63]
    observed = []
    for offset, _ in ram_pairs:
        response = await host.exchange(encode_request(CMD_READ, address_field(offset, ram=True)))
        assert response.ok and response.ram and response.offset == offset
        observed.append(response.payload[0])
    compare_signature(bytes(observed), bytes(value for _, value in ram_pairs), 'CART-RAM')
    response = await host.exchange(encode_request(CMD_READ, address_field(0x6000, ram=True), length=256))
    assert response.ok and response.payload[0] == 0x53 and len(response.payload) == 256
    response = await host.exchange(encode_request(CMD_READ, address_field(0x7F00, ram=True), length=256))
    assert response.ok and response.payload[-1] == 0x63
    assert not response.framing and not response.timeout and not response.overrun, 'no line event during the run'

    # Why: proves the check can fail: the signature comparison must reject a single flipped byte.
    corrupted = bytearray(wram_expected)
    corrupted[0] ^= 0x01
    try:
        compare_signature(wram_observed, bytes(corrupted), 'WRAM-negative')
    except AssertionError:
        pass
    else:
        raise AssertionError('signature comparison accepted a corrupted expectation')
    return retirements, run_clocks, banks_executed, len(wram_observed)
