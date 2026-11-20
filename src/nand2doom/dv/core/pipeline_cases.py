"""Hazard, flush, serialization, halt and random-stream cases.

Every case runs through the model-checked runner against the six-stage
``n2d_rv32im_core``: a forwarding, stall, flush or drain defect shows up as an
observation that differs from the independent instruction-set model. Cases
that probe a pipeline timing window (distance, halt offset) are swept.
"""

from __future__ import annotations

import random

import cocotb

from .core_runner import HaltPlan, PortTiming, RunResult, run_program, word
from .rv32im_model import (
    encode_b,
    encode_csr,
    encode_i,
    encode_j,
    encode_r,
    encode_s,
    encode_u,
    load_immediate,
    m_result,
)

NOP = encode_i(0, 0, 0, 0)
EBREAK = 0x0010_0073
ECALL = 0x0000_0073
MRET = 0x3020_0073

# Trap handler at word 1..4 (mtvec = 4): resume after the faulting word.
HANDLER = (
    encode_csr(0x341, 0, 2, 9),  # x9 = mepc
    encode_i(4, 9, 0, 9),
    encode_csr(0x341, 9, 1, 0),  # mepc = x9
    MRET,
)


def with_handler(body: list[int]) -> list[int]:
    """Word 0 skips the handler; the body starts by setting mtvec = 4."""
    prologue = [encode_j(4 * (1 + len(HANDLER)), 0), *HANDLER]
    setup = [encode_i(4, 0, 0, 9), encode_csr(0x305, 9, 1, 0)]
    return prologue + setup + body + [EBREAK]


def _random_timing(rng: random.Random) -> tuple[PortTiming, PortTiming]:
    return (
        PortTiming(ready_probability=rng.choice((1.0, 0.8, 0.5)), latency=rng.choice((1, (1, 3), (1, 6)))),
        PortTiming(ready_probability=rng.choice((1.0, 0.8, 0.5)), latency=rng.choice((1, (1, 3), (1, 6)))),
    )


# ---------------------------------------------------------------------------
# Producer/consumer distance sweep
# ---------------------------------------------------------------------------

def _producers() -> dict[str, list[int]]:
    """Each producer writes x5 from x1/x2 (x1=0x40, x2=7, x3=data base)."""
    return {
        "alu": [encode_r(0, 2, 1, 0, 5)],
        "lui": [encode_u(0x0000_4000, 5, 0x37)],
        "load": [encode_i(0, 3, 2, 5, 0x03)],
        "load_byte": [encode_i(1, 3, 4, 5, 0x03)],
        "mul": [encode_r(1, 2, 1, 0, 5)],
        "mulh": [encode_r(1, 1, 1, 3, 5)],
        "div": [encode_r(1, 2, 1, 4, 5)],
        "csr": [encode_csr(0x340, 0, 2, 5)],
        "jal_link": [encode_j(4, 5)],
    }


def _consumers() -> dict[str, list[int]]:
    """Each consumer reads x5 at the instruction distance being swept."""
    return {
        "alu_rs1": [encode_i(3, 5, 0, 6)],
        "alu_rs2": [encode_r(0x20, 5, 1, 0, 6)],
        "store_data": [encode_s(8, 5, 3, 2)],
        "store_address": [encode_s(0, 2, 5, 0)],  # SB: never misaligned
        "branch": [encode_b(8, 5, 1, 1), encode_i(1, 0, 0, 6)],
        "mul_operand": [encode_r(1, 5, 2, 0, 6)],
        "csr_operand": [encode_csr(0x340, 5, 1, 6)],
    }


async def check_raw_distances(dut, rng: random.Random) -> None:
    initial = bytes(range(0x00, 0x80))
    base = [
        *load_immediate(1, 0x40),
        encode_i(7, 0, 0, 2),
        encode_i(0x20, 0, 0, 3),
        encode_csr(0x340, 1, 1, 0),  # mscratch = 0x40
    ]
    count = 0
    for producer_name, producer in _producers().items():
        for distance in range(1, 5):
            body = list(base)
            for consumer in _consumers().values():
                body += producer + [NOP] * (distance - 1) + consumer
            timing = PortTiming() if distance % 2 else PortTiming(latency=(1, 3), ready_probability=0.7)
            result = await run_program(
                dut, with_handler(body), stop_cause=3, initial_memory=initial,
                max_cycles=1200, instruction=PortTiming(), data=timing,
                seed=rng.getrandbits(32),
            )
            assert result.observations[-1].cause == 3, producer_name
            count += 1
    cocotb.log.info("RAW distance sweep: %d model-checked programs", count)


# ---------------------------------------------------------------------------
# Divide under memory holds
# ---------------------------------------------------------------------------

async def check_divide_under_hold(dut, rng: random.Random) -> None:
    program = [
        *load_immediate(1, -1000),
        encode_i(7, 0, 0, 2),
        encode_i(0x40, 0, 0, 3),
        encode_s(0, 1, 3, 2),             # store under a long data hold
        encode_r(1, 2, 1, 4, 4),          # DIV x4 = x1 / x2
        encode_i(0, 3, 2, 5, 0x03),       # independent load behind the divide
        encode_r(1, 4, 5, 6, 6),          # REM depends on both
        encode_r(0, 6, 4, 0, 7),          # immediate consumer of the REM
        encode_s(4, 7, 3, 2),
    ]
    for seed in range(4):
        await run_program(
            dut, program + [EBREAK], stop_cause=3, max_cycles=600,
            instruction=PortTiming(ready_probability=0.7, latency=(1, 4)),
            data=PortTiming(ready_probability=0.5, latency=(2, 8)),
            seed=rng.getrandbits(32) ^ seed,
        )


async def check_multiply_under_memory_hold(dut, rng: random.Random) -> None:
    """A MUL result must survive an intervening delayed/faulting memory op.

    The immediate memory instruction holds the pipeline while the multiplier
    product register keeps clocking. Independent retirement checking covers
    both the older product and its dependent ALU/MUL after release or trap.
    """
    left, right = 0x1357_9BDF, 0xFFFF_FFF9
    product = m_result(0, left, right)
    expected = (m_result(0, product, right) + product + 3) & 0xFFFF_FFFF
    address = 0x80
    count = 0
    for store in (False, True):
        memory = encode_s(address, 1, 0, 2) if store else encode_i(address, 0, 2, 6, 0x03)
        body = [
            *load_immediate(1, left), *load_immediate(2, right),
            encode_r(1, 2, 1, 0, 5),       # MUL x5: retained older product
            memory,                       # immediate delayed/faulting memory
            encode_i(3, 5, 0, 11),         # dependent ALU consumes x5
            encode_r(1, 2, 5, 0, 7),       # dependent MUL consumes x5
            encode_r(0, 11, 7, 0, 8),      # dependent ALU consumes both
            encode_s(0x4C, 8, 0, 2),
        ]
        for fault in (False, True):
            for latency in (1, 2, 3, 8, 17, 31):
                result = await run_program(
                    dut, with_handler(body), stop_cause=3, max_cycles=600,
                    initial_memory=bytes(range(256)),
                    instruction=PortTiming(), data=PortTiming(latency=latency),
                    data_error_addresses=(address,) if fault else (),
                    seed=rng.getrandbits(32),
                )
                context = f"store={store} fault={fault} latency={latency}"
                assert word(result.memory, 0x4C) == expected, context
                errors = [o for o in result.observations if o.trap and o.cause in (5, 7)]
                assert len(errors) == int(fault), context
                if fault:
                    assert errors[0].cause == (7 if store else 5), context
                count += 1
    cocotb.log.info("MUL memory lifetime: %d model-checked delayed/faulting programs", count)


# ---------------------------------------------------------------------------
# RV32M corner vectors, back-to-back multiply and divide/remainder pairs
# ---------------------------------------------------------------------------

M_CORNERS = (
    0x0000_0000, 0x0000_0001, 0xFFFF_FFFF, 0x0000_0002, 0xFFFF_FFFE,
    0x8000_0000, 0x7FFF_FFFF, 0x8000_0001, 0x0000_FFFF, 0x0001_0000,
    0x1234_5678, 0xFFFF_FFF9,
)

# MUL, MULH, MULHSU, MULHU, then DIV/REM and DIVU/REMU in pairs over the
# same operands, so each remainder follows its quotient.
_M_ORDER = (0, 1, 2, 3, 4, 6, 5, 7)


def m_corner_pairs(rng: random.Random, random_pairs: int = 48) -> list[tuple[int, int]]:
    """Every ordered corner pair plus seeded random and small-magnitude pairs."""
    pairs = [(left, right) for left in M_CORNERS for right in M_CORNERS]
    for index in range(random_pairs):
        if index % 3 == 2:
            pairs.append((rng.getrandbits(32), rng.getrandbits(rng.choice((4, 9, 16)))))
        else:
            pairs.append((rng.getrandbits(32), rng.getrandbits(32)))
    return pairs


def m_vector_program(pairs) -> list[int]:
    """Each pair: x1, x2 operands; all eight ops into x3..x10; dependents.

    The MUL result feeds the next MUL at distance one (MUL-to-MUL and
    MUL-use), and a store consumes the last remainder.
    """
    words: list[int] = []
    for left, right in pairs:
        words += load_immediate(1, left) + load_immediate(2, right)
        for offset, funct3 in enumerate(_M_ORDER):
            words.append(encode_r(1, 2, 1, funct3, 3 + offset))
        words.append(encode_r(1, 4, 3, 0, 11))   # MUL x11 = x3 * x4, MULH just before
        words.append(encode_r(1, 11, 11, 1, 12))  # MULH x12 = x11 * x11, back to back
        words.append(encode_r(0, 12, 10, 0, 13))  # ADD consumes the MULH immediately
        words.append(encode_s(0x40, 13, 0, 2))
    return words + [EBREAK]


async def check_m_corners(dut, rng: random.Random) -> int:
    """All eight M ops over the corner cross product, model-checked."""
    pairs = m_corner_pairs(rng)
    chunk = 24
    for start in range(0, len(pairs), chunk):
        program = m_vector_program(pairs[start:start + chunk])
        await run_program(dut, program, stop_cause=3, max_cycles=8000)
    cocotb.log.info("M corner vectors: %d pairs x 8 ops", len(pairs))
    return len(pairs)


async def check_divide_pairs(dut, rng: random.Random) -> None:
    """Quotient/remainder pairs that may and may not share one division.

    A remainder over unchanged operands follows its quotient directly, after
    an independent instruction, after a multiply, and after an operand is
    rewritten with the same value. A changed operand value, a signedness
    change, or an intervening divide with other operands must recompute.
    """
    body = [
        *load_immediate(1, -100_003),
        encode_i(77, 0, 0, 2),
        encode_r(1, 2, 1, 4, 3),         # DIV x3
        encode_r(1, 2, 1, 6, 4),         # REM x4, same operands, adjacent
        encode_r(1, 2, 1, 5, 5),         # DIVU: signedness changes
        encode_i(1, 0, 0, 9),            # independent ALU work
        encode_r(1, 2, 1, 7, 6),         # REMU after one instruction
        encode_r(1, 2, 1, 4, 7),         # DIV
        encode_i(1, 2, 0, 2),            # x2 changes
        encode_r(1, 2, 1, 6, 8),         # REM must recompute
        encode_i(0, 2, 0, 2),            # x2 rewritten with its own value
        encode_r(1, 2, 1, 6, 10),        # REM: same operands again
        encode_r(1, 2, 2, 4, 11),        # DIV x2/x2: other operands
        encode_r(1, 2, 1, 0, 12),        # MUL between
        encode_r(1, 2, 1, 4, 13),        # DIV x1/x2
        encode_r(1, 2, 1, 6, 14),        # REM x1/x2
        encode_r(0, 14, 13, 0, 15),      # immediate consumer of both
        encode_i(0, 0, 0, 2),            # divide by zero
        encode_r(1, 2, 1, 4, 16),
        encode_r(1, 2, 1, 6, 17),        # remainder of divide by zero
        *load_immediate(1, 0x8000_0000),
        encode_i(-1, 0, 0, 2),
        encode_r(1, 2, 1, 4, 18),        # signed overflow
        encode_r(1, 2, 1, 6, 19),
        encode_s(0x40, 19, 0, 2),
    ]
    for seed in range(3):
        instruction, data = _random_timing(random.Random(rng.getrandbits(32) ^ seed))
        await run_program(
            dut, with_handler(body), stop_cause=3, max_cycles=1500,
            instruction=instruction, data=data, seed=seed,
        )


async def check_divide_killed_by_older_trap(dut, rng: random.Random) -> None:
    """An older load faults while the younger divide is in flight.

    The handler resumes after the load, so the divide re-executes and the
    following remainder must use a fully computed quotient/remainder. The
    data latency sweep places the fault at every divide phase.
    """
    error_address = 0x80
    body = [
        *load_immediate(1, 0x7654_3210),
        encode_i(13, 0, 0, 2),
        encode_i(error_address, 0, 2, 5, 0x03),  # faulting load
        encode_r(1, 2, 1, 5, 6),                 # DIVU issues under the load hold
        encode_r(1, 2, 1, 7, 7),                 # REMU over the same operands
        encode_r(0, 7, 6, 0, 8),
        encode_s(0x40, 8, 0, 2),
    ]
    for latency in range(1, 40, 3):
        await run_program(
            dut, with_handler(body), stop_cause=3, max_cycles=600,
            data_error_addresses=(error_address,),
            data=PortTiming(latency=latency), seed=rng.getrandbits(32),
        )


# ---------------------------------------------------------------------------
# Control-transfer flush: younger stores must never reach memory
# ---------------------------------------------------------------------------

async def check_flush_suppresses_younger_store(dut, rng: random.Random) -> None:
    poison = [encode_s(0x40, 1, 0, 2), encode_s(0x44, 1, 0, 2), encode_s(0x48, 1, 0, 2)]
    transfers = {
        "beq_taken": encode_b(16, 0, 0, 0),
        "bne_taken": encode_b(16, 2, 0, 1),
        "jal": encode_j(16, 0),
        "jalr": encode_i(0, 3, 0, 0, 0x67),
    }
    for name, transfer in transfers.items():
        prefix = [*load_immediate(1, 0x5555_AAAA), encode_i(1, 0, 0, 2)]
        target = 4 * (len(prefix) + 2 + 4)
        prefix.append(encode_i(target, 0, 0, 3))
        program = prefix + [transfer] + poison + [encode_i(9, 0, 0, 4)]
        assert len(program) * 4 == target, name
        program += [encode_s(0x4C, 1, 0, 2), EBREAK]
        for timing_seed in range(3):
            instruction, data = _random_timing(random.Random(rng.getrandbits(32) ^ timing_seed))
            result = await run_program(
                dut, program, stop_cause=3, max_cycles=300,
                instruction=instruction, data=data, seed=timing_seed,
            )
            for address in (0x40, 0x44, 0x48):
                assert word(result.memory, address) == 0, f"{name}: squashed store reached memory"
            assert word(result.memory, 0x4C) == 0x5555_AAAA
    # Not-taken branch: the younger store must happen.
    program = [*load_immediate(1, 0x1234_5678), encode_b(8, 0, 1, 0), encode_s(0x40, 1, 0, 2), EBREAK]
    result = await run_program(dut, program, stop_cause=3, max_cycles=100)
    assert word(result.memory, 0x40) == 0x1234_5678


# ---------------------------------------------------------------------------
# SYSTEM serialization and exact counters
# ---------------------------------------------------------------------------

async def check_system_serialization(dut, rng: random.Random) -> None:
    initial = (0x0BAD_F00D).to_bytes(4, "little")
    body = [
        encode_i(0, 0, 2, 1, 0x03),      # long-latency load
        encode_csr(0x340, 1, 1, 2),      # CSRRW consumes it immediately
        encode_csr(0xB02, 0, 2, 3),      # exact minstret after the load and CSR
        encode_r(1, 1, 1, 0, 4),         # MUL in flight
        encode_csr(0xB02, 0, 2, 5),      # minstret behind a multiply
        encode_s(8, 1, 0, 2),            # store in flight
        encode_csr(0x340, 0, 2, 6),      # CSR read behind a store
        encode_csr(0xB02, 6, 1, 0),      # minstret write overrides its own count
        encode_csr(0xB02, 0, 2, 7),
        encode_csr(0xB00, 0, 2, 8),      # mcycle: value taken from the DUT
        ECALL,                            # handler returns past it
        encode_csr(0x342, 0, 2, 10),     # mcause of the ECALL
        encode_csr(0x341, 0, 2, 11),     # mepc returned past by the handler
    ]
    for seed in range(3):
        instruction, data = _random_timing(random.Random(rng.getrandbits(32) ^ seed))
        await run_program(
            dut, with_handler(body), stop_cause=3, initial_memory=initial,
            max_cycles=600, instruction=instruction, data=data, seed=seed,
        )


# ---------------------------------------------------------------------------
# Precise data-access trap ahead of younger trapping or effectful work
# ---------------------------------------------------------------------------

async def check_data_error_precedes_younger_work(dut, rng: random.Random) -> None:
    error_address = 0x80
    youngers = {
        "ecall": [ECALL],
        "store": [encode_s(0x40, 1, 0, 2)],
        "csr_write": [encode_csr(0x340, 1, 1, 0)],
        "illegal": [0x0000_0000],
        "misaligned_load": [encode_i(2, 0, 2, 6, 0x03)],
        "branch": [encode_b(8, 0, 0, 0), encode_s(0x44, 1, 0, 2)],
    }
    for kind in ("load", "store"):
        access = (encode_i(error_address, 0, 2, 5, 0x03) if kind == "load"
                  else encode_s(error_address, 1, 0, 2))
        for name, younger in youngers.items():
            body = [*load_immediate(1, 0x7777_1111), access, *younger, encode_csr(0x340, 0, 2, 12)]
            for seed in range(2):
                instruction, data = _random_timing(random.Random(rng.getrandbits(32) ^ seed))
                result = await run_program(
                    dut, with_handler(body), stop_cause=3, max_cycles=400,
                    data_error_addresses=(error_address,),
                    instruction=instruction, data=data, seed=seed,
                )
                causes = [item.cause for item in result.observations if item.trap]
                assert causes[0] == (5 if kind == "load" else 7), (kind, name, causes)


# ---------------------------------------------------------------------------
# Debug halt swept over every cycle offset, resume equivalence, Reset-PC
# ---------------------------------------------------------------------------

def _halt_program() -> list[int]:
    return with_handler([
        *load_immediate(1, -77),
        encode_i(9, 0, 0, 2),
        encode_i(0x40, 0, 0, 3),
        encode_s(0, 1, 3, 2),
        encode_i(0, 3, 2, 4, 0x03),
        encode_r(1, 2, 4, 0, 5),
        encode_r(1, 2, 1, 4, 6),          # divide
        encode_b(8, 2, 1, 1),             # taken, forward: not predicted
        encode_s(4, 1, 3, 2),             # squashed
        encode_i(2, 0, 0, 10),            # x10 = 2
        encode_i(-1, 10, 0, 10),          # loop: x10 -= 1
        encode_b(-4, 0, 10, 1),           # backward, predicted taken; exits by mispredict
        encode_b(-12, 0, 10, 1),          # backward, predicted taken, never taken
        encode_r(0, 6, 5, 0, 7),
        encode_csr(0x340, 7, 1, 0),
        encode_i(1, 0, 2, 8, 0x03),       # misaligned load trap and return
        encode_s(8, 7, 3, 2),
        encode_j(8, 1),
        NOP,
        encode_i(1, 7, 0, 7),
    ])


async def check_halt_sweep(dut, rng: random.Random, stride: int = 1) -> int:
    program = _halt_program()
    reference = await run_program(dut, program, stop_cause=3, max_cycles=800)
    span = reference.cycles
    count = 0
    acknowledged = 0
    for offset in range(0, span, stride):
        result = await run_program(
            dut, program, stop_cause=3, max_cycles=1600, halts=(HaltPlan(offset),),
        )
        assert result.halts_requested == 1
        acknowledged += result.halts_acknowledged
        count += 1
    # A halt requested while the final EBREAK is already in flight ends with
    # that stop observation during the drain; at most a pipeline's depth of
    # trailing offsets (plus the reset cycles) can do so.
    assert acknowledged >= count - 10 // stride - 1, (acknowledged, count)
    timing_rng = random.Random(rng.getrandbits(32))
    for offset in range(0, 2 * span, 3 * stride):
        instruction, data = _random_timing(timing_rng)
        result = await run_program(
            dut, program, stop_cause=3, max_cycles=4000,
            halts=(HaltPlan(offset, hold=timing_rng.randint(1, 4)),
                   HaltPlan(offset + timing_rng.randint(5, 40))),
            instruction=instruction, data=data, seed=offset,
        )
        acknowledged += result.halts_acknowledged
        count += 1
    cocotb.log.info(
        "halt sweep: %d runs, %d halts acknowledged, reference %d cycles",
        count, acknowledged, reference.cycles,
    )
    return count


async def check_reset_pc(dut, rng: random.Random) -> None:
    # A halted Reset-PC restarts at zero with registers and CSRs preserved;
    # the second pass therefore differs, and the model follows it exactly.
    program = with_handler([
        encode_csr(0x340, 0, 2, 1),
        encode_i(1, 1, 0, 1),
        encode_csr(0x340, 1, 1, 0),
        encode_i(3, 0, 0, 2),
        encode_b(8, 1, 2, 4),             # stop looping once mscratch >= 3
        encode_j(-20, 0),
    ])
    for offset in (2, 9, 17, 30):
        result = await run_program(
            dut, program, stop_cause=3, max_cycles=1200,
            halts=(HaltPlan(offset, reset_pc=True),),
        )
        assert result.halts_acknowledged == 1
        assert sum(item.pc == 0 for item in result.observations) == 2


async def check_divide_halt_and_reset(dut, rng: random.Random) -> None:
    """Halt swept across a divide, and reset cancelling an in-flight divide."""
    from cocotb.triggers import FallingEdge, RisingEdge

    for funct3 in range(4, 8):
        # x1 = 0x80001000, x2 = 5: a 29- or 30-iteration early-out divide, so
        # the halt sweep and the reset land inside it.
        program = [encode_u(0x8000_1000, 1, 0x37), encode_i(5, 0, 0, 2), encode_r(1, 2, 1, funct3, 3),
                   encode_i(1, 3, 0, 4), EBREAK]
        acknowledged = 0
        for offset in range(0, 48, 2):
            result = await run_program(
                dut, program, stop_cause=3, max_cycles=400, halts=(HaltPlan(offset),),
                instruction=PortTiming((0, 1), latency=2),
            )
            acknowledged += result.halts_acknowledged
        assert acknowledged >= 12, f"divide halt sweep acknowledged only {acknowledged}"

        # Reset with the divide in flight: nothing from the old epoch retires.
        await run_program(dut, program, stop_pc=4, max_cycles=64)
        for _ in range(4):
            await RisingEdge(dut.clk_i)
        await FallingEdge(dut.clk_i)
        dut.rst_i.value = 1
        dut.enable_i.value = 0
        await RisingEdge(dut.clk_i)
        await FallingEdge(dut.clk_i)
        dut.rst_i.value = 0
        dut.instruction_response_valid_i.value = 0
        dut.data_response_valid_i.value = 0
        for _ in range(36):
            await RisingEdge(dut.clk_i)
            await FallingEdge(dut.clk_i)
            assert not int(dut.observation_valid_o.value), "reset failed to cancel divide"
            assert not int(dut.halt_ack_o.value)
            assert not int(dut.instruction_request_valid_o.value)
            assert not int(dut.data_request_valid_o.value)
        dut.enable_i.value = 1


async def check_reset_and_halt_ports(dut) -> None:
    """Pin-level reset, halt, resume and Reset-PC without core timing."""
    from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge

    from .core_runner import reset_core
    from .observation import read_observation

    async def tick() -> None:
        await RisingEdge(dut.clk_i)
        await FallingEdge(dut.clk_i)

    async def wait_ack(limit: int) -> list:
        retired = []
        for _ in range(limit):
            await RisingEdge(dut.clk_i)
            await ReadOnly()
            if int(dut.observation_valid_o.value):
                retired.append(read_observation(dut))
            ack = int(dut.halt_ack_o.value)
            await FallingEdge(dut.clk_i)
            dut.instruction_response_valid_i.value = 0
            if ack:
                return retired
        raise AssertionError("halt not acknowledged")

    dut.rst_i.value = 1
    await reset_core(dut)  # leaves ready low
    dut.rst_i.value = 1
    await tick()
    assert not int(dut.instruction_request_valid_o.value)
    dut.rst_i.value = 0
    for _ in range(3):
        await tick()
        assert int(dut.instruction_request_valid_o.value)
        assert int(dut.instruction_request_address_o.value) == 0

    # Accept one request, then reset its transaction epoch before any response.
    dut.instruction_request_ready_i.value = 1
    await tick()
    dut.instruction_request_ready_i.value = 0
    dut.rst_i.value = 1
    await tick()
    assert not int(dut.observation_valid_o.value)
    assert not int(dut.instruction_request_valid_o.value)
    dut.rst_i.value = 0
    dut.halt_request_i.value = 1
    assert not await wait_ack(8)
    for _ in range(3):
        await tick()
        assert int(dut.halt_ack_o.value)
        assert not int(dut.instruction_request_valid_o.value)
    dut.halt_request_i.value = 0
    dut.resume_i.value = 1
    await tick()
    dut.resume_i.value = 0
    assert not int(dut.halt_ack_o.value)
    assert int(dut.instruction_request_valid_o.value)
    assert int(dut.instruction_request_address_o.value) == 0

    # Accept a fetch, request halt while its response is owed, then answer
    # it. The core may retire it or discard it, but resume must fetch the
    # architectural next PC of whatever retired.
    dut.instruction_request_ready_i.value = 1
    await tick()
    dut.instruction_request_ready_i.value = 0
    dut.halt_request_i.value = 1
    dut.instruction_response_data_i.value = encode_i(1, 0, 0, 1)
    dut.instruction_response_valid_i.value = 1
    retired = await wait_ack(16)
    if retired:
        assert len(retired) == 1
        assert retired[0].pc == 0 and retired[0].next_pc == 4 and retired[0].rd_write_data == 1
    resume_pc = 4 if retired else 0
    dut.halt_request_i.value = 0
    dut.resume_i.value = 1
    await tick()
    dut.resume_i.value = 0
    assert int(dut.instruction_request_valid_o.value)
    assert int(dut.instruction_request_address_o.value) == resume_pc

    # Halt again, Reset-PC while halted, then resume at zero.
    dut.halt_request_i.value = 1
    await wait_ack(8)
    dut.halt_request_i.value = 0
    dut.reset_pc_request_i.value = 1
    await tick()
    dut.reset_pc_request_i.value = 0
    dut.resume_i.value = 1
    await tick()
    dut.resume_i.value = 0
    assert int(dut.instruction_request_valid_o.value)
    assert int(dut.instruction_request_address_o.value) == 0


# ---------------------------------------------------------------------------
# Static branch prediction: backward taken, JAL early redirect, recovery
# ---------------------------------------------------------------------------

def _countdown_loop(count: int, body: list[int]) -> list[int]:
    """x10 = count; loop: body; x10 -= 1; bne x10, x0, loop (backward)."""
    loop = body + [encode_i(-1, 10, 0, 10)]
    return [encode_i(count, 0, 0, 10), *loop, encode_b(-4 * len(loop), 0, 10, 1)]


async def check_static_prediction(dut, rng: random.Random) -> None:
    """Predicted-taken transfers retire the architectural stream, a
    predicted-taken branch that falls through recovers, speculative fetch
    on the predicted path has no effect, misaligned or faulting predicted
    targets trap exactly as unpredicted ones, and a correct prediction
    costs one refill clock."""
    # A loop storing its count each iteration, then the fall-through store;
    # the backward branch exits by mispredict recovery.
    loop_store = _countdown_loop(5, [
        encode_i(0, 3, 2, 4, 0x03),       # lw x4, 0(x3)
        encode_r(0, 10, 4, 0, 4),         # x4 += x10
        encode_s(0, 4, 3, 2),             # sw x4, 0(x3)
    ])
    program = [encode_i(0x40, 0, 0, 3), *loop_store, encode_i(1, 0, 0, 5),
               encode_s(4, 5, 3, 2), EBREAK]
    for seed in range(3):
        instruction, data = _random_timing(random.Random(rng.getrandbits(32) ^ seed))
        result = await run_program(dut, program, stop_cause=3, max_cycles=600,
                                   instruction=instruction, data=data, seed=seed)
        assert word(result.memory, 0x40) == 15 and word(result.memory, 0x44) == 1

    # Slow, stalling fetch: the predicted redirect of the loop branch and its
    # Q102H mispredict flush twice while one stale response is still owed;
    # that response must be dropped, never retired (seeds 30, 38, 45, 49
    # and 59 reach this window).
    slow_fetch = PortTiming(ready_probability=0.5, latency=(1, 3))
    slow_data = PortTiming(ready_probability=0.8, latency=(1, 6))
    for seed in range(64):
        result = await run_program(dut, program, stop_cause=3, max_cycles=600,
                                   instruction=slow_fetch, data=slow_data, seed=seed)
        assert word(result.memory, 0x40) == 15 and word(result.memory, 0x44) == 1

    # A never-taken backward branch is predicted taken: its target block of
    # stores is fetched speculatively but must never reach memory, and the
    # fall-through store must. The same for a never-taken backward JAL-free
    # block reached only through a forward JAL (predicted, taken).
    prefix = [*load_immediate(1, 0x5555_AAAA), encode_i(1, 0, 0, 2)]
    poison = [encode_s(0x40, 1, 0, 2), encode_s(0x44, 1, 0, 2), encode_s(0x48, 1, 0, 2)]
    program = prefix + [encode_j(4 * (len(poison) + 1), 0)] + poison
    program += [encode_b(-4 * len(poison), 0, 2, 0),     # beq x2, x0: never taken
                encode_s(0x4C, 1, 0, 2), EBREAK]
    for seed in range(3):
        instruction, data = _random_timing(random.Random(rng.getrandbits(32) ^ seed))
        result = await run_program(dut, program, stop_cause=3, max_cycles=300,
                                   instruction=instruction, data=data, seed=seed)
        for address in (0x40, 0x44, 0x48):
            assert word(result.memory, address) == 0, "speculative store reached memory"
        assert word(result.memory, 0x4C) == 0x5555_AAAA

    # Backward JAL (predicted taken, always taken) with its link consumed at
    # once, a forward taken branch (not predicted) and a predicted loop
    # whose body is a forward not-taken branch.
    program = with_handler([
        encode_i(0x40, 0, 0, 3),
        encode_j(16, 0),                  # 1: forward JAL to 5
        encode_i(1, 0, 0, 4),             # 2: target of the backward JAL
        encode_i(0, 1, 0, 6),             # 3: x6 = link
        encode_j(16, 0),                  # 4: forward JAL to 8
        encode_j(-12, 1),                 # 5: backward JAL to 2 (link x1 = pc + 4)
        encode_s(0, 1, 3, 2),             # 6: skipped
        encode_s(4, 1, 3, 2),             # 7: skipped
        encode_b(8, 0, 4, 1),             # 8: bne x4, x0, +8: forward taken
        encode_s(8, 1, 3, 2),             # 9: skipped
        *_countdown_loop(3, [encode_b(8, 10, 4, 0),  # beq x4, x10: taken once (x10 == 1)
                             encode_s(12, 10, 3, 2)]),  # sw x10, 12(x3): skipped once
        encode_s(16, 6, 3, 2),
    ])
    for seed in range(3):
        instruction, data = _random_timing(random.Random(rng.getrandbits(32) ^ seed))
        result = await run_program(dut, program, stop_cause=3, max_cycles=400,
                                   instruction=instruction, data=data, seed=seed)
        assert word(result.memory, 0x40) == 0 and word(result.memory, 0x44) == 0
        assert word(result.memory, 0x48) == 0
        assert word(result.memory, 0x4C) == 2          # stored for x10 = 3 then 2
        assert word(result.memory, 0x50) != 0          # the backward JAL's link

    # Misaligned backward targets are not predicted and trap precisely
    # (cause 0, mtval = target); the handler resumes past them.
    program = with_handler([
        encode_i(0x40, 0, 0, 3),
        encode_b(-2, 0, 0, 0),            # beq x0, x0, -2: misaligned target
        encode_j(-6, 0),                  # misaligned backward JAL
        encode_i(1, 0, 0, 5),
        encode_s(0, 5, 3, 2),
    ])
    result = await run_program(dut, program, stop_cause=3, max_cycles=300)
    traps = [item for item in result.observations if item.trap and item.cause == 0]
    assert len(traps) == 2 and all(item.trap_value & 2 for item in traps)
    assert word(result.memory, 0x40) == 1

    # A predicted target that fetch-faults: taken, the faulting instruction
    # traps with cause 1 after the branch retires; not taken, the stale
    # error response is dropped and nothing traps.
    body = [
        encode_i(0x40, 0, 0, 3),
        encode_i(2, 0, 0, 2),
        encode_j(8, 0),                   # over the faulting word
        0,                                # fetch-error target (word F)
        encode_i(1, 5, 0, 5),             # x5 += 1 (also the handler's return point)
        encode_b(-8, 0, 5, 0),            # beq x5, x0, F: never taken
        encode_b(-12, 2, 5, 1),           # bne x5, x2, F: taken once, then falls through
        encode_s(0, 5, 3, 2),
    ]
    program = with_handler(body)
    fault_word = 1 + len(HANDLER) + 2 + 3
    assert program[fault_word] == 0
    for seed in range(2):
        instruction, data = _random_timing(random.Random(rng.getrandbits(32) ^ seed))
        result = await run_program(dut, program, stop_cause=3, max_cycles=400,
                                   instruction_error_addresses=(4 * fault_word,),
                                   instruction=instruction, data=data, seed=seed)
        causes = [item.cause for item in result.observations if item.trap]
        assert causes == [1, 3], causes
        assert word(result.memory, 0x40) == 2

    # Refill cost under the one-cycle responder: a two-instruction loop of
    # 32 iterations costs two retirements plus one refill clock per
    # iteration, so the span is bounded near 3 clocks per iteration.
    iterations = 32
    program = [*_countdown_loop(iterations, []), EBREAK]
    result = await run_program(dut, program, stop_cause=3, max_cycles=300,
                               instruction=PortTiming(), data=PortTiming())
    span = result.last_observation_cycle - result.first_observation_cycle + 1
    assert span <= 3 * iterations + 12, f"loop span {span} clocks for {iterations} iterations"
    cocotb.log.info("predicted loop: %d iterations in %d clocks", iterations, span)


RA, SP = 1, 2
RET = encode_i(0, RA, 0, 0, 0x67)  # jalr x0, 0(ra)
assert RET == 0x0000_8067


def assemble(lines: list, base: int = 0) -> list[int]:
    """Two-pass label assembler: a str ending in ':' defines a label; a
    callable(pc, labels) returns one instruction word; an int is a word."""
    labels: dict[str, int] = {}
    pc = base
    for item in lines:
        if isinstance(item, str):
            labels[item[:-1]] = pc
        else:
            pc += 4
    words = []
    pc = base
    for item in lines:
        if isinstance(item, str):
            continue
        words.append(item(pc, labels) if callable(item) else item)
        pc += 4
    return words


def call(label: str, rd: int = RA):
    return lambda pc, labels: encode_j(labels[label] - pc, rd)


def jump(label: str):
    return call(label, 0)


def bne(rs1: int, rs2: int, label: str):
    return lambda pc, labels: encode_b(labels[label] - pc, rs2, rs1, 1)


def beq(rs1: int, rs2: int, label: str):
    return lambda pc, labels: encode_b(labels[label] - pc, rs2, rs1, 0)


def link_to(rd: int, label: str) -> list:
    """rd = address of label (AUIPC + ADDI, both relative to the AUIPC)."""
    return [lambda pc, labels: encode_u(0, rd, 0x17),
            lambda pc, labels: encode_i(labels[label] - (pc - 4), rd, 0, rd)]


def push_ra() -> list[int]:
    return [encode_i(-4, SP, 0, SP), encode_s(0, RA, SP, 2)]


def pop_ra() -> list[int]:
    return [encode_i(0, SP, 2, RA, 0x03), encode_i(4, SP, 0, SP)]


def return_program() -> list[int]:
    """Calls and returns exercising every return-address-stack path; the
    final EBREAK stops it. Each step stores a marker so a skipped or doubled
    return shows in memory as well as in the model-checked stream.

    0x600.. markers, 0x680 recursion result, stack below 0x800."""
    poison = [encode_s(0x5FC, RA, 0, 2)] * 3  # sw ra, 0x5fc(x0): must never retire
    lines = [
        jump("main"), *HANDLER,
        "main:",
        encode_i(4, 0, 0, 9), encode_csr(0x305, 9, 1, 0),   # mtvec = 4
        encode_i(0x7FC, 0, 0, SP), encode_i(4, SP, 0, SP),  # sp = 0x800
        encode_i(0x600, 0, 0, 3),
        # Underflow: a return with an empty stack is fetched sequentially.
        *link_to(RA, "after_empty"), RET, *poison,
        "after_empty:",
        encode_s(0, RA, 3, 2),                               # 0x600 = after_empty
        # Nesting: main -> outer -> middle -> leaf, each returning in order.
        call("outer"),
        encode_i(1, 0, 0, 5), encode_s(4, 5, 3, 2),          # 0x604 = 1
        # Overflow: recursion twelve deep, deeper than the stack.
        encode_i(12, 0, 0, 10), encode_i(0, 0, 0, 11),
        call("recurse"),
        encode_s(0x80, 11, 3, 2),                            # 0x680 = 12 + 11 + .. + 1
        # Wrong entry: the callee replaces its return address, so the
        # predicted target (call + 4) is wrong and the poison is speculative.
        call("redirect_callee"), *poison,
        "redirected:",
        encode_i(2, 0, 0, 5), encode_s(8, 5, 3, 2),          # 0x608 = 2
        # A Q103H data trap discards a predicted return behind its load;
        # the handler resumes at the return, now with a cleared stack.
        call("trap_callee"),
        encode_i(3, 0, 0, 5), encode_s(12, 5, 3, 2),         # 0x60c = 3
        # A predicted return to a misaligned address traps (cause 0) in
        # Q102H; the handler resumes after it and the callee returns again.
        call("misaligned_callee"),
        encode_i(4, 0, 0, 5), encode_s(16, 5, 3, 2),         # 0x610 = 4
        # Back-to-back: a call whose callee is a bare return, in a loop.
        encode_i(6, 0, 0, 10),
        "spin:",
        call("leaf"),
        encode_i(-1, 10, 0, 10),
        bne(10, 0, "spin"),
        encode_s(20, 6, 3, 2),                               # 0x614 = leaf count
        EBREAK,
        "outer:", *push_ra(), call("middle"), *pop_ra(), RET, *poison,
        "middle:", *push_ra(), call("leaf"), *pop_ra(), RET, *poison,
        "leaf:", encode_i(1, 6, 0, 6), RET, *poison,
        "recurse:",
        encode_r(0, 10, 11, 0, 11),                          # x11 += x10
        encode_i(-1, 10, 0, 10),
        beq(10, 0, "recurse_done"),
        *push_ra(), call("recurse"), *pop_ra(),
        "recurse_done:", RET, *poison,
        "redirect_callee:", *link_to(RA, "redirected"), RET, *poison,
        "trap_callee:",
        encode_i(0, 0, 2, 7, 0x03),                          # lw x7, 0(x0): data error
        RET, *poison,
        "misaligned_callee:",
        encode_i(2, RA, 0, RA), RET,                         # target ra + 2: cause 0
        encode_i(-2, RA, 0, RA), RET, *poison,
    ]
    return assemble(lines)


def _check_return_memory(result: RunResult, leaf_calls: int = 7) -> None:
    assert word(result.memory, 0x5FC) == 0, "speculative poison store retired"
    assert word(result.memory, 0x600) != 0
    assert [word(result.memory, 0x604 + 4 * index) for index in range(4)] == [1, 2, 3, 4]
    assert word(result.memory, 0x614) == leaf_calls
    assert word(result.memory, 0x680) == sum(range(1, 13))


async def check_return_prediction(dut, rng: random.Random) -> None:
    """Return-address-stack prediction: nesting, overflow, underflow, a
    wrong stacked address, a data trap discarding a predicted return, a
    predicted return trapping on a misaligned target, back-to-back call and
    return, under random port timing and across a halt at every offset.
    Every retirement is checked against the ISA model."""
    program = return_program()
    errors = (0,)
    reference = await run_program(dut, program, stop_cause=3, max_cycles=2000,
                                  data_error_addresses=errors)
    _check_return_memory(reference)
    causes = [item.cause for item in reference.observations if item.trap]
    assert causes == [5, 0, 3], causes
    for seed in range(16):
        instruction, data = _random_timing(random.Random(rng.getrandbits(32) ^ seed))
        result = await run_program(dut, program, stop_cause=3, max_cycles=8000,
                                   data_error_addresses=errors,
                                   instruction=instruction, data=data, seed=seed)
        _check_return_memory(result)
    # Halt and resume at every offset: the drain finishes Q102H and the owed
    # fetch while the stack keeps its entries.
    for offset in range(0, reference.cycles, 2):
        result = await run_program(dut, program, stop_cause=3, max_cycles=4000,
                                   data_error_addresses=errors, halts=(HaltPlan(offset),))
        _check_return_memory(result)
    cocotb.log.info("return prediction: reference %d clocks, %d halt offsets",
                    reference.cycles, (reference.cycles + 1) // 2)


# BTB alias sites: the same index and partial tag at one and two strides.
# A stride is a multiple of the bytes an index plus 8-bit partial tag covers
# (1024 per entry: PC bits 17..2 at the default 256 entries), at least 1 MiB,
# so every fetch BTB size confuses them.
ALIAS_BRANCH = 0x400
ALIAS_JUMP = 0x440
MIB = 0x10_0000
BTB_TAG_BITS = 8


def btb_alias_span(entries: int) -> int:
    """Bytes of code one index plus partial tag covers: addresses this far
    apart share an entry and its tag (core/HAS.md "Fetch branch target
    buffer"). Zero entries (no fetch BTB) has no aliasing; one word."""
    return max(entries, 1) * 4 << BTB_TAG_BITS


def btb_alias_program(stride: int = MIB) -> list[int]:
    """Train BTB entries, then execute different control transfers that
    alias them: a never-taken forward branch hitting a taken entry, a taken
    branch whose target differs, an indirect jump whose register target
    differs, and a non-transfer word predicted at fetch with the jump's
    target (rejected in Q101H). Every wrong prediction must recover without
    retiring the predicted path. x6 accumulates one marker per executed block; 0x600
    receives it. Poison stores (0x5fc) sit on every wrong predicted path."""
    poison = encode_s(0x5FC, RA, 0, 2)
    ret = RET

    def call_abs(address: int) -> list[int]:
        return [*load_immediate(5, address), encode_i(0, 5, 0, RA, 0x67)]

    main = assemble([
        encode_i(0x7FC, 0, 0, SP),
        encode_i(0, 0, 0, 6),
        *link_to(8, "t1"),
        *call_abs(ALIAS_BRANCH), *call_abs(ALIAS_BRANCH),        # train: taken to +12
        *call_abs(ALIAS_BRANCH + stride),                        # alias, not taken
        *call_abs(ALIAS_BRANCH), *call_abs(ALIAS_BRANCH),        # retrain
        *call_abs(ALIAS_BRANCH + 2 * stride),                    # alias, taken to +8
        *call_abs(ALIAS_BRANCH),                                 # entry now holds +8 target
        *call_abs(ALIAS_JUMP), *call_abs(ALIAS_JUMP),            # train: jump to t1
        *link_to(8, "t2"),
        *call_abs(ALIAS_JUMP + stride),                          # alias jump to t2
        *call_abs(ALIAS_JUMP + 2 * stride),                      # alias non-transfer
        encode_s(0x600, 6, 0, 2),
        EBREAK,
        "t1:", encode_i(16, 6, 0, 6), ret, poison,
        "t2:", encode_i(256, 6, 0, 6), ret, poison,
    ])
    assert 4 * len(main) <= ALIAS_BRANCH
    blocks = {
        ALIAS_BRANCH: [encode_b(12, 0, 0, 0), poison, poison, encode_i(1024, 6, 0, 6), ret],
        ALIAS_BRANCH + stride: [encode_b(12, 0, 0, 1), encode_i(1, 6, 0, 6), ret, poison],
        ALIAS_BRANCH + 2 * stride: [encode_b(8, 0, 0, 0), poison, encode_i(4, 6, 0, 6), ret],
        ALIAS_JUMP: [encode_i(0, 8, 0, 0, 0x67), poison],
        ALIAS_JUMP + stride: [encode_i(0, 8, 0, 0, 0x67), poison],
        ALIAS_JUMP + 2 * stride: [encode_i(2, 6, 0, 6), ret, poison],
    }
    words = main + [NOP] * ((ALIAS_JUMP + 2 * stride) // 4 + 8 - len(main))
    for address, block in blocks.items():
        words[address // 4: address // 4 + len(block)] = block
    return words


# Five ALIAS_BRANCH calls, one per alias, two jumps to t1, one to t2 and
# the non-transfer alias of the trained jump.
BTB_ALIAS_SUM = 5 * 1024 + 1 + 4 + 2 * 16 + 256 + 2


async def check_btb_aliasing(dut, rng: random.Random) -> None:
    """Partial-tag BTB aliases and stale targets recover in Q102H: a hit on
    a not-taken branch, a taken branch with another target and an indirect
    jump with another register target each retire only the architectural
    path, under the one-cycle responder and random port timing, at the
    core's BTB size."""
    from .core_runner import btb_entries

    program = btb_alias_program(max(MIB, btb_alias_span(btb_entries(dut))))
    for seed in range(6):
        if seed == 0:
            instruction, data = PortTiming(), PortTiming()
        else:
            instruction, data = _random_timing(random.Random(rng.getrandbits(32) ^ seed))
        result = await run_program(dut, program, stop_cause=3, max_cycles=3000,
                                   instruction=instruction, data=data, seed=seed)
        assert word(result.memory, 0x5FC) == 0, "speculative poison store retired"
        assert word(result.memory, 0x600) == BTB_ALIAS_SUM, hex(word(result.memory, 0x600))
    cocotb.log.info("BTB aliasing: 6 timings, last run %d clocks", result.cycles)


# ---------------------------------------------------------------------------
# Seeded random instruction streams
# ---------------------------------------------------------------------------

DATA_BASE = 0x200


def random_program(rng: random.Random, length: int = 160) -> tuple[list[int], set[int], set[int]]:
    """Forward-only random RV32IM stream with traps resumed by the handler.

    x8 holds the data base and x9 belongs to the handler; destinations are
    x0..x7. Some data words and one body word are access-error addresses.
    """
    regs = range(1, 8)
    body: list[int] = []
    loop_counts: list[int] = []  # body indices of loop-count updates, never fetch faults
    for register in regs:
        body += load_immediate(register, rng.getrandbits(32))
    body += load_immediate(8, DATA_BASE)
    while len(body) < length:
        kind = rng.choices(
            ("alu", "alui", "lui", "load", "store", "mul", "div", "branch", "jal",
             "csr", "misaligned", "ecall", "illegal", "loop", "backward_not_taken"),
            weights=(20, 14, 3, 14, 12, 8, 3, 8, 3, 4, 2, 1, 1, 3, 2),
        )[0]
        rd = rng.choice((0, *regs, *regs))
        rs1 = rng.choice((0, *regs, *regs))
        rs2 = rng.choice((0, *regs, *regs))
        if kind == "alu":
            funct3 = rng.randrange(8)
            funct7 = 0x20 if funct3 in (0, 5) and rng.random() < 0.4 else 0
            body.append(encode_r(funct7, rs2, rs1, funct3, rd))
        elif kind == "alui":
            funct3 = rng.choice((0, 2, 3, 4, 6, 7, 1, 5))
            if funct3 in (1, 5):
                imm = rng.randrange(32) | (0x400 if funct3 == 5 and rng.random() < 0.5 else 0)
            else:
                imm = rng.randrange(-2048, 2048)
            body.append(encode_i(imm, rs1, funct3, rd))
        elif kind == "lui":
            body.append(encode_u(rng.getrandbits(32), rd, rng.choice((0x37, 0x17))))
        elif kind == "load":
            funct3 = rng.choice((0, 1, 2, 4, 5))
            size = 1 << (funct3 & 3)
            body.append(encode_i(rng.randrange(0, 64, size), 8, funct3, rd, 0x03))
        elif kind == "store":
            funct3 = rng.randrange(3)
            body.append(encode_s(rng.randrange(0, 64, 1 << funct3), rs2, 8, funct3))
        elif kind == "mul":
            body.append(encode_r(1, rs2, rs1, rng.randrange(4), rd))
        elif kind == "div":
            body.append(encode_r(1, rs2, rs1, rng.randrange(4, 8), rd))
        elif kind == "branch":
            skip = rng.randint(1, 3)
            body.append(encode_b(4 * (skip + 1), rs2, rs1, rng.choice((0, 1, 4, 5, 6, 7))))
        elif kind == "jal":
            body.append(encode_j(4 * rng.randint(1, 3), rd))
        elif kind == "csr":
            body.append(rng.choice((
                encode_csr(0x340, rs1, rng.choice((1, 2, 3)), rd),
                encode_csr(0x340, rng.randrange(32), rng.choice((5, 6, 7)), rd),
                encode_csr(0xB02, 0, 2, rd),
                encode_csr(0xB00, 0, 2, rd),
            )))
        elif kind == "misaligned":
            body.append(rng.choice((
                encode_i(1, 8, 2, rd, 0x03),
                encode_s(2, rs2, 8, 2),
                encode_i(3, 8, 1, rd, 0x03),
            )))
        elif kind == "ecall":
            body.append(ECALL)
        elif kind == "loop":
            # x10 counts a bounded loop closed by a backward branch, which
            # the core predicts taken: correct until the exit mispredicts.
            # The count is decremented modulo 4, so the loop also exits
            # within four iterations when a forward jump enters it.
            iterations = rng.randint(1, 3)
            inner = [encode_r(0, rs2, rs1, rng.choice((0, 4, 6, 7)), rd)
                     for _ in range(rng.randint(0, 2))]
            body.append(encode_i(iterations, 0, 0, 10))
            body += inner
            loop_counts += [len(body), len(body) + 1]
            body += [encode_i(-1, 10, 0, 10), encode_i(3, 10, 7, 10)]
            body.append(encode_b(-4 * (len(inner) + 2), 0, 10, 1))
        elif kind == "backward_not_taken":
            # x10 is zero outside a loop, so this backward branch is always
            # predicted taken and never taken.
            body.append(encode_b(-4 * rng.randint(1, min(8, len(body))), 0, 10, 1))
        else:
            body.append(0xFFFF_FFFF)
    program = with_handler(body + [NOP] * 4)
    data_errors = {DATA_BASE + 4 * rng.randrange(16) for _ in range(2)}
    prologue = 1 + len(HANDLER) + 2
    # A fetch fault skips its word when the handler returns, which must not
    # be a loop-count update (the loop might never exit).
    candidates = [index for index in range(prologue + 40, len(program) - 2)
                  if index - prologue not in loop_counts]
    fetch_errors = {4 * rng.choice(candidates)}
    return program, data_errors, fetch_errors


async def check_random_streams(dut, seed: int, count: int) -> int:
    retired = 0
    for index in range(count):
        rng = random.Random((seed << 16) ^ index)
        program, data_errors, fetch_errors = random_program(rng)
        initial = bytes(rng.getrandbits(8) for _ in range(DATA_BASE + 0x80))
        instruction, data = _random_timing(rng)
        result = await run_program(
            dut, program, stop_cause=3, initial_memory=initial, max_cycles=12000,
            instruction_error_addresses=fetch_errors, data_error_addresses=data_errors,
            instruction=instruction, data=data, seed=rng.getrandbits(32),
        )
        retired += len(result.observations)
    cocotb.log.info("random streams: seed=%d programs=%d observations=%d", seed, count, retired)
    return retired


# ---------------------------------------------------------------------------
# Doom-like inner loops for IPC
# ---------------------------------------------------------------------------

def kernel_program() -> list[int]:
    """A column-draw loop and a dot product, the shapes of Doom's hot loops.

    Column draw (64 pixels): texel = src[(frac >> 16) & 127]; dest = cmap[texel];
    frac += step. Dot product (32 words): sum += a[i] * b[i]. Registers x10 and
    up; data lives in the 4 KiB model memory.
    """
    column = [
        encode_i(16, 11, 5, 15) | (0x20 << 25),  # srai x15, x11(frac), 16
        encode_i(127, 15, 7, 15),                # andi x15, x15, 127
        encode_r(0, 15, 12, 0, 15),              # add x15, x12(src), x15
        encode_i(0, 15, 4, 16, 0x03),            # lbu x16, 0(x15)
        encode_r(0, 16, 13, 0, 16),              # add x16, x13(cmap), x16
        encode_i(0, 16, 4, 16, 0x03),            # lbu x16, 0(x16)
        encode_s(0, 16, 14, 0),                  # sb x16, 0(x14 dest)
        encode_i(4, 14, 0, 14),                  # dest += 4
        encode_r(0, 17, 11, 0, 11),              # frac += step
        encode_i(-1, 10, 0, 10),                 # count -= 1
    ]
    column.append(encode_b(-4 * len(column), 0, 10, 1))  # bnez count, loop
    dot = [
        encode_i(0, 20, 2, 23, 0x03),            # lw x23, 0(x20)
        encode_i(0, 21, 2, 24, 0x03),            # lw x24, 0(x21)
        encode_r(1, 24, 23, 0, 25),              # mul x25, x23, x24
        encode_r(0, 25, 22, 0, 22),              # sum += x25
        encode_i(4, 20, 0, 20),
        encode_i(4, 21, 0, 21),
        encode_i(-1, 10, 0, 10),
    ]
    dot.append(encode_b(-4 * len(dot), 0, 10, 1))
    return [
        encode_i(64, 0, 0, 10),
        *load_immediate(11, 0x0003_8000),        # frac
        encode_i(0x100, 0, 0, 12),               # src texture column
        encode_i(0x200, 0, 0, 13),               # colormap
        encode_i(0x400, 0, 0, 14),               # dest
        *load_immediate(17, 0x0001_9000),        # step
        *column,
        encode_i(32, 0, 0, 10),
        encode_i(0x600, 0, 0, 20),
        encode_i(0x700, 0, 0, 21),
        encode_i(0, 0, 0, 22),
        *dot,
        encode_s(0x7FC, 22, 0, 2),
        EBREAK,
    ]


# ---------------------------------------------------------------------------
# IPC under the one-cycle-hit responder
# ---------------------------------------------------------------------------

async def measure_ipc(dut, name: str, words, **kwargs) -> RunResult:
    result = await run_program(
        dut, words, instruction=PortTiming(), data=PortTiming(), **kwargs,
    )
    cocotb.log.info(
        "IPC core=%s program=%s observations=%d cycles=%d ipc=%.3f",
        dut._name, name, len(result.observations), result.cycles,
        len(result.observations) / result.cycles,
    )
    return result
