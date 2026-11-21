from __future__ import annotations

import random
from dataclasses import dataclass, field

import cocotb
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge

from .cache_model import read_line, write_line


@dataclass
class LineResponder:
    dut: object
    memory: bytearray
    latency: int = 2
    cycle: int = 0
    pending: tuple[bool, int, int, int, int] | None = None
    held: tuple[int, int, int, int, bool] | None = None
    transactions: list[tuple[bool, int, int, int]] = field(default_factory=list)
    prefetches: list[bool] = field(default_factory=list)
    fail_read_addresses: set[int] = field(default_factory=set)
    fail_write_addresses: set[int] = field(default_factory=set)
    # Why: None keeps the fixed pattern so directed checks stay exact; a seeded
    # generator for readiness and response latency (two edges minimum, like the
    # fixed default) widens timing coverage reproducibly.
    rng: random.Random | None = None
    max_latency: int = 6
    block_writes: bool = False
    block_prefetches: bool = False

    def _ready(self) -> bool:
        if self.block_prefetches and int(self.dut.line_request_valid_o.value) and int(self.dut.line_request_prefetch_o.value):
            return False
        if self.block_writes and int(self.dut.line_request_valid_o.value) and int(self.dut.line_request_write_o.value):
            return False
        if self.pending is not None:
            return False
        if self.rng is None:
            return self.cycle % 4 != 1
        return self.rng.random() < 0.6

    def _latency(self) -> int:
        if self.rng is None:
            return self.latency
        return self.rng.randint(2, self.max_latency)

    async def run(self) -> None:
        self.dut.line_request_ready_i.value = 0
        self.dut.line_response_valid_i.value = 0
        self.dut.line_response_data_i.value = 0
        self.dut.line_response_error_i.value = 0
        while True:
            await FallingEdge(self.dut.clk_i)
            ready = int(self._ready())
            self.dut.line_request_ready_i.value = ready
            response_now = self.pending is not None and self.pending[4] == 0
            self.dut.line_response_valid_i.value = int(response_now)
            if self.pending is not None:
                write, address, data, byte_enable, _ = self.pending
                self.dut.line_response_data_i.value = read_line(self.memory, address)
                self.dut.line_response_error_i.value = int(
                    address in (self.fail_write_addresses if write else self.fail_read_addresses)
                )
            await ReadOnly()
            if int(self.dut.line_request_valid_o.value):
                payload = (
                    int(self.dut.line_request_write_o.value),
                    int(self.dut.line_request_address_o.value),
                    int(self.dut.line_request_write_data_o.value),
                    int(self.dut.line_request_byte_enable_o.value),
                )
                prefetch = bool(int(self.dut.line_request_prefetch_o.value)) if hasattr(
                    self.dut, "line_request_prefetch_o") else False
                if self.held is not None:
                    assert (*payload, prefetch) == self.held, (
                        f"line request changed under backpressure: {self.held} -> {payload}")
                if ready:
                    assert self.pending is None
                    self.transactions.append((bool(payload[0]), payload[1], payload[2], payload[3]))
                    self.prefetches.append(prefetch)
                    self.pending = (*payload, self._latency() - 1)
                    self.held = None
                else:
                    self.held = (*payload, prefetch)
            else:
                self.held = None
            await RisingEdge(self.dut.clk_i)
            if response_now and self.pending is not None:
                write, address, data, byte_enable, _ = self.pending
                if write and address not in self.fail_write_addresses:
                    assert byte_enable == 0xFFFF
                    write_line(self.memory, address, data)
                self.pending = None
            elif self.pending is not None:
                self.pending = (*self.pending[:4], self.pending[4] - 1)
            self.cycle += 1


async def reset_cache(dut) -> None:
    dut.reset_i.value = 1
    dut.cpu_request_valid_i.value = 0
    dut.maintenance_valid_i.value = 0
    if hasattr(dut, "cpu_request_write_i"):
        dut.cpu_request_write_i.value = 0
        dut.cpu_request_write_data_i.value = 0
        dut.cpu_request_byte_enable_i.value = 0
    await RisingEdge(dut.clk_i)
    await RisingEdge(dut.clk_i)
    await FallingEdge(dut.clk_i)
    dut.reset_i.value = 0


async def cpu_access(
    dut,
    address: int,
    *,
    write: bool = False,
    write_data: int = 0,
    byte_enable: int = 0,
    limit: int = 80,
    expected_cycles: int | None = None,
) -> tuple[int, bool]:
    """Issue one request and return (data, error).

    expected_cycles counts rising edges from the accept edge to the edge that
    samples the response; a hit is exactly one.
    """
    await FallingEdge(dut.clk_i)
    dut.cpu_request_valid_i.value = 1
    dut.cpu_request_address_i.value = address
    if hasattr(dut, "cpu_request_write_i"):
        dut.cpu_request_write_i.value = int(write)
        dut.cpu_request_write_data_i.value = write_data
        dut.cpu_request_byte_enable_i.value = byte_enable
    accepted = False
    for _ in range(limit):
        await ReadOnly()
        accepted = bool(int(dut.cpu_request_ready_o.value))
        await RisingEdge(dut.clk_i)
        if accepted:
            break
        await FallingEdge(dut.clk_i)
    assert accepted, f"CPU request was not accepted: address={address:#x}"
    # Why: the cycle after the accept edge may already carry a hit response, so
    # the request is withdrawn at the falling edge, before the next sampling
    # edge, or it would be accepted twice.
    for cycle in range(limit):
        await ReadOnly()
        if int(dut.cpu_response_valid_o.value):
            if expected_cycles is not None:
                assert cycle + 1 == expected_cycles, (
                    f"address={address:#x} response latency expected={expected_cycles} "
                    f"observed={cycle + 1}"
                )
            result = int(dut.cpu_response_data_o.value), bool(dut.cpu_response_error_o.value)
            await FallingEdge(dut.clk_i)
            dut.cpu_request_valid_i.value = 0
            return result
        await FallingEdge(dut.clk_i)
        dut.cpu_request_valid_i.value = 0
        await RisingEdge(dut.clk_i)
    raise AssertionError(f"CPU response timeout: address={address:#x} limit={limit}")


@dataclass
class Op:
    address: int
    write: bool = False
    write_data: int = 0
    byte_enable: int = 0


@dataclass
class Completion:
    op: Op
    data: int
    error: bool
    latency: int


async def run_stream(
    dut,
    ops: list[Op],
    *,
    rng: random.Random | None = None,
    gap_probability: float = 0.0,
    limit: int = 200,
) -> list[Completion]:
    """Pipelined requester obeying the one-outstanding contract.

    A new request is presented when nothing is outstanding or when the
    outstanding response is valid in the current cycle, so hits run
    back-to-back. Response valid and request ready depend only on registered
    cache state, so both are settled at the falling edge. Each completion
    records the accept-to-response edge count.
    """
    completions: list[Completion] = []
    outstanding: Op | None = None
    accepted_cycle = 0
    presented: Op | None = None
    cycle = 0
    stalled = 0
    index = 0
    while index < len(ops) or outstanding is not None or presented is not None:
        await FallingEdge(dut.clk_i)
        response_valid = bool(int(dut.cpu_response_valid_o.value))
        if response_valid:
            assert outstanding is not None, "response pulse without an outstanding request"
            completions.append(Completion(
                outstanding,
                int(dut.cpu_response_data_o.value),
                bool(int(dut.cpu_response_error_o.value)),
                cycle - accepted_cycle,
            ))
            outstanding = None
            stalled = 0
        if presented is None and outstanding is None and index < len(ops):
            if rng is None or rng.random() >= gap_probability:
                presented = ops[index]
                index += 1
        dut.cpu_request_valid_i.value = int(presented is not None)
        if presented is not None:
            dut.cpu_request_address_i.value = presented.address
            if hasattr(dut, "cpu_request_write_i"):
                dut.cpu_request_write_i.value = int(presented.write)
                dut.cpu_request_write_data_i.value = presented.write_data
                dut.cpu_request_byte_enable_i.value = presented.byte_enable
        await ReadOnly()
        if presented is not None and int(dut.cpu_request_ready_o.value):
            outstanding = presented
            presented = None
            accepted_cycle = cycle
        await RisingEdge(dut.clk_i)
        cycle += 1
        stalled += 1
        assert stalled < limit, (
            f"stream made no progress for {limit} cycles: outstanding={outstanding} "
            f"presented={presented} index={index} "
            f"line_valid={int(dut.line_request_valid_o.value)} "
            f"line_ready={int(dut.line_request_ready_i.value)}"
        )
    await FallingEdge(dut.clk_i)
    dut.cpu_request_valid_i.value = 0
    return completions


async def maintain(dut, *, limit: int = 4096) -> bool:
    # Why: 4096 covers a clean four-way walk (2,048 entries) with margin, so
    # only a hung walk times out.
    await FallingEdge(dut.clk_i)
    dut.maintenance_valid_i.value = 1
    while True:
        await ReadOnly()
        accepted = int(dut.maintenance_ready_o.value)
        await RisingEdge(dut.clk_i)
        if accepted:
            break
        await FallingEdge(dut.clk_i)
    await FallingEdge(dut.clk_i)
    dut.maintenance_valid_i.value = 0
    for _ in range(limit):
        await RisingEdge(dut.clk_i)
        await ReadOnly()
        if int(dut.maintenance_done_o.value):
            return bool(int(getattr(dut, "maintenance_error_o", 0).value)) if hasattr(dut, "maintenance_error_o") else False
        await FallingEdge(dut.clk_i)
    raise AssertionError("maintenance did not complete within bound")
