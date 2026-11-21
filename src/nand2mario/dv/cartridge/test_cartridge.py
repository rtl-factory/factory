"""Focused checks of the cartridge mapper on its public ports against the
independent Python MBC1 model. The bench plays the memory responder on the
request side and the backing store on the backing side, with randomized ready
and response latency, and holds the physical ROM/RAM contents itself. Every
backing request is compared with the model's translation; every CPU-side
response is compared with the model's byte-level behavior."""
import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge

from . import mbc1_model as m

CLOCK_PERIOD_NS = 40
DATA = 1
BACKING_VALID, BACKING_WRITE, BACKING_RAM = 31, 30, 29


def request_word(valid, write, kind, address, data):
    return (valid << 27) | (write << 26) | (kind << 24) | (address << 8) | data


def response_fields(word):
    return (word >> 9) & 1, (word >> 8) & 1, word & 0xFF


def backing_fields(word):
    """(valid, write, ram, address, write_data) of a backing_request_t."""
    return ((word >> BACKING_VALID) & 1, (word >> BACKING_WRITE) & 1, (word >> BACKING_RAM) & 1,
            (word >> 8) & 0x1FFFFF, word & 0xFF)


class Coverage:
    """Phase-local counters; a randomized phase asserts its own coverage."""

    def __init__(self):
        self.rom_banks = set()
        self.low_window_remaps = 0
        self.ram_enabled = 0
        self.ram_local = 0
        self.ram_banks = set()
        self.forwarded_latencies = []
        self.register_writes = 0


class Bench:
    def __init__(self, dut, seed):
        self.dut = dut
        self.rng = random.Random(seed)
        self.bound = int(os.environ['DV_MAX_CYCLES'])
        self.cycles = 0
        self.model = m.Cartridge()
        self.coverage = Coverage()
        self.ready_delays = (0, 0, 0, 1, 2, 3)
        self.response_delays = (1, 1, 2, 3, 4)
        self.transactions = 0

    def idle(self):
        d = self.dut
        d.request_i.value = 0
        d.backing_ready_i.value = 0
        d.backing_response_i.value = 0
        d.load_write_i.value = 0
        d.load_address_i.value = 0
        d.load_data_i.value = 0

    def power_up(self):
        """The header registers have no power-up value: give them seeded
        garbage through the simulator so no check can rely on zeros."""
        for register in (self.dut.header_type_q, self.dut.header_rom_size_q, self.dut.header_ram_size_q):
            register.value = self.rng.randrange(1, 256)

    async def edge(self):
        self.cycles += 1
        assert self.cycles <= self.bound, 'finite simulation bound exceeded'
        await RisingEdge(self.dut.clk_i)

    async def reset(self, cycles=2):
        d = self.dut
        await FallingEdge(d.clk_i)
        d.reset_i.value = 1
        d.request_i.value = 0
        d.backing_ready_i.value = 0
        d.backing_response_i.value = 0
        for _ in range(cycles):
            await self.edge()
        await FallingEdge(d.clk_i)
        d.reset_i.value = 0
        self.model.reset()

    async def load(self, address, byte):
        """One load-port write (the backing receives it elsewhere; here only
        the mapper's header observation matters)."""
        d = self.dut
        await FallingEdge(d.clk_i)
        d.load_write_i.value = 1
        d.load_address_i.value = address
        d.load_data_i.value = byte
        await self.edge()
        await FallingEdge(d.clk_i)
        d.load_write_i.value = 0
        self.model.load(address, byte)

    async def load_header(self, cartridge_type, rom_size, ram_size):
        for address, byte in ((m.HEADER_TYPE, cartridge_type), (m.HEADER_ROM_SIZE, rom_size),
                              (m.HEADER_RAM_SIZE, ram_size)):
            await self.load(address, byte)

    def serve(self, store, physical, write, data):
        """The bench's backing: returns the byte a read must deliver (defining
        a random one for a never-defined physical byte) and applies writes."""
        known, image = (self.model.rom_known, self.model.rom) if store == 'rom' else (self.model.ram_known, self.model.ram)
        if write:
            return 0
        if not known[physical]:
            image[physical] = self.rng.randrange(256)
            known[physical] = 1
        return image[physical]

    async def transact(self, address, write=False, data=0, ready_delay=None, response_delay=None, hold_after=None):
        """One request as the memory responder presents it: valid until the
        mapper samples it ready, then withdrawn while the response is awaited.
        Returns (read_data, latency); checks the backing request against the
        model's translation and the latency against the played delays."""
        d = self.dut
        store, physical = self.model.translate(address, write)
        local = store == 'local'
        if ready_delay is None:
            ready_delay = self.rng.choice(self.ready_delays)
        if response_delay is None:
            response_delay = self.rng.choice(self.response_delays)
        if hold_after is None:
            hold_after = self.rng.randrange(3)
        word = request_word(1, int(write), DATA, address, data)
        expected_latency = 1 if local else ready_delay + response_delay
        await FallingEdge(d.clk_i)
        d.request_i.value = word
        d.backing_ready_i.value = int(ready_delay == 0)
        edges, accepted, served, response_at = 0, False, None, None
        for _ in range(24):
            await ReadOnly()
            valid, fault, read_data = response_fields(int(d.response_o.value))
            ready = int(d.ready_o.value)
            b_valid, b_write, b_ram, b_address, b_data = backing_fields(int(d.backing_request_o.value))
            b_ready = int(d.backing_ready_i.value)
            if not accepted:
                assert int(d.request_i.value) == word
                if local:
                    assert not b_valid, f'local request {address:04x} must not reach the backing'
                    assert ready == 1, 'a local request is accepted immediately'
                else:
                    assert b_valid, f'forwarded request {address:04x} must reach the backing while held'
                    expected = (int(write), int(store == 'ram'), physical, data if write else b_data)
                    observed = (b_write, b_ram, b_address, b_data)
                    assert observed == expected, (f'backing request for {address:04x}: observed '
                                                  f'write={b_write} ram={b_ram} address={b_address:06x} data={b_data:02x} '
                                                  f'expected write={expected[0]} ram={expected[1]} address={physical:06x}')
                    assert ready == b_ready, 'ready passes straight through from the backing'
                if ready:
                    accepted = True
                    if not local:
                        served = self.serve(store, physical, write, data)
                        response_at = edges + 1 + response_delay
            else:
                assert not b_valid, 'no backing request while a response is outstanding'
            await self.edge()
            edges += 1
            if valid:
                break
            await FallingEdge(d.clk_i)
            if accepted:
                d.request_i.value = word & ~(1 << 27)   # Why: a requester withdraws valid once its request is accepted.
                d.backing_ready_i.value = 0
                if not local and edges + 1 == response_at:
                    d.backing_response_i.value = (1 << 8) | served
                else:
                    d.backing_response_i.value = 0
            else:
                ready_delay -= 1
                d.backing_ready_i.value = int(ready_delay <= 0)
        else:
            raise AssertionError(f'no response within 24 edges for {address:04x}')
        assert fault == 0, f'the cartridge never faults ({address:04x})'
        latency = edges - 1
        await FallingEdge(d.clk_i)
        d.request_i.value = 0
        d.backing_response_i.value = 0
        d.backing_ready_i.value = 0
        for _ in range(hold_after):
            await ReadOnly()
            spurious, _, _ = response_fields(int(d.response_o.value))
            assert not spurious, f'response without a request after {address:04x}'
            assert not (int(d.backing_request_o.value) >> BACKING_VALID) & 1, 'backing request without a request'
            await self.edge()
            await FallingEdge(d.clk_i)
        if write:
            self.model.write(address, data)
        else:
            expected = self.model.read(address)
            assert read_data == expected, (f'read {address:04x} ({store}) observed {read_data:02x} expected {expected:02x}')
        self.transactions += 1
        self.note_coverage(address, write, store, physical, local, expected_latency)
        return read_data, latency, expected_latency

    def note_coverage(self, address, write, store, physical, local, latency):
        c = self.coverage
        if not local:
            c.forwarded_latencies.append(latency)
        if store == 'rom':
            c.rom_banks.add(physical >> 14)
            if address < 0x4000 and physical >> 14:
                c.low_window_remaps += 1
        elif store == 'ram':
            c.ram_enabled += 1
            c.ram_banks.add(physical >> 13)
        elif address >= 0xA000:
            c.ram_local += 1
        elif write and self.model.mbc1:
            c.register_writes += 1

    async def read(self, address, **kw):
        data, latency, expected = await self.transact(address, **kw)
        assert latency == expected, f'latency {latency} expected {expected} at {address:04x}'
        return data

    async def write(self, address, data, **kw):
        _, latency, expected = await self.transact(address, write=True, data=data, **kw)
        assert latency == expected, f'latency {latency} expected {expected} at {address:04x}'

    async def expect_rom(self, address, bank):
        """Read `address` and require the model to have mapped it to `bank`."""
        store, physical = self.model.translate(address, False)
        assert (store, physical >> 14) == ('rom', bank), (f'model maps {address:04x} to {store} bank {physical >> 14}, '
                                                        f'test expects bank {bank}')
        return await self.read(address)

    async def expect_ram(self, address, bank):
        store, physical = self.model.translate(address, False)
        assert (store, physical >> 13) == ('ram', bank), (f'model maps {address:04x} to {store} bank {physical >> 13}, '
                                                        f'test expects RAM bank {bank}')
        return await self.read(address)

    async def expect_local(self, address, write=False, data=0):
        store, _ = self.model.translate(address, write)
        assert store == 'local', f'test expects {address:04x} to be answered locally, model says {store}'
        return await self.transact(address, write=write, data=data)


@cocotb.test(timeout_time=40, timeout_unit='ms')
async def mbc1_contract(dut):
    seed = int(os.environ['DV_SEED'])
    assert int(os.environ['DV_CLOCK_HZ']) == 25_000_000
    cocotb.log.info('nand2mario cartridge mapper seed=%d', seed)
    b = Bench(dut, seed)
    b.idle()
    b.power_up()
    cocotb.start_soon(Clock(dut.clk_i, CLOCK_PERIOD_NS, unit='ns').start())
    await b.reset()

    # Ref: Pan Docs "No MBC": ROM-only maps 32 KiB directly, ignores register writes and has no RAM.
    await b.load_header(m.ROM_ONLY, 0x00, 0x00)
    for address in (0x0000, 0x3FFF, 0x4000, 0x7FFF, 0x0150):
        assert b.model.translate(address, False) == ('rom', address)
        await b.read(address)
    for address, value in ((0x2000, 0x05), (0x4000, 0x03), (0x6000, 0x01), (0x0000, 0x0A), (0x7FFF, 0xFF)):
        await b.expect_local(address, write=True, data=value)
    for address in (0x0000, 0x3FFF, 0x4000, 0x7FFF):
        assert b.model.translate(address, False) == ('rom', address), 'ROM-only translation must stay identity'
        await b.read(address)
    for address in (0xA000, 0xB5A5, 0xBFFF):
        data, _, _ = await b.expect_local(address)
        assert data == m.OPEN_BUS
        await b.expect_local(address, write=True, data=0x5A)
    for address in (0x0146, 0x014A, 0x0100, 0x7FFF, 0x1FFFFF):
        await b.load(address, b.rng.randrange(256))
    await b.write(0x2000, 0x02)
    assert b.model.translate(0x4000, False) == ('rom', 0x4000)
    await b.read(0x4000)

    for cartridge_type in (0x05, 0x13, 0x1B, 0xFC):
        await b.load_header(cartridge_type, 0x05, 0x03)
        await b.expect_local(0x2000, write=True, data=0x03)
        await b.expect_local(0x0000, write=True, data=0x0A)
        assert b.model.translate(0x4000, False) == ('rom', 0x4000)
        await b.read(0x4000)
        data, _, _ = await b.expect_local(0xA000)
        assert data == m.OPEN_BUS

    # Ref: Pan Docs "MBC1": BANK1 resets to 1, stores five bits and maps zero to 1;
    # BANK2 (bits 5-6) reaches the high window, and the low window in mode 1;
    # only bit 0 of the mode register is stored.
    await b.load_header(m.MBC1, 0x06, 0x00)
    await b.reset()
    await b.expect_rom(0x4000, 1)
    await b.expect_rom(0x0000, 0)
    await b.write(0x2000, 0x00)
    await b.expect_rom(0x4000, 1)
    await b.write(0x2000, 0x1F)
    await b.expect_rom(0x7FFF, 31)
    await b.write(0x4000, 0x03)
    await b.expect_rom(0x7FFF, 127)
    await b.expect_rom(0x3FFF, 0)
    await b.write(0x6000, 0x01)
    await b.expect_rom(0x0000, 96)
    await b.expect_rom(0x3FFF, 96)
    await b.expect_rom(0x4000, 127)
    await b.write(0x4000, 0x01)
    await b.expect_rom(0x0000, 32)
    await b.write(0x2000, 0x60)
    await b.expect_rom(0x4000, 33)
    await b.write(0x6000, 0xFE)
    await b.expect_rom(0x0000, 0)
    await b.expect_rom(0x4000, 33)
    await b.write(0x0000, 0x0A)
    data, _, _ = await b.expect_local(0xA000)
    assert data == m.OPEN_BUS
    await b.expect_local(0xBFFF, write=True, data=0x11)

    # Ref: Pan Docs "MBC1": the bank number is masked to the ROM size after the
    # five-bit zero check, RAM enables on a low nibble of $A, and BANK2 picks the
    # RAM bank in mode 1 only.
    await b.load_header(m.MBC1_RAM_BATTERY, 0x03, 0x03)
    await b.reset()
    await b.write(0x2000, 0x10)
    await b.expect_rom(0x4000, 0)
    await b.write(0x2000, 0x12)
    await b.expect_rom(0x4000, 2)
    await b.write(0x4000, 0x03)
    await b.expect_rom(0x4000, 2)
    await b.write(0x6000, 0x01)
    await b.expect_rom(0x0000, 0)
    await b.write(0x6000, 0x00)
    data, _, _ = await b.expect_local(0xA000)
    assert data == m.OPEN_BUS
    await b.expect_local(0xA000, write=True, data=0x77)
    for value, enabled in ((0x0A, True), (0x0B, False), (0x1A, True), (0xA0, False), (0xFA, True), (0x00, False)):
        await b.write(0x0000, value)
        assert b.model.registers[m.RAMG] == enabled
        if enabled:
            await b.write(0xA000, 0x80 | value)
            assert await b.expect_ram(0xA000, 0) == 0x80 | value
        else:
            data, _, _ = await b.expect_local(0xA000)
            assert data == m.OPEN_BUS
    await b.write(0x0000, 0x0A)
    assert await b.expect_ram(0xA000, 0) == 0x80 | 0xFA, 'a disabled write is dropped'
    await b.write(0xBFFF, 0x3C)
    assert await b.expect_ram(0xBFFF, 0) == 0x3C
    await b.write(0x4000, 0x02)
    assert await b.expect_ram(0xA000, 0) == 0x80 | 0xFA
    await b.write(0x6000, 0x01)
    await b.write(0xA000, 0xC2)
    assert await b.expect_ram(0xA000, 2) == 0xC2
    await b.write(0x4000, 0x03)
    await b.write(0xA123, 0xC3)
    assert await b.expect_ram(0xA123, 3) == 0xC3
    await b.write(0x4000, 0x00)
    assert await b.expect_ram(0xA000, 0) == 0x80 | 0xFA
    await b.write(0x6000, 0x00)
    await b.write(0x4000, 0x02)
    assert await b.expect_ram(0xA000, 0) == 0x80 | 0xFA
    await b.write(0x6000, 0x01)
    assert await b.expect_ram(0xA000, 2) == 0xC2

    # Ref: Pan Docs "The Cartridge Header": RAM-bearing types with RAM size $00 (or the unused $01) have no RAM:
    # RAMG is accepted, an enabled read is local $FF and a write is dropped.
    for cartridge_type in (m.MBC1_RAM, m.MBC1_RAM_BATTERY):
        for ram_code in (0x00, 0x01):
            await b.load_header(cartridge_type, 0x06, ram_code)
            await b.reset()
            await b.write(0x0000, 0x0A)
            assert b.model.registers[m.RAMG] and not b.model.ram_present
            data, _, _ = await b.expect_local(0xA000)
            assert data == m.OPEN_BUS
            await b.expect_local(0xBFFF, write=True, data=0x5A)
            data, _, _ = await b.expect_local(0xBFFF)
            assert data == m.OPEN_BUS
            await b.write(0x6000, 0x01)
            await b.write(0x4000, 0x02)
            await b.expect_local(0xA000, write=True, data=0x5B)
            await b.expect_rom(0x0000, 64)
    await b.load_header(m.MBC1_RAM, 0x06, 0x02)
    await b.reset()
    await b.write(0x0000, 0x0A)
    await b.write(0xA000, 0x2C)
    assert await b.expect_ram(0xA000, 0) == 0x2C

    await b.load_header(m.MBC1_RAM_BATTERY, 0x06, 0x03)
    await b.reset()
    await b.write(0x2000, 0x02)
    await b.write(0x4000, 0x03)
    await b.write(0x6000, 0x01)
    await b.write(0x0000, 0x0A)
    await b.expect_rom(0x0000, 96)
    await b.expect_rom(0x4000, 98)
    await b.write(0xA000, 0x77)
    assert await b.expect_ram(0xA000, 3) == 0x77
    for cartridge_type in (m.ROM_ONLY, 0x13):
        await b.load_header(cartridge_type, 0x00, 0x00)
        assert b.model.registers[m.BANK1] == 2, 'the stale registers stay'
        for address in (0x0000, 0x3FFF, 0x4000, 0x4123, 0x7FFF):
            assert b.model.translate(address, False) == ('rom', address)
            await b.read(address)
        data, _, _ = await b.expect_local(0xA000)
        assert data == m.OPEN_BUS
        await b.expect_local(0x2000, write=True, data=0x05)
        await b.expect_local(0xA000, write=True, data=0x11)
    await b.load_header(m.MBC1_RAM_BATTERY, 0x06, 0x03)
    await b.expect_rom(0x0000, 96)
    await b.expect_rom(0x4000, 98)
    assert await b.expect_ram(0xA000, 3) == 0x77

    # Ref: Pan Docs "MBC1": 8 KiB RAM has one bank in either mode; 1 MiB ROM uses BANK2 bit 0.
    await b.load_header(m.MBC1_RAM, 0x05, 0x02)
    await b.reset()
    await b.write(0x0000, 0x0A)
    await b.write(0x6000, 0x01)
    await b.write(0x4000, 0x03)
    await b.write(0xA000, 0x8A)
    assert await b.expect_ram(0xA000, 0) == 0x8A
    await b.expect_rom(0x0000, 32)
    await b.expect_rom(0x4000, 33)
    await b.write(0x4000, 0x02)
    await b.expect_rom(0x0000, 0)
    await b.expect_rom(0x4000, 1)

    for code in (0x07, 0x08, 0x52, 0x54, 0xFF):
        await b.load_header(m.MBC1, code, 0x00)
        await b.reset()
        await b.write(0x2000, 0x1F)
        await b.write(0x4000, 0x03)
        await b.expect_rom(0x4000, 127)
    await b.load_header(m.MBC1, 0x00, 0x00)
    await b.reset()
    await b.write(0x2000, 0x1E)
    await b.expect_rom(0x4000, 0)
    await b.write(0x2000, 0x1F)
    await b.expect_rom(0x4000, 1)

    await b.load_header(m.MBC1_RAM_BATTERY, 0x05, 0x03)
    await b.reset()
    await b.write(0x0000, 0x0A)
    await b.write(0x6000, 0x01)
    await b.write(0x4000, 0x01)
    await b.write(0x2000, 0x05)
    await b.expect_rom(0x0000, 32)
    await b.expect_rom(0x4000, 37)
    await b.write(0xA000, 0x99)
    assert await b.expect_ram(0xA000, 1) == 0x99
    await b.reset()
    assert b.model.registers == {m.BANK1: 1, m.BANK2: 0, m.MODE: 0, m.RAMG: False}
    await b.expect_rom(0x0000, 0)
    await b.expect_rom(0x4000, 1)
    data, _, _ = await b.expect_local(0xA000)
    assert data == m.OPEN_BUS
    await b.write(0x0000, 0x0A)
    await b.write(0x6000, 0x01)
    await b.write(0x4000, 0x01)
    assert await b.expect_ram(0xA000, 1) == 0x99, 'RAM contents survive reset'
    await FallingEdge(dut.clk_i)
    dut.request_i.value = request_word(1, 0, DATA, 0x4000, 0)
    dut.backing_ready_i.value = 1
    await b.edge()
    await b.reset()
    await ReadOnly()
    assert not response_fields(int(dut.response_o.value))[0]
    await b.edge()

    await b.write(0x6000, 0x00)
    await b.write(0x2000, 0x07)
    data, latency, expected = await b.transact(0x4000, ready_delay=6, response_delay=6)
    assert latency == expected == 12, ('long stall', latency, expected)
    data, latency, expected = await b.transact(0x0000, ready_delay=0, response_delay=1)
    assert latency == expected == 1, ('no stall', latency, expected)
    await b.write(0x0000, 0x0A)
    _, latency, expected = await b.transact(0xA010, write=True, data=0x42, ready_delay=3, response_delay=2)
    assert latency == expected == 5, ('stalled write', latency, expected)
    data, latency, expected = await b.transact(0xA010, ready_delay=0, response_delay=4)
    assert (data, latency, expected) == (0x42, 4, 4), ('slow read', data, latency, expected)

    await b.load_header(m.MBC1_RAM_BATTERY, 0x06, 0x03)
    await b.reset()
    b.coverage = Coverage()
    for _ in range(500):
        roll = b.rng.random()
        if roll < 0.30:
            register = b.rng.choice((0x0000, 0x2000, 0x4000, 0x6000))
            address = register | b.rng.randrange(0x2000)
            if register == 0x0000:
                value = b.rng.choice((0x0A, 0x1A, 0xFA, 0x00, 0x0B, b.rng.randrange(256)))
            elif register == 0x6000:
                value = b.rng.randrange(2) if b.rng.random() < 0.8 else b.rng.randrange(256)
            else:
                value = b.rng.randrange(256)
            await b.write(address, value)
        elif roll < 0.70:
            await b.read(b.rng.randrange(0x8000))
        elif roll < 0.85:
            await b.write(0xA000 | b.rng.randrange(0x2000), b.rng.randrange(256))
        else:
            await b.read(0xA000 | b.rng.randrange(0x2000))
    c = b.coverage
    cocotb.log.info('random phase: rom_banks=%d low_window_remaps=%d ram_enabled=%d ram_local=%d ram_banks=%s register_writes=%d',
                    len(c.rom_banks), c.low_window_remaps, c.ram_enabled, c.ram_local, sorted(c.ram_banks), c.register_writes)
    assert len(c.rom_banks) >= 12, 'random phase must reach many ROM banks'
    assert c.low_window_remaps >= 3, 'random phase must observe the mode-1 low window remap'
    assert c.ram_enabled >= 10 and c.ram_local >= 10, 'random phase must see enabled and disabled RAM accesses'
    assert len(c.ram_banks) >= 2, 'random phase must bank the RAM'
    assert c.register_writes >= 50
    assert min(c.forwarded_latencies) == 1 and max(c.forwarded_latencies) >= 5, (
        'random phase must exercise both the unstalled and the stalled backing', sorted(set(c.forwarded_latencies)))
    cocotb.log.info('cartridge mapper checks passed seed=%d cycles=%d transactions=%d', seed, b.cycles, b.transactions)
