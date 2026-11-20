"""Public-port checks of the nand2mario serial port against the Python model
and closed-form shift/completion expectations."""
import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge

from .serial_model import BIT_PERIOD, BYTE_PERIOD, REGISTERS, SB, SC, SerialModel

CLOCK_PERIOD_NS = 40
DATA_KIND = 1
UNCLAIMED = (0xFF00, 0xFF03, 0xFF04, 0xFF0F, 0xFF7F)
MACHINE_CYCLE_EDGES = 24


def request_word(valid, write, address, data):
    return (valid << 27) | (write << 26) | (DATA_KIND << 24) | (address << 8) | data


def response_fields(word):
    return (word >> 9) & 1, (word >> 8) & 1, word & 0xFF


def shifted(value, bits):
    """SB after ``bits`` shifts of ``value`` with ones shifted in."""
    return ((value << bits) | ((1 << bits) - 1)) & 0xFF


class Bench:
    """Drives one system edge at a time: tick and optional register access,
    comparing every read, selection, interrupt pulse and observation with
    the model."""

    def __init__(self, dut, seed):
        self.dut = dut
        self.rng = random.Random(seed)
        self.bound = int(os.environ['DV_MAX_CYCLES'])
        self.cycles = 0
        self.model = SerialModel()
        self.expected_byte = None
        self.completions = []

    def idle(self):
        self.dut.shift_clock_i.value = 0
        self.dut.io_request_i.value = 0

    async def reset(self):
        d = self.dut
        await FallingEdge(d.clk_i)
        d.reset_i.value = 1
        for _ in range(2):
            d.shift_clock_i.value = 1
            d.io_request_i.value = request_word(1, 1, self.rng.choice(REGISTERS), 0x81)
            await RisingEdge(d.clk_i)
            await FallingEdge(d.clk_i)
            assert int(d.interrupt_o.value) == 0 and int(d.tx_valid_o.value) == 0
        self.idle()
        d.reset_i.value = 0
        self.model.reset()
        await RisingEdge(d.clk_i)
        self.expected_byte = None

    async def edge(self, tick=False, write=None, read=None):
        """One system edge with the given stimulus; returns the read byte."""
        d = self.dut
        await FallingEdge(d.clk_i)
        # The bench plays the timer: a free-running divider whose bit 6
        # falling edge is the shift clock pulse on every 128th tick.
        d.shift_clock_i.value = int(tick and self.model.shift_clock)
        address = write[0] if write else read
        if write:
            d.io_request_i.value = request_word(1, 1, write[0], write[1])
        elif read is not None:
            d.io_request_i.value = request_word(1, 0, read, 0)
        else:
            d.io_request_i.value = 0
        await ReadOnly()
        interrupt, tx_valid = int(d.interrupt_o.value), int(d.tx_valid_o.value)
        expected = int(self.expected_byte is not None)
        assert interrupt == expected and tx_valid == expected, (
            f'cycle {self.cycles}: interrupt {interrupt} tx_valid {tx_valid} expected {expected}')
        if tx_valid:
            data = int(d.tx_data_o.value)
            assert data == self.expected_byte, f'cycle {self.cycles}: tx_data {data:02x} expected {self.expected_byte:02x}'
            self.completions.append((self.cycles, data))
        selected = int(d.io_selected_o.value)
        valid, fault, data = response_fields(int(d.io_response_o.value))
        assert fault == 0, 'the serial port never faults'
        if address is None:
            assert valid == 0, 'no response without a request'
        else:
            claimed = address in REGISTERS
            assert (selected, valid) == (int(claimed), int(claimed)), f'claim of {address:04x}'
            if read is not None and claimed:
                expected = self.model.read(read)
                assert data == expected, (
                    f'cycle {self.cycles}: read {read:04x} observed {data:02x} expected {expected:02x}')
        self.cycles += 1
        assert self.cycles <= self.bound, 'finite simulation bound exceeded'
        await RisingEdge(d.clk_i)
        self.expected_byte = self.model.edge(tick, write)
        return data if read is not None else None

    async def ticks(self, count, spacing=1):
        """``count`` machine-cycle ticks, each preceded by ``spacing - 1`` idle edges."""
        for _ in range(count):
            for _ in range(spacing - 1):
                await self.edge()
            await self.edge(tick=True)

    async def write(self, address, value, tick=False):
        await self.edge(tick=tick, write=(address, value))

    async def read(self, address, tick=False):
        return await self.edge(tick=tick, read=address)

    async def start(self, byte, control=0x81):
        # The shift clock is free running; align it so the first shift
        # lands BIT_PERIOD ticks after the SC write.
        while self.model.prescale:
            await self.edge(tick=True)
        await self.write(SB, byte)
        await self.write(SC, control)


@cocotb.test(timeout_time=60, timeout_unit='ms')
async def serial_contract(dut):
    seed = int(os.environ['DV_SEED'])
    assert int(os.environ['DV_CLOCK_HZ']) == 25_000_000
    cocotb.log.info('nand2mario serial seed=%d', seed)
    b = Bench(dut, seed)
    b.idle()
    dut.reset_i.value = 1
    cocotb.start_soon(Clock(dut.clk_i, CLOCK_PERIOD_NS, unit='ns').start())
    await RisingEdge(dut.clk_i)
    await b.reset()

    # Reset values, the address claim, and the SC read mask.
    assert (await b.read(SB), await b.read(SC)) == (0x00, 0x7E), 'reset values'
    for address in UNCLAIMED:
        await b.read(address)
        await b.write(address, 0x81)
    assert (await b.read(SB), await b.read(SC)) == (0x00, 0x7E)
    await b.write(SC, 0x7E)
    assert await b.read(SC) == 0x7E, 'bits [6:1] are not stored'
    await b.write(SC, 0x01)
    assert await b.read(SC) == 0x7F, 'clock select alone transfers nothing'
    await b.ticks(2 * BYTE_PERIOD)
    assert (await b.read(SB), await b.read(SC)) == (0x00, 0x7F)
    await b.write(SC, 0x00)
    await b.write(SB, 0x3C)
    assert (await b.read(SB), await b.read(SC)) == (0x3C, 0x7E)

    # Closed-form transfer: one shift every 128 ticks, ones shifted in, the
    # eighth shift completes 1024 ticks after the SC write with one pulse,
    # the transmitted byte, SC bit 7 clear and SB all ones.
    await b.start(0xA5)
    assert (await b.read(SB), await b.read(SC)) == (0xA5, 0xFF)
    for k in range(1, 8):
        await b.ticks(BIT_PERIOD - 1)                    # reads are not ticks
        assert await b.read(SB) == shifted(0xA5, k - 1), ('before shift', k)
        await b.ticks(1)
        assert await b.read(SB) == shifted(0xA5, k), ('after shift', k)
        assert await b.read(SC) == 0xFF
    assert len(b.completions) == 0
    await b.ticks(BIT_PERIOD - 1)
    assert len(b.completions) == 0, 'no completion before the 1024th tick'
    await b.ticks(1)
    await b.edge()                                   # the pulse cycle
    assert len(b.completions) == 1 and b.completions[0][1] == 0xA5, b.completions
    assert (await b.read(SB), await b.read(SC)) == (0xFF, 0x7F)
    await b.ticks(2 * BYTE_PERIOD)
    assert len(b.completions) == 1, 'a completed transfer does not repeat'
    assert int(dut.tx_data_o.value) == 0xA5, 'tx_data holds the last byte'

    # Idle edges do not advance; only ticks do. The real 24-edge spacing
    # gives the same 1024-tick byte.
    await b.start(0x5A)
    for _ in range(3 * BIT_PERIOD):
        await b.edge()
    assert await b.read(SB) == 0x5A, 'idle edges do not shift'
    await b.ticks(BYTE_PERIOD - 1, spacing=MACHINE_CYCLE_EDGES)
    assert len(b.completions) == 1
    await b.ticks(1, spacing=MACHINE_CYCLE_EDGES)
    await b.edge()
    assert len(b.completions) == 2 and b.completions[1][1] == 0x5A

    # External clock: the enable stays set, nothing shifts, software clears it.
    await b.start(0x69, 0x80)
    await b.ticks(2 * BYTE_PERIOD)
    assert (await b.read(SB), await b.read(SC)) == (0x69, 0xFE), 'external clock waits'
    assert len(b.completions) == 2
    await b.write(SC, 0x00)
    assert (await b.read(SB), await b.read(SC)) == (0x69, 0x7E)
    await b.ticks(BYTE_PERIOD)
    assert len(b.completions) == 2

    # Cancel mid-transfer: three shifts happened, no completion; a restart
    # mid-transfer completes eight shift clocks after the second write with the bits
    # shifted from the restart; an SB write mid-transfer replaces the value.
    await b.start(0xA5)
    await b.ticks(3 * BIT_PERIOD + 5)
    await b.write(SC, 0x01)
    assert (await b.read(SB), await b.read(SC)) == (shifted(0xA5, 3), 0x7F)
    await b.ticks(2 * BYTE_PERIOD)
    assert len(b.completions) == 2, 'a cancelled transfer never completes'
    await b.start(0x0F)
    await b.ticks(5 * BIT_PERIOD + 7)
    await b.write(SC, 0x81)                          # restart with SB = shifted(0x0F, 5)
    restart_value = shifted(0x0F, 5)
    assert await b.read(SB) == restart_value
    # The restart clears the bit count but not the free-running clock, so
    # the byte completes 7 ticks sooner than a phase-aligned start.
    await b.ticks(BYTE_PERIOD - 7 - 1)
    assert len(b.completions) == 2
    await b.ticks(1)
    await b.edge()
    assert len(b.completions) == 3 and b.completions[2][1] == restart_value
    await b.start(0xF0)
    await b.ticks(2 * BIT_PERIOD)
    await b.write(SB, 0x0F)                          # the remaining six bits come from 0x0F
    await b.ticks(6 * BIT_PERIOD)
    await b.edge()
    assert len(b.completions) == 4 and b.completions[3][1] == ((0xF0 >> 6) << 6 | (0x0F >> 2)) & 0xFF
    # An SB write on the shift tick wins; the shifted-out bit was the old bit 7.
    await b.start(0x80)
    await b.ticks(BIT_PERIOD - 1)
    await b.write(SB, 0x00, tick=True)               # shift tick: old bit 7 (1) goes out
    assert await b.read(SB) == 0x00
    await b.ticks(7 * BIT_PERIOD)
    await b.edge()
    assert len(b.completions) == 5 and b.completions[4][1] == 0x80
    # An SC write on the completing tick: the completion still happens and
    # the written control bits replace the cleared enable (restart).
    await b.start(0x81)
    await b.ticks(BYTE_PERIOD - 1)
    await b.write(SC, 0x81, tick=True)
    await b.edge()
    assert len(b.completions) == 6 and b.completions[5][1] == 0x81
    assert await b.read(SC) == 0xFF, 'the same-edge SC write restarts'
    await b.write(SC, 0x00)

    # Reset mid-transfer cancels it.
    await b.start(0x3C)
    await b.ticks(4 * BIT_PERIOD)
    await b.reset()
    assert (await b.read(SB), await b.read(SC)) == (0x00, 0x7E)
    await b.ticks(BYTE_PERIOD)
    assert len(b.completions) == 6

    # Seeded random traffic against the model, then steered transfer
    # episodes with a perturbation each, counting completions.
    for _ in range(1500):
        roll = b.rng.random()
        tick = b.rng.random() < 0.5
        if roll < 0.1:
            await b.edge(tick=tick, write=(b.rng.choice((SB, SC, SC, 0xFF00, 0xFF03)), b.rng.randrange(256)))
        elif roll < 0.5:
            await b.edge(tick=tick, read=b.rng.choice(REGISTERS))
        else:
            await b.edge(tick=tick)
    await b.write(SC, 0x00)
    await b.ticks(1)
    completions_before = len(b.completions)
    kinds = ('none', 'sb_write', 'cancel', 'restart')
    counts = dict.fromkeys(kinds, 0)
    for episode in range(12):
        kind = kinds[episode % len(kinds)]
        byte = b.rng.randrange(256)
        spacing = b.rng.choice((1, 2, 3))
        await b.start(byte)
        await b.ticks(b.rng.randrange(1, BYTE_PERIOD - 1), spacing)
        if kind == 'sb_write':
            await b.write(SB, b.rng.randrange(256))
        elif kind == 'cancel':
            await b.write(SC, b.rng.choice((0x00, 0x01, 0x80)))
        elif kind == 'restart':
            await b.write(SC, 0x81)
        await b.ticks(BYTE_PERIOD + 1, spacing)
        await b.edge()
        for address in REGISTERS:
            await b.read(address)
        counts[kind] += 1
        await b.write(SC, 0x00)
    completed = len(b.completions) - completions_before
    assert completed == 9, f'episodes completed {completed} transfers, expected 9 (all but the cancels)'
    assert all(count == 3 for count in counts.values()), counts
    cocotb.log.info('serial checks passed seed=%d cycles=%d completions=%d', seed, b.cycles, len(b.completions))
