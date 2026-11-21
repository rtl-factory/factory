from __future__ import annotations

import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge

from .cache_driver import LineResponder, Op, cpu_access, maintain, reset_cache, run_stream
from .cache_model import CacheModel


def _check_stream(oracle: CacheModel, completions, label: str) -> None:
    for done in completions:
        hits_before = oracle.hits
        expected = oracle.access(done.op.address)
        hit = oracle.hits > hits_before
        assert not done.error and done.data == expected, (
            f"{label}: address={done.op.address:#x} expected={expected:#010x} observed={done.data:#010x}"
        )
        if hit:
            assert done.latency == 1, f"{label}: hit at {done.op.address:#x} took {done.latency} edges"


@cocotb.test(timeout_time=2000, timeout_unit="us")
async def icache_contract(dut):
    seed = int(os.environ.get("DV_SEED", "30"))
    rng = random.Random(seed)
    cocotb.start_soon(Clock(dut.clk_i, 40, unit="ns").start())
    memory = bytearray((index * 37 + 11) & 0xFF for index in range(0x8000))
    oracle = CacheModel(bytearray(memory), writable=False)
    responder = LineResponder(dut, memory)
    service = cocotb.start_soon(responder.run())
    await reset_cache(dut)

    addresses = [0x0000, 0x0004, 0x0008, 0x000C, 0x2000, 0x4000, 0x0000]
    for address in addresses:
        hits_before = oracle.hits
        expected = oracle.access(address)
        observed, error = await cpu_access(
            dut, address, expected_cycles=1 if oracle.hits > hits_before else None)
        assert not error and observed == expected, (
            f"read mismatch address={address:#x} expected={expected:#010x} observed={observed:#010x}"
        )

    actual = [(write, address, data) for write, address, data, _ in responder.transactions]
    assert actual == oracle.line_transactions
    assert oracle.hits == 3 and oracle.misses == 4

    # Ref: memory MAS "CPU port handshake": hits are sampled one edge after
    # acceptance, so back-to-back hits accept once per cycle.
    stream = [Op(0x0000 + 4 * (i % 4)) for i in range(12)]
    _check_stream(oracle, await run_stream(dut, stream), "back-to-back hits")

    # Why: a hit accepted on the edge that samples a miss's refill response
    # must not reorder against it, alternating with further misses in other
    # sets.
    stream = [Op(0x0100), Op(0x0004), Op(0x0200), Op(0x0104), Op(0x0300), Op(0x0208)]
    _check_stream(oracle, await run_stream(dut, stream), "hit under refill")
    actual = [(write, address, data) for write, address, data, _ in responder.transactions]
    assert actual == oracle.line_transactions

    assert not await maintain(dut)
    oracle.maintain()
    before = len(responder.transactions)
    expected = oracle.access(0x0004)
    observed, error = await cpu_access(dut, 0x0004)
    assert not error and observed == expected
    assert len(responder.transactions) == before + 1, "invalidate did not force a refill"

    _, error = await cpu_access(dut, 0x0400_0000, expected_cycles=1)
    assert error, "address above the 26-bit physical range must fail"
    _, error = await cpu_access(dut, 0x0000_0002, expected_cycles=1)
    assert error, "unaligned instruction request must fail"
    responder.fail_read_addresses.add(0x6000)
    _, error = await cpu_access(dut, 0x6000)
    assert error, "line refill error must reach the CPU response"
    responder.fail_read_addresses.clear()
    observed, error = await cpu_access(dut, 0x6000)
    assert not error and observed == oracle.access(0x6000)

    # Ref: core MAS "Pipelined memory handshake": an instruction request may be
    # withdrawn before acceptance, so one presented while maintenance holds
    # ready low and then dropped must have no effect.
    await FallingEdge(dut.clk_i)
    dut.maintenance_valid_i.value = 1
    dut.cpu_request_valid_i.value = 1
    dut.cpu_request_address_i.value = 0x7000
    await ReadOnly()
    assert not int(dut.cpu_request_ready_o.value), "maintenance must hold CPU ready low"
    await RisingEdge(dut.clk_i)
    await FallingEdge(dut.clk_i)
    dut.maintenance_valid_i.value = 0
    dut.cpu_request_valid_i.value = 0
    for _ in range(600):
        await RisingEdge(dut.clk_i)
        await ReadOnly()
        assert not int(dut.cpu_response_valid_o.value), "withdrawn request produced a response"
        if int(dut.maintenance_done_o.value):
            break
    else:
        raise AssertionError("maintenance did not complete within bound")
    oracle.maintain()

    responder.rng = random.Random(seed + 1)
    responder.transactions.clear()
    oracle.line_transactions.clear()
    stream = [
        Op((rng.randrange(4) << 13) | (rng.randrange(4) << 4) | (rng.randrange(4) << 2))
        for _ in range(400)
    ]
    _check_stream(oracle, await run_stream(dut, stream, rng=rng, gap_probability=0.2), "random")
    actual = [(write, address, data) for write, address, data, _ in responder.transactions]
    assert actual == oracle.line_transactions
    service.cancel()
    cocotb.log.info("seed=%d hits=%d misses=%d line_transactions=%d", seed, oracle.hits, oracle.misses, len(responder.transactions))
