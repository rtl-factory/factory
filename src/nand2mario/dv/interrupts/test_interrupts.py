"""Public-port checks of the nand2mario interrupt-flag register against the
Python model and a literal per-bit truth table."""
import itertools
import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge

from .interrupts_model import IF, MASK, InterruptsModel

CLOCK_PERIOD_NS = 40
DATA_KIND = 1
UNCLAIMED = (0xFF00, 0xFF04, 0xFF07, 0xFF0E, 0xFF10, 0xFF7F)


def request_word(valid, write, address, data):
    return (valid << 27) | (write << 26) | (DATA_KIND << 24) | (address << 8) | data


def response_fields(word):
    return (word >> 9) & 1, (word >> 8) & 1, word & 0xFF


class Bench:
    def __init__(self, dut, seed):
        self.dut = dut
        self.rng = random.Random(seed)
        self.bound = int(os.environ['DV_MAX_CYCLES'])
        self.cycles = 0
        self.model = InterruptsModel()

    def idle(self):
        self.dut.source_set_i.value = 0
        self.dut.interrupt_ack_i.value = 0
        self.dut.io_request_i.value = 0

    async def reset(self):
        d = self.dut
        await FallingEdge(d.clk_i)
        d.reset_i.value = 1
        for _ in range(2):
            d.source_set_i.value = MASK
            d.io_request_i.value = request_word(1, 1, IF, 0xFF)
            await RisingEdge(d.clk_i)
            await FallingEdge(d.clk_i)
        self.idle()
        d.reset_i.value = 0
        self.model.reset()

    async def edge(self, sources=0, write=None, ack=0, read=None):
        """One system edge: sources, an optional CPU access and an acknowledge.
        Returns the read byte when a read was presented."""
        d = self.dut
        await FallingEdge(d.clk_i)
        d.source_set_i.value = sources
        d.interrupt_ack_i.value = ack
        address = write[0] if write else read
        if write:
            d.io_request_i.value = request_word(1, 1, write[0], write[1])
        elif read is not None:
            d.io_request_i.value = request_word(1, 0, read, 0)
        else:
            d.io_request_i.value = 0
        await ReadOnly()
        flags = int(d.interrupt_flags_o.value)
        assert flags == self.model.flags, f'cycle {self.cycles}: flags {flags:02x} expected {self.model.flags:02x}'
        selected = int(d.io_selected_o.value)
        valid, fault, data = response_fields(int(d.io_response_o.value))
        assert fault == 0
        if address is None:
            assert valid == 0
        else:
            claimed = address == IF
            assert (selected, valid) == (int(claimed), int(claimed)), f'claim of {address:04x}'
            if read is not None and claimed:
                assert data == self.model.read(), f'cycle {self.cycles}: IF read {data:02x} expected {self.model.read():02x}'
        self.cycles += 1
        assert self.cycles <= self.bound, 'finite simulation bound exceeded'
        await RisingEdge(d.clk_i)
        cpu_write = write[1] if write and write[0] == IF else None
        self.model.edge(sources, cpu_write, ack)
        return data if read is not None else None


@cocotb.test(timeout_time=20, timeout_unit='ms')
async def interrupt_flags_contract(dut):
    seed = int(os.environ['DV_SEED'])
    assert int(os.environ['DV_CLOCK_HZ']) == 25_000_000
    cocotb.log.info('nand2mario interrupt flags seed=%d', seed)
    b = Bench(dut, seed)
    b.idle()
    dut.reset_i.value = 1
    cocotb.start_soon(Clock(dut.clk_i, CLOCK_PERIOD_NS, unit='ns').start())
    await RisingEdge(dut.clk_i)
    await b.reset()

    # Ref: Pan Docs "Power Up Sequence": IF is $E1 at DMG boot-ROM handoff (VBlank pending).
    assert await b.edge(read=IF) == 0xE1
    for address in UNCLAIMED:
        await b.edge(read=address)
        await b.edge(write=(address, 0x1F))
    assert await b.edge(read=IF) == 0xE1

    for bit in range(5):
        for stored, source, written, ack in itertools.product((0, 1), (0, 1), (None, 0, 1), (0, 1)):
            others = 0b10101 if bit % 2 else 0b01010
            others &= ~(1 << bit) & MASK
            await b.edge(write=(IF, others | (stored << bit)))
            if written is None:
                write, expected_others = None, others
            else:
                expected_others = (others ^ 0b00111) & ~(1 << bit) & MASK
                write = (IF, expected_others | (written << bit))
            await b.edge(sources=source << bit, write=write, ack=ack << bit)
            if ack:
                expected_bit = 0
            elif written is not None:
                expected_bit = written
            else:
                expected_bit = stored | source
            value = await b.edge(read=IF)
            assert value == 0xE0 | expected_others | (expected_bit << bit), (
                'truth table', bit, stored, source, written, ack, hex(value))

    await b.edge(write=(IF, 0))
    await b.edge(sources=0b10110)
    assert await b.edge(read=IF) == 0xF6
    await b.edge(ack=0b00010)
    assert await b.edge(read=IF) == 0xF4
    await b.edge(write=(IF, 0xFF))
    assert await b.edge(read=IF) == 0xFF
    await b.edge(write=(IF, 0x00))
    assert await b.edge(read=IF) == 0xE0

    await b.edge(sources=MASK)
    await b.reset()
    assert await b.edge(read=IF) == 0xE1

    for _ in range(2000):
        roll = b.rng.random()
        sources = b.rng.randrange(32) if b.rng.random() < 0.3 else 0
        ack = (1 << b.rng.randrange(5)) if b.rng.random() < 0.2 else 0
        if roll < 0.2:
            await b.edge(sources=sources, ack=ack, write=(b.rng.choice((IF, IF, 0xFF0E, 0xFF10)), b.rng.randrange(256)))
        elif roll < 0.6:
            await b.edge(sources=sources, ack=ack, read=IF)
        else:
            await b.edge(sources=sources, ack=ack)
    cocotb.log.info('interrupt flag checks passed seed=%d cycles=%d', seed, b.cycles)
