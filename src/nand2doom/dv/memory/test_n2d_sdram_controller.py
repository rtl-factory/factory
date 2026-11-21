from __future__ import annotations

import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge

from src.nand2doom.dv.memory.sdram_pin_model import (
    ACTIVE, MIN_EDGES, MODE_VALUE, MRS, NOP, PRECHARGE, READ, REFRESH, WRITE,
    SdramPinModel, join, split,
)


MAX_CYCLES = int(os.environ.get("DV_MAX_CYCLES", "12000"))
POISON = 0xD15A
# Ref: line service from acceptance edge to registered response
# (wiki/src/nand2doom/rtl/memory/mas.md).
LATENCY = {("read", "closed"): 10, ("read", "hit"): 9, ("read", "miss"): 11,
           ("write", "closed"): 9, ("write", "hit"): 8, ("write", "miss"): 10}


def check_model_rejects_violations() -> None:
    """The pin model's datasheet minimums are oracles too: the shortest legal
    spacing passes and one edge less fails, for each parameter the trimmed
    controller now uses at its minimum (tRP, tRCD) or keeps at two clocks
    (tRFC), plus the DQ turnaround and burst interruption."""
    assert MIN_EDGES == {"tRCD": 1, "tRP": 1, "tRC": 2, "tRAS": 1, "tRRD": 1,
                         "tWR": 1, "tRFC": 2, "tMRD": 1}, MIN_EDGES

    def ready() -> SdramPinModel:
        model = SdramPinModel()
        model.edge(0, MRS, 0, MODE_VALUE)
        return model

    def expect(fail: str, steps) -> None:
        model = ready()
        try:
            for now, cmd, bank, address, *extra in steps:
                model.edge(now, cmd, bank, address, *extra)
        except AssertionError as error:
            assert fail in str(error), (fail, str(error))
            return
        raise AssertionError(f"pin model accepted a {fail}")

    legal = ready()
    for now, cmd, bank, address in ((10, ACTIVE, 0, 5), (11, READ, 0, 0), (19, NOP, 0, 0),
                                    (20, PRECHARGE, 0, 0), (21, ACTIVE, 0, 6), (22, WRITE, 0, 0)):
        legal.edge(now, cmd, bank, address, 0, 0, 1 if cmd == WRITE else 0)
    # Why: there is no separate tRC case, because at 25 MHz one-clock tRAS plus
    # tRP already implies tRC (two clocks).
    expect("tRP violation", [(10, ACTIVE, 0, 5), (12, PRECHARGE, 0, 0), (12, ACTIVE, 0, 6)])
    expect("tRCD violation", [(10, ACTIVE, 0, 5), (10, READ, 0, 0)])
    expect("tRFC violation", [(10, REFRESH, 0, 0), (11, ACTIVE, 0, 5)])
    expect("interrupts a read burst", [(10, ACTIVE, 0, 5), (11, READ, 0, 0), (15, PRECHARGE, 0, 0)])
    expect("DQ turnaround violation", [(10, ACTIVE, 0, 5), (11, READ, 0, 0), (20, WRITE, 0, 0, 0, 0, 1)])


def command(dut) -> tuple[int, int, int]:
    return int(dut.sdram_ras_n_o.value), int(dut.sdram_cas_n_o.value), int(dut.sdram_we_n_o.value)


@cocotb.test()
async def sdram_pin_timing_contract(dut):
    seed = int(os.environ.get("DV_SEED", "31"))
    rng = random.Random(seed)
    cocotb.log.info("seed=%d max_cycles=%d", seed, MAX_CYCLES)
    check_model_rejects_violations()
    cocotb.start_soon(Clock(dut.clk_i, 40, unit="ns").start())
    dut.reset_i.value = 1
    dut.request_valid_i.value = 0
    dut.request_write_i.value = 0
    dut.request_address_i.value = 0
    dut.request_write_data_i.value = 0
    dut.request_byte_enable_i.value = 0
    dut.sdram_dq_i.value = POISON
    for _ in range(3): await RisingEdge(dut.clk_i)
    dut.reset_i.value = 0

    cycle = {"value": 0}
    model = SdramPinModel()
    init_commands: list[tuple[int, int, int]] = []

    async def drive_read_data():
        while True:
            await FallingEdge(dut.clk_i)
            dut.sdram_dq_i.value = model.due.pop(cycle["value"] + 1, POISON)

    async def strict_device():
        while True:
            await RisingEdge(dut.clk_i)
            cycle["value"] += 1
            cmd = command(dut)
            if not int(dut.init_done_o.value) and cmd != NOP:
                init_commands.append(cmd)
            assert int(dut.sdram_cke_o.value) and not int(dut.sdram_cs_n_o.value)
            model.edge(cycle["value"], cmd, int(dut.sdram_bank_o.value),
                       int(dut.sdram_address_o.value), int(dut.sdram_dq_o.value),
                       int(dut.sdram_dqm_o.value), int(dut.sdram_dq_oe_o.value))

    cocotb.start_soon(drive_read_data())
    cocotb.start_soon(strict_device())
    for _ in range(MAX_CYCLES):
        await RisingEdge(dut.clk_i)
        if int(dut.init_done_o.value): break
    else: raise AssertionError("SDRAM initialization timeout")
    assert init_commands == [PRECHARGE, REFRESH, REFRESH, MRS], init_commands
    assert int(dut.init_done_o.value)

    async def transact(write: bool, address: int, data: int = 0, enables: int = 0):
        """One request; returns (data, error, clocks from acceptance to response)."""
        dut.request_write_i.value = write
        dut.request_address_i.value = address
        dut.request_write_data_i.value = data
        dut.request_byte_enable_i.value = enables
        dut.request_valid_i.value = 1
        for _ in range(MAX_CYCLES):
            await RisingEdge(dut.clk_i)
            if int(dut.request_ready_o.value): break
        else: raise AssertionError("SDRAM request was never accepted")
        accepted = cycle["value"]
        dut.request_valid_i.value = 0
        for _ in range(MAX_CYCLES):
            await RisingEdge(dut.clk_i)
            if int(dut.response_valid_o.value):
                return (int(dut.response_data_o.value), int(dut.response_error_o.value),
                        cycle["value"] - accepted - 1)
        raise AssertionError("SDRAM transaction timeout")

    # Why: each directed burst pins one mapping: byte enables become per-beat
    # DQM, the line lands at the mapped bank/row/column, and each open-row case
    # has its latency.
    async def directed(write, address, data=0, enables=0, case="hit"):
        hits, activates = model.row_hits, model.activates
        observed, error, latency = await transact(write, address, data, enables)
        assert not error, f"unexpected error at {address:#x}"
        kind = "write" if write else "read"
        assert latency == LATENCY[(kind, case)], (kind, case, hex(address), latency)
        assert (model.row_hits - hits, model.activates - activates) == \
            {"hit": (1, 0), "closed": (0, 1), "miss": (0, 1)}[case], (kind, case)
        return observed

    line0 = int.from_bytes(bytes((seed + i) & 0xFF for i in range(16)), "little")
    await directed(True, 0x001230, line0, 0xFFFF, "closed")
    bank, row, column = split(0x001230)
    assert (bank, row, column) == (2, 0, 0x118)
    assert [model.words[join(bank, row, column + beat)] for beat in range(8)] == \
        [(line0 >> (16 * beat)) & 0xFFFF for beat in range(8)], "burst write beat order"
    observed = await directed(False, 0x001230, case="hit")
    assert observed == line0, f"CAS-two burst read expected={line0:032x} observed={observed:032x}"
    # Why: same bank, different row: only that bank may be precharged before
    # the activate, or other banks lose their open rows.
    patch = int.from_bytes(bytes(0xE0 + i for i in range(16)), "little")
    other_row = 0x1001230
    assert split(other_row)[0] == bank and split(other_row)[1] != row
    await directed(True, 0x000800, 0, 0x0000, "closed")  # Why: opens bank one, which the single-bank precharge must keep open
    await directed(True, other_row, patch, 0x00F0, "miss")
    assert 1 in model.rows, "single-bank precharge must keep other banks open"
    observed = await directed(False, other_row, case="hit")
    expected = sum((((0xE0 + i) if 4 <= i < 8 else 0) & 0xFF) << (8*i) for i in range(16))
    assert observed == expected, "DQM masked burst write mismatch"
    observed = await directed(False, 0x001230, case="miss")
    assert observed == line0, "row-miss read data"
    await directed(True, 0x001240, ~line0 & ((1 << 128) - 1), 0x8001, "hit")
    observed = await directed(False, 0x001240, case="hit")
    assert observed == (((~line0) & 0xFF) | (((~line0) >> 120 & 0xFF) << 120)), "edge-beat DQM"
    await directed(False, join(3, 0x0C00, 0x118), case="closed")
    _, error, _ = await transact(False, 0x1003)
    assert error, "misaligned line request was not rejected"
    # Why: the common controller ignores byte enables on reads, because
    # nand2mario drives them.
    _, error, _ = await transact(False, 0x1000, 0, 0x0001)
    assert not error, "a read with byte enables was rejected"

    # Why: back-to-back random traffic (each request presented on the clock
    # after the previous response) reaches refresh interleavings the directed
    # cases miss: every response must match a byte-level reference, refresh
    # interrupts open rows, and the deadline holds across intervals.
    regions = [rng.randrange(0, 1 << 26, 1 << 16) for _ in range(3)]
    shadow: dict[int, int] = {}
    hits_before, activates_before = model.row_hits, model.activates
    for _ in range(160):
        region = rng.choice(regions)
        offset = rng.randrange(0, 1 << 16, 16) if rng.random() < 0.3 else \
            rng.choice((0x0000, 0x0010, 0x0800, 0x2000, 0x4810))
        address = region + offset
        if rng.random() < 0.5:
            data = rng.getrandbits(128)
            enables = rng.choice((0xFFFF, rng.getrandbits(16)))
            _, error, _ = await transact(True, address, data, enables)
            for lane in range(16):
                if enables >> lane & 1:
                    shadow[address + lane] = (data >> (8 * lane)) & 0xFF
        else:
            observed, error, _ = await transact(False, address)
            expected = sum(shadow.get(address + lane, 0) << (8 * lane) for lane in range(16))
            assert observed == expected, f"random read {address:#x} expected={expected:032x} observed={observed:032x}"
        assert not error
    dut.request_valid_i.value = 0
    gaps = [right - left for left, right in zip(model.refresh, model.refresh[1:])]
    assert len(gaps) >= 8
    assert max(gaps) <= 195, f"refresh deadline exceeded: gaps={gaps}"
    assert model.row_hits - hits_before >= 10 and model.activates - activates_before >= 20
    for _ in range(64):
        await RisingEdge(dut.clk_i)
    assert not model.due, "read beats left after the last burst"
    cocotb.log.info("PASS init=%s refresh_count=%d max_gap=%d bursts=%d row_hits=%d "
                    "activates=%d precharges=%d", init_commands, len(model.refresh),
                    max(gaps), model.bursts, model.row_hits, model.activates, model.precharges)
