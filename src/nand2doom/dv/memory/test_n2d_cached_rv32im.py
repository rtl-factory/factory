from __future__ import annotations

import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge

from src.nand2doom.dv.core.rv32im_model import encode_b, encode_i, encode_r, encode_s

from .cache_model import read_line, read_word, write_line


def _program() -> tuple[int, ...]:
    return (
        encode_i(0x80, 0, 0, 5),
        encode_i(5, 0, 0, 1),
        encode_i(1, 0, 0, 2),
        encode_r(1, 1, 2, 0, 2),
        encode_i(-1, 1, 0, 1),
        encode_b(-8, 0, 1, 1),
        encode_i(7, 2, 0, 3),
        encode_s(0, 3, 5, 2),
        encode_i(0, 5, 2, 4, 0x03),
        encode_i(0, 5, 2, 6, 0x03),
        0x0010_0073,
    )


class DualLineMemory:
    def __init__(self, dut, memory: bytearray, rng: random.Random) -> None:
        self.dut = dut
        self.memory = memory
        self.rng = rng
        self.cycle = 0
        self.pending_i = None
        self.pending_d = None
        self.i_transactions = []
        self.d_transactions = []
        self.i_hits = 0
        self.i_misses = 0
        self.d_hits = 0
        self.d_misses = 0

    async def run(self) -> None:
        d = self.dut
        d.instruction_line_request_ready_i.value = 0
        d.instruction_line_response_valid_i.value = 0
        d.instruction_line_response_data_i.value = 0
        d.instruction_line_response_error_i.value = 0
        d.data_line_request_ready_i.value = 0
        d.data_line_response_valid_i.value = 0
        d.data_line_response_data_i.value = 0
        d.data_line_response_error_i.value = 0
        while True:
            await FallingEdge(d.clk_i)
            i_ready = self.pending_i is None and self.rng.random() < 0.6
            d_ready = self.pending_d is None and self.rng.random() < 0.6
            d.instruction_line_request_ready_i.value = i_ready
            d.data_line_request_ready_i.value = d_ready
            i_response = self.pending_i is not None and self.pending_i[4] == 0
            d_response = self.pending_d is not None and self.pending_d[4] == 0
            d.instruction_line_response_valid_i.value = i_response
            d.data_line_response_valid_i.value = d_response
            d.instruction_line_response_data_i.value = (
                read_line(self.memory, self.pending_i[1]) if self.pending_i else 0
            )
            d.data_line_response_data_i.value = (
                read_line(self.memory, self.pending_d[1]) if self.pending_d else 0
            )
            await ReadOnly()
            if int(d.instruction_line_request_valid_o.value) and i_ready:
                payload = (
                    int(d.instruction_line_request_write_o.value),
                    int(d.instruction_line_request_address_o.value),
                    int(d.instruction_line_request_write_data_o.value),
                    int(d.instruction_line_request_byte_enable_o.value),
                )
                assert payload[0] == 0 and payload[2:] == (0, 0)
                self.i_transactions.append(payload)
                self.pending_i = (*payload, self.rng.randint(1, 4))
            if int(d.data_line_request_valid_o.value) and d_ready:
                payload = (
                    int(d.data_line_request_write_o.value),
                    int(d.data_line_request_address_o.value),
                    int(d.data_line_request_write_data_o.value),
                    int(d.data_line_request_byte_enable_o.value),
                )
                self.d_transactions.append(payload)
                self.pending_d = (*payload, self.rng.randint(1, 5))
            await RisingEdge(d.clk_i)
            await ReadOnly()
            self.i_hits += int(d.instruction_hit_event_o.value)
            self.i_misses += int(d.instruction_miss_event_o.value)
            self.d_hits += int(d.data_hit_event_o.value)
            self.d_misses += int(d.data_miss_event_o.value)
            if i_response:
                self.pending_i = None
            elif self.pending_i is not None:
                self.pending_i = (*self.pending_i[:4], self.pending_i[4] - 1)
            if d_response:
                write, address, data, byte_enable, _ = self.pending_d
                if write:
                    assert byte_enable == 0xFFFF
                    write_line(self.memory, address, data)
                self.pending_d = None
            elif self.pending_d is not None:
                self.pending_d = (*self.pending_d[:4], self.pending_d[4] - 1)
            self.cycle += 1


class PortLatencyMonitor:
    """Measures accept-to-response edges on one internal cache CPU port."""

    def __init__(self, dut, cache) -> None:
        self.dut = dut
        self.cache = cache
        self.accepted_cycle: int | None = None
        self.latencies: list[int] = []

    async def run(self) -> None:
        cycle = 0
        while True:
            await RisingEdge(self.dut.clk_i)
            await ReadOnly()
            cycle += 1
            if int(self.cache.cpu_response_valid_o.value):
                assert self.accepted_cycle is not None, "cache response without a request"
                # Visible after the accept edge, sampled by the next edge.
                self.latencies.append(cycle - self.accepted_cycle + 1)
                self.accepted_cycle = None
            # The accept edge is the next rising edge; sample its handshake now.
            await FallingEdge(self.dut.clk_i)
            await ReadOnly()
            if int(self.cache.cpu_request_valid_i.value) and int(self.cache.cpu_request_ready_o.value):
                assert self.accepted_cycle is None, "second request accepted while one is outstanding"
                self.accepted_cycle = cycle + 1


async def _start_maintenance(dut, prefix: str, limit: int) -> None:
    valid = getattr(dut, f"{prefix}_maintenance_valid_i")
    ready = getattr(dut, f"{prefix}_maintenance_ready_o")
    done = getattr(dut, f"{prefix}_maintenance_done_o")
    await FallingEdge(dut.clk_i)
    valid.value = 1
    while True:
        await ReadOnly()
        accepted = int(ready.value)
        await RisingEdge(dut.clk_i)
        if accepted:
            break
        await FallingEdge(dut.clk_i)
    await FallingEdge(dut.clk_i)
    valid.value = 0
    for _ in range(limit):
        await RisingEdge(dut.clk_i)
        await ReadOnly()
        if int(done.value):
            return
        await FallingEdge(dut.clk_i)
    raise AssertionError(f"{prefix} maintenance timeout")


@cocotb.test(timeout_time=500, timeout_unit="us")
async def cached_rv32im_memory_traffic(dut):
    seed = int(os.environ.get("DV_SEED", "30"))
    cocotb.start_soon(Clock(dut.clk_i, 40, unit="ns").start())
    memory = bytearray(0x1000)
    program = _program()
    for index, word in enumerate(program):
        memory[index * 4 : index * 4 + 4] = word.to_bytes(4, "little")
    service = DualLineMemory(dut, memory, random.Random(seed))
    service_task = cocotb.start_soon(service.run())
    i_monitor = PortLatencyMonitor(dut, dut.u_icache)
    d_monitor = PortLatencyMonitor(dut, dut.u_dcache)
    i_task = cocotb.start_soon(i_monitor.run())
    d_task = cocotb.start_soon(d_monitor.run())

    dut.rst_i.value = 1
    dut.enable_i.value = 0
    dut.instruction_maintenance_valid_i.value = 0
    dut.data_maintenance_valid_i.value = 0
    await RisingEdge(dut.clk_i)
    await RisingEdge(dut.clk_i)
    await FallingEdge(dut.clk_i)
    dut.rst_i.value = 0
    dut.enable_i.value = 1

    load_results = []
    retired = 0
    for cycle in range(700):
        await RisingEdge(dut.clk_i)
        await ReadOnly()
        if int(dut.observation_valid_o.value):
            retired += 1
            if int(dut.observation_memory_valid_o.value) and not int(
                dut.observation_memory_write_o.value
            ):
                load_results.append(int(dut.observation_memory_read_data_o.value))
            if int(dut.observation_trap_o.value):
                assert int(dut.observation_cause_o.value) == 3, "expected final EBREAK trap"
                break
        await FallingEdge(dut.clk_i)
    else:
        raise AssertionError("cached RV32IM program did not reach EBREAK")

    assert load_results == [127, 127]
    assert service.i_hits > 0 and service.i_misses > 0
    assert service.d_hits > 0 and service.d_misses > 0
    assert len(service.i_transactions) >= 3
    assert len(service.d_transactions) == 1, "store allocation should serve both following loads"
    # Every lookup hit completes exactly one edge after acceptance; misses
    # take longer, so the one-edge count equals the hit-event count.
    assert i_monitor.latencies.count(1) == service.i_hits, (i_monitor.latencies, service.i_hits)
    assert d_monitor.latencies.count(1) == service.d_hits, (d_monitor.latencies, service.d_hits)
    assert all(latency > 1 for latency in i_monitor.latencies if latency != 1)

    await FallingEdge(dut.clk_i)
    dut.enable_i.value = 0
    # A four-way walk visits 2,048 entries, plus one dirty-line writeback.
    await _start_maintenance(dut, "data", 2400)
    assert not int(dut.data_maintenance_error_o.value)
    assert read_word(memory, 0x80) == 127, "D-cache maintenance did not write back signature"
    await _start_maintenance(dut, "instruction", 700)
    service_task.cancel()
    i_task.cancel()
    d_task.cancel()
    cocotb.log.info(
        "seed=%d retired=%d i_hits=%d i_misses=%d d_hits=%d d_misses=%d",
        seed,
        retired,
        service.i_hits,
        service.i_misses,
        service.d_hits,
        service.d_misses,
    )
