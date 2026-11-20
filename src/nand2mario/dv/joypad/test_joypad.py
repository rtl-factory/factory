"""Public-port checks of the nand2mario joypad register against the Python
model and literal row/select expectations."""
import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge

from .joypad_model import (A, B, BUTTONS, DOWN, LEFT, P1, RIGHT, SELECT, SELECT_ACTIONS,
                           SELECT_BOTH, SELECT_DIRECTIONS, SELECT_NONE, START, UP, JoypadModel)

CLOCK_PERIOD_NS = 40
DATA_KIND = 1
UNCLAIMED = (0xFF01, 0xFF02, 0xFF04, 0xFF0F, 0xFF7F)


def request_word(valid, write, address, data):
    return (valid << 27) | (write << 26) | (DATA_KIND << 24) | (address << 8) | data


def response_fields(word):
    return (word >> 9) & 1, (word >> 8) & 1, word & 0xFF


def p1_value(select, lines):
    """Literal P1 byte: two high bits, the select bits, the lines active-low."""
    return 0xC0 | (select << 4) | (~lines & 0xF)


class Bench:
    """Drives one system edge at a time: the button byte, an optional P1
    access, and compares the response, selected-active level, interrupt and
    wake pulses with the model."""

    def __init__(self, dut, seed):
        self.dut = dut
        self.rng = random.Random(seed)
        self.bound = int(os.environ['DV_MAX_CYCLES'])
        self.cycles = 0
        self.model = JoypadModel()
        self.buttons = 0
        self.expected_event = False
        self.events = 0

    def idle(self):
        self.dut.buttons_i.value = 0
        self.dut.io_request_i.value = 0
        self.buttons = 0

    async def reset(self, held=0):
        """Reset with ``held`` buttons pressed and accesses presented; both
        have no effect while reset is high."""
        d = self.dut
        await FallingEdge(d.clk_i)
        d.reset_i.value = 1
        for _ in range(2):
            d.buttons_i.value = held
            d.io_request_i.value = request_word(1, 1, P1, 0x30)
            await RisingEdge(d.clk_i)
            await FallingEdge(d.clk_i)
            assert int(d.interrupt_o.value) == 0 and int(d.selected_active_o.value) == 0
        d.io_request_i.value = 0
        d.buttons_i.value = held
        self.buttons = held
        d.reset_i.value = 0
        self.model.reset()
        # The first edge after release samples the held buttons.
        await RisingEdge(d.clk_i)
        self.expected_event = self.model.edge(held)

    async def edge(self, buttons=None, write=None, read=None):
        """One system edge with the given stimulus; returns the read byte."""
        d = self.dut
        await FallingEdge(d.clk_i)
        if buttons is not None:
            self.buttons = buttons
            d.buttons_i.value = buttons
        address = write[0] if write else read
        if write:
            d.io_request_i.value = request_word(1, 1, write[0], write[1])
        elif read is not None:
            d.io_request_i.value = request_word(1, 0, read, 0)
        else:
            d.io_request_i.value = 0
        await ReadOnly()
        interrupt, wake = int(d.interrupt_o.value), int(d.wake_o.value)
        assert interrupt == int(self.expected_event), (
            f'cycle {self.cycles}: interrupt pulse {interrupt} expected {int(self.expected_event)}')
        assert wake == interrupt, 'the wake request is the interrupt pulse'
        self.events += interrupt
        active = int(d.selected_active_o.value)
        assert active == int(self.model.selected_active()), (
            f'cycle {self.cycles}: selected_active {active} expected {int(self.model.selected_active())}')
        selected = int(d.io_selected_o.value)
        valid, fault, data = response_fields(int(d.io_response_o.value))
        assert fault == 0, 'the joypad never faults'
        if address is None:
            assert valid == 0, 'no response without a request'
        else:
            claimed = address == P1
            assert (selected, valid) == (int(claimed), int(claimed)), f'claim of {address:04x}'
            if read is not None and claimed:
                expected = self.model.read()
                assert data == expected, (
                    f'cycle {self.cycles}: P1 read {data:02x} expected {expected:02x}')
        self.cycles += 1
        assert self.cycles <= self.bound, 'finite simulation bound exceeded'
        await RisingEdge(d.clk_i)
        cpu_write = write[1] if write and write[0] == P1 else None
        self.expected_event = self.model.edge(self.buttons, cpu_write)
        return data if read is not None else None

    async def write(self, value, buttons=None):
        await self.edge(buttons=buttons, write=(P1, value))

    async def read(self, buttons=None):
        return await self.edge(buttons=buttons, read=P1)

    async def pulse_count(self, edges):
        """Idle edges; returns how many interrupt pulses they showed."""
        before = self.events
        for _ in range(edges):
            await self.edge()
        return self.events - before


@cocotb.test(timeout_time=20, timeout_unit='ms')
async def joypad_contract(dut):
    seed = int(os.environ['DV_SEED'])
    assert int(os.environ['DV_CLOCK_HZ']) == 25_000_000
    cocotb.log.info('nand2mario joypad seed=%d', seed)
    b = Bench(dut, seed)
    b.idle()
    dut.reset_i.value = 1
    cocotb.start_soon(Clock(dut.clk_i, CLOCK_PERIOD_NS, unit='ns').start())
    await RisingEdge(dut.clk_i)
    await b.reset()

    # Reset state: both rows selected, nothing pressed, upper bits high; the
    # address claim; writes to unclaimed addresses change nothing.
    assert await b.read() == 0xCF, 'reset P1'
    for address in UNCLAIMED:
        await b.edge(read=address)
        await b.edge(write=(address, 0x30))
    assert await b.read() == 0xCF
    assert await b.pulse_count(4) == 0

    # Literal row table: each button alone under every select value reads
    # its line low only when its row is selected; the select bits read back
    # and the other P1 write bits are ignored.
    rows = {RIGHT: (SELECT_DIRECTIONS, 1), LEFT: (SELECT_DIRECTIONS, 2), UP: (SELECT_DIRECTIONS, 4),
            DOWN: (SELECT_DIRECTIONS, 8), A: (SELECT_ACTIONS, 1), B: (SELECT_ACTIONS, 2),
            SELECT: (SELECT_ACTIONS, 4), START: (SELECT_ACTIONS, 8)}
    for button, (row, line) in rows.items():
        for select in (SELECT_BOTH, SELECT_ACTIONS, SELECT_DIRECTIONS, SELECT_NONE):
            await b.write((select << 4) | 0xCF, buttons=0)      # other bits ignored
            await b.edge(buttons=button)
            visible = select in (row, SELECT_BOTH)
            assert await b.read() == p1_value(select, line if visible else 0), (
                'row table', hex(button), select, hex(await b.read()))
    await b.edge(buttons=0)

    # Two rows together: Right and Start under each select value.
    await b.write(SELECT_BOTH << 4, buttons=RIGHT | START)
    assert await b.read() == 0xC6
    await b.write(SELECT_ACTIONS << 4)
    assert await b.read() == 0xD7
    await b.write(SELECT_DIRECTIONS << 4)
    assert await b.read() == 0xEE
    await b.write(SELECT_NONE << 4)
    assert await b.read() == 0xFF
    await b.write(0x00)
    assert await b.read() == 0xC6
    await b.write(0xFF)
    assert await b.read() == 0xFF
    await b.write(0x0F)
    assert await b.read() == 0xC6
    await b.edge(buttons=0)

    # Events: a press on a selected line pulses exactly one cycle after the
    # sampling edge; holding, releasing and unselected presses do not.
    await b.write(SELECT_DIRECTIONS << 4, buttons=0)
    assert await b.pulse_count(2) == 0
    await b.edge(buttons=UP)                       # sampled on this edge
    assert await b.pulse_count(1) == 1, 'pulse one cycle after the press'
    assert await b.pulse_count(8) == 0, 'a held button pulses once'
    assert int(dut.selected_active_o.value) == 1
    await b.edge(buttons=UP | A)                   # action row not selected
    assert await b.pulse_count(3) == 0, 'an unselected press is silent'
    await b.edge(buttons=UP | A | DOWN)            # second selected line
    assert await b.pulse_count(1) == 1
    await b.edge(buttons=A)                        # releases
    assert await b.pulse_count(3) == 0, 'a release is silent'
    assert int(dut.selected_active_o.value) == 0
    # A select write exposing an already pressed button is a fall.
    await b.write(SELECT_ACTIONS << 4)
    assert await b.pulse_count(1) == 1, 'select write exposing A pulses'
    assert await b.read() == 0xDE
    await b.write(SELECT_DIRECTIONS << 4)          # hides A: lines rise
    assert await b.pulse_count(3) == 0
    await b.write(SELECT_BOTH << 4)                # exposes A again
    assert await b.pulse_count(1) == 1
    # Two selected lines falling on one edge make one pulse; a press and an
    # exposing select write on the same edge make one pulse.
    await b.edge(buttons=0)
    await b.write(SELECT_NONE << 4)
    await b.edge(buttons=LEFT | RIGHT)
    assert await b.pulse_count(3) == 0
    await b.write(SELECT_DIRECTIONS << 4, buttons=LEFT | RIGHT | B)
    assert await b.pulse_count(1) == 1
    await b.write(SELECT_BOTH << 4, buttons=LEFT | RIGHT | B | START)
    assert await b.pulse_count(1) == 1
    assert await b.read() == 0xC4

    # Reset clears the sample and selects both rows; a button held through
    # reset is a fall on the first edge after release.
    await b.reset(held=START)
    assert await b.pulse_count(1) == 1, 'held button requests after reset release'
    assert await b.read() == 0xC7
    await b.reset()
    assert await b.pulse_count(3) == 0
    assert await b.read() == 0xCF

    # Seeded random traffic against the model: button changes, P1 and
    # unclaimed writes, reads, with event coverage counted.
    events_before = b.events
    for _ in range(2000):
        roll = b.rng.random()
        buttons = b.rng.randrange(256) if b.rng.random() < 0.3 else None
        if roll < 0.15:
            await b.edge(buttons=buttons, write=(b.rng.choice((P1, P1, 0xFF01, 0xFF04)), b.rng.randrange(256)))
        elif roll < 0.55:
            await b.edge(buttons=buttons, read=P1)
        else:
            await b.edge(buttons=buttons)
    await b.edge(buttons=0)
    assert await b.pulse_count(1) in (0, 1)
    random_events = b.events - events_before
    assert random_events >= 100, f'random phase saw only {random_events} events'
    cocotb.log.info('joypad checks passed seed=%d cycles=%d events=%d random_events=%d',
                    seed, b.cycles, b.events, random_events)
