"""Public-port library ABI checks against independent bytes, CRC and bank rules.

The source port is deliberately stalled and responds after seeded delays.
Only the DUT owns the active ROM. Tests never write its internal store or
state; all observations are cartridge reads, backing requests and core reset.
"""
import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge, Timer

from . import fixture


def request(address, value=None):
    return (1 << 27) | ((value is not None) << 26) | (1 << 24) | (address << 8) | (value or 0)


class Bench:
    def __init__(self, dut):
        self.d = dut
        self.random = random.Random(int(os.environ['DV_SEED']))
        self.cycles = 0
        self.requests = []
        self.memory = bytearray((i * 13 + (i >> 12)) & 255 for i in range(1 << 20))
        self.menu = fixture.game_image(16, 'GAME MENU')
        data = fixture.library_bytes(self.menu)
        self.memory[:len(data)] = data
        self.pending = None
        self.responses = 0
        self.forced_delay = None
        self.reset_edges = 0
        self.was_reset = True
        self.last_copy_response = None
        self.reset_dwell = []

    async def backing(self):
        while True:
            await FallingEdge(self.d.clk_i)
            self.cycles += 1
            assert self.cycles <= int(os.environ['DV_MAX_CYCLES']), 'finite cycle bound'
            due = self.pending is not None and self.pending[1] == 0
            ready = self.pending is None and self.random.randrange(5) != 0
            self.d.backing_ready_i.value = int(ready)
            self.d.backing_response_i.value = ((1 << 8) | self.memory[self.pending[0]]) if due else 0
            await Timer(1, unit='ns')
            word = int(self.d.backing_request_o.value)
            take = ready and bool(word >> 31)
            if take:
                assert not word & (3 << 29), 'engine source request must be a ROM read'
                address = (word >> 8) & 0x1fffff
                assert address < len(self.memory), ('source address range', address)
            await RisingEdge(self.d.clk_i)
            is_reset = bool(int(self.d.core_reset_o.value))
            if is_reset and not self.was_reset:
                self.reset_edges += 1
            if due and is_reset:
                self.last_copy_response = self.cycles
            if self.was_reset and not is_reset and self.last_copy_response is not None:
                dwell = self.cycles - self.last_copy_response
                assert dwell >= 8192, ('VRAM reset initialization dwell', dwell)
                self.reset_dwell.append(dwell)
            self.was_reset = is_reset
            if due:
                self.pending = None
                self.responses += 1
            elif self.pending is not None:
                self.pending = (self.pending[0], self.pending[1] - 1)
            if take:
                delay = self.forced_delay if self.forced_delay is not None else self.random.randrange(3)
                self.pending = (address, delay)
                self.requests.append(address)

    async def access(self, address, value=None):
        await FallingEdge(self.d.clk_i)
        self.d.request_i.value = request(address, value)
        assert int(self.d.ready_o.value), 'active store should accept without wait'
        await RisingEdge(self.d.clk_i)
        await ReadOnly()
        response = int(self.d.response_o.value)
        await FallingEdge(self.d.clk_i)
        self.d.request_i.value = 0
        for _ in range(4):
            if response >> 9:
                assert not response & (1 << 8), 'cartridge bus fault'
                return response & 255
            await RisingEdge(self.d.clk_i)
            await ReadOnly()
            response = int(self.d.response_o.value)
        raise AssertionError('cartridge response bound')

    async def idle(self, maximum=500_000):
        start = self.cycles
        while True:
            await Timer(4000, unit='ns')
            await ReadOnly()
            if not int(self.d.core_reset_o.value):
                if not (await self.access(0xa000)) & 0x80:
                    return
            assert self.cycles - start <= maximum, 'library completion bound'

    async def window(self, bank):
        await self.access(0x2000, bank)
        await self.idle()
        assert (await self.access(0xa001)) == bank & 63
        assert (await self.access(0xa000)) & 0x60 == 0x60

    async def select(self, index):
        await self.access(0x6000, index)
        await self.idle()


@cocotb.test()
async def library_contract(dut):
    assert int(os.environ['DV_CLOCK_HZ']) == 25_000_000
    b = Bench(dut)
    dut.reset_i.value = 1
    dut.host_hold_i.value = 0
    dut.backing_initialized_i.value = 0
    dut.request_i.value = 0
    dut.backing_ready_i.value = 0
    dut.backing_response_i.value = 0
    Clock(dut.clk_i, 40, unit='ns', impl='gpi').start()
    task = cocotb.start_soon(b.backing())
    try:
        await Timer(200, unit='ns')
        dut.reset_i.value = 0
        await Timer(400, unit='ns')
        assert int(dut.core_reset_o.value) and not b.requests, 'wait for source initialization'
        dut.backing_initialized_i.value = 1
        await b.idle()
        assert (await b.access(0xa003)) == 0xff, 'boot must preserve first-boot sentinel'
        assert (await b.access(0xa002)) == 1
        assert b.requests[:32] == list(range(0x88200, 0x88220)), 'menu entry fetch order'
        assert b.requests[32:] == list(range(0x80000, 0x88000)), 'whole menu copy and CRC order'
        for address in (0, 0x100, 0x134, 0x147, 0x150, 0x3fff):
            assert await b.access(address) == b.menu[address]
        for address in (0xa004, 0xa100, 0xbfff):
            assert await b.access(address) == 0xff
        await b.window(0x52)
        assert await b.access(0x4000) == b.memory[18 * 16384]
        old = await b.access(0x4567)
        b.memory[18 * 16384 + 0x567] ^= 0xff
        assert await b.access(0x4567) == old
        await b.window(18)
        assert await b.access(0x4567) == old ^ 0xff
        await b.window(255)
        assert await b.access(0x7fff) == b.memory[0xfffff]
        await b.access(0x2000, 34)
        assert await b.access(0x4000) == 0xff
        assert await b.access(0x100) == b.menu[0x100], "fixed menu remains readable during fill"
        await b.access(0x2000, 7)
        await b.access(0x6000, 1)
        await b.idle()
        assert await b.access(0xa001) == 34
        assert await b.access(0xa003) == 0xff
        assert await b.access(0x4000) == 1
        before = len(b.requests)
        for index in (17, 255):
            await b.access(0x6000, index)
        assert len(b.requests) == before, 'out-of-range selection must be inert'
        for index in (3, 4):  # Why: slot 3 is absent and slot 4 has an illegal length.
            await b.select(index)
            assert await b.access(0xa002) == 2
            assert await b.access(0xa003) == index
            assert (await b.access(0xa000)) & 0x40, 'refusal must retain snapshot'
        b.memory[0x88000 + 2 * 32 + 1] = 99
        await b.select(2)
        assert await b.access(0xa002) == 2
        b.memory[0x88000 + 2 * 32 + 1] = 1
        b.memory[0x88000 + 2 * 32 + 24] = 1
        await b.select(2)
        assert await b.access(0xa002) == 2
        b.memory[0x88000 + 2 * 32 + 24] = 0
        # Why: the RTL must independently reject a two-slot image at slot 15;
        # a host packer check alone cannot protect against malformed bytes.
        entry15 = 0x88000 + 15 * 32
        previous15 = bytes(b.memory[entry15:entry15 + 32])
        b.memory[entry15:entry15 + 32] = fixture.pack_entry(dict(valid=1, profile=3, length=65536,
                                                               crc32=0, title=bytes(16)))
        await b.select(15)
        assert await b.access(0xa002) == 2
        b.memory[entry15:entry15 + 32] = previous15
        await b.access(0x6000, 1)
        await Timer(15_000_000, unit='ns')
        assert not int(dut.core_reset_o.value)
        assert await b.access(0xa000) == 0xff
        assert await b.access(0x134) == ord('E')
        # Why: a refused menu return must leave a running game and its active
        # bytes usable. Observe reset events, not merely the final low level.
        b.memory[0x88200] = 0
        resets_before = b.reset_edges
        await b.access(0x6000, 16)
        await Timer(20_000, unit='ns')
        assert not int(dut.core_reset_o.value) and b.reset_edges == resets_before
        assert await b.access(0x134) == ord('E')
        b.memory[0x88200] = 1
        await b.access(0x6000, 16)
        await b.idle()
        assert await b.access(0xa003) == 1, 'return retains last selected index'
        assert await b.access(0xa002) == 1
        await b.access(0x6000, 5)
        await Timer(24_000_000, unit='ns')
        assert not int(dut.core_reset_o.value)
        assert await b.access(0x4000) == 0x18
        await b.access(0x2000, 2)
        assert await b.access(0x4000) == 0x3e
        assert await b.access(0x4001) == 16
        await b.access(0x6000, 16)
        await b.idle()
        assert await b.access(0xa003) == 5
        b.memory[7 * 32768 + 0x700] ^= 1
        await b.access(0x6000, 7)
        await Timer(15_000_000, unit='ns')
        assert int(dut.core_reset_o.value), 'CRC mismatch must keep CPU reset'
        count = len(b.requests)
        await Timer(4000, unit='ns')
        assert count == len(b.requests), 'failed image must not auto-retry'
        dut.host_hold_i.value = 1
        b.memory[7 * 32768 + 0x700] ^= 1  # Why: the host repairs the failed image while held.
        await Timer(400, unit='ns')
        dut.host_hold_i.value = 0
        await b.idle()
        assert await b.access(0xa003) == 7
        assert await b.access(0x134) == ord('G')
        b.forced_delay = 20
        await b.access(0x2000, 1)
        while b.pending is None:
            await Timer(40, unit='ns')
        dut.host_hold_i.value = 1
        await Timer(80, unit='ns')
        dut.host_hold_i.value = 0
        b.forced_delay = None
        await b.idle()
        assert await b.access(0x100) == b.menu[0x100]
        assert await b.access(0xa002) == 1
        assert len(b.reset_dwell) >= 7, b.reset_dwell
        cocotb.log.info('reset dwell clocks=%s (minimum8192)', b.reset_dwell)
        cocotb.log.info('library contract PASS seed=%s cycles=%d source_reads=%d responses=%d',
                         os.environ['DV_SEED'], b.cycles, len(b.requests), b.responses)
    finally:
        task.cancel()
