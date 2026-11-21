"""Focused checks of the OAM DMA block in front of the real memory map
against the independent Python DMA and memory models: the register, the
start delay and the 160-cycle transfer window in machine ticks, the byte
schedule observed on the OAM write port, every source class (ROM, VRAM with
and without PPU blocking, WRAM, the echo pages, absent cartridge RAM), the
CPU restriction during a transfer (open-bus reads and dropped writes on
OAM and on the bus of the source, external or VRAM; the other bus, the I/O
window, HRAM and IE still served) at the window boundaries, a restart
written during a transfer, a restart while armed, a reset in the
middle of a transfer, and the OAM contents through the CPU and the PPU
port afterwards. The memory map's cartridge port is served by the real
mapper and a 32 KiB on-chip backing, so ROM and cartridge RAM source pages
take the cartridge path (cartridge RAM is disabled after reset and reads
open bus)."""
import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge
from cocotb.utils import get_sim_time

from ..memory.memory_model import OPEN_BUS, GameBoyMemory
from ..memory.test_memory import request_word, response_fields
from . import dma_model as dm

CLOCK_PERIOD_NS = 40
DATA = 1


class Bench:
    def __init__(self, dut, seed):
        self.dut = dut
        self.rng = random.Random(seed)
        self.bound = int(os.environ['DV_MAX_CYCLES'])
        self.cycles = 0
        self.ticks = 0
        self.last_tick_cycle = None
        self.memory = GameBoyMemory(strict=False)
        self.dma = dm.DmaModel()
        self.writes = []
        self.active_log = []
        self.monitor_active = True

    def idle(self):
        d = self.dut
        d.cpu_request_i.value = 0
        d.rom_load_write_i.value = 0
        d.rom_load_address_i.value = 0
        d.rom_load_data_i.value = 0
        d.backing_stall_i.value = 0
        d.vram_blocked_i.value = 0
        d.oam_blocked_i.value = 0
        d.ppu_oam_read_i.value = 0
        d.ppu_oam_address_i.value = 0

    def power_up(self):
        for store in (self.dut.u_backing.u_rom, self.dut.u_backing.u_ram, self.dut.u_memory.u_vram,
                      self.dut.u_memory.u_wram, self.dut.u_memory.u_oam, self.dut.u_memory.u_hram):
            for index in range(len(store.memory)):
                store.memory[index].value = self.rng.randrange(1, 256)

    async def edge(self):
        self.cycles += 1
        assert self.cycles <= self.bound, 'finite simulation bound exceeded'
        await RisingEdge(self.dut.clk_i)

    async def monitor(self):
        """Step the DMA model at every machine tick, check the active level
        after each tick and record OAM writes with the tick they follow."""
        d = self.dut
        while True:
            await RisingEdge(d.clk_i)
            await ReadOnly()
            if int(d.oam_write_o.value):
                self.writes.append((self.ticks, int(d.oam_address_o.value), int(d.oam_data_o.value)))
                self.memory[0xFE00 + int(d.oam_address_o.value)] = int(d.oam_data_o.value)
            if int(d.machine_cycle_enable_o.value) and not self.monitor_active:
                self.ticks += 1
                self.last_tick_cycle = self.cycles
                continue
            if int(d.machine_cycle_enable_o.value):
                # Why: the enable is sampled on this edge: the tick's effects show
                # from the next cycle on.
                self.ticks += 1
                self.last_tick_cycle = self.cycles
                byte = self.dma.tick()
                await RisingEdge(d.clk_i)
                await ReadOnly()
                assert int(d.dma_active_o.value) == int(self.dma.active), (
                    f'tick {self.ticks}: active {int(d.dma_active_o.value)} expected {int(self.dma.active)}')
                self.active_log.append((self.ticks, self.dma.active))
                if byte is not None:
                    self.expected_writes.append((self.ticks, byte))

    async def reset(self):
        d = self.dut
        await FallingEdge(d.clk_i)
        d.reset_i.value = 1
        self.idle()
        for _ in range(3):
            await self.edge()
        await FallingEdge(d.clk_i)
        d.reset_i.value = 0
        self.dma.reset()

    async def load_rom(self, image):
        d = self.dut
        for address, byte in enumerate(image):
            await FallingEdge(d.clk_i)
            d.rom_load_write_i.value = 1
            d.rom_load_address_i.value = address
            d.rom_load_data_i.value = byte
            await self.edge()
        await FallingEdge(d.clk_i)
        d.rom_load_write_i.value = 0
        self.memory.load_rom(image)

    async def transact(self, address, write=False, data=0, blocked=None, max_latency=4, now=False):
        """One CPU request held until its response; returns (data, latency).
        ``blocked`` says whether the DMA restriction must apply (None: use
        the model's active level at presentation, away from a tick). With
        ``now`` the caller is already at the falling edge of presentation."""
        d = self.dut
        if not now:
            await FallingEdge(d.clk_i)
        if blocked is None:
            blocked = self.dma.active and dm.restricted(address, self.dma.page)
        d.cpu_request_i.value = request_word(1, int(write), DATA, address, data)
        latency = 0
        while True:
            await ReadOnly()
            valid, fault, read_data = response_fields(int(d.cpu_response_o.value))
            if valid:
                break
            await self.edge()
            latency += 1
            assert latency <= max_latency, f'no response within {max_latency} edges at {address:04x}'
        assert fault == 0
        await self.edge()
        await FallingEdge(d.clk_i)
        d.cpu_request_i.value = 0
        if blocked:
            # Why: restricted: a read shows open bus, a write reaches nothing (the
            # models are left untouched, so later reads must still agree).
            if not write:
                assert read_data == OPEN_BUS, f'restricted read {address:04x} returned {read_data:02x}'
        elif address == dm.DMA:
            if write:
                self.dma.write(data)
            else:
                assert read_data == self.dma.read(), f'DMA register read {read_data:02x} expected {self.dma.read():02x}'
        elif write:
            self.memory[address] = data
        elif self.memory.is_defined(address):
            assert read_data == self.memory[address], (
                f'read {address:04x} observed {read_data:02x} expected {self.memory[address]:02x}')
        return read_data, latency

    async def read(self, address, **kw):
        data, _ = await self.transact(address, **kw)
        return data

    async def write(self, address, data, **kw):
        await self.transact(address, write=True, data=data, **kw)

    async def ppu_oam(self, offset):
        d = self.dut
        await FallingEdge(d.clk_i)
        d.ppu_oam_read_i.value = 1
        d.ppu_oam_address_i.value = offset
        await self.edge()
        await FallingEdge(d.clk_i)
        d.ppu_oam_read_i.value = 0
        await ReadOnly()
        assert int(d.ppu_oam_valid_o.value) == 1
        return int(d.ppu_oam_data_o.value)

    async def wait_ticks(self, count):
        """Idle for ``count`` machine ticks."""
        target = self.ticks + count
        while self.ticks < target:
            await self.edge()

    async def present_at_tick(self, target, address, blocked):
        """Present one read at the falling edge inside the enable pulse of
        machine tick ``target``, so the DMA block accepts it on the very edge
        at which it samples that tick (where the transfer level changes)."""
        d = self.dut
        while True:
            await FallingEdge(d.clk_i)
            if self.last_tick_cycle == self.cycles and self.ticks == target:
                break
            await self.edge()
        return await self.transact(address, blocked=blocked, max_latency=3, now=True)

    async def settle_after_tick(self, edges):
        """Idle until ``edges`` system edges have passed since the last tick,
        waiting for the next tick when the current cycle is too advanced."""
        while True:
            since = self.cycles - self.last_tick_cycle
            if since == edges:
                return
            if since > edges:
                await self.wait_ticks(1)
                continue
            await self.edge()

    async def oam_image(self, through='cpu'):
        if through == 'cpu':
            return bytes([await self.read(0xFE00 + offset) for offset in range(160)])
        return bytes([await self.ppu_oam(offset) for offset in range(160)])


def compare_bytes(observed, expected, label):
    assert len(observed) == len(expected), (label, len(observed), len(expected))
    for offset, (got, want) in enumerate(zip(observed, expected)):
        assert got == want, f'{label}[{offset:02x}] observed {got:02x} expected {want:02x}'


async def run_transfer(b, page, during=None):
    """Write $FF46 at a random phase of a machine cycle, run ``during`` while
    the transfer is active (it may issue CPU accesses), wait for the end and
    check the write schedule: byte k is written in the machine cycle that
    starts two ticks after the write's tick plus k. Returns the tick of the
    write."""
    await b.settle_after_tick(b.rng.randrange(2, 16))
    b.expected_writes = []
    b.writes.clear()
    write_tick = b.ticks
    await b.write(dm.DMA, page)
    assert b.ticks == write_tick, 'the register write must land inside one machine cycle'
    await b.wait_ticks(2)
    await ReadOnly()
    assert b.dma.active and int(b.dut.dma_active_o.value) == 1, 'transfer must be active two ticks after the write'
    await FallingEdge(b.dut.clk_i)
    if during is not None:
        await during()
    while b.dma.active:
        await b.edge()
    await b.wait_ticks(1)
    assert b.ticks - write_tick == dm.TRANSFER_BYTES + 2 + 1, (b.ticks, write_tick)
    assert len(b.writes) == dm.TRANSFER_BYTES, ('OAM writes observed', len(b.writes))
    for (tick, offset, _), (expected_tick, expected_offset) in zip(b.writes, b.expected_writes):
        assert (tick, offset) == (expected_tick, expected_offset), (
            f'OAM write of byte {offset:02x} after tick {tick}, expected byte {expected_offset:02x} after tick {expected_tick}')
    assert b.writes[0][0] == write_tick + 2 and b.writes[-1][0] == write_tick + 161, (b.writes[0], b.writes[-1])
    return write_tick


@cocotb.test(timeout_time=20, timeout_unit='ms')
async def dma_contract(dut):
    seed = int(os.environ['DV_SEED'])
    assert int(os.environ['DV_CLOCK_HZ']) == 25_000_000
    cocotb.log.info('nand2mario OAM DMA seed=%d', seed)
    b = Bench(dut, seed)
    b.idle()
    b.power_up()
    cocotb.start_soon(Clock(dut.clk_i, CLOCK_PERIOD_NS, unit='ns').start())
    await b.reset()
    b.expected_writes = []
    cocotb.start_soon(b.monitor())

    assert int(dut.dma_active_o.value) == 0
    assert await b.read(dm.DMA) == 0
    image = bytes(b.rng.randrange(256) for _ in range(32768))
    await b.load_rom(image)
    data, latency = await b.transact(0x1234)
    assert (data, latency) == (image[0x1234], 1)
    for base in (0xC000, 0xC300, 0xDF00, 0x8000, 0x9100):
        for offset in range(160):
            await b.write(base + offset, b.rng.randrange(256))
    await b.write(0xFF80, 0x5A)
    await b.write(0xFFFF, 0x1F)

    # Ref: Pan Docs "OAM DMA Transfer": with a WRAM source (page $C0, external bus), during
    # the window: HRAM, IE, the DMA register and VRAM (the other bus) are
    # served; WRAM, ROM, cartridge RAM and OAM read open bus and drop
    # writes; latencies stay within four edges.
    async def traffic():
        await b.write(0xFF80, 0x11)
        await b.write(0xFFFE, 0x22)
        assert await b.read(0xFF80) == 0x11 and await b.read(0xFFFE) == 0x22, 'HRAM ends served during DMA'
        assert await b.read(dm.DMA) == 0xC0, 'the DMA register reads back during DMA'
        await b.write(0x8000, 0x3C)
        assert await b.read(0x8000) == 0x3C, 'VRAM is on the other bus during a WRAM-source DMA'
        for address in (0xFE9F, 0xFE10, 0xC000, 0x0100, 0xA000):
            assert await b.read(address) == OPEN_BUS, f'{address:04x} must read open bus during DMA'
        await b.write(0xFFFF, 0x1F)
        for _ in range(50):
            address = b.rng.choice((0xFF80, 0xFFFE, 0xC000, 0xC0A0, 0x8000, 0x9FFF, 0x0100, 0xFE10, 0xA000))
            if b.rng.random() < 0.4:
                await b.write(address, b.rng.randrange(256))
            else:
                await b.read(address)
            if b.rng.random() < 0.5:
                await b.wait_ticks(1)
    await run_transfer(b, 0xC0, traffic)
    compare_bytes(await b.oam_image(), dm.transfer_image(b.memory, 0xC0), 'OAM after WRAM transfer')
    compare_bytes(await b.oam_image('ppu'), dm.transfer_image(b.memory, 0xC0), 'OAM through the PPU port')
    assert await b.read(0xC000) == b.memory[0xC000] and await b.read(0x8000) == b.memory[0x8000], (
        'restricted writes must have been dropped')
    assert await b.read(0xFFFF) == b.memory[0xFFFF] and await b.read(dm.DMA) == 0xC0, 'IE served during DMA'
    assert await b.read(0xFF80) == b.memory[0xFF80] and await b.read(0xFFFE) == b.memory[0xFFFE]
    cocotb.log.info('transfer 1 done: hram ends %02x %02x', b.memory[0xFF80], b.memory[0xFFFE])

    # Why: proves the check can fail: the OAM comparison must reject one flipped byte.
    corrupted = bytearray(dm.transfer_image(b.memory, 0xC0))
    corrupted[77] ^= 0x80
    try:
        compare_bytes(await b.oam_image(), bytes(corrupted), 'OAM-negative')
    except AssertionError:
        pass
    else:
        raise AssertionError('OAM comparison accepted a corrupted expectation')

    await b.settle_after_tick(3)
    start_tick = b.ticks
    await b.write(dm.DMA, 0xC3)
    data, _ = await b.present_at_tick(start_tick + 2, 0xC000, blocked=False)
    assert data == b.memory[0xC000], (start_tick, data)
    data, _ = await b.present_at_tick(start_tick + 162, 0xC000, blocked=True)
    assert data == OPEN_BUS, (start_tick, data)
    assert b.dma.active is False and int(b.dut.dma_active_o.value) == 0
    await b.wait_ticks(1)
    compare_bytes(await b.oam_image(), dm.transfer_image(b.memory, 0xC3), 'OAM after page $C3')

    await run_transfer(b, 0x12)
    compare_bytes(await b.oam_image(), image[0x1200:0x12A0], 'OAM after ROM transfer')

    # Why: the level is raised at the falling edge inside the tick of byte 40 and
    # lowered inside the tick of byte 90; each byte's source read is accepted
    # within the two edges after its tick, so bytes 40-89 read open bus.
    async def block_vram():
        await b.wait_ticks(40)
        await FallingEdge(dut.clk_i)
        dut.vram_blocked_i.value = 1
        await b.wait_ticks(50)
        await FallingEdge(dut.clk_i)
        dut.vram_blocked_i.value = 0
    await run_transfer(b, 0x91, block_vram)
    expected = bytearray(dm.transfer_image(b.memory, 0x91))
    for offset in range(40, 90):
        expected[offset] = OPEN_BUS
    compare_bytes(await b.oam_image(), bytes(expected), 'OAM after VRAM transfer with blocking')
    # Ref: Pan Docs "OAM DMA Transfer": a VRAM-source transfer (VRAM bus) with CPU traffic: the external bus
    # (WRAM, ROM, cartridge RAM) and I/O are served; VRAM and OAM read
    # open bus and drop writes (the models keep the old bytes).
    async def vram_source_traffic():
        await b.write(0xC010, 0x6B)
        assert await b.read(0xC010) == 0x6B, 'WRAM served during a VRAM-source DMA'
        assert await b.read(0x0123) == image[0x0123], 'ROM served during a VRAM-source DMA'
        await b.read(0xA000)
        for address in (0x8000, 0x9FFF, 0x9100, 0xFE00, 0xFE9F):
            assert await b.read(address) == OPEN_BUS, f'{address:04x} must read open bus during a VRAM-source DMA'
            await b.write(address, 0xA5)
        for _ in range(40):
            address = b.rng.choice((0xC000, 0xC0A0, 0xDFFF, 0x0100, 0x4000, 0xA000, 0x8000, 0x9FFF, 0xFE10, 0xFF80))
            if b.rng.random() < 0.4 and address >= 0x8000:
                await b.write(address, b.rng.randrange(256))
            else:
                await b.read(address)
    vram_before = (await b.read(0x8000), await b.read(0x9FFF))
    await run_transfer(b, 0x91, vram_source_traffic)
    compare_bytes(await b.oam_image(), dm.transfer_image(b.memory, 0x91), 'OAM after VRAM transfer with CPU traffic')
    assert (await b.read(0x8000), await b.read(0x9FFF)) == vram_before, 'VRAM writes during a VRAM-source DMA dropped'
    assert await b.read(0xC010) == 0x6B

    # Ref: Pan Docs "OAM DMA Transfer": an echo-source transfer ($E3 reads page $C3, external bus) with traffic:
    # WRAM through both aliases is blocked, VRAM is served.
    async def echo_source_traffic():
        for address in (0xC300, 0xE300, 0xD000):
            assert await b.read(address) == OPEN_BUS, f'{address:04x} must read open bus during an echo-source DMA'
        await b.write(0xC320, 0x99)
        await b.write(0x8010, 0x77)
        assert await b.read(0x8010) == 0x77, 'VRAM served during an echo-source DMA'
    await run_transfer(b, 0xE3, echo_source_traffic)
    compare_bytes(await b.oam_image(), dm.transfer_image(b.memory, 0xC3), 'OAM after echo transfer with CPU traffic')
    assert await b.read(0xC320) == b.memory[0xC320], 'WRAM write during an external-bus DMA dropped'

    for page, source in ((0xE3, 0xC3), (0xFF, 0xDF)):
        await run_transfer(b, page)
        compare_bytes(await b.oam_image(), dm.transfer_image(b.memory, source), f'OAM after echo page {page:02x}')
    await run_transfer(b, 0xA0)
    compare_bytes(await b.oam_image(), bytes([OPEN_BUS] * 160), 'OAM after cartridge RAM transfer')

    # Ref: Mooneye "oam_dma_restart": a write to the DMA register in the middle of a transfer (I/O is not
    # restricted) restarts it: the running transfer goes on until the
    # restart starts two ticks later at offset 0 from the new page, the
    # restriction stays continuous and the register reads the new page.
    await b.settle_after_tick(5)
    b.writes.clear()
    b.active_log.clear()
    b.expected_writes = []
    first_tick = b.ticks
    await b.write(dm.DMA, 0xC0)
    await b.wait_ticks(50)
    restart_tick = b.ticks
    await b.write(dm.DMA, 0xDF)
    assert b.dma.active and b.dma.page == 0xDF
    await b.wait_ticks(3)
    while b.dma.active:
        await b.edge()
    await b.wait_ticks(1)
    offsets = [offset for _, offset, _ in b.writes]
    assert offsets == [offset for _, offset in b.expected_writes], 'restart schedule must follow the model'
    assert offsets[-160:] == list(range(160)) and offsets[0] == 0, (len(offsets), offsets[:4])
    active_span = [tick for tick, active in b.active_log if active]
    assert active_span == list(range(first_tick + 2, restart_tick + 2 + 160)), (
        active_span[0], active_span[-1], first_tick, restart_tick)
    compare_bytes(await b.oam_image(), dm.transfer_image(b.memory, 0xDF), 'OAM after a restart during a transfer')
    assert await b.read(dm.DMA) == 0xDF

    await b.settle_after_tick(5)
    b.writes.clear()
    b.active_log.clear()
    first_tick = b.ticks
    await b.write(dm.DMA, 0x12)
    await b.wait_ticks(1)
    await b.write(dm.DMA, 0xC3)
    assert not b.dma.active
    while not b.dma.active:
        await b.edge()
    while b.dma.active:
        await b.edge()
    await b.wait_ticks(1)
    offsets = [offset for _, offset, _ in b.writes]
    assert offsets == [0] + list(range(160)), (len(offsets), offsets[:4])
    assert offsets == [offset for _, offset in b.expected_writes[-161:]], 'restart schedule must follow the model'
    active_span = [tick for tick, active in b.active_log if active]
    assert active_span == list(range(first_tick + 2, first_tick + 2 + 161)), (active_span[0], active_span[-1], first_tick)
    compare_bytes(await b.oam_image(), dm.transfer_image(b.memory, 0xC3), 'OAM after a restart while armed')
    assert await b.read(dm.DMA) == 0xC3

    # Why: regression for #442: stalls longer than a machine cycle on ROM bytes must
    # stretch the transfer by the late cycles instead of dropping or
    # repeating a byte; OAM ends up an exact copy and the register, level
    # and CPU restriction behave as usual afterwards.
    await b.settle_after_tick(5)
    b.writes.clear()
    b.monitor_active = False
    await b.write(dm.DMA, 0x23)
    late = 0
    for _ in range(6):
        await b.wait_ticks(b.rng.randrange(8, 20))
        await FallingEdge(dut.clk_i)
        dut.backing_stall_i.value = 1
        stall = b.rng.randrange(30, 80)          # Why: more than one machine cycle.
        for _ in range(stall):
            await b.edge()
        await FallingEdge(dut.clk_i)
        dut.backing_stall_i.value = 0
        late += 1
    start = b.ticks
    while int(dut.dma_active_o.value) or len(b.writes) < 160:
        await b.edge()
        assert b.ticks - start < 400, 'stretched transfer never ended'
    offsets = [offset for _, offset, _ in b.writes]
    assert offsets == list(range(160)), ('each byte exactly once, in order', offsets[:8])
    ticks = [tick for tick, _, _ in b.writes]
    assert ticks[-1] - ticks[0] > 159, 'stalls longer than a cycle must stretch the transfer'
    b.monitor_active = True
    await b.wait_ticks(2)
    b.dma.reset()
    b.dma.page = 0x23
    compare_bytes(await b.oam_image(), image[0x2300:0x23A0], 'OAM after a stalled-backing transfer')
    assert await b.read(dm.DMA) == 0x23

    await b.settle_after_tick(5)
    b.writes.clear()
    await b.write(dm.DMA, 0x12)
    await b.wait_ticks(30)
    copied = len(b.writes)
    await b.reset()
    b.expected_writes = []
    await ReadOnly()
    assert int(dut.dma_active_o.value) == 0
    await b.edge()
    assert await b.read(dm.DMA) == 0
    assert copied >= 20
    compare_bytes((await b.oam_image())[:copied], image[0x1200:0x1200 + copied], 'OAM after reset')
    data, latency = await b.transact(0xC000)
    assert latency == 1 and data == b.memory[0xC000]
    edges = int(get_sim_time(unit='ns')) // CLOCK_PERIOD_NS
    assert edges <= b.bound, f'finite simulation bound exceeded ({edges} edges)'
    cocotb.log.info('OAM DMA checks passed seed=%d edges=%d ticks=%d', seed, edges, b.ticks)
