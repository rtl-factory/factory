"""nand2mario SDRAM controller (adapt-MIT) against the owned device model.

The bench drives the controller's request interface and watches the pins
through the independent Python monitor: the power-up initialization sequence
and its exact clock numbers, CL2 single-beat reads and byte-masked writes at
bank/row/column extremes, the fixed response and ready latencies, refresh
cadence under idle and under traffic, and a randomized phase against a
byte-lane shadow with phase-local coverage. The device model's timing
assertions are active throughout; the second testcase lowers the refresh
cadence to prove the model's deadline check is live (expected fatal).
"""
import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge

from .sdram_pin_monitor import SdramPinMonitor, join, split

CLOCK_PERIOD_NS = 40
INIT_PRECHARGE_CLOCK = 5000
INIT_REFRESH_CLOCKS = [5003 + 4 * k for k in range(8)]
INIT_MODE_CLOCK = 5035
INITIALIZED_CLOCK, IDLE_CLOCK = 5036, 5038
READ_RESPONSE, READ_IDLE, WRITE_RESPONSE, WRITE_IDLE = 9, 10, 10, 11
REFRESH_INTERVAL, REFRESH_DEADLINE, REFRESH_CLOCKS, REFRESH_IDLE_GAP = 160, 195, 5, 162


class Bench:
    def __init__(self, dut, seed):
        self.dut = dut
        self.rng = random.Random(seed)
        self.bound = int(os.environ['DV_MAX_CYCLES'])
        self.clock = -1
        self.monitor = SdramPinMonitor()
        self.initialized_clock = self.idle_clock = None
        self.dqm_before_init = set()
        self.dqm_after_init = set()
        self.shadow = {}

    def idle(self):
        d = self.dut
        d.request_valid_i.value = 0
        d.request_write_i.value = 0
        d.request_address_i.value = 0
        d.request_write_data_i.value = 0
        d.request_byte_enable_i.value = 0

    async def watch(self):
        """Every edge: number the clock that starts here and decode its pins."""
        d = self.dut
        while True:
            await RisingEdge(d.clk_i)
            self.clock += 1
            assert self.clock <= self.bound, 'finite simulation bound exceeded'
            await ReadOnly()
            if int(d.reset_i.value):
                continue
            self.monitor.edge(self.clock, int(d.dram_cke_o.value), int(d.dram_cs_n_o.value),
                              int(d.dram_ras_n_o.value), int(d.dram_cas_n_o.value), int(d.dram_we_n_o.value),
                              int(d.dram_ba_o.value), int(d.dram_addr_o.value), int(d.dram_dq_oe_o.value))
            dqm = (int(d.dram_dqmh_o.value), int(d.dram_dqml_o.value))
            if int(d.initialized_o.value):
                if self.initialized_clock is None:
                    self.initialized_clock = self.clock
                if not int(d.dram_dq_oe_o.value):
                    self.dqm_after_init.add(dqm)
            else:
                self.dqm_before_init.add(dqm)
            if self.idle_clock is None and int(d.idle_o.value):
                self.idle_clock = self.clock

    async def transact(self, write, address, data=0, enables=0b11):
        """Present one request until accepted; return (read word, clocks from
        presentation to acceptance, response latency, ready-return latency)."""
        d = self.dut
        await FallingEdge(d.clk_i)
        d.request_valid_i.value = 1
        d.request_write_i.value = int(write)
        d.request_address_i.value = address
        d.request_write_data_i.value = data
        d.request_byte_enable_i.value = enables
        waited = 0
        while True:
            await ReadOnly()
            if int(d.request_ready_o.value):
                break
            waited += 1
            assert waited <= REFRESH_CLOCKS + WRITE_IDLE, f'request at {address:#09x} never accepted'
            await RisingEdge(d.clk_i)
        await RisingEdge(d.clk_i)
        await ReadOnly()
        # Why: contract clock 0 is the clock the accepting edge ends; the ACTIVATE
        # command is presented in clock 1.
        accepted = self.clock - 1
        await FallingEdge(d.clk_i)
        d.request_valid_i.value = 0
        response = ready = None
        word = None
        for _ in range(WRITE_IDLE + REFRESH_CLOCKS + 2):
            await RisingEdge(d.clk_i)
            await ReadOnly()
            if int(d.response_valid_o.value):
                assert response is None, 'a second response pulse'
                response = self.clock - accepted
                word = int(d.response_data_o.value)
            if ready is None and int(d.idle_o.value):
                ready = self.clock - accepted
            if response is not None and ready is not None:
                break
        assert response is not None and ready is not None, 'response or idle never returned'
        if write:
            for lane in range(2):
                if enables >> lane & 1:
                    self.shadow[address | lane] = (data >> (8 * lane)) & 0xFF
        return word, waited, response, ready

    def expected_word(self, address):
        """Shadow bytes where the bench wrote them, the device model's garbage
        elsewhere (contents are undefined at power-up, never zero by
        assumption)."""
        bank, row, column, _ = split(address)
        garbage = int(self.dut.u_sdram.memory[(bank << 23) | (row << 10) | column].value)
        word = 0
        for lane in range(2):
            byte = self.shadow.get(address | lane, (garbage >> (8 * lane)) & 0xFF)
            word |= byte << (8 * lane)
        return word

    async def write(self, address, data, enables=0b11):
        _, waited, response, ready = await self.transact(True, address, data, enables)
        assert (response, ready) == (WRITE_RESPONSE, WRITE_IDLE), ('write latency', address, response, ready)
        return waited

    async def read(self, address):
        word, waited, response, ready = await self.transact(False, address)
        assert (response, ready) == (READ_RESPONSE, READ_IDLE), ('read latency', address, response, ready)
        expected = self.expected_word(address)
        assert word == expected, f'read {address:#09x} observed {word:04x} expected {expected:04x}'
        return waited


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


@cocotb.test(timeout_time=40, timeout_unit='ms')
async def sdram_ctrl_contract(dut):
    seed = int(os.environ['DV_SEED'])
    assert int(os.environ['DV_CLOCK_HZ']) == 25_000_000
    cocotb.log.info('nand2mario SDRAM controller seed=%d', seed)
    b = await start(dut, seed)

    while b.idle_clock is None:
        await RisingEdge(dut.clk_i)
        await ReadOnly()
        assert b.clock < IDLE_CLOCK + 2, 'controller never reached IDLE'
    assert b.monitor.init_sequence() == ['PRECHARGE'] + ['REFRESH'] * 8 + ['MRS'], b.monitor.commands[:12]
    assert b.monitor.first_command == INIT_PRECHARGE_CLOCK, b.monitor.first_command
    assert b.monitor.refresh_clocks[:8] == INIT_REFRESH_CLOCKS, b.monitor.refresh_clocks[:8]
    assert b.monitor.commands[9][0] == INIT_MODE_CLOCK, b.monitor.commands[9]
    assert (b.initialized_clock, b.idle_clock) == (INITIALIZED_CLOCK, IDLE_CLOCK), (b.initialized_clock, b.idle_clock)
    assert b.dqm_before_init == {(1, 1)}, b.dqm_before_init
    assert int(dut.request_ready_o.value) == 1

    refreshes = len(b.monitor.refresh_clocks)
    runs, run = [], 0
    while len(b.monitor.refresh_clocks) < refreshes + 3 or run:
        await RisingEdge(dut.clk_i)
        await ReadOnly()
        if int(dut.request_ready_o.value):
            if run:
                runs.append(run)
            run = 0
        else:
            run += 1
    idle_gaps = b.monitor.refresh_gaps()[-2:]
    assert idle_gaps == [REFRESH_IDLE_GAP] * 2, idle_gaps
    assert runs == [REFRESH_CLOCKS] * 3, runs

    base = join(1, 0x0123, 0x0100)
    await b.write(base, 0xBEEF)
    await b.read(base)
    await b.write(base + 2, 0xAAAA, 0b01)
    await b.read(base + 2)
    await b.write(base + 2, 0x5555, 0b10)
    assert b.expected_word(base + 2) == 0x55AA
    await b.read(base + 2)
    await b.write(base + 4, 0x1234, 0b10)
    await b.read(base + 4)
    for address in (join(0, 0, 0), join(3, 8191, 1023), join(2, 8191, 0), join(1, 0, 1023),
                    join(0, 4096, 512), join(3, 1, 1)):
        await b.write(address, (address * 0x9E37) & 0xFFFF)
        await b.read(address)
    assert {bank for bank, *_ in (split(a) for a in b.shadow)} == {0, 1, 2, 3}
    assert b.dqm_after_init == {(0, 0)}, b.dqm_after_init

    stalls, refreshes_before = [], len(b.monitor.refresh_clocks)
    for i in range(80):
        address = join(i & 3, 0x0200 + i, 8 * i)
        stalls.append(await b.write(address, 0x4000 + i))
        stalls.append(await b.read(address))
    assert set(stalls) <= {0, REFRESH_CLOCKS} and stalls.count(REFRESH_CLOCKS) >= 3, stalls
    traffic_gaps = b.monitor.refresh_gaps()[refreshes_before - 1:]
    assert max(traffic_gaps) <= REFRESH_DEADLINE and max(traffic_gaps) > REFRESH_IDLE_GAP, traffic_gaps

    regions = [b.rng.randrange(0, 1 << 26, 1 << 12) for _ in range(6)]
    lanes, reads, writes, stalled = {0b01: 0, 0b10: 0, 0b11: 0}, 0, 0, 0
    before = len(b.monitor.refresh_clocks)
    for _ in range(240):
        address = b.rng.choice(regions) + b.rng.randrange(0, 1 << 12, 2)
        if b.rng.random() < 0.55:
            enables = b.rng.choice((0b11, 0b11, 0b01, 0b10))
            lanes[enables] += 1
            writes += 1
            stalled += await b.write(address, b.rng.getrandbits(16), enables) > 0
        else:
            reads += 1
            stalled += await b.read(address) > 0
    for address in b.rng.sample(sorted({a & ~1 for a in b.shadow}), 24):
        await b.read(address)
    gaps = b.monitor.refresh_gaps()[before - 1:]
    assert min(lanes.values()) >= 10 and reads >= 60 and writes >= 60, (lanes, reads, writes)
    assert stalled >= 5 and len(gaps) >= 10 and max(gaps) <= REFRESH_DEADLINE, (stalled, gaps)
    await FallingEdge(dut.clk_i)
    b.idle()
    for _ in range(64):
        await RisingEdge(dut.clk_i)
        await ReadOnly()
        assert not int(dut.response_valid_o.value), 'stray response'
    cocotb.log.info('SDRAM controller checks passed seed=%d clocks=%d refreshes=%d activates=%d max_gap=%d',
                    seed, b.clock, len(b.monitor.refresh_clocks), b.monitor.activates, max(b.monitor.refresh_gaps()))


@cocotb.test(timeout_time=2, timeout_unit='ms')
async def refresh_deadline_is_enforced(dut):
    """Expected failure: with REFRESH_INTERVAL raised to 220 (and the
    controller's own deadline check moved to 250, past the model's 195) the
    device model's refresh interval assertion must fire. Acceptance is the
    named fatal."""
    seed = int(os.environ['DV_SEED'])
    cocotb.log.info('nand2mario SDRAM refresh negative control seed=%d', seed)
    b = await start(dut, seed)
    while b.clock < IDLE_CLOCK + 2 * REFRESH_DEADLINE:
        await RisingEdge(dut.clk_i)
    raise AssertionError('the device model did not reject the late refresh')
