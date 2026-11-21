from __future__ import annotations

import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge

from .cache_driver import LineResponder, Op, cpu_access, maintain, reset_cache, run_stream
from .cache_model import CacheModel, read_word


def _check_stream(oracle: CacheModel, completions, label: str) -> None:
    for done in completions:
        op = done.op
        hits_before = oracle.hits
        expected = oracle.access(
            op.address, write=op.write, write_data=op.write_data, byte_enable=op.byte_enable
        )
        hit = oracle.hits > hits_before
        assert not done.error and done.data == expected, (
            f"{label}: write={op.write} address={op.address:#x} "
            f"expected={expected:#010x} observed={done.data:#010x}"
        )
        if hit:
            assert done.latency == 1, f"{label}: hit at {op.address:#x} took {done.latency} edges"


def _transactions(responder: LineResponder) -> list[tuple[bool, int, int]]:
    return [(wr, addr, payload) for wr, addr, payload, _ in responder.transactions]


async def _drain_buffer(dut, limit: int = 120) -> None:
    for _ in range(limit):
        await FallingEdge(dut.clk_i)
        await ReadOnly()
        if not int(dut.write_buffer_pending_o.value):
            return
    raise AssertionError("write buffer did not drain within test bound")


def _by_line(transactions):
    # Why: different lines may reorder, so only each line's operations and
    # payloads are compared in order, and every expected operation must be
    # present.
    result = {}
    for wr, addr, payload in transactions:
        result.setdefault(addr, []).append((wr, payload))
    return result


async def _check_transactions(responder: LineResponder, oracle: CacheModel, label: str) -> None:
    await _drain_buffer(responder.dut)
    actual = _by_line(_transactions(responder))
    expected = _by_line(oracle.line_transactions)
    assert actual == expected, f"{label}: per-line transactions differ: {actual} != {expected}"


SET_COUNT = 512
# Ref: memory MAS "Maintained Halt bound": one 24-clock Halt walk slot per
# way and set entry.
WALK_SLOT_CLOCKS = 24


async def _reset_and_sweep(dut, *, present: int | None = None) -> None:
    """Reset, then prove the metadata sweep: request and maintenance ready
    stay low for exactly one clock per set. With present set, a load of that
    address is held valid through the sweep; it must not be accepted early
    and completes before this returns."""
    await reset_cache(dut)
    if present is not None:
        dut.cpu_request_valid_i.value = 1
        dut.cpu_request_address_i.value = present
        dut.cpu_request_write_i.value = 0
    low = 0
    while True:
        await ReadOnly()
        if int(dut.cpu_request_ready_o.value) or int(dut.maintenance_ready_o.value):
            break
        await RisingEdge(dut.clk_i)
        low += 1
        assert low <= SET_COUNT, "D-cache metadata sweep did not finish"
    assert low == SET_COUNT, f"metadata sweep held ready low {low} clocks, expected {SET_COUNT}"
    if present is not None:
        await RisingEdge(dut.clk_i)
        await FallingEdge(dut.clk_i)
        dut.cpu_request_valid_i.value = 0
        for _ in range(40):
            await ReadOnly()
            if int(dut.cpu_response_valid_o.value):
                break
            await RisingEdge(dut.clk_i)
        else:
            raise AssertionError("request held through the sweep got no response")
        await FallingEdge(dut.clk_i)


async def _maintain_cycles(dut, *, limit: int) -> tuple[bool, int]:
    """Maintenance from acceptance to the done pulse, in rising edges."""
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
    for cycles in range(1, limit + 1):
        await ReadOnly()
        if int(dut.maintenance_done_o.value):
            error = bool(int(dut.maintenance_error_o.value))
            await FallingEdge(dut.clk_i)
            return error, cycles
        await RisingEdge(dut.clk_i)
    raise AssertionError("maintenance did not complete within bound")


def _plru_line(tag: int) -> int:
    # Why: index bits 0x500 put every tag of the replacement-order checks in set 0x50,
    # so the traces below share one tree.
    return (tag << 13) | 0x500


# Why: hand-derived replacement traces for set 0x50, so an oracle bug cannot
# hide a matching RTL bug: (operation, expected new (write, address) line
# transactions). Two ways: tag 2 evicts tag 0 (way 0), then tag 3 evicts dirty
# tag 1 (way 1). Four ways: once all four are full, the miss after "hit 0"
# evicts tag 2 (way 2) where true LRU would evict tag 1; then 5 evicts 0, 6
# evicts 4, and the dirty victims leave in tree-PLRU order (tag 1 for 7, tag 3
# for 4).
_PLRU_TRACES = {
    2: [
        (Op(_plru_line(0)), [(False, 0)]),
        (Op(_plru_line(1)), [(False, 1)]),
        (Op(_plru_line(0)), []),
        (Op(_plru_line(1), True, 0x1111_0001, 0b1111), []),
        (Op(_plru_line(2)), [(False, 2)]),
        (Op(_plru_line(3)), [(True, 1), (False, 3)]),
        (Op(_plru_line(2)), []),
        (Op(_plru_line(3)), []),
    ],
    4: [
        (Op(_plru_line(0)), [(False, 0)]),
        (Op(_plru_line(1)), [(False, 1)]),
        (Op(_plru_line(2)), [(False, 2)]),
        (Op(_plru_line(3)), [(False, 3)]),
        (Op(_plru_line(0)), []),
        (Op(_plru_line(4)), [(False, 4)]),
        (Op(_plru_line(1), True, 0x1111_0001, 0b1111), []),
        (Op(_plru_line(3), True, 0x3333_0003, 0b1111), []),
        (Op(_plru_line(5)), [(False, 5)]),
        (Op(_plru_line(6)), [(False, 6)]),
        (Op(_plru_line(7)), [(True, 1), (False, 7)]),
        (Op(_plru_line(4)), [(True, 3), (False, 4)]),
        (Op(_plru_line(5)), []),
        (Op(_plru_line(6)), []),
        (Op(_plru_line(7)), []),
        (Op(_plru_line(4)), []),
    ],
}


@cocotb.test(timeout_time=20000, timeout_unit="us")
async def dcache_contract(dut):
    seed = int(os.environ.get("DV_SEED", "30"))
    rng = random.Random(seed)
    ways = int(dut.WAYS.value)
    assert ways in _PLRU_TRACES, f"unsupported D-cache WAYS={ways}"
    cocotb.log.info("D-cache geometry: %d ways, %d KiB", ways, ways * 8)
    cocotb.start_soon(Clock(dut.clk_i, 40, unit="ns").start())
    initial = bytearray((index * 19 + 3) & 0xFF for index in range(0x10000))
    memory = bytearray(initial)
    oracle = CacheModel(bytearray(initial), writable=True, ways=ways)
    responder = LineResponder(dut, memory, latency=3)
    service = cocotb.start_soon(responder.run())
    await _reset_and_sweep(dut)

    operations = [
        (False, 0x0010, 0, 0),
        (False, 0x0014, 0, 0),
        (True, 0x0014, 0xA1B2_C3D4, 0b0101),
        (False, 0x0014, 0, 0),
        (True, 0x2010, 0x5566_7788, 0b1111),
        (True, 0x4010, 0xDEAD_BEEF, 0b1111),
        (False, 0x0014, 0, 0),
        (True, 0x0300, 0x0000_0011, 0b0001),
        (True, 0x0301, 0x0000_2200, 0b0010),
        (True, 0x0302, 0x4433_0000, 0b1100),
        (False, 0x0300, 0, 0),
        # Why: a different set, so clean-victim replacement is proven
        # independently of the dirty traffic above.
        (False, 0x0100, 0, 0),
        (False, 0x2100, 0, 0),
        (False, 0x4100, 0, 0),
    ]
    for write, address, data, byte_enable in operations:
        hits_before = oracle.hits
        expected = oracle.access(
            address, write=write, write_data=data, byte_enable=byte_enable
        )
        observed, error = await cpu_access(
            dut, address, write=write, write_data=data, byte_enable=byte_enable,
            expected_cycles=1 if oracle.hits > hits_before else None,
        )
        assert not error and observed == expected, (
            f"access mismatch write={write} address={address:#x} expected={expected:#010x} observed={observed:#010x}"
        )
        await _check_transactions(responder, oracle, "directed access")

    assert oracle.access(0x0300) == 0x4433_2211
    observed, error = await cpu_access(dut, 0x0300)
    assert not error and observed == 0x4433_2211

    # Why: back-to-back store-then-load on the same line and set: each load is
    # accepted on the edge that writes the preceding store hit, so it reads the
    # old RAM word and must take the forwarded line. Way one of set 0x30
    # (0x2300) and way zero (0x0300) are interleaved to prove the record is
    # way-specific, and a store pair checks store-after-store merging.
    stream = [
        Op(0x2300), Op(0x0304),
        Op(0x0300, True, 0x0000_00AA, 0b0001), Op(0x0300),
        Op(0x0304, True, 0x1234_5678, 0b1111), Op(0x0300),
        Op(0x0304), Op(0x2300, True, 0x9900_0000, 0b1000), Op(0x0304),
        Op(0x2300), Op(0x0308, True, 0x0000_BB00, 0b0010),
        Op(0x0308, True, 0x00CC_0000, 0b0100), Op(0x0308), Op(0x2304),
    ]
    _check_stream(oracle, await run_stream(dut, stream), "store-then-load")
    await _check_transactions(responder, oracle, "store-then-load")

    # Why: a hit accepted on the edge that samples a refill (and a dirty
    # writeback) completion must not reorder against it, so back-to-back hits
    # run under refills.
    stream = [Op(0x0010 + 4 * (i % 4)) for i in range(8)]
    stream += [Op(0x6010), Op(0x0300), Op(0x4300), Op(0x0304),
               Op(0x2018, True, 0x0BAD_F00D, 0b1111), Op(0x2018)]
    _check_stream(oracle, await run_stream(dut, stream), "hit under refill")
    await _check_transactions(responder, oracle, "hit under refill")

    for op, wanted in _PLRU_TRACES[ways]:
        before = len(responder.transactions)
        hits_before = oracle.hits
        expected = oracle.access(op.address, write=op.write, write_data=op.write_data,
                                 byte_enable=op.byte_enable)
        observed, error = await cpu_access(
            dut, op.address, write=op.write, write_data=op.write_data,
            byte_enable=op.byte_enable,
            expected_cycles=1 if oracle.hits > hits_before else None)
        assert not error and observed == expected, f"PLRU trace at {op.address:#x}"
        await _drain_buffer(dut)
        seen = [(wr, addr) for wr, addr, _ in _transactions(responder)[before:]]
        assert sorted(seen) == sorted((wr, _plru_line(tag)) for wr, tag in wanted), (
            f"PLRU trace at {op.address:#x}: line transactions {seen}")
    await _check_transactions(responder, oracle, "replacement order")

    # Why: with every way of set 0x50 dirty, the maintenance walk below must
    # write all of them back, in way order, and invalidate the set.
    resident = [_plru_line(tag) for tag in ((2, 3) if ways == 2 else (5, 7, 6, 4))]
    for way, address in enumerate(resident):
        expected = oracle.access(address + 4, write=True, write_data=0x5A00_0000 | way,
                                 byte_enable=0b1111)
        observed, error = await cpu_access(dut, address + 4, write=True,
                                           write_data=0x5A00_0000 | way,
                                           byte_enable=0b1111, expected_cycles=1)
        assert not error and observed == expected
    walk_start = len(responder.transactions)

    oracle.maintain()
    assert not await maintain(dut)
    actual = [(wr, addr, payload) for wr, addr, payload, _ in responder.transactions]
    assert _by_line(actual) == _by_line(oracle.line_transactions)
    assert memory == oracle.memory, "flush did not make dirty bytes visible in line memory"
    set_50 = [addr for wr, addr, _ in actual[walk_start:] if wr and (addr >> 4) & 0x1FF == 0x50]
    assert set_50 == resident, f"dirty set 0x50 walk order {[hex(a) for a in set_50]}"
    for way, address in enumerate(resident):
        assert read_word(memory, address + 4) == 0x5A00_0000 | way
    before = len(responder.transactions)
    for address in resident:
        assert (await cpu_access(dut, address))[0] == oracle.access(address)
    assert len(responder.transactions) == before + len(resident), "set 0x50 was not invalidated"
    assert read_word(memory, 0x2010) == 0x5566_7788
    assert read_word(memory, 0x4010) == 0xDEAD_BEEF

    before = len(responder.transactions)
    expected = oracle.access(0x4010)
    observed, error = await cpu_access(dut, 0x4010)
    assert not error and observed == expected
    assert len(responder.transactions) == before + 1, "maintenance did not invalidate the D-cache"

    expected = oracle.access(0x0700, write=True, write_data=0xCAFE_BABE, byte_enable=0xF)
    observed, error = await cpu_access(
        dut, 0x0700, write=True, write_data=0xCAFE_BABE, byte_enable=0xF
    )
    assert not error and observed == expected
    responder.fail_write_addresses.add(0x0700)
    assert await maintain(dut), "maintenance writeback error was not reported"
    assert read_word(memory, 0x0700) != 0xCAFE_BABE
    responder.fail_write_addresses.clear()
    assert not await maintain(dut), "retry after maintenance error did not complete"
    oracle.maintain()
    assert read_word(memory, 0x0700) == 0xCAFE_BABE, "failed writeback was not retryable"

    _, error = await cpu_access(dut, 0x0400_0000)
    assert error
    responder.fail_read_addresses.add(0x6000)
    _, error = await cpu_access(dut, 0x6000)
    assert error, "line refill error must reach the CPU response"
    responder.fail_read_addresses.clear()
    observed, error = await cpu_access(dut, 0x6000)
    assert not error and observed == oracle.access(0x6000)

    await _setword_cases(dut, rng, responder, oracle, memory, ways)

    # Why: random traffic under random line backpressure, latency and request
    # gaps reaches orderings the directed cases miss, and maintenance
    # afterwards must publish every dirty byte. The injected failures above are
    # logged by the responder but not by the oracle, so the transaction
    # comparison restarts here.
    responder.rng = random.Random(seed + 1)
    responder.transactions.clear()
    oracle.line_transactions.clear()
    stream = []
    for _ in range(500):
        # Why: six tags over four sets overflow four ways as well as two, so
        # evictions happen in both geometries.
        address = (rng.randrange(6) << 13) | (rng.randrange(4) << 4) | (rng.randrange(4) << 2)
        if rng.random() < 0.4:
            byte_enable = rng.choice((0b0001, 0b0010, 0b0100, 0b1000, 0b0011, 0b1100, 0b1111))
            stream.append(Op(address, True, rng.getrandbits(32), byte_enable))
        else:
            stream.append(Op(address))
    _check_stream(oracle, await run_stream(dut, stream, rng=rng, gap_probability=0.2), "random")
    await _check_transactions(responder, oracle, "random traffic")
    oracle.maintain()
    assert not await maintain(dut)
    await _check_transactions(responder, oracle, "maintenance after traffic")
    assert memory == oracle.memory, "maintenance after random traffic lost dirty bytes"
    service.cancel()
    cocotb.log.info("seed=%d hits=%d misses=%d line_transactions=%d", seed, oracle.hits, oracle.misses, len(responder.transactions))


async def _setword_cases(dut, rng: random.Random, responder: LineResponder,
                         oracle: CacheModel, memory: bytearray, ways: int) -> None:
    """Per-set metadata word: same-set hazards, dirty eviction, the halt walk
    budget and the reset sweep. The injected failures before this are not in
    the oracle's log, so both logs restart here and again on return."""
    responder.transactions.clear()
    responder.prefetches.clear()
    oracle.line_transactions.clear()
    # Why: same-set back-to-back hazards. Every request after the first is
    # accepted on the edge that writes the previous hit's word (dirty and PLRU)
    # or follows an install, so a lost forward shows up as a wrong victim, a
    # missing or extra writeback, or wrong data. Pinned to one set with WAYS +
    # 2 tags, with no request gaps.
    def same_set(tag: int, word: int = 0) -> int:
        return (tag << 13) | 0x0A00 | (word << 2)

    # Why: this fixed prefix makes each hazard happen at least once before the
    # random part: fill every way, store-hit each, then a load hit, a
    # dirty-eviction miss, a store hit on the install and a load hit on its
    # line, back to back.
    stream = [Op(same_set(tag)) for tag in range(ways)]
    stream += [Op(same_set(tag), True, 0xD1D1_0000 | tag, 0b1111)
               for tag in range(ways)]
    stream += [Op(same_set(1)),
               Op(same_set(ways)),
               Op(same_set(ways), True, 0x0000_0E0E, 0b0011),
               Op(same_set(ways, 1))]
    for _ in range(300):
        address = same_set(rng.randrange(ways + 2), rng.randrange(4))
        if rng.random() < 0.4:
            stream.append(Op(address, True, rng.getrandbits(32), rng.choice((0b0001, 0b1111))))
        else:
            stream.append(Op(address))
    before = len(responder.transactions)
    _check_stream(oracle, await run_stream(dut, stream), "same-set hazards")
    await _check_transactions(responder, oracle, "same-set hazards")
    assert any(w for w, _, _ in _transactions(responder)[before + ways:]), "the miss did not evict a dirty line"
    evictions = sum(w for w, _, _ in _transactions(responder)[before:])
    assert evictions > 20, f"same-set stream evicted only {evictions} dirty lines"
    oracle.maintain()
    assert not await maintain(dut)
    assert memory == oracle.memory, "maintenance after same-set hazards lost dirty bytes"

    # Ref: memory MAS "Maintained Halt bound": a clean walk is one entry per
    # clock plus its read cycle and the registered done pulse, and a walk with
    # every entry dirty stays within WALK_SLOT_CLOCKS per entry.
    entries = ways * SET_COUNT
    error, cycles = await _maintain_cycles(dut, limit=entries + 16)
    assert not error and cycles == entries + 3, f"clean walk took {cycles} clocks"
    stream = [Op((tag << 13) | (index << 4), True, (tag << 16) | index, 0b1111)
              for tag in range(ways) for index in range(SET_COUNT)]
    _check_stream(oracle, await run_stream(dut, stream), "fill dirty")
    before = len(responder.transactions)
    oracle.maintain()
    error, cycles = await _maintain_cycles(dut, limit=entries * WALK_SLOT_CLOCKS)
    assert not error, "dirty walk reported an error"
    assert len(responder.transactions) - before == entries, "dirty walk missed entries"
    assert memory == oracle.memory, "dirty walk lost bytes"
    cocotb.log.info("halt walk: %d dirty entries in %d clocks (budget %d)",
                    entries, cycles, entries * WALK_SLOT_CLOCKS)
    await _check_transactions(responder, oracle, "dirty walk")

    # Why: reset must clear every word: a dirty resident line is forgotten (no
    # writeback), an invalidate requested during the sweep waits for it and
    # then walks a clean cache, and a load held through the sweep refills.
    await cpu_access(dut, 0x0A10, write=True, write_data=0x5EED_0001, byte_enable=0b1111)
    before = len(responder.transactions)
    await reset_cache(dut)
    dut.maintenance_valid_i.value = 1
    held = 0
    while True:
        await ReadOnly()
        if int(dut.maintenance_ready_o.value):
            break
        await RisingEdge(dut.clk_i)
        held += 1
        assert held <= SET_COUNT, "maintenance never became ready after reset"
    assert held == SET_COUNT, f"maintenance accepted after {held} sweep clocks"
    await RisingEdge(dut.clk_i)
    await FallingEdge(dut.clk_i)
    dut.maintenance_valid_i.value = 0
    for _ in range(entries + 16):
        await ReadOnly()
        if int(dut.maintenance_done_o.value):
            assert not int(dut.maintenance_error_o.value)
            break
        await RisingEdge(dut.clk_i)
    else:
        raise AssertionError("maintenance during the sweep did not complete")
    await FallingEdge(dut.clk_i)
    assert len(responder.transactions) == before, "reset, sweep or clean walk issued line traffic"
    await _reset_and_sweep(dut, present=0x0A10)
    assert _transactions(responder)[before:] == [(False, 0x0A10 & ~0xF, 0)], (
        "load held through the sweep did not refill exactly once")
    observed, error = await cpu_access(dut, 0x0A10, expected_cycles=1)
    assert not error and observed == read_word(memory, 0x0A10) != 0x5EED_0001
    # Why: the oracle must restart from reset too, or it would expect hits on
    # lines the cache forgot: empty sets, then that refill.
    fresh = CacheModel(oracle.memory, writable=True, ways=ways)
    oracle.entries, oracle.plru = fresh.entries, fresh.plru
    oracle.access(0x0A10)
    oracle.access(0x0A10)
    oracle.line_transactions.clear()
    responder.transactions.clear()
    responder.prefetches.clear()


async def _count_prefetch_events(dut, counts: dict[str, int]) -> None:
    while True:
        await FallingEdge(dut.clk_i)
        for name in counts:
            counts[name] += int(getattr(dut, f"prefetch_{name}_event_o").value)


def _check_demand_traffic(responder: LineResponder, oracle: CacheModel, label: str) -> int:
    """The cache's demand line traffic is the oracle's, less refills served
    from the prefetch buffer; return how many refills the buffer served."""
    demand = [t for t, pf in zip(_transactions(responder), responder.prefetches) if not pf]
    prefetched = {addr for (_, addr, _), pf in zip(_transactions(responder), responder.prefetches)
                  if pf}
    for (write, addr, _), pf in zip(_transactions(responder), responder.prefetches):
        assert not (pf and write), f"{label}: prefetch write at {addr:#x}"
    actual = _by_line(demand)
    served = 0
    for write, addr, payload in oracle.line_transactions:
        observed = actual.get(addr, [])
        if observed and observed[0] == (write, payload):
            observed.pop(0)
        else:
            assert not write and addr in prefetched, (
                f"{label}: missing demand transaction write={write} address={addr:#x}")
            served += 1
    assert not any(actual.values()), f"{label}: unexpected demand transactions {actual}"

    return served


async def _settle(dut, cycles: int = 12) -> None:
    for _ in range(cycles):
        await RisingEdge(dut.clk_i)


@cocotb.test(timeout_time=3000, timeout_unit="us")
async def dcache_prefetch_contract(dut):
    """Next-line prefetch: buffer hits, store coherence, writeback and
    maintenance drops, and data and demand traffic equal to the plain cache."""
    seed = int(os.environ.get("DV_SEED", "30"))
    rng = random.Random(seed)
    ways = int(dut.WAYS.value)
    lines = int(dut.PREFETCH_LINES.value)
    assert lines in (1, 2), f"prefetch target needs PREFETCH_LINES 1 or 2, not {lines}"
    cocotb.log.info("D-cache geometry: %d ways, %d prefetch buffer lines", ways, lines)
    cocotb.start_soon(Clock(dut.clk_i, 40, unit="ns").start())
    initial = bytearray((index * 23 + 5) & 0xFF for index in range(0x20000))
    memory = bytearray(initial)
    oracle = CacheModel(bytearray(initial), writable=True, ways=ways)
    responder = LineResponder(dut, memory, latency=3)
    service = cocotb.start_soon(responder.run())
    await _reset_and_sweep(dut)
    counts = {"issue": 0, "useful": 0, "drop": 0, "cancel": 0, "deferred": 0}
    cocotb.start_soon(_count_prefetch_events(dut, counts))

    def prefetch_addresses() -> list[int]:
        return [addr for (_, addr, _), pf in zip(_transactions(responder), responder.prefetches)
                if pf]

    async def access(address: int, *, write: bool = False, data: int = 0,
                     byte_enable: int = 0, latency: int | None = None) -> None:
        expected = oracle.access(address, write=write, write_data=data, byte_enable=byte_enable)
        observed, error = await cpu_access(dut, address, write=write, write_data=data,
                                           byte_enable=byte_enable, expected_cycles=latency)
        assert not error and observed == expected, (
            f"write={write} address={address:#x} expected={expected:#010x} "
            f"observed={observed:#010x}")

    await access(0x1000)
    await _settle(dut)
    assert _transactions(responder) == [(False, 0x1000, 0), (False, 0x1010, 0)]
    assert responder.prefetches == [False, True]
    assert counts["issue"] == 1

    # Why: a prefetch hit must install the buffered line without a line request
    # and respond three edges after acceptance, then prefetch the next line.
    before = len(responder.transactions)
    # Why: separate cpu_access calls insert an idle gap, so the next demand is
    # presented on the response edge here; it must win over prefetch selection.
    ops = [Op(0x1014), Op(0x1020), Op(0x1024)]
    completions = await run_stream(dut, ops)
    _check_stream(oracle, completions, "response-edge demand priority")
    assert completions[0].latency == 3 and completions[2].latency == 1
    await _settle(dut)
    assert _transactions(responder)[before:] == [(False, 0x1020, 0), (False, 0x1030, 0)]
    assert responder.prefetches[before:] == [False, True]
    assert counts["useful"] == 1 and counts["issue"] == 2 and counts["cancel"] == 1, counts

    # Why: a store to a buffered line must install it with the store merged, so
    # the line is dirty and a later load and flush see it.
    await access(0x1034, write=True, data=0xA5A5_5A5A, byte_enable=0b1111, latency=3)
    await access(0x1034, latency=1)
    await access(0x1036, write=True, data=0x00C3_0000, byte_enable=0b0100, latency=1)
    await access(0x1034, latency=1)
    _check_demand_traffic(responder, oracle, "prefetch then store")

    oracle.maintain()
    assert not await maintain(dut)
    assert memory == oracle.memory, "flush lost a store to a prefetched line"
    await _settle(dut)

    # Why: line C is dirty in the cache when its previous line's miss
    # prefetches C from memory, so the buffered copy is stale. Evicting C
    # writes it back and must drop that copy, or the next miss on C reads stale
    # memory.
    line_c = 0x0500
    others = [(tag << 13) | line_c for tag in range(1, ways + 1)]
    await access(line_c + 4, write=True, data=0xC0DE_0001, byte_enable=0b1111)
    for address in others[:-1]:
        await access(address)
    await access(line_c - 0x10)
    await _settle(dut)
    assert prefetch_addresses()[-1] == line_c, "previous-line miss did not prefetch line C"
    drops = counts["drop"]
    before = len(responder.transactions)
    await access(others[-1])  # Why: tree PLRU after C and the others makes this access evict dirty line C
    await _settle(dut)
    assert (True, line_c) in [(w, a) for (w, a, _) in _transactions(responder)[before:]], (
        "line C was not written back")
    assert counts["drop"] > drops, "writeback did not drop the buffered copy of line C"
    before = len(responder.transactions)
    await access(line_c + 4)
    assert (False, line_c, 0) in _transactions(responder)[before:], (
        "line C was served from a stale prefetch buffer")
    _check_demand_traffic(responder, oracle, "stale copy")

    # Why: maintenance presented on the refill-response edge must suppress the
    # queued intent, and after an idle opportunity it discards an actually
    # issued line instead; counting both as one would hide a lost line.
    async def maintenance_on_response():
        for _ in range(80):
            await RisingEdge(dut.clk_i)
            await ReadOnly()
            if int(dut.cpu_response_valid_o.value):
                oracle.maintain()
                return await maintain(dut)
        raise AssertionError("no response for maintenance priority")

    for address, settle in ((0x3000, 0), (0x5000, 12)):
        oracle.maintain()
        assert not await maintain(dut)
        await _settle(dut)
        drops = counts["drop"]
        cancels = counts["cancel"]
        if not settle:
            priority_maintenance = cocotb.start_soon(maintenance_on_response())
        await access(address)
        if settle:
            await _settle(dut, settle)
            oracle.maintain()
            assert not await maintain(dut)
        else:
            assert not await priority_maintenance
        await _settle(dut)
        if settle:
            assert prefetch_addresses()[-1] == address + 0x10
            assert counts["drop"] == drops + 1, f"maintenance after {address:#x} dropped no line"
        else:
            assert address + 0x10 not in prefetch_addresses()[-1:], "maintenance offered a queued candidate"
            assert counts["cancel"] == cancels + 1, "maintenance did not cancel queued intent"
        before = len(responder.transactions)
        await access(address + 0x10)
        assert _transactions(responder)[before] == (False, address + 0x10, 0), (
            f"line {address + 0x10:#x} was served from the buffer after maintenance")
        assert not responder.prefetches[before]
    _check_demand_traffic(responder, oracle, "maintenance")

    # Why: unrelated queued intent must yield to demand, and no prefetch may
    # launch during the delayed demand response; the newer install supersedes
    # it.
    oracle.maintain()
    assert not await maintain(dut)
    before = len(responder.transactions)
    cancels = counts["cancel"]
    responder.latency = 12
    pending = cocotb.start_soon(run_stream(dut, [Op(0x8000), Op(0xC000)]))
    for _ in range(80):
        await FallingEdge(dut.clk_i)
        await ReadOnly()
        if pending.done():
            break
        if int(dut.line_request_valid_o.value):
            assert not int(dut.line_request_prefetch_o.value), "prefetch launched while demand outstanding"
    else:
        raise AssertionError("priority demand did not complete")
    _check_stream(oracle, await pending, "unrelated demand priority")
    responder.latency = 3
    await _settle(dut, 18)
    assert _transactions(responder)[before:] == [(False, 0x8000, 0), (False, 0xC000, 0), (False, 0xC010, 0)]
    assert responder.prefetches[before:] == [False, False, True]
    assert counts["cancel"] == cancels + 1 and counts["deferred"] > 0

    # Why: a presented prefetch is held even when another demand arrives,
    # because retracting it after its external offer would break the
    # ready/valid contract.
    oracle.maintain()
    assert not await maintain(dut)
    responder.block_prefetches = True
    await access(0x9000)
    await _settle(dut, 5)
    await ReadOnly()
    assert int(dut.line_request_valid_o.value) and int(dut.line_request_prefetch_o.value)
    assert int(dut.line_request_address_o.value) == 0x9010
    cancels = counts["cancel"]
    pending = cocotb.start_soon(access(0xD000))
    for _ in range(12):
        await FallingEdge(dut.clk_i)
        await ReadOnly()
        assert int(dut.line_request_valid_o.value) and int(dut.line_request_prefetch_o.value), "presented prefetch withdrawn under demand"
        assert int(dut.line_request_address_o.value) == 0x9010, "held prefetch payload changed"
        assert not pending.done(), "demand bypassed an unaccepted held prefetch"
    assert counts["cancel"] == cancels
    await FallingEdge(dut.clk_i)
    responder.block_prefetches = False
    await pending
    await _settle(dut, 12)
    _check_demand_traffic(responder, oracle, "priority and held contract")

    responder.rng = random.Random(seed + 1)
    for phase in range(3):
        stream = []
        cursors = [rng.randrange(0x400) << 4 for _ in range(3)]
        for _ in range(400):
            pick = rng.randrange(3)
            if rng.random() < 0.1:
                cursors[pick] = rng.randrange(0x1000) << 4
            elif rng.random() < 0.3:
                cursors[pick] = (cursors[pick] + 0x10) & 0xFFF0
            address = cursors[pick] | (rng.randrange(4) << 2)
            if rng.random() < 0.35:
                byte_enable = rng.choice((0b0001, 0b0010, 0b0100, 0b1000, 0b0011, 0b1100, 0b1111))
                stream.append(Op(address, True, rng.getrandbits(32), byte_enable))
            else:
                stream.append(Op(address))
        for done in await run_stream(dut, stream, rng=rng, gap_probability=0.2):
            op = done.op
            expected = oracle.access(op.address, write=op.write, write_data=op.write_data,
                                     byte_enable=op.byte_enable)
            assert not done.error and done.data == expected, (
                f"random {phase}: write={op.write} address={op.address:#x} "
                f"expected={expected:#010x} observed={done.data:#010x}")
        oracle.maintain()
        assert not await maintain(dut)
        assert memory == oracle.memory, f"random {phase}: maintenance lost dirty bytes"
    # Why: explicit idle opportunities alongside the dense randomized demand
    # stream keep substantial useful-line coverage without requiring
    # speculative issue during pending demand.
    for address in range(0x18000, 0x18800, 0x10):
        await access(address)
        await _settle(dut, 12)
    oracle.maintain()
    assert not await maintain(dut)
    assert memory == oracle.memory, "idle-opportunity flush lost bytes"
    await _settle(dut)
    served = _check_demand_traffic(responder, oracle, "random")
    issued = responder.prefetches.count(True)
    assert counts["issue"] == issued, f"issue events {counts['issue']} != prefetches {issued}"
    assert counts["useful"] == served, f"useful events {counts['useful']} != buffer refills {served}"
    assert counts["useful"] + counts["drop"] == issued, counts
    assert served > 100, f"random traffic used only {served} prefetched lines"
    service.cancel()
    cocotb.log.info("seed=%d hits=%d misses=%d prefetches=%d useful=%d dropped=%d",
                    seed, oracle.hits, oracle.misses, issued, counts["useful"], counts["drop"])


@cocotb.test(timeout_time=3000, timeout_unit="us")
async def dcache_write_buffer_contract(dut):
    """Read-first eviction, full-slot backpressure, reread ordering, drain
    errors and retry, and maintenance while the dirty victim is stalled."""
    cocotb.start_soon(Clock(dut.clk_i, 40, unit="ns").start())
    ways = int(dut.WAYS.value)
    memory = bytearray((i * 13 + 7) & 255 for i in range(0x20000))
    oracle = CacheModel(bytearray(memory), writable=True, ways=ways)
    responder = LineResponder(dut, memory, latency=12)
    service = cocotb.start_soon(responder.run())
    await _reset_and_sweep(dut)

    async def access(op: Op):
        expected = oracle.access(op.address, write=op.write, write_data=op.write_data,
                                 byte_enable=op.byte_enable)
        observed, error = await cpu_access(dut, op.address, write=op.write,
                                           write_data=op.write_data,
                                           byte_enable=op.byte_enable, limit=160)
        assert not error and observed == expected, f"buffer access {op.address:#x}: {observed:#x} != {expected:#x}"

    for tag in range(ways):
        await access(Op(tag << 13, True, 0xABCD_0000 | tag, 15))
    victim = oracle.entries[0][oracle.plru_victim(0)].tag << 13
    responder.block_writes = True
    before = len(responder.transactions)
    await access(Op(ways << 13))
    assert responder.transactions[before][0:2] == (False, ways << 13), "dirty miss did not refill first"
    assert int(dut.write_buffer_pending_o.value), "dirty victim was not retained after refill"
    assert read_word(memory, victim) != 0xABCD_0000 | (victim >> 13), "victim already wrote memory"

    # Why: an immediate reread while the old dirty victim's write is stalled
    # must wait for publication, or it would read the stale backing bytes.
    reread = cocotb.start_soon(access(Op(victim)))
    await _settle(dut, 18)
    assert not reread.done(), "immediate reread bypassed the stalled victim write"
    responder.block_writes = False
    await reread
    await _drain_buffer(dut)
    assert read_word(memory, victim) == 0xABCD_0000 | (victim >> 13)
    for entry in oracle.entries[0]:
        if entry.valid:
            await access(Op(entry.tag << 13, True, 0xABCD_0000 | entry.tag, 15))
    responder.block_writes = True
    await access(Op((ways + 1) << 13))

    # Why: a second dirty miss must not overwrite the occupied slot; the
    # responder checks ready/valid payload stability during this stall.
    pending = cocotb.start_soon(access(Op((ways + 2) << 13)))
    await _settle(dut, 18)
    await ReadOnly()
    assert int(dut.write_buffer_wait_o.value), "full buffer did not block another dirty eviction"
    assert not pending.done(), "full-slot miss completed before its drain"
    await FallingEdge(dut.clk_i)
    responder.block_writes = False
    await pending
    await _drain_buffer(dut)
    assert read_word(memory, victim) == 0xABCD_0000 | (victim >> 13)
    await access(Op(victim))

    # Why: a failed refill must leave the dirty victim in its array, or a
    # failed replacement would own the only copy in a slot.
    oracle.maintain()
    assert not await maintain(dut)
    for tag in range(ways):
        await access(Op(0x100 | (tag << 13), True, 0xC0DE_0000 | tag, 15))
    victim = oracle.entries[0x10][oracle.plru_victim(0x10)].tag << 13 | 0x100
    failing = 0x100 | (ways << 13)
    responder.fail_read_addresses.add(failing)
    assert (await cpu_access(dut, failing, limit=160))[1], "refill failure was hidden"
    assert not int(dut.write_buffer_pending_o.value), "failed refill displaced a dirty array line"
    assert (await cpu_access(dut, victim, expected_cycles=1))[0] == 0xC0DE_0000 | (victim >> 13)
    oracle.access(victim)  # Why: mirror the directed hit in the oracle, since it updates replacement order
    responder.fail_read_addresses.clear()
    victim = oracle.entries[0x10][oracle.plru_victim(0x10)].tag << 13 | 0x100

    # Why: only tag zero is dirty and touching the other clean ways makes it
    # the next victim, so the same-line hazard is isolated from full-slot
    # pressure.
    oracle.maintain()
    assert not await maintain(dut)
    for tag in range(ways):
        await access(Op(0x300 | (tag << 13)))
    await access(Op(0x300, True, 0xB00F_0000, 15))
    for tag in range(1, ways):
        await access(Op(0x300 | (tag << 13)))
    victim = 0x300
    failing = 0x300 | (ways << 13)
    assert oracle.entries[0x30][oracle.plru_victim(0x30)].tag == 0

    # Why: a background error must retain the displaced bytes: a same-line
    # reread must retry or drain (and return error if that fails), never fetch
    # stale memory; maintenance also reports error, and a retry drains
    # losslessly.
    responder.fail_write_addresses.add(victim)
    await access(Op(failing))
    await _settle(dut, 24)
    assert int(dut.write_buffer_pending_o.value), "failed background drain lost dirty bytes"
    _, error = await cpu_access(dut, victim, limit=160)
    assert error, "read of failed buffered victim returned stale memory instead of drain error"
    assert await maintain(dut), "failed buffer drain did not fail maintenance"
    assert int(dut.write_buffer_pending_o.value), "maintenance error lost the buffered victim"
    responder.fail_write_addresses.clear()
    oracle.maintain()
    assert not await maintain(dut)
    assert memory == oracle.memory, "retry failed to publish all dirty bytes"

    # Why: maintenance accepted while a write is presented but unaccepted must
    # not complete or invalidate away its only copy, so it drains before the
    # scan.
    for tag in range(ways):
        await access(Op(0x200 | (tag << 13), True, 0x5EED_0000 | tag, 15))
    responder.block_writes = True
    await access(Op(0x200 | (ways << 13)))
    pending_maintenance = cocotb.start_soon(maintain(dut))
    await _settle(dut, 20)
    assert not pending_maintenance.done(), "maintenance completed while buffer write was stalled"
    responder.block_writes = False
    assert not await pending_maintenance
    oracle.maintain()
    assert memory == oracle.memory, "halt/flush did not publish the stalled victim"
    service.cancel()
