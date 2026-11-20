"""Public-port checks of the nand2mario timer against the Python model and
closed-form rate expectations."""
import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge

from .timer_model import DIV, PERIOD, REGISTERS, SELECT_BIT, TAC, TIMA, TMA, TimerModel

CLOCK_PERIOD_NS = 40
DATA_KIND = 1
UNCLAIMED = (0xFF00, 0xFF03, 0xFF08, 0xFF0F, 0xFF7F)


def request_word(valid, write, address, data):
    return (valid << 27) | (write << 26) | (DATA_KIND << 24) | (address << 8) | data


def response_fields(word):
    return (word >> 9) & 1, (word >> 8) & 1, word & 0xFF


class Bench:
    """Drives one system edge at a time: tick, optional register access and
    STOP divider reset, and compares every read, selection and interrupt
    pulse with the model."""

    def __init__(self, dut, seed):
        self.dut = dut
        self.rng = random.Random(seed)
        self.bound = int(os.environ['DV_MAX_CYCLES'])
        self.cycles = 0
        self.model = TimerModel()
        self.expected_interrupt = False
        self.interrupts = 0

    def idle(self):
        self.dut.machine_cycle_enable_i.value = 0
        self.dut.divider_reset_i.value = 0
        self.dut.io_request_i.value = 0

    async def reset(self):
        d = self.dut
        await FallingEdge(d.clk_i)
        d.reset_i.value = 1
        for _ in range(2):
            # Accesses and ticks during reset have no effect.
            d.machine_cycle_enable_i.value = 1
            d.io_request_i.value = request_word(1, 1, self.rng.choice(REGISTERS), self.rng.randrange(256))
            await RisingEdge(d.clk_i)
            await FallingEdge(d.clk_i)
        self.idle()
        d.reset_i.value = 0
        self.model.reset()
        self.expected_interrupt = False

    async def edge(self, tick=False, write=None, read=None, divider_reset=False):
        """One system edge with the given stimulus; returns the read byte."""
        d = self.dut
        await FallingEdge(d.clk_i)
        d.machine_cycle_enable_i.value = int(tick)
        d.divider_reset_i.value = int(divider_reset)
        address = write[0] if write else read
        if write:
            d.io_request_i.value = request_word(1, 1, write[0], write[1])
        elif read is not None:
            d.io_request_i.value = request_word(1, 0, read, 0)
        else:
            d.io_request_i.value = 0
        await ReadOnly()
        interrupt = int(d.interrupt_o.value)
        assert interrupt == int(self.expected_interrupt), (
            f'cycle {self.cycles}: interrupt pulse {interrupt} expected {int(self.expected_interrupt)}')
        self.interrupts += interrupt
        selected = int(d.io_selected_o.value)
        valid, fault, data = response_fields(int(d.io_response_o.value))
        assert fault == 0, 'the timer never faults'
        if address is None:
            assert valid == 0, 'no response without a request'
        else:
            claimed = address in REGISTERS
            assert selected == int(claimed), f'selection of {address:04x} is {selected}'
            assert valid == int(claimed), f'response valid for {address:04x} is {valid}'
            if read is not None and claimed:
                expected = self.model.read(read)
                assert data == expected, (
                    f'cycle {self.cycles}: read {read:04x} observed {data:02x} expected {expected:02x}')
        self.cycles += 1
        assert self.cycles <= self.bound, 'finite simulation bound exceeded'
        await RisingEdge(d.clk_i)
        self.expected_interrupt = self.model.edge(tick, write, divider_reset)
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

    async def registers(self):
        return [await self.read(address) for address in REGISTERS]


@cocotb.test(timeout_time=60, timeout_unit='ms')
async def timer_contract(dut):
    seed = int(os.environ['DV_SEED'])
    assert int(os.environ['DV_CLOCK_HZ']) == 25_000_000
    cocotb.log.info('nand2mario timer seed=%d', seed)
    b = Bench(dut, seed)
    b.idle()
    dut.reset_i.value = 1
    cocotb.start_soon(Clock(dut.clk_i, CLOCK_PERIOD_NS, unit='ns').start())
    await RisingEdge(dut.clk_i)
    await b.reset()

    # Reset values (DIV $AB: the DMG boot-ROM handoff divider), address
    # claim and the open interrupt line.
    assert await b.registers() == [0xAB, 0x00, 0x00, 0xF8], 'reset values'
    for address in UNCLAIMED:
        assert await b.read(address) is not None   # unclaimed: selection and valid checked inside
    assert await b.read(TAC) == 0xF8

    # Rates from the closed form: after a DIV reset the first falling edge of
    # the selected bit comes after one full period, so k periods give TIMA=k.
    for select, period in PERIOD.items():
        await b.write(DIV, 0x5A)
        await b.write(TIMA, 0x00)
        await b.write(TAC, 0x04 | select)
        for k in range(1, 4):
            await b.ticks(period)
            assert await b.read(TIMA) == k, ('rate', select, k)
        await b.write(TAC, 0x00)
    # The same rate with the real 23/24-edge tick cadence.
    await b.write(DIV, 0)
    await b.write(TIMA, 0)
    await b.write(TAC, 0x05)
    await b.ticks(PERIOD[1] * 5, spacing=24)
    assert await b.read(TIMA) == 5
    await b.write(TAC, 0)

    # DIV counts every 64 ticks and wraps after 256 increments.
    await b.write(DIV, 0)
    await b.ticks(63)
    assert await b.read(DIV) == 0
    await b.ticks(1)
    assert await b.read(DIV) == 1
    await b.ticks(64 * 254)
    assert await b.read(DIV) == 255
    await b.ticks(64)
    assert await b.read(DIV) == 0

    # Write-induced falling edges: a DIV write while the selected bit is high
    # increments TIMA; while it is low it does not. Disabling TAC or changing
    # the selected bit from a high to a low bit also increments.
    await b.write(DIV, 0)
    await b.write(TIMA, 0x10)
    await b.write(TAC, 0x05)             # bit 1 of the divider
    await b.ticks(2)                     # divider 2: bit 1 high
    await b.write(DIV, 0)
    assert await b.read(TIMA) == 0x11
    await b.ticks(1)                     # divider 1: bit 1 low
    await b.write(DIV, 0)
    assert await b.read(TIMA) == 0x11
    await b.ticks(2)
    await b.write(TAC, 0x01)             # disable with the bit high
    assert await b.read(TIMA) == 0x12
    await b.write(TAC, 0x05)             # re-enable with bit 1 high: a rising edge
    assert await b.read(TIMA) == 0x12
    await b.ticks(1)                     # divider 3: bit 1 still high
    await b.write(TAC, 0x06)             # select bit 3 (low): falling edge
    assert await b.read(TIMA) == 0x13
    await b.write(TAC, 0x04)             # bit 3 low -> bit 7 low: no edge
    assert await b.read(TIMA) == 0x13
    await b.write(TAC, 0x00)

    # Overflow: $FF -> $00 for exactly one machine cycle, then TMA is loaded
    # and the interrupt pulses once, visible for one system cycle.
    await b.write(DIV, 0)
    await b.write(TMA, 0xA5)
    await b.write(TIMA, 0xFE)
    await b.write(TAC, 0x05)
    # Each read samples the state after k ticks, then ticks once more; the
    # selected bit falls at ticks 4, 8 and 12.
    expected = [0xFE] * 4 + [0xFF] * 4 + [0x00] + [0xA5] * 3 + [0xA6] * 2
    observed = []
    before = b.interrupts
    for _ in expected:
        observed.append(await b.read(TIMA, tick=True))
    assert observed == expected, ('overflow sequence', observed, expected)
    assert b.interrupts - before == 1, 'exactly one interrupt pulse per reload'
    await b.write(TAC, 0x00)

    async def arm(tma=0xA5):
        """TIMA=$FE at period 4 with the divider reset: the overflow tick is
        the eighth tick; after it the reload is pending."""
        await b.write(TAC, 0x00)
        await b.ticks(2)                 # leave any reload hold from the previous case
        await b.write(DIV, 0)
        await b.write(TMA, tma)
        await b.write(TIMA, 0xFE)
        await b.write(TAC, 0x05)
        await b.ticks(8)
        assert await b.read(TIMA) == 0x00

    # A TIMA write while the reload is pending cancels it: no reload, no pulse.
    await arm()
    await b.write(TIMA, 0x12)
    before = b.interrupts
    await b.ticks(2)
    assert await b.read(TIMA) == 0x12 and b.interrupts == before
    # A TIMA write on the reload tick itself is ignored; the reload wins.
    await arm()
    await b.write(TIMA, 0x12, tick=True)
    assert await b.read(TIMA) == 0xA5 and b.interrupts == before + 1
    # A TIMA write in the machine cycle after the reload is ignored.
    await arm()
    await b.ticks(1)
    await b.write(TIMA, 0x12)
    assert await b.read(TIMA) == 0xA5
    await b.ticks(1)
    await b.write(TIMA, 0x34)            # the hold is over: the write lands
    assert await b.read(TIMA) == 0x34
    # A TMA write in that same cycle lands in TIMA too; a TMA write while
    # the reload is pending supplies the reloaded value.
    await arm()
    await b.ticks(1)
    await b.write(TMA, 0x77)
    assert (await b.registers())[1:3] == [0x77, 0x77]
    await arm()
    await b.write(TMA, 0x66)
    await b.ticks(1)
    assert await b.read(TIMA) == 0x66
    # TMA=$FF: every falling edge overflows and reloads again.
    await arm(tma=0xFF)
    before = b.interrupts
    await b.ticks(1)                     # reload -> $FF
    await b.ticks(4 * 3 + 3)             # three more overflow/reload pairs
    assert b.interrupts - before == 4
    await b.write(TAC, 0x00)

    # The CPU's STOP divider reset zeroes the divider on its tick and counts
    # a falling edge like a DIV write.
    await b.write(DIV, 0)
    await b.write(TIMA, 0x20)
    await b.write(TAC, 0x05)
    await b.ticks(2)
    await b.edge(tick=True, divider_reset=True)
    assert (await b.registers())[0:2] == [0x00, 0x21]
    await b.ticks(63)
    assert await b.read(DIV) == 0        # the divider restarted from zero
    await b.ticks(1)
    assert await b.read(DIV) == 1

    # Reset while a reload is pending cancels it and returns every register
    # to its reset value (three ticks leave DIV at $AB).
    await arm()
    await b.reset()
    before = b.interrupts
    await b.ticks(3)
    assert await b.registers() == [0xAB, 0, 0, 0xF8] and b.interrupts == before

    # Seeded random traffic: dense and sparse ticks, writes to every claimed
    # and some unclaimed address (coinciding with ticks half the time), STOP
    # resets on ticks, and reads of all registers between them. Unbiased
    # writes keep TIMA from reaching $FF, so overflow coverage comes from the
    # steered episodes below, not from this phase.
    for _ in range(2000):
        roll = b.rng.random()
        tick = b.rng.random() < 0.5
        if roll < 0.15:
            address = b.rng.choice(REGISTERS + UNCLAIMED[1:3])
            await b.write(address, b.rng.randrange(256), tick=tick)
        elif roll < 0.17:
            await b.edge(tick=True, divider_reset=True)
        elif roll < 0.6:
            await b.read(b.rng.choice(REGISTERS), tick=tick)
        else:
            await b.edge(tick=tick)

    # Seeded overflow episodes: each arms a random rate, TMA and near-$FF
    # TIMA, runs random-density ticks with reads, DIV writes, STOP resets and
    # selection changes until the model reports the pending reload, then
    # applies one of six write placements around the reload (round robin, so
    # every placement is covered for any seed) and compares the aftermath.
    # Coverage is measured inside this phase only.
    before_random = b.interrupts
    coverage = {name: 0 for name in ('overflow', 'none', 'tima_cancel', 'tima_on_reload', 'tima_in_hold',
                                     'tma_in_hold', 'tma_pending', 'div_write_increment', 'stop_reset_increment',
                                     'select_change_increment', 'reload_from_ff')}
    actions = ('none', 'tima_cancel', 'tima_on_reload', 'tima_in_hold', 'tma_in_hold', 'tma_pending')
    for episode in range(36):
        action = actions[episode % len(actions)]
        select = b.rng.randrange(4)
        density = 1.0 if select == 0 else b.rng.choice((1.0, 0.5))
        tma = b.rng.choice((0xFF, b.rng.randrange(256)))
        await b.write(TAC, 0x00)
        await b.ticks(2)                 # leave any reload hold from the previous episode
        if b.rng.random() < 0.5:
            await b.write(DIV, b.rng.randrange(256), tick=b.rng.random() < 0.5)
        await b.write(TMA, tma)
        await b.write(TIMA, b.rng.randrange(0xFC, 0x100))
        await b.write(TAC, 0x04 | select, tick=b.rng.random() < 0.5)
        # One to three perturbations per episode of one kind (DIV write, STOP
        # divider reset or selection change, round robin), injected mostly
        # while the selected bit is high so they produce write-induced
        # increments; each delays the overflow by at most one period of the
        # slowest rate, so the budget is an upper bound on the edges needed.
        kind = ('div', 'stop', 'select')[episode % 3]
        perturbations = b.rng.randrange(1, 4)
        want_high = b.rng.random() < 0.7
        budget = 2 * (5 + perturbations) * PERIOD[0] + 300
        while b.model.reload != 'pending':
            budget -= 1
            assert budget > 0, ('episode did not reach overflow', episode, select)
            tick = b.rng.random() < density
            selected_high = b.model.selected(b.model.divider, b.model.tac)
            if perturbations and selected_high == want_high and b.rng.random() < 0.1:
                perturbations -= 1
                if kind == 'div':
                    coverage['div_write_increment'] += selected_high
                    await b.write(DIV, b.rng.randrange(256), tick=tick)
                elif kind == 'stop':
                    coverage['stop_reset_increment'] += selected_high
                    await b.edge(tick=True, divider_reset=True)
                else:
                    advanced = (b.model.divider + (1 if tick else 0)) % (1 << 14)
                    low = [x for x in range(4) if not b.model.selected(advanced, 0x04 | x)]
                    new_select = b.rng.choice(low) if selected_high and low else b.rng.randrange(4)
                    coverage['select_change_increment'] += int(
                        b.model.selected(advanced, b.model.tac) and not b.model.selected(advanced, 0x04 | new_select))
                    await b.write(TAC, 0x04 | new_select, tick=tick)
            elif b.rng.random() < 0.25:
                await b.read(b.rng.choice(REGISTERS), tick=tick)
            else:
                await b.edge(tick=tick)
        coverage['overflow'] += 1
        coverage['reload_from_ff'] += int(b.model.tma == 0xFF)
        value = b.rng.randrange(256)
        if action == 'none':
            await b.ticks(1)
        elif action == 'tima_cancel':
            await b.write(TIMA, value)           # cycle A: cancels the reload
        elif action == 'tima_on_reload':
            await b.write(TIMA, value, tick=True)  # reload tick: ignored
        elif action == 'tima_in_hold':
            await b.ticks(1)
            await b.write(TIMA, value)           # cycle B: ignored
        elif action == 'tma_in_hold':
            await b.ticks(1)
            await b.write(TMA, value)            # cycle B: lands in TIMA too
        else:
            await b.write(TMA, value)            # cycle A: supplies the reloaded value
            await b.ticks(1)
        coverage[action] += 1
        for _ in range(b.rng.randrange(2, 6)):
            await b.read(b.rng.choice(REGISTERS), tick=b.rng.random() < density)
        await b.registers()
    random_interrupts = b.interrupts - before_random
    cocotb.log.info('episode coverage: %s random_interrupts=%d', coverage, random_interrupts)
    assert random_interrupts >= 25, f'the episodes must reload: {random_interrupts} interrupts'
    assert all(coverage[name] == 6 for name in actions), coverage
    assert coverage['overflow'] == 36 and coverage['reload_from_ff'] >= 3, coverage
    assert all(coverage[name] >= 3 for name in ('div_write_increment', 'stop_reset_increment',
                                                 'select_change_increment')), coverage
    cocotb.log.info('timer checks passed seed=%d cycles=%d interrupts=%d', seed, b.cycles, b.interrupts)
