"""Platform PMON line-memory events against the real memory arbiter.

MEM_BACKPRESSURE (33) must equal the arbiter's downstream request
valid-and-not-ready; master contention and waits behind an outstanding
transaction are MEM_MASTER_WAIT (77) only. Line events 29/30/32/34/14/18/19
are checked against an independent transaction model, and so are the
line-memory owner events 87..91: exactly one from a transaction's acceptance
clock through its response clock, never together with MEM_BACKPRESSURE.
"""

from __future__ import annotations

import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge, Timer

CLOCK_PERIOD_NS = 40
I, D, DEBUG = 0, 1, 2
OWNER_EVENTS = (87, 88, 89, 90, 91)


def _owner_event(master: int, write: int, prefetch: int) -> int:
    """MEM_OWNER_* ID: fetch, data demand read, data prefetch, data write, debug."""
    if master == I:
        return 87
    if master == DEBUG:
        return 91
    return 90 if write else (89 if prefetch else 88)


def _bit(events: int, event_id: int) -> int:
    return (events >> event_id) & 1


class Bench:
    def __init__(self, dut) -> None:
        self.dut = dut
        self.valid = [0, 0, 0]
        self.write = [0, 0, 0]
        self.prefetch = 0
        self.outstanding = None  # Why: None or one (master, write, cycles until response, owner event), because the controller holds one transaction outstanding
        self.counts = {33: 0, 77: 0, 29: 0, 30: 0, 32: 0, 34: 0, 14: 0, 18: 0, 19: 0,
                       **{event_id: 0 for event_id in OWNER_EVENTS}}
        self.contention_cycles = 0
        self.refusal_cycles = 0
        self.busy_wait_cycles = 0

    async def reset(self) -> None:
        dut = self.dut
        dut.reset_i.value = 1
        dut.master_request_valid_i.value = 0
        dut.master_request_write_i.value = 0
        dut.data_request_prefetch_i.value = 0
        dut.memory_request_ready_i.value = 0
        dut.memory_response_valid_i.value = 0
        dut.memory_response_error_i.value = 0
        await RisingEdge(dut.clk_i)
        await RisingEdge(dut.clk_i)
        await FallingEdge(dut.clk_i)
        dut.reset_i.value = 0

    async def step(self, memory_ready: int, latency: int, error: int = 0) -> None:
        """Drive one cycle and check the combinational event vector."""
        dut = self.dut
        respond = self.outstanding is not None and self.outstanding[2] == 0
        dut.master_request_valid_i.value = sum(v << i for i, v in enumerate(self.valid))
        dut.master_request_write_i.value = sum(w << i for i, w in enumerate(self.write))
        dut.data_request_prefetch_i.value = self.prefetch
        dut.memory_request_ready_i.value = memory_ready
        dut.memory_response_valid_i.value = int(respond)
        dut.memory_response_error_i.value = error if respond else 0
        await Timer(1, unit="ns")
        events = int(dut.events_o.value)
        downstream_valid = int(dut.memory_request_valid_o.value)
        ready = int(dut.master_request_ready_o.value)
        refused = downstream_valid and not memory_ready
        # Ref: core MAS "Performance monitor": 33 keeps its nand2linux meaning,
        # downstream request valid && !ready.
        assert _bit(events, 33) == int(refused), (events, downstream_valid, memory_ready)
        waiting = any(self.valid[m] and not (ready >> m) & 1 for m in range(3))
        assert _bit(events, 77) == int(waiting)
        if waiting and not refused:
            if self.outstanding is not None and not respond:
                self.busy_wait_cycles += 1
            else:
                self.contention_cycles += 1
        if refused:
            self.refusal_cycles += 1

        accepted = [m for m in range(3) if self.valid[m] and (ready >> m) & 1]
        assert len(accepted) <= 1
        expected = {
            29: int(accepted == [I]),
            30: int(accepted == [D] and not self.write[D]),
            32: int(bool(accepted) and accepted[0] in (D, DEBUG) and self.write[accepted[0]] == 1),
            19: int(accepted == [D] and self.write[D] == 1),
            34: int(self.outstanding is not None and self.outstanding[0] in (I, D)
                    and not self.outstanding[1]),
            14: int(respond and self.outstanding[0] == I and not error),
            18: int(respond and self.outstanding[0] == D and not self.outstanding[1] and not error),
        }
        owner = None
        if accepted:
            owner = _owner_event(accepted[0], self.write[accepted[0]], self.prefetch)
        elif self.outstanding is not None:
            owner = self.outstanding[3]
        for event_id in OWNER_EVENTS:
            expected[event_id] = int(event_id == owner)
        assert not (refused and owner is not None)
        for event_id, value in expected.items():
            assert _bit(events, event_id) == value, (event_id, events)
        for event_id in self.counts:
            self.counts[event_id] += _bit(events, event_id)

        await RisingEdge(dut.clk_i)
        await ReadOnly()
        if respond:
            self.outstanding = None
        elif self.outstanding is not None:
            self.outstanding = (*self.outstanding[:2], self.outstanding[2] - 1,
                                self.outstanding[3])
        if accepted:
            master = accepted[0]
            assert self.outstanding is None
            self.outstanding = (master, self.write[master], latency - 1,
                                _owner_event(master, self.write[master], self.prefetch))
            self.valid[master] = 0
            if master == D:
                self.prefetch = 0
        await FallingEdge(dut.clk_i)


@cocotb.test(timeout_time=2, timeout_unit="ms")
async def pmon_platform_line_events(dut) -> None:
    seed = int(os.environ.get("DV_SEED", "71"))
    cocotb.log.info("PMON platform events seed=%d", seed)
    rng = random.Random(seed)
    cocotb.start_soon(Clock(dut.clk_i, CLOCK_PERIOD_NS, unit="ns").start())
    bench = Bench(dut)
    await bench.reset()

    # Why: with a ready backend the downstream request is accepted at once, so
    # two contending masters raise MEM_MASTER_WAIT (77) for the loser but no
    # MEM_BACKPRESSURE (33).
    bench.valid = [1, 1, 0]
    await bench.step(memory_ready=1, latency=3)
    assert bench.counts[33] == 0 and bench.counts[77] == 1
    # Why: the loser waits behind the outstanding transaction while the
    # downstream request is idle, which is master wait (77), not backpressure
    # (33).
    for _ in range(3):
        await bench.step(memory_ready=1, latency=3)
    assert bench.counts[33] == 0 and bench.counts[77] == 4
    await bench.step(memory_ready=1, latency=3)  # Why: D is accepted only after the response, so the backpressure count stays zero
    while bench.outstanding is not None:
        await bench.step(memory_ready=1, latency=3)
    assert bench.counts[33] == 0

    # Why: one master valid with the backend not ready for four clocks is a
    # real downstream refusal, so backpressure (33) must count exactly four.
    bench.valid = [0, 1, 0]
    bench.write = [0, 1, 0]
    for _ in range(4):
        await bench.step(memory_ready=0, latency=2)
    assert bench.counts[33] == 4
    await bench.step(memory_ready=1, latency=2)
    while bench.outstanding is not None:
        await bench.step(memory_ready=1, latency=2)
    assert bench.counts[33] == 4 and bench.counts[19] == 1 and bench.counts[32] == 1

    # Why: randomized traffic cross-checks every cycle against the arbiter,
    # reaching overlaps the directed cases miss.
    bench.write = [0, 0, 0]
    for _ in range(3000):
        for master in range(3):
            if not bench.valid[master] and rng.random() < 0.3:
                bench.valid[master] = 1
                bench.write[master] = 0 if master == I else rng.randint(0, 1)
                if master == D:
                    bench.prefetch = int(not bench.write[D] and rng.random() < 0.3)
        await bench.step(memory_ready=int(rng.random() < 0.6), latency=rng.randint(1, 4),
                         error=int(rng.random() < 0.1))
    cocotb.log.info(
        "contention=%d busy_wait=%d refusal=%d counts=%s",
        bench.contention_cycles, bench.busy_wait_cycles, bench.refusal_cycles, bench.counts)
    assert bench.contention_cycles > 100 and bench.busy_wait_cycles > 100
    assert bench.refusal_cycles > 100
    assert bench.counts[77] > bench.counts[33]
    assert all(bench.counts[event_id] > 50 for event_id in OWNER_EVENTS), bench.counts
