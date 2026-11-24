"""Public-port checks of the nand2mario memory map against the Python model.
The bench plays the peripheral port and the cartridge port (with randomized
ready and response latency) itself."""
import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge

from .memory_model import OPEN_BUS, REGIONS, GameBoyMemory, boundaries, destination

CLOCK_PERIOD_NS = 40
FETCH, DATA, STACK = 0, 1, 2
CARTRIDGE = ('rom', 'cart_ram')


class PortCartridge:
    """Byte store the bench serves on the cartridge port: every cartridge
    address holds the last byte written through the port or served for it.
    The mapper is not under test here; the focused cartridge target owns it."""

    def __init__(self):
        self.bytes = {}

    def is_defined(self, address):
        return address in self.bytes

    def read(self, address):
        return self.bytes[address]

    def write(self, address, value):
        self.bytes[address] = value & 0xFF

    def load_image(self, image, start=0):
        for offset, byte in enumerate(image, start):
            self.bytes[offset] = byte

    def reset(self):
        pass


def request_word(valid, write, kind, address, data):
    return (valid << 27) | (write << 26) | (kind << 24) | (address << 8) | data


def response_fields(word):
    return (word >> 9) & 1, (word >> 8) & 1, word & 0xFF


class Bench:
    """Drives one requester, observes one response per request, and plays the
    peripheral port responder with a programmable delay and fault."""

    def __init__(self, dut, seed):
        self.dut = dut
        self.rng = random.Random(seed)
        self.bound = int(os.environ['DV_MAX_CYCLES'])
        self.cycles = 0
        self.io_delay = 0
        self.io_fault = False
        self.io_data = OPEN_BUS
        self.io_requests = []
        self.io_writes = []
        self.cart_ready_delays = (0,)
        self.cart_response_delays = (1,)
        self.cart_requests = []
        self.cart_latencies = []
        self.cartridge = PortCartridge()
        self.model = GameBoyMemory(io_read=lambda a: self.io_data,
                                   io_write=lambda a, v: self.io_writes.append((a, v)), strict=False,
                                   cartridge=self.cartridge)
        self.undefined_reads = 0

    def power_up(self):
        """Stores have no power-up value: fill every byte with seeded garbage
        through the simulator before the first edge so the checks cannot rely
        on the simulator's default zeros."""
        for store in (self.dut.u_vram, self.dut.u_wram, self.dut.u_oam, self.dut.u_hram):
            for index in range(len(store.u_ram.memory)):
                store.u_ram.memory[index].value = self.rng.randrange(1, 256)

    def idle(self):
        d = self.dut
        d.request_i.value = 0
        d.io_response_i.value = 0
        d.cart_ready_i.value = 0
        d.cart_response_i.value = 0
        d.ppu_vram_read_i.value = 0
        d.ppu_vram_address_i.value = 0
        d.ppu_oam_read_i.value = 0
        d.ppu_oam_address_i.value = 0
        d.dma_oam_write_i.value = 0
        d.vram_clear_i.value = 0
        d.vram_clear_address_i.value = 0
        d.dma_oam_address_i.value = 0
        d.dma_oam_data_i.value = 0
        d.vram_blocked_i.value = 0
        d.oam_blocked_i.value = 0

    async def ppu_read(self, offset, store='vram'):
        """One PPU VRAM or OAM port read: sampled on an edge, data valid the
        next cycle."""
        d = self.dut
        read, address, valid, data_port = {
            'vram': (d.ppu_vram_read_i, d.ppu_vram_address_i, d.ppu_vram_valid_o, d.ppu_vram_data_o),
            'oam': (d.ppu_oam_read_i, d.ppu_oam_address_i, d.ppu_oam_valid_o, d.ppu_oam_data_o),
        }[store]
        await FallingEdge(d.clk_i)
        read.value = 1
        address.value = offset
        await self.edge()
        await FallingEdge(d.clk_i)
        read.value = 0
        await ReadOnly()
        assert int(valid.value) == 1, f'PPU {store} read must be answered on the next cycle'
        data = int(data_port.value)
        await self.edge()
        await ReadOnly()
        assert int(valid.value) == 0, f'PPU {store} valid must fall without a read'
        return data

    async def dma_write(self, offset, data):
        """One DMA OAM port write on one edge; the model's OAM byte follows."""
        d = self.dut
        await FallingEdge(d.clk_i)
        d.dma_oam_write_i.value = 1
        d.dma_oam_address_i.value = offset
        d.dma_oam_data_i.value = data
        await self.edge()
        await FallingEdge(d.clk_i)
        d.dma_oam_write_i.value = 0
        d.vram_clear_i.value = 0
        d.vram_clear_address_i.value = 0
        self.model[0xFE00 + offset] = data

    async def edge(self):
        """Advance one system edge within the finite bound."""
        self.cycles += 1
        assert self.cycles <= self.bound, 'finite simulation bound exceeded'
        await RisingEdge(self.dut.clk_i)

    async def reset(self, cycles=2):
        d = self.dut
        await FallingEdge(d.clk_i)
        d.reset_i.value = 1
        self.idle()
        for _ in range(cycles):
            await self.edge()
        await FallingEdge(d.clk_i)
        d.reset_i.value = 0
        self.model.ie = 0

    def serve_cartridge(self, address, write, data):
        """The byte the bench's cartridge delivers for a forwarded read: the
        stored byte, or a fresh random byte it records as that address's value."""
        if write:
            return 0
        if not self.cartridge.is_defined(address):
            self.cartridge.write(address, self.rng.randrange(256))
        return self.cartridge.read(address)

    async def transact(self, address, write=False, data=0, kind=None, hold_after=1,
                       expect_blocked=False, cart_ready_delay=None, cart_response_delay=None):
        """One request; returns (read_data, latency) and checks the protocol.

        Values are sampled in the read-only phase before each rising edge,
        exactly what the requester sees at that edge. Latency counts edges
        after the acceptance edge until the edge that samples the response.
        A PPU-blocked access reads open bus and leaves the model unchanged.
        A cartridge access is forwarded on the acceptance cycle and held until
        the bench's cartridge is ready; its response completes the request.
        """
        d = self.dut
        if kind is None:
            kind = STACK if write and self.rng.random() < 0.3 else (DATA if write else self.rng.choice((FETCH, DATA, STACK)))
        word = request_word(1, int(write), kind, address, data)
        name, _ = destination(address)
        cart = name in CARTRIDGE
        if cart_ready_delay is None:
            cart_ready_delay = self.rng.choice(self.cart_ready_delays)
        if cart_response_delay is None:
            cart_response_delay = self.rng.choice(self.cart_response_delays)
        expected_cart_latency = cart_ready_delay + cart_response_delay
        await FallingEdge(d.clk_i)
        d.request_i.value = word
        d.io_response_i.value = 0
        d.cart_ready_i.value = int(cart_ready_delay == 0)
        d.cart_response_i.value = 0
        edges, io_seen, io_wait = 0, False, self.io_delay
        cart_seen, cart_accepted, cart_respond_at, cart_data = False, False, None, 0
        for _ in range(24):
            await ReadOnly()
            valid, fault, read_data = response_fields(int(d.response_o.value))
            cart_request = int(d.cart_request_o.value)
            if (cart_request >> 27) & 1:
                assert cart, f'cartridge forward for non-cartridge address {address:04x}'
                assert not cart_accepted, 'the cartridge request is withdrawn once accepted'
                assert cart_request & ((1 << 27) - 1) == word & ((1 << 27) - 1), 'cartridge port must forward the live request'
                cart_seen = True
                if int(d.cart_ready_i.value):
                    cart_accepted = True
                    cart_respond_at = edges + 1 + cart_response_delay
                    cart_data = self.serve_cartridge(address, write, data)
            else:
                assert not cart or cart_accepted, (
                    f'cartridge request for {address:04x} must be presented from the acceptance cycle until accepted')
            await self.edge()
            edges += 1
            if valid:
                break
            await FallingEdge(d.clk_i)
            if cart:
                if cart_accepted:
                    d.cart_ready_i.value = 0
                    d.cart_response_i.value = ((1 << 9) | cart_data) if edges + 1 == cart_respond_at else 0
                else:
                    cart_ready_delay -= 1
                    d.cart_ready_i.value = int(cart_ready_delay <= 0)
            io_request = int(d.io_request_o.value)
            if (io_request >> 27) & 1:
                assert io_request & ((1 << 27) - 1) == word & ((1 << 27) - 1), 'peripheral port must forward the held request'
                io_seen = True
                if io_wait == 0:
                    d.io_response_i.value = (1 << 9) | (int(self.io_fault) << 8) | self.io_data
                else:
                    io_wait -= 1
        else:
            raise AssertionError(f'no response within 24 edges for address {address:04x}')
        assert io_seen == (name == 'io'), f'peripheral forward mismatch for {address:04x}'
        assert cart_seen == cart and cart_accepted == cart, f'cartridge forward mismatch for {address:04x}'
        if io_seen:
            self.io_requests.append((address, int(write), data))
        if cart:
            self.cart_requests.append((address, int(write), data))
            self.cart_latencies.append(edges - 1)
            assert edges - 1 == expected_cart_latency, (
                f'cartridge latency {edges - 1} expected {expected_cart_latency} at {address:04x}')
        await FallingEdge(d.clk_i)
        d.request_i.value = 0
        d.io_response_i.value = 0
        d.cart_response_i.value = 0
        d.cart_ready_i.value = 0
        for _ in range(hold_after):
            await ReadOnly()
            spurious, _, _ = response_fields(int(d.response_o.value))
            assert not spurious, f'response without a request after {address:04x}'
            assert not (int(d.io_request_o.value) >> 27) & 1, 'peripheral forward without a request'
            assert not (int(d.cart_request_o.value) >> 27) & 1, 'cartridge forward without a request'
            await self.edge()
            await FallingEdge(d.clk_i)
        expected_fault = int(name == 'io' and self.io_fault)
        assert fault == expected_fault, (f'fault {fault} expected {expected_fault} at {address:04x}')
        if expect_blocked:
            assert write or read_data == OPEN_BUS, f'blocked read {address:04x} returned {read_data:02x}'
        elif write:
            self.model[address] = data
        elif self.model.is_defined(address):
            expected = self.model[address]
            assert read_data == expected, (
                f'read {address:04x} ({name}) observed {read_data:02x} expected {expected:02x}')
        else:
            # Why: an undefined store byte has no power-up contract in the HAS, so only
            # protocol and latency are checked, not the value.
            self.undefined_reads += 1
        return read_data, edges - 1

    async def read(self, address, **kw):
        data, _ = await self.transact(address, **kw)
        return data

    async def write(self, address, data, **kw):
        await self.transact(address, write=True, data=data, **kw)


@cocotb.test(timeout_time=10, timeout_unit='ms')
async def memory_map_contract(dut):
    seed = int(os.environ['DV_SEED'])
    assert int(os.environ['DV_CLOCK_HZ']) == 25_000_000
    cocotb.log.info('nand2mario memory map seed=%d', seed)
    b = Bench(dut, seed)
    b.idle()
    b.power_up()
    cocotb.start_soon(Clock(dut.clk_i, CLOCK_PERIOD_NS, unit='ns').start())
    await b.reset()

    for address in (0x0000, 0x0001, 0x00FF, 0x0100, 0x3FFF, 0x4000, 0x7FFE, 0x7FFF):
        data, latency = await b.transact(address, kind=FETCH)
        assert latency == 1, f'cartridge latency {latency} at {address:04x}'
    await b.write(0x2000, 0x05)
    await b.write(0x7FFF, 0x5A)
    assert b.cart_requests[-2:] == [(0x2000, 1, 0x05), (0x7FFF, 1, 0x5A)]
    assert await b.read(0x7FFF) == 0x5A
    data, latency = await b.transact(0x4123, cart_ready_delay=3, cart_response_delay=2)
    assert latency == 5, ('stalled cartridge read', latency)
    _, latency = await b.transact(0xA000, write=True, data=0x77, cart_ready_delay=0, cart_response_delay=4)
    assert latency == 4, ('slow cartridge write', latency)
    data, latency = await b.transact(0xA000, cart_ready_delay=2, cart_response_delay=1)
    assert (data, latency) == (0x77, 3), ('stalled cartridge RAM read', data, latency)
    assert b.cart_requests[-3:] == [(0x4123, 0, 0), (0xA000, 1, 0x77), (0xA000, 0, 0)]

    # Why: every region boundary reads back through the model's expectation,
    # because stores keep the byte and open-bus/ROM destinations do not.
    for address in boundaries():
        value = (address * 7 + 3) & 0xFF
        await b.write(address, value)
        data, latency = await b.transact(address)
        assert latency == 1, f'latency {latency} at boundary {address:04x}'
        name, _ = destination(address)
        if name in ('unusable', 'io'):
            assert data == OPEN_BUS
        else:
            assert data == value, 'stores and the bench cartridge keep the byte'
    for address in (0xFEA0, 0xFEC7, 0xFEFF):
        assert await b.read(address) == OPEN_BUS
    for address in (0xA000, 0xB123, 0xBFFF):
        await b.read(address)

    # Ref: Pan Docs "Memory Map": echo RAM $E000-$FDFF mirrors $C000-$DDFF.
    await b.write(0xC000, 0x11)
    assert await b.read(0xE000) == 0x11
    await b.write(0xFDFF, 0x22)
    assert await b.read(0xDDFF) == 0x22
    await b.write(0xE5A5, 0x33)
    assert await b.read(0xC5A5) == 0x33
    await b.write(0xDE00, 0x44)
    assert await b.read(0xC000) == 0x11 and await b.read(0xDE00) == 0x44

    # Ref: Pan Docs "Interrupts": only IE bits 0-4 select sources; all eight bits read back.
    for value in (0xFF, 0x00, 0xE5, 0x1F, 0xA0):
        await b.write(0xFFFF, value)
        assert await b.read(0xFFFF) == value
        assert int(dut.interrupt_enable_o.value) == value & 0x1F
    await b.write(0xFF80, 0x5A)
    await b.write(0xFFFE, 0xA5)
    assert await b.read(0xFF80) == 0x5A and await b.read(0xFFFE) == 0xA5
    assert await b.read(0xFFFF) == 0xA0

    # Ref: Pan Docs "Memory Map": the unusable $FEA0-$FEFF window never aliases OAM.
    await b.write(0xFE00, 0x01)
    await b.write(0xFE9F, 0x9F)
    await b.write(0xFEA0, 0xAA)
    assert await b.read(0xFE00) == 0x01 and await b.read(0xFE9F) == 0x9F
    await b.write(0x8000, 0x80)
    await b.write(0x9FFF, 0x9F)
    assert await b.read(0x8000) == 0x80 and await b.read(0x9FFF) == 0x9F

    assert await b.ppu_read(0x0000) == 0x80 and await b.ppu_read(0x1FFF) == 0x9F
    await b.write(0x8123, 0x5C)
    assert await b.ppu_read(0x0123) == 0x5C

    assert await b.ppu_read(0x00, 'oam') == 0x01 and await b.ppu_read(0x9F, 'oam') == 0x9F
    await b.write(0xFE42, 0xC3)
    assert await b.ppu_read(0x42, 'oam') == 0xC3

    await b.dma_write(0x00, 0xD0)
    await b.dma_write(0x9F, 0xD9)
    assert await b.read(0xFE00) == 0xD0 and await b.read(0xFE9F) == 0xD9
    dut.oam_blocked_i.value = 1
    await b.dma_write(0x42, 0xDA)
    dut.oam_blocked_i.value = 0
    assert await b.ppu_read(0x42, 'oam') == 0xDA and await b.read(0xFE42) == 0xDA
    await b.write(0xFE00, 0x01)
    await b.write(0xFE9F, 0x9F)

    await FallingEdge(dut.clk_i)
    dut.vram_blocked_i.value = 1
    data, latency = await b.transact(0x8000, expect_blocked=True)
    assert (data, latency) == (OPEN_BUS, 1), ('blocked VRAM read', data, latency)
    await b.write(0x8000, 0x3E, expect_blocked=True)
    assert await b.read(0xFE00) == 0x01 and await b.read(0xC000) == 0x11
    await FallingEdge(dut.clk_i)
    dut.vram_blocked_i.value = 0
    assert await b.read(0x8000) == 0x80, 'a blocked VRAM write must be dropped'
    await FallingEdge(dut.clk_i)
    dut.oam_blocked_i.value = 1
    data, latency = await b.transact(0xFE9F, expect_blocked=True)
    assert (data, latency) == (OPEN_BUS, 1), ('blocked OAM read', data, latency)
    await b.write(0xFE9F, 0x77, expect_blocked=True)
    assert await b.read(0x9FFF) == 0x9F
    await FallingEdge(dut.clk_i)
    dut.oam_blocked_i.value = 0
    assert await b.read(0xFE9F) == 0x9F, 'a blocked OAM write must be dropped'
    assert await b.ppu_read(0x0000) == 0x80

    b.io_data = 0x42
    b.io_writes.clear()
    b.io_requests.clear()
    assert await b.read(0xFF00, kind=DATA) == 0x42
    assert await b.read(0xFF7F, kind=DATA) == 0x42
    await b.write(0xFF44, 0x99, kind=DATA)
    assert b.io_writes == [(0xFF44, 0x99)]
    b.io_delay = 3
    data, latency = await b.transact(0xFF05, kind=DATA)
    assert (data, latency) == (0x42, 4), ('slow peripheral', data, latency)
    b.io_delay = 0
    b.io_fault = True
    await b.read(0xFF0F, kind=DATA)
    b.io_fault = False
    b.io_data = OPEN_BUS
    assert await b.read(0xFF7F, kind=DATA) == OPEN_BUS
    assert b.io_requests[0] == (0xFF00, 0, 0) and b.io_requests[2] == (0xFF44, 1, 0x99)

    await b.write(0xFFFF, 0x1F)
    await FallingEdge(dut.clk_i)
    dut.request_i.value = request_word(1, 0, DATA, 0xC000, 0)
    await b.edge()
    await b.reset()
    await ReadOnly()
    assert not response_fields(int(dut.response_o.value))[0], 'reset must drop the pending response'
    await b.edge()
    assert int(dut.interrupt_enable_o.value) == 0
    assert await b.read(0xFFFF) == 0 and await b.read(0xC000) == 0x11
    await b.read(0x0000)
    await FallingEdge(dut.clk_i)
    dut.request_i.value = request_word(1, 0, FETCH, 0x4000, 0)
    dut.cart_ready_i.value = 0
    await b.edge()
    await b.edge()
    await b.reset()
    await ReadOnly()
    assert not response_fields(int(dut.response_o.value))[0]
    assert not (int(dut.cart_request_o.value) >> 27) & 1
    await b.edge()
    await b.read(0x7FFF)

    # Why: seeded random traffic over the whole space against the model. Reads of
    # store bytes nothing has defined check protocol only; most reads land on
    # defined bytes (ROM, hot addresses written first) and compare values.
    hot = [b.rng.randrange(0x10000) for _ in range(32)]
    for address in hot:
        await b.write(address, b.rng.randrange(256))
    checked = b.undefined_reads
    b.cart_ready_delays = (0, 0, 1, 2, 3)
    b.cart_response_delays = (1, 1, 2, 3)
    cart_before = len(b.cart_latencies)
    for index in range(600):
        address = b.rng.choice(hot) if b.rng.random() < 0.5 else b.rng.randrange(0x10000)
        if b.rng.random() < 0.5:
            await b.write(address, b.rng.randrange(256), hold_after=b.rng.randrange(3))
        else:
            await b.read(address, hold_after=b.rng.randrange(3))
    assert b.undefined_reads - checked < 150, 'random phase must mostly read defined bytes'
    phase_latencies = b.cart_latencies[cart_before:]
    assert len(phase_latencies) >= 100 and min(phase_latencies) == 1 and max(phase_latencies) >= 5, (
        'random phase must forward many cartridge accesses with and without stalls', len(phase_latencies))
    cocotb.log.info('memory map checks passed seed=%d cycles=%d undefined_reads=%d cartridge_accesses=%d',
                    seed, b.cycles, b.undefined_reads, len(b.cart_latencies))
