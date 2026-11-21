"""nand2mario SDRAM cartridge backing on its two public ports over the
controller and the owned device model: the initialization stall, ROM and RAM
placement and byte lanes, exact response latencies, the mapper-first
arbitration between the backing port and the load port, randomized two-port
traffic against a byte shadow, and the rejection of a ROM write through the
backing port (expected-fatal testcase)."""
import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge

from .test_sdram_ctrl import IDLE_CLOCK, READ_RESPONSE, REFRESH_CLOCKS, WRITE_IDLE, WRITE_RESPONSE

CLOCK_PERIOD_NS = 40
ROM_BYTES, RAM_BYTES = 1 << 21, 1 << 15
RAM_DEVICE_BASE = 1 << 21        # Why: cartridge RAM byte 0 sits right above the 2 MiB ROM in the device.


def request_word(valid, write, ram, address, data):
    return (valid << 31) | (write << 30) | (ram << 29) | (address << 8) | data


def response_fields(word):
    return (word >> 8) & 1, word & 0xFF


def device_word_index(byte_address):
    """Index into the device model's x16 storage: {bank, row, column}."""
    return ((byte_address >> 24) & 3) << 23 | ((byte_address >> 11) & 0x1FFF) << 10 | (byte_address >> 1) & 0x3FF


class Bench:
    def __init__(self, dut, seed):
        self.dut = dut
        self.rng = random.Random(seed)
        self.bound = int(os.environ['DV_MAX_CYCLES'])
        self.clock = -1
        self.rom, self.ram = {}, {}
        self.stalls = []

    def idle(self):
        self.dut.request_i.value = 0
        self.dut.load_request_i.value = 0

    async def watch(self):
        while True:
            await RisingEdge(self.dut.clk_i)
            self.clock += 1
            assert self.clock <= self.bound, 'finite simulation bound exceeded'

    def port(self, load):
        d = self.dut
        return ((d.load_request_i, d.load_ready_o, d.load_response_o) if load else
                (d.request_i, d.ready_o, d.response_o))

    async def transact(self, load, address, ram=False, write=False, data=0, limit=None):
        """One request on the backing port (or the load port); returns the
        read byte, the presentation-to-acceptance delay and the response
        latency from acceptance, after checking exactly one response."""
        request, ready, response = self.port(load)
        limit = limit if limit is not None else REFRESH_CLOCKS + WRITE_IDLE
        await FallingEdge(self.dut.clk_i)
        request.value = request_word(1, int(write), int(ram), address, data)
        waited = 0
        while True:
            await ReadOnly()
            if int(ready.value):
                break
            waited += 1
            assert waited <= limit, f'request {address:#08x} never accepted'
            await RisingEdge(self.dut.clk_i)
        await RisingEdge(self.dut.clk_i)
        await ReadOnly()
        accepted = self.clock - 1
        await FallingEdge(self.dut.clk_i)
        request.value = 0
        latency = None
        for _ in range(WRITE_RESPONSE + 2):
            await RisingEdge(self.dut.clk_i)
            await ReadOnly()
            valid, byte = response_fields(int(response.value))
            if valid:
                latency = self.clock - accepted
                break
        assert latency is not None, 'no response'
        await RisingEdge(self.dut.clk_i)
        await ReadOnly()
        assert not response_fields(int(response.value))[0], 'exactly one response per request'
        self.stalls.append(waited)
        if write:
            (self.ram if ram else self.rom)[address % (RAM_BYTES if ram else ROM_BYTES)] = data
        return byte, waited, latency

    async def write(self, address, data, ram=False, load=True):
        _, _, latency = await self.transact(load, address, ram, True, data)
        assert latency == WRITE_RESPONSE, ('write latency', address, latency)

    async def read(self, address, ram=False, load=False):
        byte, _, latency = await self.transact(load, address, ram)
        expected = (self.ram if ram else self.rom)[address % (RAM_BYTES if ram else ROM_BYTES)]
        assert latency == READ_RESPONSE, ('read latency', address, latency)
        assert byte == expected, f'{"RAM" if ram else "ROM"} {address:#08x} observed {byte:02x} expected {expected:02x}'
        return byte

    def device_byte(self, device_address):
        word = int(self.dut.u_sdram.memory[device_word_index(device_address)].value)
        return (word >> (8 * (device_address & 1))) & 0xFF


async def start(dut, seed):
    b = Bench(dut, seed)
    b.idle()
    dut.reset_i.value = 1
    cocotb.start_soon(Clock(dut.clk_i, CLOCK_PERIOD_NS, unit='ns').start())
    for _ in range(3):
        await RisingEdge(dut.clk_i)
    await FallingEdge(dut.clk_i)
    dut.reset_i.value = 0
    cocotb.start_soon(b.watch())
    return b


@cocotb.test(timeout_time=60, timeout_unit='ms')
async def backing_contract(dut):
    seed = int(os.environ['DV_SEED'])
    assert int(os.environ['DV_CLOCK_HZ']) == 25_000_000
    cocotb.log.info('nand2mario SDRAM backing seed=%d', seed)
    b = await start(dut, seed)

    initialized_clock = None
    await FallingEdge(dut.clk_i)
    dut.load_request_i.value = request_word(1, 1, 0, 0x147, 0x03)
    while True:
        await ReadOnly()
        if initialized_clock is None and int(dut.initialized_o.value):
            initialized_clock = b.clock
        if int(dut.load_ready_o.value):
            break
        assert not int(dut.ready_o.value), 'the backing port is not ready before initialization'
        await RisingEdge(dut.clk_i)
    assert (initialized_clock, b.clock) == (IDLE_CLOCK - 2, IDLE_CLOCK), (initialized_clock, b.clock)
    await RisingEdge(dut.clk_i)
    await FallingEdge(dut.clk_i)
    dut.load_request_i.value = 0
    for _ in range(WRITE_RESPONSE + 1):
        await RisingEdge(dut.clk_i)
    b.rom[0x147] = 0x03
    assert await b.read(0x147) == 0x03

    cocotb.log.info('section: ROM through the load port clock=%d', b.clock)
    addresses = sorted({0, 1, 2, 3, 0x148, 0x149, 0x3FFF, 0x4000, 0x7FFF, ROM_BYTES - 2, ROM_BYTES - 1}
                       | {b.rng.randrange(ROM_BYTES) for _ in range(48)})
    for address in addresses:
        await b.write(address, b.rng.randrange(256))
    for address in addresses:
        await b.read(address)
        assert await b.read(address, load=True) == b.rom[address]
        assert b.device_byte(address) == b.rom[address], 'ROM byte at device address = ROM offset'
    cocotb.log.info('section: RAM section clock=%d', b.clock)
    ram_addresses = sorted({0, 1, 0x147, 0x1FFF, 0x2000, RAM_BYTES - 2, RAM_BYTES - 1}
                           | {b.rng.randrange(RAM_BYTES) for _ in range(24)})
    for address in ram_addresses:
        await b.write(address, (address * 7 + 3) & 0xFF, ram=True, load=False)
    for address in ram_addresses:
        await b.read(address, ram=True)
        assert await b.read(address, ram=True, load=True) == b.ram[address]
        assert b.device_byte(RAM_DEVICE_BASE + address) == b.ram[address]
    for address in (0, 1, 0x147):
        await b.read(address)
    await b.write(0x2345, 0x5A, ram=True)
    assert await b.read(0x2345, ram=True) == 0x5A
    # Why: address bits above a store wrap (the mapper may present a 21-bit RAM
    # address; the backing uses bits [14:0]).
    assert await b.read(RAM_BYTES + 0x2345, ram=True) == 0x5A

    cocotb.log.info('section: arbitration clock=%d', b.clock)
    await b.write(0x1000, 0x11)
    await b.write(0x1001, 0x22)
    await FallingEdge(dut.clk_i)
    dut.request_i.value = request_word(1, 0, 0, 0x1000, 0)
    dut.load_request_i.value = request_word(1, 0, 0, 0x1001, 0)
    await ReadOnly()
    while not int(dut.ready_o.value):           # Why: a refresh may be in progress.
        await RisingEdge(dut.clk_i)
        await ReadOnly()
    assert int(dut.load_ready_o.value) == 0, 'the mapper wins the shared edge'
    await RisingEdge(dut.clk_i)
    await ReadOnly()
    accepted = b.clock - 1
    await FallingEdge(dut.clk_i)
    dut.request_i.value = 0
    mapper_response = loader_accept = loader_response = None
    while loader_response is None:
        await RisingEdge(dut.clk_i)
        await ReadOnly()
        assert b.clock - accepted < 40, 'arbitration did not complete'
        if int(dut.load_ready_o.value) and loader_accept is None:
            loader_accept = b.clock
        valid, byte = response_fields(int(dut.response_o.value))
        if valid:
            assert mapper_response is None
            mapper_response = (b.clock - accepted, byte)
        valid, byte = response_fields(int(dut.load_response_o.value))
        if valid:
            loader_response = (b.clock - loader_accept, byte)
        if loader_accept is not None and b.clock == loader_accept + 1:
            await FallingEdge(dut.clk_i)
            dut.load_request_i.value = 0
    assert mapper_response == (READ_RESPONSE, 0x11), mapper_response
    assert loader_accept - accepted in (READ_RESPONSE + 1, READ_RESPONSE + 1 + REFRESH_CLOCKS), (loader_accept, accepted)
    assert loader_response == (READ_RESPONSE, 0x22), loader_response
    await RisingEdge(dut.clk_i)              # Why: the response is sampled before the next request.
    cocotb.log.info('section: mapper waits for loader clock=%d', b.clock)
    # Why: a mapper request presented while a load transaction is in flight waits
    # for it (ready is a function of state, not of the live request).
    await FallingEdge(dut.clk_i)
    dut.load_request_i.value = request_word(1, 1, 0, 0x1002, 0x33)
    await ReadOnly()
    while not int(dut.load_ready_o.value):
        await RisingEdge(dut.clk_i)
        await ReadOnly()
    await RisingEdge(dut.clk_i)
    await ReadOnly()
    load_accepted = b.clock - 1
    await FallingEdge(dut.clk_i)
    dut.load_request_i.value = 0
    dut.request_i.value = request_word(1, 0, 0, 0x1002, 0)
    await ReadOnly()
    while not int(dut.ready_o.value):
        await RisingEdge(dut.clk_i)
        await ReadOnly()
    assert b.clock - load_accepted in (WRITE_IDLE, WRITE_IDLE + REFRESH_CLOCKS), (b.clock, load_accepted)
    await RisingEdge(dut.clk_i)
    await FallingEdge(dut.clk_i)
    dut.request_i.value = 0
    b.rom[0x1002] = 0x33
    valid = 0
    while not valid:
        await RisingEdge(dut.clk_i)
        await ReadOnly()
        valid, byte = response_fields(int(dut.response_o.value))
    assert byte == 0x33, 'the mapper read the byte the load port wrote just before'
    await RisingEdge(dut.clk_i)

    cocotb.log.info('section: random phase clock=%d', b.clock)
    region = b.rng.randrange(0, ROM_BYTES - 0x2000, 0x100)
    counts = {'rom_load_write': 0, 'rom_mapper_read': 0, 'rom_load_read': 0, 'ram_mapper_write': 0,
              'ram_mapper_read': 0, 'ram_load_read': 0, 'ram_load_write': 0}
    stalls_before = len(b.stalls)
    for _ in range(300):
        kind = b.rng.choice(list(counts))
        if kind.startswith('rom'):
            address = b.rng.choice((region + b.rng.randrange(0x2000), b.rng.choice(addresses)))
        else:
            address = b.rng.choice((b.rng.randrange(RAM_BYTES), b.rng.choice(ram_addresses)))
        if kind == 'rom_load_write':
            await b.write(address, b.rng.randrange(256))
        elif kind == 'rom_mapper_read':
            if address not in b.rom:
                continue
            await b.read(address)
        elif kind == 'rom_load_read':
            if address not in b.rom:
                continue
            await b.read(address, load=True)
        elif kind == 'ram_mapper_write':
            await b.write(address, b.rng.randrange(256), ram=True, load=False)
        elif kind == 'ram_load_write':
            await b.write(address, b.rng.randrange(256), ram=True)
        elif kind == 'ram_mapper_read':
            if address not in b.ram:
                continue
            await b.read(address, ram=True)
        else:
            if address not in b.ram:
                continue
            await b.read(address, ram=True, load=True)
        counts[kind] += 1
    stalls = b.stalls[stalls_before:]
    assert min(counts.values()) >= 15, counts
    assert set(stalls) <= {0, *range(1, REFRESH_CLOCKS + 1)} and sum(1 for s in stalls if s) >= 5, stalls
    lanes = {a & 1 for a in b.rom} | {2 + (a & 1) for a in b.ram}
    assert lanes == {0, 1, 2, 3}, 'both lanes of both regions written'
    await FallingEdge(dut.clk_i)
    b.idle()
    for _ in range(32):
        await RisingEdge(dut.clk_i)
        await ReadOnly()
        assert not response_fields(int(dut.response_o.value))[0]
        assert not response_fields(int(dut.load_response_o.value))[0]
    cocotb.log.info('SDRAM backing checks passed seed=%d clocks=%d counts=%s', seed, b.clock, counts)


@cocotb.test(timeout_time=2, timeout_unit='ms')
async def rom_write_is_rejected(dut):
    """Expected failure: a ROM write through the backing port trips the
    `rom_write_rejected` assertion. Acceptance is the named fatal."""
    seed = int(os.environ['DV_SEED'])
    cocotb.log.info('nand2mario SDRAM backing negative control seed=%d', seed)
    b = await start(dut, seed)
    while not int(dut.initialized_o.value):
        await RisingEdge(dut.clk_i)
        assert b.clock < IDLE_CLOCK + 10
    await FallingEdge(dut.clk_i)
    dut.request_i.value = request_word(1, 1, 0, 0x1234, 0x56)
    for _ in range(4):
        await RisingEdge(dut.clk_i)
    raise AssertionError('the ROM write through the backing port was not rejected by the RTL assertion')
