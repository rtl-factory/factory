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
from .rv32_asm import (
    EBREAK, ECALL, MCAUSE, MCYCLE, MEPC, MINSTRET, MRET, MSCRATCH, MTVEC, NOP, RA, RET, SP,
    X0, X1, X2, X3, X4, X5, X6, X7, X8, X9, X10, X11, X12, X13, X14, X15, X16, X17, X18,
    X19, X20, X21, X22, X23, X24, X25, add, addi, andi, assemble, beq, blt, bne, bnez, call,
    csrr, csrrw, csrw, div, divu, encode_b, encode_csr, encode_i, encode_r, encode_s,
    encode_u, j, jal, jalr, la, lbu, lh, li, lui, lw, mul, mulh, mulhu, rem, remu, sb, srai,
    sub, sw,
)
from .rv32im_model import m_result

# Why: the handler sits at words 1..4 (mtvec = 4) and returns to mepc + 4,
# so a trapping instruction is skipped, not retried.
HANDLER = (
    csrr(X9, MEPC),
    addi(X9, X9, 4),
    csrw(MEPC, X9),
    MRET,
)


def with_handler(body: list[int]) -> list[int]:
    """Word 0 skips the handler; the body starts by setting mtvec = 4."""
    prologue = [j(4 * (1 + len(HANDLER))), *HANDLER]
    setup = [addi(X9, X0, 4), csrw(MTVEC, X9)]
    return prologue + setup + body + [EBREAK]


def _random_timing(rng: random.Random) -> tuple[PortTiming, PortTiming]:
    return (
        PortTiming(ready_probability=rng.choice((1.0, 0.8, 0.5)), latency=rng.choice((1, (1, 3), (1, 6)))),
        PortTiming(ready_probability=rng.choice((1.0, 0.8, 0.5)), latency=rng.choice((1, (1, 3), (1, 6)))),
    )


def _producers() -> dict[str, list[int]]:
    """Each producer writes x5 from x1/x2 (x1=0x40, x2=7, x3=data base)."""
    return {
        "alu": [add(X5, X1, X2)],
        "lui": [lui(X5, 0x4)],
        "load": [lw(X5, 0, X3)],
        "load_byte": [lbu(X5, 1, X3)],
        "mul": [mul(X5, X1, X2)],
        "mulhu": [mulhu(X5, X1, X1)],
        "div": [div(X5, X1, X2)],
        "csr": [csrr(X5, MSCRATCH)],
        "jal_link": [jal(X5, 4)],
    }


def _consumers() -> dict[str, list[int]]:
    """Each consumer reads x5 at the instruction distance being swept; the
    address consumer is a byte store, so it is never misaligned."""
    return {
        "alu_rs1": [addi(X6, X5, 3)],
        "alu_rs2": [sub(X6, X1, X5)],
        "store_data": [sw(X5, 8, X3)],
        "store_address": [sb(X2, 0, X5)],
        "branch": [bne(X1, X5, 8), addi(X6, X0, 1)],
        "mul_operand": [mul(X6, X2, X5)],
        "csr_operand": [csrrw(X6, MSCRATCH, X5)],
    }


async def check_raw_distances(dut, rng: random.Random) -> None:
    initial = bytes(range(0x00, 0x80))
    base = [
        *li(X1, 0x40),
        addi(X2, X0, 7),
        addi(X3, X0, 0x20),
        csrw(MSCRATCH, X1),
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


async def check_divide_under_hold(dut, rng: random.Random) -> None:
    program = [
        *li(X1, -1000),
        addi(X2, X0, 7),
        addi(X3, X0, 0x40),
        # Why: the divide issues behind a store under a long data hold with an
        # independent load behind it, so the REM must wait on both the divider
        # and the load; a missed interlock shows up in the REM or its immediate
        # consumer.
        sw(X1, 0, X3),
        div(X4, X1, X2),
        lw(X5, 0, X3),
        rem(X6, X5, X4),
        add(X7, X4, X6),
        sw(X7, 4, X3),
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
        memory = sw(X1, address, X0) if store else lw(X6, address, X0)
        body = [
            *li(X1, left), *li(X2, right),
            mul(X5, X1, X2),
            memory,
            addi(X11, X5, 3),
            mul(X7, X5, X2),
            add(X8, X7, X11),
            sw(X8, 0x4C, X0),
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


M_CORNERS = (
    0x0000_0000, 0x0000_0001, 0xFFFF_FFFF, 0x0000_0002, 0xFFFF_FFFE,
    0x8000_0000, 0x7FFF_FFFF, 0x8000_0001, 0x0000_FFFF, 0x0001_0000,
    0x1234_5678, 0xFFFF_FFF9,
)

# Why: MUL, MULH, MULHSU, MULHU, then DIV/REM and DIVU/REMU in pairs over the
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

    A MUL of two results feeds a MULH at distance one (MUL-to-MUL), an ADD
    consumes that MULH at once (MUL-use), and a store takes the sum.
    """
    words: list[int] = []
    for left, right in pairs:
        words += li(X1, left) + li(X2, right)
        for offset, funct3 in enumerate(_M_ORDER):
            words.append(encode_r(1, 2, 1, funct3, 3 + offset))
        words.append(mul(X11, X3, X4))
        words.append(mulh(X12, X11, X11))
        words.append(add(X13, X10, X12))
        words.append(sw(X13, 0x40, X0))
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
    Divide by zero and the signed overflow (-2^31 / -1) close the program.
    """
    body = [
        *li(X1, -100_003),
        addi(X2, X0, 77),
        div(X3, X1, X2),
        rem(X4, X1, X2),
        divu(X5, X1, X2),
        addi(X9, X0, 1),
        remu(X6, X1, X2),
        div(X7, X1, X2),
        addi(X2, X2, 1),
        rem(X8, X1, X2),
        addi(X2, X2, 0),
        rem(X10, X1, X2),
        div(X11, X2, X2),
        mul(X12, X1, X2),
        div(X13, X1, X2),
        rem(X14, X1, X2),
        add(X15, X13, X14),
        addi(X2, X0, 0),
        div(X16, X1, X2),
        rem(X17, X1, X2),
        *li(X1, 0x8000_0000),
        addi(X2, X0, -1),
        div(X18, X1, X2),
        rem(X19, X1, X2),
        sw(X19, 0x40, X0),
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
        *li(X1, 0x7654_3210),
        addi(X2, X0, 13),
        lw(X5, error_address, X0),
        divu(X6, X1, X2),
        remu(X7, X1, X2),
        add(X8, X6, X7),
        sw(X8, 0x40, X0),
    ]
    for latency in range(1, 40, 3):
        await run_program(
            dut, with_handler(body), stop_cause=3, max_cycles=600,
            data_error_addresses=(error_address,),
            data=PortTiming(latency=latency), seed=rng.getrandbits(32),
        )


async def check_flush_suppresses_younger_store(dut, rng: random.Random) -> None:
    poison = [sw(X1, 0x40, X0), sw(X1, 0x44, X0), sw(X1, 0x48, X0)]
    transfers = {
        "beq_taken": beq(X0, X0, 16),
        "bne_taken": bne(X0, X2, 16),
        "jal": j(16),
        "jalr": jalr(X0, 0, X3),
    }
    for name, transfer in transfers.items():
        prefix = [*li(X1, 0x5555_AAAA), addi(X2, X0, 1)]
        target = 4 * (len(prefix) + 2 + 4)
        prefix.append(addi(X3, X0, target))
        program = prefix + [transfer] + poison + [addi(X4, X0, 9)]
        assert len(program) * 4 == target, name
        program += [sw(X1, 0x4C, X0), EBREAK]
        for timing_seed in range(3):
            instruction, data = _random_timing(random.Random(rng.getrandbits(32) ^ timing_seed))
            result = await run_program(
                dut, program, stop_cause=3, max_cycles=300,
                instruction=instruction, data=data, seed=timing_seed,
            )
            for address in (0x40, 0x44, 0x48):
                assert word(result.memory, address) == 0, f"{name}: squashed store reached memory"
            assert word(result.memory, 0x4C) == 0x5555_AAAA
    # Why: control case, so a flush that also squashed the not-taken path's
    # younger store would fail here.
    program = [*li(X1, 0x1234_5678), beq(X1, X0, 8), sw(X1, 0x40, X0), EBREAK]
    result = await run_program(dut, program, stop_cause=3, max_cycles=100)
    assert word(result.memory, 0x40) == 0x1234_5678


async def check_system_serialization(dut, rng: random.Random) -> None:
    initial = (0x0BAD_F00D).to_bytes(4, "little")
    body = [
        # Why: placing each CSR access right behind a long-latency load, a MUL or a
        # store in flight forces SYSTEM serialization and exact minstret counting,
        # including a minstret write. The model takes mcycle from the DUT, and the
        # ECALL handler returns past it before mcause and mepc are read.
        lw(X1, 0, X0),
        csrrw(X2, MSCRATCH, X1),
        csrr(X3, MINSTRET),
        mul(X4, X1, X1),
        csrr(X5, MINSTRET),
        sw(X1, 8, X0),
        csrr(X6, MSCRATCH),
        csrw(MINSTRET, X6),
        csrr(X7, MINSTRET),
        csrr(X8, MCYCLE),
        ECALL,
        csrr(X10, MCAUSE),
        csrr(X11, MEPC),
    ]
    for seed in range(3):
        instruction, data = _random_timing(random.Random(rng.getrandbits(32) ^ seed))
        await run_program(
            dut, with_handler(body), stop_cause=3, initial_memory=initial,
            max_cycles=600, instruction=instruction, data=data, seed=seed,
        )


async def check_data_error_precedes_younger_work(dut, rng: random.Random) -> None:
    error_address = 0x80
    youngers = {
        "ecall": [ECALL],
        "store": [sw(X1, 0x40, X0)],
        "csr_write": [csrw(MSCRATCH, X1)],
        "illegal": [0x0000_0000],
        "misaligned_load": [lw(X6, 2, X0)],
        "branch": [beq(X0, X0, 8), sw(X1, 0x44, X0)],
    }
    for kind in ("load", "store"):
        access = (lw(X5, error_address, X0) if kind == "load"
                  else sw(X1, error_address, X0))
        for name, younger in youngers.items():
            body = [*li(X1, 0x7777_1111), access, *younger, csrr(X12, MSCRATCH)]
            for seed in range(2):
                instruction, data = _random_timing(random.Random(rng.getrandbits(32) ^ seed))
                result = await run_program(
                    dut, with_handler(body), stop_cause=3, max_cycles=400,
                    data_error_addresses=(error_address,),
                    instruction=instruction, data=data, seed=seed,
                )
                causes = [item.cause for item in result.observations if item.trap]
                assert causes[0] == (5 if kind == "load" else 7), (kind, name, causes)


def _halt_program() -> list[int]:
    return with_handler([
        *li(X1, -77),
        addi(X2, X0, 9),
        addi(X3, X0, 0x40),
        sw(X1, 0, X3),
        lw(X4, 0, X3),
        mul(X5, X4, X2),
        # Why: a halt only exposes a drain defect for work in flight at that moment,
        # so the program holds every kind: a divide, a forward taken branch (not
        # predicted) over a squashed store, a predicted backward loop that exits by
        # mispredict, a predicted never-taken backward branch, a misaligned-load
        # trap and return, and a JAL.
        div(X6, X1, X2),
        bne(X1, X2, 8),
        sw(X1, 4, X3),
        addi(X10, X0, 2),
        addi(X10, X10, -1),
        bne(X10, X0, -4),
        bne(X10, X0, -12),
        add(X7, X5, X6),
        csrw(MSCRATCH, X7),
        lw(X8, 1, X0),
        sw(X7, 8, X3),
        jal(X1, 8),
        NOP,
        addi(X7, X7, 1),
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
    # Why: a halt requested while the final EBREAK is already in flight ends with
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
    # Why: a halted Reset-PC restarts at zero with registers and CSRs preserved,
    # so each pass bumps mscratch until it reaches 3; the passes differ and the
    # model must follow them exactly.
    program = with_handler([
        csrr(X1, MSCRATCH),
        addi(X1, X1, 1),
        csrw(MSCRATCH, X1),
        addi(X2, X0, 3),
        blt(X2, X1, 8),
        j(-20),
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
        # Why: x1 = 0x80001000, x2 = 5 is a 29- or 30-iteration early-out divide, so
        # the halt sweep and the reset land inside it.
        program = [lui(X1, 0x80001), addi(X2, X0, 5), encode_r(1, 2, 1, funct3, 3),
                   addi(X4, X3, 1), EBREAK]
        acknowledged = 0
        for offset in range(0, 48, 2):
            result = await run_program(
                dut, program, stop_cause=3, max_cycles=400, halts=(HaltPlan(offset),),
                instruction=PortTiming((0, 1), latency=2),
            )
            acknowledged += result.halts_acknowledged
        assert acknowledged >= 12, f"divide halt sweep acknowledged only {acknowledged}"

        await run_program(dut, program, stop_pc=4, max_cycles=64)
        for _ in range(4):
            await RisingEdge(dut.clk_i)
        await FallingEdge(dut.clk_i)
        dut.reset_i.value = 1
        dut.enable_i.value = 0
        await RisingEdge(dut.clk_i)
        await FallingEdge(dut.clk_i)
        dut.reset_i.value = 0
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

    dut.reset_i.value = 1
    await reset_core(dut)  # Why: leaves ready low, so nothing is accepted until the test raises it
    dut.reset_i.value = 1
    await tick()
    assert not int(dut.instruction_request_valid_o.value)
    dut.reset_i.value = 0
    for _ in range(3):
        await tick()
        assert int(dut.instruction_request_valid_o.value)
        assert int(dut.instruction_request_address_o.value) == 0

    dut.instruction_request_ready_i.value = 1
    await tick()
    dut.instruction_request_ready_i.value = 0
    dut.reset_i.value = 1
    await tick()
    assert not int(dut.observation_valid_o.value)
    assert not int(dut.instruction_request_valid_o.value)
    dut.reset_i.value = 0
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

    # Why: the core may retire or discard a fetch whose response is owed at the
    # halt, so the check accepts both and only requires resume at the
    # architectural next PC.
    dut.instruction_request_ready_i.value = 1
    await tick()
    dut.instruction_request_ready_i.value = 0
    dut.halt_request_i.value = 1
    dut.instruction_response_data_i.value = addi(X1, X0, 1)
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


def _countdown_loop(count: int, body: list[int]) -> list[int]:
    """x10 = count; loop: body; x10 -= 1; bne x10, x0, loop (backward)."""
    loop = body + [addi(X10, X10, -1)]
    return [addi(X10, X0, count), *loop, bne(X10, X0, -4 * len(loop))]


async def check_static_prediction(dut, rng: random.Random) -> None:
    """Predicted-taken transfers retire the architectural stream, a
    predicted-taken branch that falls through recovers, speculative fetch
    on the predicted path has no effect, misaligned or faulting predicted
    targets trap exactly as unpredicted ones, and a correct prediction
    costs one refill clock."""
    # Why: the backward branch is predicted taken, so the loop exit exercises
    # mispredict recovery, and the per-iteration stores make a lost or doubled
    # iteration visible in memory.
    loop_store = _countdown_loop(5, [
        lw(X4, 0, X3),
        add(X4, X4, X10),
        sw(X4, 0, X3),
    ])
    program = [addi(X3, X0, 0x40), *loop_store, addi(X5, X0, 1),
               sw(X5, 4, X3), EBREAK]
    for seed in range(3):
        instruction, data = _random_timing(random.Random(rng.getrandbits(32) ^ seed))
        result = await run_program(dut, program, stop_cause=3, max_cycles=600,
                                   instruction=instruction, data=data, seed=seed)
        assert word(result.memory, 0x40) == 15 and word(result.memory, 0x44) == 1

    # Why: slow, stalling fetch makes the predicted redirect of the loop branch and
    # its Q102H mispredict flush twice while one stale response is still owed;
    # that response must be dropped, never retired (seeds 30, 38, 45, 49 and 59
    # reach this window).
    slow_fetch = PortTiming(ready_probability=0.5, latency=(1, 3))
    slow_data = PortTiming(ready_probability=0.8, latency=(1, 6))
    for seed in range(64):
        result = await run_program(dut, program, stop_cause=3, max_cycles=600,
                                   instruction=slow_fetch, data=slow_data, seed=seed)
        assert word(result.memory, 0x40) == 15 and word(result.memory, 0x44) == 1

    # Why: a never-taken backward branch is predicted taken, so its target block
    # of stores is fetched speculatively; the check fails if any of them reaches
    # memory or the fall-through store does not. The block is reached only
    # through a forward JAL (predicted, taken).
    prefix = [*li(X1, 0x5555_AAAA), addi(X2, X0, 1)]
    poison = [sw(X1, 0x40, X0), sw(X1, 0x44, X0), sw(X1, 0x48, X0)]
    program = prefix + [j(4 * (len(poison) + 1))] + poison
    program += [beq(X2, X0, -4 * len(poison)),
                sw(X1, 0x4C, X0), EBREAK]
    for seed in range(3):
        instruction, data = _random_timing(random.Random(rng.getrandbits(32) ^ seed))
        result = await run_program(dut, program, stop_cause=3, max_cycles=300,
                                   instruction=instruction, data=data, seed=seed)
        for address in (0x40, 0x44, 0x48):
            assert word(result.memory, address) == 0, "speculative store reached memory"
        assert word(result.memory, 0x4C) == 0x5555_AAAA

    # Why: one program checks link forwarding behind a backward JAL (predicted
    # taken, always taken), an unpredicted forward taken branch over a store and
    # a predicted loop whose body is a forward branch, taken once (x10 == 1) to
    # skip its store. The stores to 0x40..0x48 are never executed.
    program = with_handler(assemble([
        addi(X3, X0, 0x40),
        j("backward_jal"),
        "link_target:",
        addi(X4, X0, 1),
        addi(X6, X1, 0),
        j("forward_bne"),
        "backward_jal:",
        jal(X1, "link_target"),
        sw(X1, 0, X3),
        sw(X1, 4, X3),
        "forward_bne:",
        bne(X4, X0, "countdown"),
        sw(X1, 8, X3),
        "countdown:",
        *_countdown_loop(3, [beq(X4, X10, 8),
                             sw(X10, 12, X3)]),
        sw(X6, 16, X3),
    ]))
    for seed in range(3):
        instruction, data = _random_timing(random.Random(rng.getrandbits(32) ^ seed))
        result = await run_program(dut, program, stop_cause=3, max_cycles=400,
                                   instruction=instruction, data=data, seed=seed)
        assert word(result.memory, 0x40) == 0 and word(result.memory, 0x44) == 0
        assert word(result.memory, 0x48) == 0
        assert word(result.memory, 0x4C) == 2          # Why: x10 = 1 skips the store, so x10 = 2 stores last
        assert word(result.memory, 0x50) != 0          # Why: x6 stored the backward JAL's link, so zero means the link was lost

    # Ref: core MAS "Static prediction": a misaligned static target is fetched
    # sequentially and traps in Q102H (cause 0, mtval = target); the handler
    # resumes past both so each trap is counted.
    program = with_handler([
        addi(X3, X0, 0x40),
        beq(X0, X0, -2),
        j(-6),
        addi(X5, X0, 1),
        sw(X5, 0, X3),
    ])
    result = await run_program(dut, program, stop_cause=3, max_cycles=300)
    traps = [item for item in result.observations if item.trap and item.cause == 0]
    assert len(traps) == 2 and all(item.trap_value & 2 for item in traps)
    assert word(result.memory, 0x40) == 1

    # Why: a fetch error on a predicted target must trap only when that path is
    # architectural: taken, the faulting instruction traps with cause 1 after the
    # branch retires; not taken, the stale error response is dropped and nothing
    # traps. The word at "fault", jumped over, is the fetch-error target; the
    # handler returns to "resume" after it, the beq back to it is never taken
    # and the bne back to it is taken once.
    body = assemble([
        addi(X3, X0, 0x40),
        addi(X2, X0, 2),
        j("resume"),
        "fault:",
        0,
        "resume:",
        addi(X5, X5, 1),
        beq(X5, X0, "fault"),
        bne(X5, X2, "fault"),
        sw(X5, 0, X3),
    ])
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

    # Why: under the one-cycle responder a two-instruction loop of 32 iterations
    # costs two retirements plus one refill clock per iteration, so the span is
    # bounded near 3 clocks per iteration.
    iterations = 32
    program = [*_countdown_loop(iterations, []), EBREAK]
    result = await run_program(dut, program, stop_cause=3, max_cycles=300,
                               instruction=PortTiming(), data=PortTiming())
    span = result.last_observation_cycle - result.first_observation_cycle + 1
    assert span <= 3 * iterations + 12, f"loop span {span} clocks for {iterations} iterations"
    cocotb.log.info("predicted loop: %d iterations in %d clocks", iterations, span)


def push_ra() -> list[int]:
    return [addi(SP, SP, -4), sw(RA, 0, SP)]


def pop_ra() -> list[int]:
    return [lw(RA, 0, SP), addi(SP, SP, 4)]


def return_program() -> list[int]:
    """Calls and returns exercising every return-address-stack path; the
    final EBREAK stops it. Each step stores a marker so a skipped or doubled
    return shows in memory as well as in the model-checked stream.

    0x600.. markers, 0x680 recursion result, stack below 0x800."""
    poison = [sw(RA, 0x5FC, X0)] * 3
    lines = [
        j("main"), *HANDLER,
        "main:",
        addi(X9, X0, 4), csrw(MTVEC, X9),
        addi(SP, X0, 0x7FC), addi(SP, SP, 4),
        addi(X3, X0, 0x600),
        # Ref: core MAS "Return-address stack": with an empty stack a return is
        # not predicted, so it is fetched sequentially.
        *la(RA, "after_empty"), RET, *poison,
        "after_empty:",
        sw(RA, 0, X3),
        # Why: nesting main -> outer -> middle -> leaf makes each return pop the
        # entry its own call pushed; a wrong pop order skips or doubles a marker.
        call("outer"),
        addi(X5, X0, 1), sw(X5, 4, X3),
        # Ref: core MAS "Return-address stack": a push when full overwrites the
        # oldest entry, so twelve-deep recursion, deeper than the stack,
        # mispredicts the outer returns.
        addi(X10, X0, 12), addi(X11, X0, 0),
        call("recurse"),
        sw(X11, 0x80, X3),
        # Why: the callee replaces its return address, so the stacked target
        # (call + 4) is wrong and the poison behind the call is fetched only
        # speculatively.
        call("redirect_callee"), *poison,
        "redirected:",
        addi(X5, X0, 2), sw(X5, 8, X3),
        # Ref: core MAS "Return-address stack": a Q103H trap clears the count,
        # so the trap discards the predicted return behind its load and the
        # handler resumes at the return with a cleared stack.
        call("trap_callee"),
        addi(X5, X0, 3), sw(X5, 12, X3),
        # Why: a predicted return to a misaligned address must still trap
        # (cause 0) in Q102H; the handler resumes after it and the callee
        # returns again.
        call("misaligned_callee"),
        addi(X5, X0, 4), sw(X5, 16, X3),
        # Why: a call whose callee is a bare return, in a loop, pushes and pops
        # on adjacent slots, the tightest stack update timing.
        addi(X10, X0, 6),
        "spin:",
        call("leaf"),
        addi(X10, X10, -1),
        bne(X10, X0, "spin"),
        sw(X6, 20, X3),
        EBREAK,
        "outer:", *push_ra(), call("middle"), *pop_ra(), RET, *poison,
        "middle:", *push_ra(), call("leaf"), *pop_ra(), RET, *poison,
        "leaf:", addi(X6, X6, 1), RET, *poison,
        "recurse:",
        add(X11, X11, X10),
        addi(X10, X10, -1),
        beq(X10, X0, "recurse_done"),
        *push_ra(), call("recurse"), *pop_ra(),
        "recurse_done:", RET, *poison,
        "redirect_callee:", *la(RA, "redirected"), RET, *poison,
        "trap_callee:",
        lw(X7, 0, X0),
        RET, *poison,
        "misaligned_callee:",
        addi(RA, RA, 2), RET,
        addi(RA, RA, -2), RET, *poison,
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
    # Ref: core MAS "Return-address stack": a debug halt keeps the stack, so
    # halting at every offset must finish Q102H and the owed fetch without losing
    # entries.
    for offset in range(0, reference.cycles, 2):
        result = await run_program(dut, program, stop_cause=3, max_cycles=4000,
                                   data_error_addresses=errors, halts=(HaltPlan(offset),))
        _check_return_memory(result)
    cocotb.log.info("return prediction: reference %d clocks, %d halt offsets",
                    reference.cycles, (reference.cycles + 1) // 2)


# Why: BTB alias sites share the index and partial tag at one and two
# strides. A stride is a multiple of the bytes an index plus 8-bit partial
# tag covers (1024 per entry: PC bits 17..2 at the default 256 entries), at
# least 1 MiB, so every fetch BTB size confuses them.
ALIAS_BRANCH = 0x400
ALIAS_JUMP = 0x440
MIB = 0x10_0000
BTB_TAG_BITS = 8


def btb_alias_span(entries: int) -> int:
    """Bytes of code one index plus partial tag covers: addresses this far
    apart share an entry and its tag (wiki/src/nand2doom/rtl/core/mas.md "Fetch branch target
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
    poison = sw(RA, 0x5FC, X0)
    ret = RET

    def call_abs(address: int) -> list[int]:
        return [*li(X5, address), jalr(RA, 0, X5)]

    # Why: two calls train ALIAS_BRANCH (taken to +12); its first alias is not
    # taken; two calls retrain it; its second alias is taken to +8, after which
    # the entry holds the +8 target for the last ALIAS_BRANCH call. Two calls
    # train ALIAS_JUMP to t1, its first alias jumps to t2 instead, and its
    # second alias is no transfer at all.
    main = assemble([
        addi(SP, X0, 0x7FC),
        addi(X6, X0, 0),
        *la(X8, "t1"),
        *call_abs(ALIAS_BRANCH), *call_abs(ALIAS_BRANCH),
        *call_abs(ALIAS_BRANCH + stride),
        *call_abs(ALIAS_BRANCH), *call_abs(ALIAS_BRANCH),
        *call_abs(ALIAS_BRANCH + 2 * stride),
        *call_abs(ALIAS_BRANCH),
        *call_abs(ALIAS_JUMP), *call_abs(ALIAS_JUMP),
        *la(X8, "t2"),
        *call_abs(ALIAS_JUMP + stride),
        *call_abs(ALIAS_JUMP + 2 * stride),
        sw(X6, 0x600, X0),
        EBREAK,
        "t1:", addi(X6, X6, 16), ret, poison,
        "t2:", addi(X6, X6, 256), ret, poison,
    ])
    assert 4 * len(main) <= ALIAS_BRANCH
    blocks = {
        ALIAS_BRANCH: [beq(X0, X0, 12), poison, poison, addi(X6, X6, 1024), ret],
        ALIAS_BRANCH + stride: [bne(X0, X0, 12), addi(X6, X6, 1), ret, poison],
        ALIAS_BRANCH + 2 * stride: [beq(X0, X0, 8), poison, addi(X6, X6, 4), ret],
        ALIAS_JUMP: [jalr(X0, 0, X8), poison],
        ALIAS_JUMP + stride: [jalr(X0, 0, X8), poison],
        ALIAS_JUMP + 2 * stride: [addi(X6, X6, 2), ret, poison],
    }
    words = main + [NOP] * ((ALIAS_JUMP + 2 * stride) // 4 + 8 - len(main))
    for address, block in blocks.items():
        words[address // 4: address // 4 + len(block)] = block
    return words


# Why: each executed block adds its marker once: five ALIAS_BRANCH calls (1024
# each), one per alias, two jumps to t1, one to t2 and the non-transfer alias
# of the trained jump, so a skipped or doubled block changes the sum.
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


DATA_BASE = 0x200


def random_program(rng: random.Random, length: int = 160) -> tuple[list[int], set[int], set[int]]:
    """Forward-only random RV32IM stream with traps resumed by the handler.

    x8 holds the data base and x9 belongs to the handler; destinations are
    x0..x7. Some data words and one body word are access-error addresses.
    """
    regs = range(1, 8)
    body: list[int] = []
    loop_counts: list[int] = []
    for register in regs:
        body += li(register, rng.getrandbits(32))
    body += li(X8, DATA_BASE)
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
            body.append(jal(rd, 4 * rng.randint(1, 3)))
        elif kind == "csr":
            body.append(rng.choice((
                encode_csr(0x340, rs1, rng.choice((1, 2, 3)), rd),
                encode_csr(0x340, rng.randrange(32), rng.choice((5, 6, 7)), rd),
                csrr(rd, MINSTRET),
                csrr(rd, MCYCLE),
            )))
        elif kind == "misaligned":
            body.append(rng.choice((
                lw(rd, 1, X8),
                sw(rs2, 2, X8),
                lh(rd, 3, X8),
            )))
        elif kind == "ecall":
            body.append(ECALL)
        elif kind == "loop":
            # Why: x10 counts a bounded loop closed by a backward branch, which the core
            # predicts taken: correct until the exit mispredicts. The count is
            # decremented modulo 4, so the loop also exits within four iterations when a
            # forward jump enters it.
            iterations = rng.randint(1, 3)
            inner = [encode_r(0, rs2, rs1, rng.choice((0, 4, 6, 7)), rd)
                     for _ in range(rng.randint(0, 2))]
            body.append(addi(X10, X0, iterations))
            body += inner
            loop_counts += [len(body), len(body) + 1]
            body += [addi(X10, X10, -1), andi(X10, X10, 3)]
            body.append(bne(X10, X0, -4 * (len(inner) + 2)))
        elif kind == "backward_not_taken":
            # Why: x10 is zero outside a loop, so this backward branch is always
            # predicted taken and never taken.
            body.append(bne(X10, X0, -4 * rng.randint(1, min(8, len(body)))))
        else:
            body.append(0xFFFF_FFFF)
    program = with_handler(body + [NOP] * 4)
    data_errors = {DATA_BASE + 4 * rng.randrange(16) for _ in range(2)}
    prologue = 1 + len(HANDLER) + 2
    # Why: a fetch fault skips its word when the handler returns, which must not
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


def kernel_program() -> list[int]:
    """A column-draw loop and a dot product, the shapes of Doom's hot loops.

    Column draw (64 pixels): texel = src[(frac >> 16) & 127]; dest = cmap[texel];
    frac += step. Dot product (32 words): total += a[i] * b[i]. Registers x10 and
    up; data lives in the 4 KiB model memory.
    """
    count, frac, src, cmap, dest, texel, pixel, step = X10, X11, X12, X13, X14, X15, X16, X17
    a, b, total, left, right, product = X20, X21, X22, X23, X24, X25
    column = [
        srai(texel, frac, 16),
        andi(texel, texel, 127),
        add(texel, src, texel),
        lbu(pixel, 0, texel),
        add(pixel, cmap, pixel),
        lbu(pixel, 0, pixel),
        sb(pixel, 0, dest),
        addi(dest, dest, 4),
        add(frac, frac, step),
        addi(count, count, -1),
    ]
    column.append(bnez(count, -4 * len(column)))
    dot = [
        lw(left, 0, a),
        lw(right, 0, b),
        mul(product, left, right),
        add(total, total, product),
        addi(a, a, 4),
        addi(b, b, 4),
        addi(count, count, -1),
    ]
    dot.append(bnez(count, -4 * len(dot)))
    return [
        addi(count, X0, 64),
        *li(frac, 0x0003_8000),
        addi(src, X0, 0x100),
        addi(cmap, X0, 0x200),
        addi(dest, X0, 0x400),
        *li(step, 0x0001_9000),
        *column,
        addi(count, X0, 32),
        addi(a, X0, 0x600),
        addi(b, X0, 0x700),
        addi(total, X0, 0),
        *dot,
        sw(total, 0x7FC, X0),
        EBREAK,
    ]


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
