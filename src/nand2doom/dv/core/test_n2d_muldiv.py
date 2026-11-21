"""Public-port contract of n2d_muldiv against the independent RV32M model.

The test drives the issue interface exactly as n2d_ctrl does: operands and
funct3 with a one-cycle issue pulse, consume while the result is valid, and
kill at any divide phase. Expected values come from ``m_result`` in
``rv32im_model.py``; expected divide timing and result reuse come from an
independent latency model of the documented early-out rules, so a value,
latency, or reuse defect fails with the operation and cycle.
"""

from __future__ import annotations

import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge

from .pipeline_cases import M_CORNERS, m_corner_pairs
from .rv32im_model import MASK32, m_result

CLOCK_HZ = 25_000_000
CLOCK_PERIOD_NS = 40
MUL_LATENCY = 2  # Ref: core MAS "Multiply and divide": the product registers two edges after issue


def _magnitude(value: int, signed_op: bool) -> int:
    return (-value) & MASK32 if signed_op and value >> 31 else value


def divide_cycles(funct3: int, left: int, right: int, reused: bool) -> int:
    """Edges from the issue edge until result_valid_o is first high."""
    if reused or right == 0:
        return 1
    signed_op = funct3 in (4, 6)
    dividend = _magnitude(left, signed_op)
    divisor = _magnitude(right, signed_op)
    if dividend == 0 or dividend.bit_length() < divisor.bit_length():
        return 2  # Ref: core MAS "Multiply and divide": setup decides quotient zero
    return 2 + dividend.bit_length() - divisor.bit_length() + 1


class Harness:
    def __init__(self, dut) -> None:
        self.dut = dut
        self.cycle = 0
        # Ref: core MAS "Multiply and divide" (Reuse): reuse key of the last
        # divide that completed (raw operands, signedness).
        self.last_key: tuple[int, int, bool] | None = None

    def idle_inputs(self) -> None:
        dut = self.dut
        dut.issue_i.value = 0
        dut.funct3_i.value = 0
        dut.rs1_i.value = 0
        dut.rs2_i.value = 0
        dut.consume_i.value = 0
        dut.kill_i.value = 0

    async def tick(self) -> None:
        await RisingEdge(self.dut.clk_i)
        await FallingEdge(self.dut.clk_i)
        self.cycle += 1

    async def reset(self) -> None:
        self.idle_inputs()
        self.dut.rst_i.value = 1
        await self.tick()
        await self.tick()
        self.dut.rst_i.value = 0
        self.last_key = None
        assert not int(self.dut.busy_o.value) and not int(self.dut.result_valid_o.value)

    def drive_issue(self, funct3: int, left: int, right: int) -> None:
        dut = self.dut
        dut.issue_i.value = 1
        dut.funct3_i.value = funct3
        dut.rs1_i.value = left
        dut.rs2_i.value = right

    def key(self, funct3: int, left: int, right: int) -> tuple[int, int, bool]:
        return (left, right, funct3 in (4, 6))

    async def divide(self, funct3: int, left: int, right: int, hold: int = 0) -> int:
        """Issue one divide, check latency and value, hold, then consume."""
        dut = self.dut
        key = self.key(funct3, left, right)
        reused = key == self.last_key
        expected_cycles = divide_cycles(funct3, left, right, reused)
        expected = m_result(funct3, left, right)
        self.drive_issue(funct3, left, right)
        await self.tick()
        self.idle_inputs()
        # Why: scribbling the operand ports after issue catches a divider that
        # reads them instead of its registered operands.
        dut.rs1_i.value = left ^ MASK32
        dut.rs2_i.value = right ^ 0x5A5A_5A5A
        edges = 1
        while not int(dut.result_valid_o.value):
            assert int(dut.busy_o.value), f"divide idle before result at edge {edges}"
            await self.tick()
            edges += 1
            assert edges <= 40, "divide did not finish"
        context = (f"funct3={funct3} left=0x{left:08x} right=0x{right:08x} "
                   f"reused={reused} cycle={self.cycle}")
        assert edges == expected_cycles, f"latency {edges} != {expected_cycles}: {context}"
        assert not int(dut.busy_o.value), context
        self.last_key = key
        for _ in range(hold + 1):
            observed = int(dut.div_result_o.value)
            assert observed == expected, f"expected 0x{expected:08x} got 0x{observed:08x}: {context}"
            assert int(dut.result_valid_o.value), context
            if _ < hold:
                await self.tick()
        dut.consume_i.value = 1
        await self.tick()
        dut.consume_i.value = 0
        assert not int(dut.result_valid_o.value) and not int(dut.busy_o.value), context
        return edges


async def _multiply_stream(harness: Harness, rng: random.Random, pairs) -> int:
    """Up to one multiply issue per cycle; each product word appears two
    edges after its issue edge, with random idle gaps in the stream."""
    dut = harness.dut
    stream = [(funct3, left, right) for left, right in pairs for funct3 in range(4)]
    rng.shuffle(stream)
    slots: list = []
    for operation in stream:
        if rng.random() < 0.15:
            slots.append(None)
        slots.append(operation)
    for index in range(len(slots) + MUL_LATENCY - 1):
        operation = slots[index] if index < len(slots) else None
        if operation is None:
            harness.idle_inputs()
            dut.rs1_i.value = rng.getrandbits(32)
            dut.rs2_i.value = rng.getrandbits(32)
            dut.funct3_i.value = rng.getrandbits(2)
        else:
            harness.drive_issue(*operation)
        await harness.tick()
        due = slots[index - (MUL_LATENCY - 1)] if index >= MUL_LATENCY - 1 else None
        if due is not None:
            funct3, left, right = due
            expected = m_result(funct3, left, right)
            observed = int(dut.mul_result_o.value)
            assert observed == expected, (
                f"MUL funct3={funct3} left=0x{left:08x} right=0x{right:08x} "
                f"expected=0x{expected:08x} got=0x{observed:08x} cycle={harness.cycle}")
    harness.idle_inputs()
    return len(stream)


async def _divide_corners(harness: Harness, rng: random.Random, pairs) -> int:
    count = 0
    for left, right in pairs:
        # Ref: core MAS "Multiply and divide" (Reuse): a divide identical to
        # the last completed one finishes at issue, so DIV then REM, DIVU then
        # REMU and then the reverse order within each signedness all take the
        # reuse path.
        for funct3 in (4, 6, 5, 7, 6, 4):
            await harness.divide(funct3, left, right, hold=rng.choice((0, 0, 1, 3)))
            count += 1
    return count


async def _kill_phases(harness: Harness, rng: random.Random) -> None:
    """Kill in setup, during iterations and while done; then re-issue."""
    dut = harness.dut
    cases = [(0x7654_3210, 13), (0xFFFF_FFF9, 3), (0x8000_0000, 0xFFFF_FFFF), (5, 7), (99, 0)]
    for left, right in cases:
        for funct3 in (4, 5):
            total = divide_cycles(funct3, left, right, reused=False)
            for kill_after in range(0, total + 2):
                harness.last_key = None
                # Why: a divide over other operands first, so the divide that
                # is killed below is a fresh start, not a reuse hit.
                await harness.divide(funct3, left ^ 0x10, right | 1)
                harness.drive_issue(funct3, left, right)
                await harness.tick()
                harness.idle_inputs()
                for _ in range(kill_after):
                    await harness.tick()
                finished = bool(int(dut.result_valid_o.value))
                dut.kill_i.value = 1
                await harness.tick()
                dut.kill_i.value = 0
                assert not int(dut.busy_o.value) and not int(dut.result_valid_o.value)
                # Ref: core MAS "Multiply and divide" (Reuse): a finished
                # divide stays reusable; an abandoned one must not.
                harness.last_key = (left, right, funct3 in (4, 6)) if finished else None
                await harness.divide(funct3 | 2, left, right)
                await harness.divide(funct3, left, right)
    # Why: a kill on the issue edge must win over the issue, or a killed
    # instruction's divide would run and could be reused.
    harness.drive_issue(4, 1000, 7)
    dut.kill_i.value = 1
    await harness.tick()
    harness.idle_inputs()
    assert not int(dut.busy_o.value) and not int(dut.result_valid_o.value)


async def _reset_cancels(harness: Harness) -> None:
    dut = harness.dut
    harness.drive_issue(5, 0xFFFF_FFFF, 3)
    await harness.tick()
    harness.idle_inputs()
    await harness.tick()
    await harness.tick()
    assert int(dut.busy_o.value)
    await harness.reset()
    # Ref: core MAS "Multiply and divide" (Reuse): reset forgets the reusable
    # result: the same divide recomputes.
    await harness.divide(5, 0xFFFF_FFFF, 3)


async def _reset_forgets_consumed_divide(harness: Harness) -> None:
    """Reset invalidates a completed, consumed and demonstrably reusable pair."""
    left, right = 0x7654_3210, 13
    for funct3 in (4, 5):
        await harness.reset()
        fresh = divide_cycles(funct3, left, right, reused=False)
        assert fresh > 1, "fixture must distinguish recompute from reuse"
        assert await harness.divide(funct3, left, right) == fresh
        # Why: consuming the paired remainder proves the reuse mark was set
        # before reset, so the recompute after reset shows that reset cleared
        # it.
        assert await harness.divide(funct3 | 2, left, right) == 1
        await harness.reset()
        assert await harness.divide(funct3, left, right) == fresh
        assert await harness.divide(funct3 | 2, left, right) == 1
    cocotb.log.info("reset consumed-divide cache: signed/unsigned fresh latency checked")


async def _multiply_beside_divide(harness: Harness, rng: random.Random) -> None:
    """The multiply pipe is independent of a divide in flight."""
    dut = harness.dut
    harness.last_key = None
    harness.drive_issue(5, 0xFFFF_FFFF, 1)
    await harness.tick()
    products = []
    for _ in range(6):
        left, right = rng.getrandbits(32), rng.getrandbits(32)
        harness.drive_issue(1, left, right)
        products.append(m_result(1, left, right))
        await harness.tick()
        assert int(dut.busy_o.value)
        if len(products) > MUL_LATENCY - 1:
            expected = products[-MUL_LATENCY]
            assert int(dut.mul_result_o.value) == expected
    harness.idle_inputs()
    while not int(dut.result_valid_o.value):
        await harness.tick()
    assert int(dut.div_result_o.value) == 0xFFFF_FFFF
    dut.consume_i.value = 1
    await harness.tick()
    dut.consume_i.value = 0
    harness.last_key = (0xFFFF_FFFF, 1, False)


@cocotb.test(timeout_time=20, timeout_unit="ms")
async def muldiv_contract(dut) -> None:
    assert int(os.environ["DV_CLOCK_HZ"]) == CLOCK_HZ
    seed = int(os.environ["DV_SEED"])
    maximum = int(os.environ["DV_MAX_CYCLES"])
    rng = random.Random(seed)
    cocotb.start_soon(Clock(dut.clk_i, CLOCK_PERIOD_NS, unit="ns").start())
    harness = Harness(dut)
    await FallingEdge(dut.clk_i)
    await harness.reset()

    pairs = m_corner_pairs(rng, random_pairs=256)
    multiplies = await _multiply_stream(harness, rng, pairs)
    divides = await _divide_corners(harness, rng, pairs)
    await _kill_phases(harness, rng)
    await _reset_cancels(harness)
    await _reset_forgets_consumed_divide(harness)
    await _multiply_beside_divide(harness, rng)
    assert harness.cycle <= maximum, f"{harness.cycle} cycles exceed {maximum}"
    cocotb.log.info(
        "n2d_muldiv PASS seed=%d corners=%d pairs=%d multiplies=%d divides=%d cycles=%d",
        seed, len(M_CORNERS), len(pairs), multiplies, divides, harness.cycle,
    )
