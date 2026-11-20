"""Directed checks of the on-chip cartridge backing on its public ports: the
load port (ROM writes every edge, ROM and RAM readback, the RAM-store sharing
rule), ROM reads, cartridge RAM writes and reads, address truncation to the
parameterized sizes, the one-edge responses, reset behavior, and the
rejection of a ROM write through the backing port (expected-fatal testcase)."""
import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge

CLOCK_PERIOD_NS = 40
ROM_BYTES, RAM_BYTES = 65536, 16384   # the registry's parameters for this target


def request_word(valid, write, ram, address, data):
    return (valid << 31) | (write << 30) | (ram << 29) | (address << 8) | data


def response_fields(word):
    return (word >> 8) & 1, word & 0xFF


class Bench:
    def __init__(self, dut, seed):
        self.dut = dut
        self.rng = random.Random(seed)
        self.bound = int(os.environ['DV_MAX_CYCLES'])
        self.cycles = 0
        self.rom = {}
        self.ram = {}

    def idle(self):
        d = self.dut
        d.request_i.value = 0
        d.load_request_i.value = 0

    def power_up(self):
        """Stores have no power-up value: seeded nonzero garbage everywhere."""
        for store in (self.dut.u_rom.memory, self.dut.u_ram.memory):
            for index in range(len(store)):
                store[index].value = self.rng.randrange(1, 256)

    async def edge(self):
        self.cycles += 1
        assert self.cycles <= self.bound, 'finite simulation bound exceeded'
        await RisingEdge(self.dut.clk_i)

    async def reset(self, cycles=2):
        d = self.dut
        await FallingEdge(d.clk_i)
        d.reset_i.value = 1
        d.request_i.value = 0
        for _ in range(cycles):
            await self.edge()
        await FallingEdge(d.clk_i)
        d.reset_i.value = 0

    async def load(self, address, byte):
        """One ROM write through the load port: accepted on its edge (the
        port is ready for ROM every edge) and completed one edge later."""
        d = self.dut
        await FallingEdge(d.clk_i)
        d.load_request_i.value = request_word(1, 1, 0, address, byte)
        await ReadOnly()
        assert int(d.load_ready_o.value) == 1, 'the load port accepts a ROM write every edge'
        await self.edge()
        await FallingEdge(d.clk_i)
        d.load_request_i.value = 0
        await ReadOnly()
        assert int(d.reset_i.value) or response_fields(int(d.load_response_o.value))[0], (
            'write completion one edge after acceptance (reset only drops the response)')
        self.rom[address % ROM_BYTES] = byte

    async def load_transact(self, address, ram=False, write=False, data=0):
        """One load-port request held until accepted; returns (read_data,
        edges from presentation to acceptance) after exactly one response."""
        d = self.dut
        await FallingEdge(d.clk_i)
        d.load_request_i.value = request_word(1, int(write), int(ram), address, data)
        waited = 0
        while True:
            await ReadOnly()
            if int(d.load_ready_o.value):
                break
            waited += 1
            assert waited < 4, 'the on-chip load port stalls at most one edge'
            await self.edge()
            await FallingEdge(d.clk_i)
        await self.edge()
        await FallingEdge(d.clk_i)
        d.load_request_i.value = 0
        await ReadOnly()
        valid, read_data = response_fields(int(d.load_response_o.value))
        assert valid, f'load response must follow one edge after acceptance ({address:06x})'
        await self.edge()
        await FallingEdge(d.clk_i)
        await ReadOnly()
        assert not response_fields(int(d.load_response_o.value))[0], 'exactly one load response per request'
        await self.edge()
        if write and ram:
            self.ram[address % RAM_BYTES] = data
        elif write:
            self.rom[address % ROM_BYTES] = data
        return read_data, waited

    async def transact(self, address, ram=False, write=False, data=0):
        """One backing request; returns (read_data, latency) and checks that
        ready is high, exactly one response arrives, and nothing follows."""
        d = self.dut
        await FallingEdge(d.clk_i)
        d.request_i.value = request_word(1, int(write), int(ram), address, data)
        await ReadOnly()
        assert int(d.ready_o.value) == 1, 'the on-chip backing is always ready'
        valid, _ = response_fields(int(d.response_o.value))
        assert not valid, 'no response before acceptance'
        await self.edge()
        await FallingEdge(d.clk_i)
        d.request_i.value = 0
        await ReadOnly()
        valid, read_data = response_fields(int(d.response_o.value))
        assert valid, f'response must follow one edge after acceptance ({address:06x})'
        await self.edge()
        await FallingEdge(d.clk_i)
        await ReadOnly()
        valid, _ = response_fields(int(d.response_o.value))
        assert not valid, 'exactly one response per request'
        await self.edge()
        if write:
            self.ram[address % RAM_BYTES] = data
        return read_data, 1

    async def read_rom(self, address):
        data, _ = await self.transact(address)
        expected = self.rom[address % ROM_BYTES]
        assert data == expected, f'ROM {address:06x} observed {data:02x} expected {expected:02x}'
        return data

    async def read_ram(self, address):
        data, _ = await self.transact(address, ram=True)
        expected = self.ram[address % RAM_BYTES]
        assert data == expected, f'RAM {address:06x} observed {data:02x} expected {expected:02x}'
        return data

    async def write_ram(self, address, data):
        await self.transact(address, ram=True, write=True, data=data)


@cocotb.test(timeout_time=20, timeout_unit='ms')
async def backing_contract(dut):
    seed = int(os.environ['DV_SEED'])
    assert int(os.environ['DV_CLOCK_HZ']) == 25_000_000
    cocotb.log.info('nand2mario cartridge backing seed=%d (directed)', seed)
    b = Bench(dut, seed)
    b.idle()
    b.power_up()
    cocotb.start_soon(Clock(dut.clk_i, CLOCK_PERIOD_NS, unit='ns').start())
    await b.reset()

    # Load: a seeded sparse set of ROM bytes including both ends, the first
    # ones while reset is held (the load port ignores reset).
    addresses = sorted({0, 1, 0x147, 0x3FFF, 0x4000, 0x7FFF, 0x8000, ROM_BYTES - 1}
                       | {b.rng.randrange(ROM_BYTES) for _ in range(400)})
    await FallingEdge(dut.clk_i)
    dut.reset_i.value = 1
    for address in addresses[:8]:
        await b.load(address, b.rng.randrange(256))
    await FallingEdge(dut.clk_i)
    dut.reset_i.value = 0
    for address in addresses[8:]:
        await b.load(address, b.rng.randrange(256))
    for address in addresses:
        await b.read_rom(address)
    # Address bits above the store size are ignored (the mapper may present a
    # 2 MiB address to a smaller backing).
    for address in (0, 0x7FFF, ROM_BYTES - 1):
        for alias in (ROM_BYTES, 3 * ROM_BYTES, (1 << 21) - ROM_BYTES):
            assert await b.read_rom(alias + address) == b.rom[address]

    # A load write and a read of the same byte on one edge: the read returns
    # the old byte (store port B is read-first against port A).
    target, old, new = addresses[5], b.rom[addresses[5]], b.rom[addresses[5]] ^ 0x5A
    await FallingEdge(dut.clk_i)
    dut.load_request_i.value = request_word(1, 1, 0, target, new)
    dut.request_i.value = request_word(1, 0, 0, target, 0)
    await b.edge()
    await FallingEdge(dut.clk_i)
    dut.load_request_i.value = 0
    dut.request_i.value = 0
    await ReadOnly()
    valid, data = response_fields(int(dut.response_o.value))
    assert (valid, data) == (1, old), ('read-first collision', valid, data, old)
    await b.edge()
    b.rom[target] = new
    assert await b.read_rom(target) == new

    # Cartridge RAM: writes answered one edge later, read back at both ends
    # and at random offsets; the ROM is untouched; address bits above the RAM
    # size are ignored.
    ram_addresses = sorted({0, 1, RAM_BYTES - 1, 0x1FFF, 0x2000} | {b.rng.randrange(RAM_BYTES) for _ in range(64)})
    for address in ram_addresses:
        await b.write_ram(address, (address * 13 + 7) & 0xFF)
    for address in ram_addresses:
        await b.read_ram(address)
    await b.write_ram(RAM_BYTES + 5, 0xA5)
    assert await b.read_ram(5) == 0xA5
    await b.write_ram(5, 0x3C)
    assert await b.read_ram((1 << 15) - RAM_BYTES + 5) == 0x3C
    for address in addresses[:16]:
        await b.read_rom(address)
    # Back-to-back write then read of one byte observes the new value.
    await b.write_ram(0x123, 0x11)
    await b.write_ram(0x123, 0x22)
    assert await b.read_ram(0x123) == 0x22

    # Reset: a pending response is dropped; both stores keep their contents;
    # a load during reset lands.
    await FallingEdge(dut.clk_i)
    dut.request_i.value = request_word(1, 0, 0, addresses[3], 0)
    await b.edge()
    await FallingEdge(dut.clk_i)
    dut.request_i.value = 0
    dut.reset_i.value = 1
    await b.edge()
    await ReadOnly()
    assert not response_fields(int(dut.response_o.value))[0], 'reset drops the response'
    await b.load(addresses[7], 0x99)
    await FallingEdge(dut.clk_i)
    dut.reset_i.value = 0
    assert await b.read_rom(addresses[7]) == 0x99
    for address in addresses[:4] + ram_addresses[:0]:
        await b.read_rom(address)
    for address in ram_addresses[:8]:
        await b.read_ram(address)

    # Load port readback and RAM access: ROM and RAM bytes read back through
    # the load port with the one-edge response, a RAM write through the load
    # port is visible to the mapper, and the port is initialized from the
    # first edge.
    assert int(dut.initialized_o.value) == 1, 'the on-chip backing needs no initialization'
    for address in addresses[:12] + [ROM_BYTES - 1]:
        data, waited = await b.load_transact(address)
        assert (data, waited) == (b.rom[address % ROM_BYTES], 0), ('load-port ROM readback', address, data)
    for address in ram_addresses[:6]:
        data, waited = await b.load_transact(address, ram=True)
        assert (data, waited) == (b.ram[address % RAM_BYTES], 0), ('load-port RAM readback', address, data)
    await b.load_transact(0x1234, ram=True, write=True, data=0x77)
    assert await b.read_ram(0x1234) == 0x77
    await b.load_transact(ROM_BYTES - 2, write=True, data=0x66)
    assert await b.read_rom(ROM_BYTES - 2) == 0x66
    # Sharing rule: a load RAM request on an edge where the mapper accesses
    # RAM is not ready and is accepted on the next edge; a load ROM request
    # on such an edge is accepted at once.
    await b.write_ram(0x0200, 0x9C)
    await FallingEdge(dut.clk_i)
    dut.request_i.value = request_word(1, 1, 1, 0x0100, 0x12)
    dut.load_request_i.value = request_word(1, 0, 1, 0x0200, 0)
    await ReadOnly()
    assert int(dut.load_ready_o.value) == 0, 'load RAM access yields to the mapper RAM access'
    await b.edge()
    await FallingEdge(dut.clk_i)
    dut.request_i.value = 0
    await ReadOnly()
    assert int(dut.load_ready_o.value) == 1
    valid, _ = response_fields(int(dut.response_o.value))
    assert valid, 'the mapper write completed'
    assert not response_fields(int(dut.load_response_o.value))[0], 'the load request was not accepted yet'
    await b.edge()
    await FallingEdge(dut.clk_i)
    dut.load_request_i.value = 0
    await ReadOnly()
    valid, data = response_fields(int(dut.load_response_o.value))
    assert (valid, data) == (1, b.ram[0x0200]), ('deferred load RAM read', valid, data)
    await b.edge()
    b.ram[0x0100] = 0x12
    assert await b.read_ram(0x0100) == 0x12
    await FallingEdge(dut.clk_i)
    dut.request_i.value = request_word(1, 1, 1, 0x0101, 0x34)
    dut.load_request_i.value = request_word(1, 0, 0, addresses[2], 0)
    await ReadOnly()
    assert int(dut.load_ready_o.value) == 1, 'a load ROM access is independent of the RAM store'
    await b.edge()
    await FallingEdge(dut.clk_i)
    dut.request_i.value = 0
    dut.load_request_i.value = 0
    await ReadOnly()
    assert response_fields(int(dut.load_response_o.value)) == (1, b.rom[addresses[2]])
    await b.edge()
    b.ram[0x0101] = 0x34
    # Reset drops a pending load response as well.
    await FallingEdge(dut.clk_i)
    dut.load_request_i.value = request_word(1, 0, 0, addresses[3], 0)
    await b.edge()
    await FallingEdge(dut.clk_i)
    dut.load_request_i.value = 0
    dut.reset_i.value = 1
    await b.edge()
    await ReadOnly()
    assert not response_fields(int(dut.load_response_o.value))[0], 'reset drops the load response'
    await FallingEdge(dut.clk_i)
    dut.reset_i.value = 0
    assert await b.read_rom(addresses[3]) == b.rom[addresses[3]]
    cocotb.log.info('cartridge backing checks passed seed=%d cycles=%d', seed, b.cycles)


@cocotb.test(timeout_time=2, timeout_unit='ms')
async def rom_write_is_rejected(dut):
    """Expected failure: a ROM write through the backing port trips the
    `rom_write_rejected` assertion. Acceptance is the named fatal."""
    seed = int(os.environ['DV_SEED'])
    cocotb.log.info('nand2mario cartridge backing negative control seed=%d', seed)
    b = Bench(dut, seed)
    b.idle()
    cocotb.start_soon(Clock(dut.clk_i, CLOCK_PERIOD_NS, unit='ns').start())
    await b.reset()
    await FallingEdge(dut.clk_i)
    dut.request_i.value = request_word(1, 1, 0, 0x1234, 0x56)
    for _ in range(4):
        await b.edge()
    raise AssertionError('the ROM write through the backing port was not rejected by the RTL assertion')
