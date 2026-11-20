from __future__ import annotations

import os
import random
import re
import shutil
import subprocess
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge, Timer

from .observation import read_observation
from .c_signature import compile_c_program, compile_signature_program
from .core_runner import PortTiming, RunResult, btb_entries, run_program, word as _word
from . import pipeline_cases
from .rv32im_model import (
    MASK32,
    c_like_signature_program,
    encode_b,
    encode_csr,
    encode_i,
    encode_j,
    encode_r,
    encode_s,
    encode_u,
    load_immediate,
    m_result,
    u32,
)

CLOCK_HZ = 25_000_000
CLOCK_PERIOD_NS = 40
# Signature of programs/doom_math.c, computed on the host from the C source.
DOOM_MATH_SIGNATURE = 0xA174_19B9


async def _run(
    dut,
    words,
    *,
    instruction_error_address: int | None = None,
    data_error_address: int | None = None,
    instruction_latency: int = 1,
    data_latency: int = 1,
    instruction_ready_pattern: tuple[int, ...] = (1,),
    data_ready_pattern: tuple[int, ...] = (1,),
    halt_on_first_data: bool = False,
    **kwargs,
) -> RunResult:
    """Directed-case adapter over the model-checked public-port runner."""
    return await run_program(
        dut,
        words,
        instruction_error_addresses=()
        if instruction_error_address is None
        else (instruction_error_address,),
        data_error_addresses=() if data_error_address is None else (data_error_address,),
        instruction=PortTiming(instruction_ready_pattern, latency=instruction_latency),
        data=PortTiming(data_ready_pattern, latency=data_latency),
        halt_on_first_data=halt_on_first_data,
        **kwargs,
    )


def _target_program(left: int, right: int, instruction: int) -> tuple[list[int], int]:
    words = load_immediate(1, left) + load_immediate(2, right)
    target_pc = len(words) * 4
    words += [instruction, 0x0010_0073]
    return words, target_pc


async def _check_m_boundaries(dut, rng: random.Random) -> None:
    vectors = [
        (0, 0),
        (1, 0),
        (MASK32, 0),
        (0x8000_0000, MASK32),
        (0x8000_0000, 2),
        (0x7FFF_FFFF, 3),
        (MASK32, MASK32),
        (0x1234_5678, 0x8765_4321),
    ]
    vectors += [(rng.getrandbits(32), rng.getrandbits(32)) for _ in range(4)]
    for funct3 in range(8):
        for left, right in vectors:
            instruction = encode_r(1, 2, 1, funct3, 3)
            words, target_pc = _target_program(left, right, instruction)
            result = await _run(dut, words, stop_pc=target_pc, max_cycles=96)
            observed = result.observations[-1]
            expected = m_result(funct3, left, right)
            assert not observed.trap
            assert observed.rd_address == 3 and observed.rd_write == 1
            assert observed.rd_write_data == expected, (
                f"M funct3={funct3} left=0x{left:08x} right=0x{right:08x} "
                f"expected=0x{expected:08x} got=0x{observed.rd_write_data:08x}"
            )


async def _check_integer_operations(dut) -> None:
    base_cases = [
        (0, 0, 0xFFFF_FFFF, 1, 0),
        (0x20, 0, 0, 1, MASK32),
        (0, 1, 0x8000_0001, 31, 0x8000_0000),
        (0, 2, 0x8000_0000, 1, 1),
        (0, 3, 0x8000_0000, 1, 0),
        (0, 4, 0xAAAA_5555, 0xFFFF_0000, 0x5555_5555),
        (0, 5, 0x8000_0000, 31, 1),
        (0x20, 5, 0x8000_0000, 31, MASK32),
        (0, 6, 0xA000_0000, 0x0F00_000F, 0xAF00_000F),
        (0, 7, 0xA5A5_5A5A, 0x0FF0_0FF0, 0x05A0_0A50),
    ]
    for funct7, funct3, left, right, expected in base_cases:
        words, pc = _target_program(left, right, encode_r(funct7, 2, 1, funct3, 3))
        result = await _run(dut, words, stop_pc=pc, max_cycles=48)
        observed = result.observations[-1]
        assert observed.rd_write_data == expected, (
            f"OP funct7=0x{funct7:02x} funct3={funct3} left=0x{left:08x} "
            f"right=0x{right:08x} expected=0x{expected:08x} observed={observed} "
            f"stream={result.observations}"
        )

    immediate_cases = [
        (0, -1, 0, MASK32),
        (2, -1, 0x8000_0000, 1),
        (3, -1, 0, 1),
        (4, -1, 0xAAAA_5555, 0x5555_AAAA),
        (6, 0x55, 0xAA00, 0xAA55),
        (7, 0x0F0, 0xA5A5, 0x00A0),
    ]
    for funct3, imm, left, expected in immediate_cases:
        words = load_immediate(1, left)
        pc = len(words) * 4
        words += [encode_i(imm, 1, funct3, 3), 0x0010_0073]
        observed = (await _run(dut, words, stop_pc=pc, max_cycles=40)).observations[-1]
        assert observed.rd_write_data == expected
    for shift, expected_sll, expected_srl, expected_sra in (
        (0, 0x8000_0001, 0x8000_0001, 0x8000_0001),
        (31, 0x8000_0000, 1, MASK32),
    ):
        for funct3, high, expected in ((1, 0, expected_sll), (5, 0, expected_srl), (5, 0x20, expected_sra)):
            words = load_immediate(1, 0x8000_0001)
            pc = len(words) * 4
            words += [encode_i((high << 5) | shift, 1, funct3, 3), 0x0010_0073]
            observed = (await _run(dut, words, stop_pc=pc, max_cycles=40)).observations[-1]
            assert observed.rd_write_data == expected


async def _check_control_memory_system(dut) -> None:
    # Upper immediates, both jumps, all six branches, and x0 suppression.
    cases = [
        ([encode_u(0xABCDE000, 3, 0x37)], 0xABCDE000, 4),
        ([encode_u(0x12345000, 3, 0x17)], 0x12345000, 4),
        ([encode_j(8, 3)], 4, 8),
    ]
    for words, expected_rd, expected_next in cases:
        observed = (await _run(dut, words, stop_pc=0, max_cycles=16)).observations[-1]
        assert observed.rd_write_data == expected_rd and observed.next_pc == expected_next

    words = load_immediate(1, 16)
    pc = len(words) * 4
    words += [encode_i(0, 1, 0, 3, 0x67)]
    observed = (await _run(dut, words, stop_pc=pc, max_cycles=32)).observations[-1]
    assert observed.rd_write_data == pc + 4 and observed.next_pc == 16

    branch_values = [
        (0, 5, 5), (1, 5, 4), (4, 0x8000_0000, 1),
        (5, 1, 0x8000_0000), (6, 1, 0x8000_0000), (7, MASK32, 1),
    ]
    for funct3, left, right in branch_values:
        words, pc = _target_program(left, right, encode_b(8, 2, 1, funct3))
        observed = (await _run(dut, words, stop_pc=pc, max_cycles=48)).observations[-1]
        assert observed.next_pc == pc + 8
    words, pc = _target_program(5, 5, encode_b(8, 2, 1, 1))
    observed = (await _run(dut, words, stop_pc=pc, max_cycles=48)).observations[-1]
    assert observed.next_pc == pc + 4

    observed = (await _run(dut, [encode_i(1, 0, 0, 0)], stop_pc=0)).observations[-1]
    assert observed.rd_write == 0 and observed.rd_address == 0

    initial = bytes((0x01, 0x7F, 0xFF, 0x80))
    load_cases = [(0, 0, 1), (0, 2, MASK32), (1, 2, 0xFFFF_80FF), (2, 0, 0x80FF_7F01), (4, 2, 0xFF), (5, 2, 0x80FF)]
    for funct3, offset, expected in load_cases:
        instruction = encode_i(offset, 0, funct3, 3, 0x03)
        observed = (await _run(dut, [instruction], stop_pc=0, initial_memory=initial)).observations[-1]
        assert observed.rd_write_data == expected
        assert observed.memory_read_mask in (1, 2, 4, 8, 3, 12, 15)

    for funct3, offset, value, expected_mask in ((0, 3, 0xAA, 8), (1, 2, 0xBEEF, 12), (2, 0, 0x12345678, 15)):
        words = load_immediate(1, value)
        pc = len(words) * 4
        words += [encode_s(offset, 1, 0, funct3)]
        result = await _run(dut, words, stop_pc=pc, max_cycles=40)
        observed = result.observations[-1]
        assert observed.memory_write_mask == expected_mask
        expected_bus = u32(value << (offset * 8))
        expected_lane_bits = sum(
            0xFF << (8 * lane) for lane in range(4) if expected_mask & (1 << lane)
        )
        assert observed.memory_write_data == expected_bus
        assert _word(result.memory, 0) == (expected_bus & expected_lane_bits)

    # FENCE retires, every CSR operation decodes, and MRET uses aligned MEPC.
    observed = (await _run(dut, [0x0000_000F], stop_pc=0)).observations[-1]
    assert not observed.trap and observed.next_pc == 4
    for funct3 in (1, 2, 3, 5, 6, 7):
        operand = 1 if funct3 < 5 else 7
        words = ([encode_i(7, 0, 0, 1)] if funct3 < 5 else []) + [encode_csr(0x340, operand, funct3, 3)]
        pc = (len(words) - 1) * 4
        observed = (await _run(dut, words, stop_pc=pc, max_cycles=32)).observations[-1]
        assert not observed.trap and observed.rd_write == 1
    words = load_immediate(1, 16) + [encode_csr(0x341, 1, 1, 0), 0x3020_0073]
    pc = (len(words) - 1) * 4
    observed = (await _run(dut, words, stop_pc=pc, max_cycles=40)).observations[-1]
    assert not observed.trap and observed.next_pc == 16

    for instruction, cause, tval in ((0x0000_0073, 11, 0), (0x0010_0073, 3, 0), (0, 2, 0)):
        observed = (await _run(dut, [instruction], stop_trap=True)).observations[-1]
        assert observed.trap and observed.cause == cause and observed.trap_value == tval
    for instruction, cause, tval in (
        (encode_i(1, 0, 1, 3, 0x03), 4, 1),
        (encode_s(2, 1, 0, 2), 6, 2),
        (encode_j(2, 0), 0, 2),
    ):
        observed = (await _run(dut, [instruction], stop_trap=True)).observations[-1]
        assert observed.trap and observed.cause == cause and observed.trap_value == tval

    observed = (
        await _run(dut, [encode_i(0, 0, 2, 3, 0x03)], stop_trap=True, data_error_address=0)
    ).observations[-1]
    assert observed.cause == 5 and observed.trap_value == 0 and not observed.rd_write
    store_words = load_immediate(1, 0x1234) + [encode_s(0, 1, 0, 2)]
    observed = (
        await _run(dut, store_words, stop_trap=True, data_error_address=0, max_cycles=40)
    ).observations[-1]
    assert observed.cause == 7 and observed.trap_value == 0 and not observed.memory_valid
    observed = (
        await _run(dut, [encode_i(0, 0, 0, 0)], stop_trap=True, instruction_error_address=0)
    ).observations[-1]
    assert observed.cause == 1 and observed.trap_value == 0 and observed.instruction == 0


async def _check_generated_signature(dut) -> None:
    result = await _run(dut, c_like_signature_program(), stop_trap=True, max_cycles=160)
    assert result.observations[-1].cause == 3
    assert _word(result.memory, 0) == 127
    assert any(item.memory_write and item.memory_write_data == 127 for item in result.observations)
    assert sum(item.instruction == encode_r(1, 1, 2, 0, 2) for item in result.observations) == 5


_C_IMAGE: tuple | None = None


def compile_signature_program_cached():
    global _C_IMAGE
    if _C_IMAGE is None:
        _C_IMAGE = compile_signature_program()
    return _C_IMAGE


async def _check_compiled_c_signature(dut) -> None:
    words, provenance = compile_signature_program_cached()
    cocotb.log.info(
        "freestanding C image words=%d provenance=%s", len(words), provenance
    )
    result = await _run(dut, words, stop_cause=3, max_cycles=512)
    assert result.observations[-1].trap and result.observations[-1].cause == 3
    assert _word(result.memory, 0x80) == 127
    assert any(
        item.memory_write
        and item.memory_address == 0x80
        and item.memory_write_data == 127
        for item in result.observations
    )


async def _check_csr_trap_state(dut) -> None:
    nop = encode_i(0, 0, 0, 0)
    words = [nop] * 16
    words[0] = encode_i(32, 0, 0, 1)
    words[1] = encode_csr(0x305, 1, 1, 0)  # mtvec = 32
    words[2] = encode_i(8, 0, 0, 1)
    words[3] = encode_csr(0x300, 1, 1, 0)  # request MIE=1, MPP WARL->M
    words[4] = 0x0000_0073               # ECALL
    words[5] = encode_csr(0x300, 0, 2, 9)   # state after MRET
    words[6] = 0x0010_0073
    words[8] = encode_csr(0x300, 0, 2, 5)  # trap mstatus
    words[9] = encode_csr(0x341, 0, 2, 6)  # mepc
    words[10] = encode_csr(0x342, 0, 2, 7)  # mcause
    words[11] = encode_csr(0x343, 0, 2, 8)  # mtval
    words[12] = encode_i(4, 6, 0, 6)
    words[13] = encode_csr(0x341, 6, 1, 0)
    words[14] = 0x3020_0073
    result = await _run(dut, words, stop_cause=3, max_cycles=192)
    assert [item.order for item in result.observations] == list(
        range(len(result.observations))
    )
    by_pc = {item.pc: item for item in result.observations}
    assert by_pc[16].trap and by_pc[16].cause == 11 and by_pc[16].next_pc == 32
    assert not by_pc[16].rd_write and not by_pc[16].memory_valid
    assert by_pc[32].rd_write_data == 0x0000_1880  # MPP=M, MPIE=1, MIE=0
    assert by_pc[36].rd_write_data == 16
    assert by_pc[40].rd_write_data == 11
    assert by_pc[44].rd_write_data == 0
    assert by_pc[56].next_pc == 20
    assert by_pc[20].rd_write_data == 0x0000_1888  # MPP=M, MPIE=1, MIE=1

    csr_cases = (
        (1, 0x0F, 0x0F),
        (2, 0x30, 0x3F),
        (3, 0x03, 0x0C),
        (5, 0x0F, 0x0F),
        (6, 0x10, 0x1F),
        (7, 0x03, 0x0C),
    )
    for funct3, operand, expected in csr_cases:
        program = [
            encode_i(0x0F, 0, 0, 1),
            encode_csr(0x340, 1, 1, 0),
        ]
        if funct3 < 5:
            program += load_immediate(2, operand)
            csr_operand = 2
        else:
            csr_operand = operand
        target_pc = len(program) * 4
        program += [
            encode_csr(0x340, csr_operand, funct3, 3),
            encode_csr(0x340, 0, 2, 4),
        ]
        result = await _run(dut, program, stop_pc=target_pc + 4, max_cycles=64)
        assert result.observations[-2].rd_write_data == 0x0F
        assert result.observations[-1].rd_write_data == expected

    # Zero-operand CSRRS/CSRRCI read without changing the CSR.
    for instruction in (encode_csr(0x340, 0, 2, 3), encode_csr(0x340, 0, 7, 3)):
        program = [
            encode_i(0x55, 0, 0, 1),
            encode_csr(0x340, 1, 1, 0),
            instruction,
            encode_csr(0x340, 0, 2, 4),
        ]
        result = await _run(dut, program, stop_pc=12, max_cycles=48)
        assert result.observations[-2].rd_write_data == 0x55
        assert result.observations[-1].rd_write_data == 0x55

    # M-only MPP is WARL=M even when software requests unsupported modes.
    program = [
        encode_i(0, 0, 0, 1),
        encode_csr(0x300, 1, 1, 0),
        encode_csr(0x300, 0, 2, 3),
    ]
    result = await _run(dut, program, stop_pc=8, max_cycles=40)
    assert result.observations[-1].rd_write_data == 0x1800

    for instruction in (encode_csr(0x301, 1, 1, 0), encode_csr(0x777, 0, 2, 3)):
        observed = (await _run(dut, [instruction], stop_trap=True)).observations[-1]
        assert observed.cause == 2 and observed.trap_value == instruction

    counters = [
        encode_csr(0xB02, 0, 2, 3),
        encode_csr(0xB02, 0, 2, 4),
        encode_csr(0xB00, 0, 2, 5),
        encode_csr(0xB00, 0, 2, 6),
    ]
    result = await _run(dut, counters, stop_pc=12, max_cycles=48)
    assert result.observations[0].rd_write_data == 0
    assert result.observations[1].rd_write_data == 1
    assert result.observations[3].rd_write_data > result.observations[2].rd_write_data

    # Encoded rs1, not its contents, controls CSR write intent. x1 resets to
    # zero, but CSRRS/CSRRC with x1 must still reject a write to read-only MISA.
    for funct3 in (2, 3):
        instruction = encode_csr(0x301, 1, funct3, 3)
        observed = (await _run(dut, [instruction], stop_trap=True)).observations[-1]
        assert observed.cause == 2 and observed.trap_value == instruction

    # Writing either half of MINSTRET overrides its implicit increment, even
    # for a read/modify/write whose nonzero source register contains zero.
    for address in (0xB02, 0xB82):
        for funct3 in (1, 2, 3):
            program = [encode_csr(address, 1, funct3, 3), encode_csr(0xB02, 0, 2, 4)]
            result = await _run(dut, program, stop_pc=4, max_cycles=32)
            assert result.observations[0].rd_write_data == 0
            assert result.observations[1].rd_write_data == 0


def _pmon_program(steps: list[tuple[list[int], int | None]]) -> tuple[list[int], dict[int, int]]:
    """Concatenate (instructions, expected rd value of the last one) steps."""
    words: list[int] = []
    expected: dict[int, int] = {}
    for instructions, value in steps:
        words += instructions
        if value is not None:
            expected[(len(words) - 1) * 4] = value
    return words + [0x0010_0073], expected


def _check_pmon_results(result: RunResult, expected: dict[int, int]) -> None:
    by_pc = {item.pc: item for item in result.observations if not item.trap}
    for pc, value in expected.items():
        assert by_pc[pc].rd_write_data == value, (
            f"pc {pc:#x}: read {by_pc[pc].rd_write_data:#x}, expected {value:#x}"
        )


async def _check_pmon(dut) -> None:
    nop = encode_i(0, 0, 0, 0)
    csrrw, csrrs = 1, 2

    def read(csr: int, rd: int = 3) -> list[int]:
        return [encode_csr(csr, 0, csrrs, rd)]

    def write(csr: int, value: int) -> list[int]:
        return load_immediate(1, value) + [encode_csr(csr, 1, csrrw, 0)]

    # Selector legalization (unsupported IDs read back NONE) and the
    # writable mcountinhibit mask 0x7d.
    steps = [(write(0x320, MASK32), None), (read(0x320), 0x7D),
             (write(0x320, 0), None), (read(0x320), 0)]
    for value, legal in ((2, 2), (7, 0), (11, 0), (35, 0), (64, 64), (69, 69),
                         (70, 0), (75, 0), (76, 76), (77, 77), (78, 78), (79, 79),
                         (80, 80), (82, 82), (83, 83), (86, 86), (91, 91), (92, 92), (93, 93), (94, 94), (95, 95), (96, 96), (97, 97), (98, 0), (130, 0),
                         (0x8000_0002, 0)):
        steps += [(write(0x323, value), None), (read(0x323), legal)]
    for index in range(4):
        steps += [(write(0x323 + index, 64 + index), None), (read(0x323 + index), 64 + index)]
    words, expected = _pmon_program(steps)
    _check_pmon_results(await _run(dut, words, stop_trap=True, max_cycles=600), expected)

    # INSTRET counts nontrap retirements selected from the cycle after the
    # selector writer; a counter-half write replaces that half, keeps the
    # other (including a pending carry) and drops its writer's own event;
    # inhibit applies from the cycle after its writer.
    words = [
        encode_i(2, 0, 0, 1),                 # 0
        encode_csr(0x323, 1, csrrw, 0),       # 4  select INSTRET
        nop,                                  # 8   count 1
        nop,                                  # 12  count 2
        encode_csr(0xB03, 0, csrrs, 3),       # 16  reads 2
        encode_csr(0xB03, 0, csrrs, 4),       # 20  reads 3
        encode_csr(0xB03, 0, csrrw, 5),       # 24  reads 4, writes 0
        encode_csr(0xB03, 0, csrrs, 6),       # 28  reads 0
        encode_i(-1, 0, 0, 7),                # 32
        encode_csr(0xB03, 7, csrrw, 0),       # 36  low = 0xffffffff
        encode_csr(0xB83, 0, csrrs, 8),       # 40  reads high 0, then carries
        encode_csr(0xB83, 0, csrrs, 9),       # 44  reads high 1
        encode_csr(0xB03, 0, csrrs, 10),      # 48  reads low 1
        encode_i(5, 0, 0, 11),                # 52  low 3 after this retires
        encode_csr(0xB83, 11, csrrw, 0),      # 56  high = 5, low kept
        encode_csr(0xB83, 0, csrrs, 12),      # 60  reads 5
        encode_csr(0xB03, 0, csrrs, 13),      # 64  reads 4
        encode_i(8, 0, 0, 14),                # 68  low 5
        encode_csr(0x320, 14, csrrw, 0),      # 72  inhibit HPM3; counted (old)
        nop,                                  # 76  not counted
        encode_csr(0xB03, 0, csrrs, 15),      # 80  reads 7
        encode_csr(0x320, 0, csrrw, 0),       # 84  resume; not counted (old)
        encode_csr(0xB03, 0, csrrs, 16),      # 88  reads 7
        encode_csr(0xB03, 0, csrrs, 17),      # 92  reads 8
        0x0010_0073,
    ]
    expected = {16: 2, 20: 3, 24: 4, 28: 0, 40: 0, 44: 1, 48: 1, 60: 5, 64: 4,
                80: 7, 88: 7, 92: 8}
    _check_pmon_results(await _run(dut, words, stop_trap=True, max_cycles=400), expected)

    # CY/IR inhibit stop mcycle and minstret; the stopping writer retires
    # under the old (counting) controls.
    words = [
        encode_i(5, 0, 0, 1),
        encode_csr(0x320, 1, csrrw, 0),
        encode_csr(0xB00, 0, csrrs, 3),
        encode_csr(0xB02, 0, csrrs, 4),
        nop,
        encode_csr(0xB00, 0, csrrs, 5),
        encode_csr(0xB02, 0, csrrs, 6),
        0x0010_0073,
    ]
    result = await _run(dut, words, stop_trap=True, max_cycles=128)
    by_pc = {item.pc: item.rd_write_data for item in result.observations}
    assert by_pc[12] == 2 and by_pc[24] == 2
    assert by_pc[20] == by_pc[8]

    # A CYCLES counter advances exactly with mcycle between reads.
    steps = [(write(0x324, 1), None)]
    steps += [(read(0xB00, 3), None), (read(0xB04, 4), None), ([nop, nop], None),
              (read(0xB00, 5), None), (read(0xB04, 6), None)]
    words, _ = _pmon_program(steps)
    result = await _run(dut, words, stop_trap=True, max_cycles=128,
                        instruction_latency=2)
    reads = [item.rd_write_data for item in result.observations if item.rd_write][-4:]
    assert reads[3] - reads[1] == reads[2] - reads[0] > 0

    # Core stall and redirect events with variable fetch and data latency.
    words, _ = _pmon_program([
        (write(0x323, 3), None),                # IFETCH_WAIT
        (write(0x324, 4), None),                # DATA_HOLD
        (write(0x325, 6), None),                # MULDIV_HOLD
        (write(0x326, 9), None),                # BRANCH_REDIRECT
    ])
    words = words[:-1]
    words += [
        encode_u(0x8000_1000, 5, 0x37),       # lui x5, 0x80001
        encode_i(0, 0, 2, 2, opcode=0x03),    # lw x2, 0(x0)
        encode_r(1, 1, 5, 5, 3),              # divu x3, x5, x1 (x1 = 9)
        encode_b(8, 0, 0, 0),                 # beq x0, x0, +8
        nop,                                  # skipped
        encode_j(4, 0),                       # jal x0, +4
        encode_csr(0xB03, 0, csrrs, 10),
        encode_csr(0xB04, 0, csrrs, 11),
        encode_csr(0xB05, 0, csrrs, 12),
        encode_csr(0xB06, 0, csrrs, 13),
        0x0010_0073,
    ]
    result = await _run(dut, words, stop_trap=True, max_cycles=600,
                        instruction_latency=3, data_latency=4)
    reads = {item.rd_address: item.rd_write_data for item in result.observations if item.rd_write}
    # IFETCH_WAIT counts clocks in which Q102H could take an instruction but
    # none had arrived: with three-cycle fetches that is about two clocks per
    # instruction outside the divide and the serialized CSR writers.
    straight_line = len(words) - 1
    assert straight_line <= reads[10] <= 3 * straight_line, reads
    # 0x80001000 / 9: setup plus msb(31) - msb(3) + 1 = 29 iterations.
    assert reads[11] == 3 and reads[12] == 30 and reads[13] == 2, reads

    # Platform events are counted from the input vector; unsupported IDs are
    # neither selectable nor visible on the registered pmon_events_o vector.
    for event in (12, 94, 95, 96, 97):
        words, _ = _pmon_program([(write(0x325, event), None), (write(0x326, 70), None),
                                  ([nop] * 80, None), (read(0xB05, 3), None),
                                  (read(0x326, 4), None), (read(0xB06, 5), None)])
        pulses = {cycle: (1 << event) | (1 << 70) for cycle in range(40, 50)}
        pulses.update({cycle: 1 << 70 for cycle in range(50, 54)})
        result = await _run(dut, words, stop_trap=True, max_cycles=256,
                            platform_events=pulses,
                            platform_observe_mask=(1 << event) | (1 << 70),
                            platform_observe_legal=1 << event)
        reads = {item.rd_address: item.rd_write_data for item in result.observations if item.rd_write}
        assert reads == {1: 70, 3: 10, 4: 0, 5: 0}, reads


async def _check_pmon_pipeline_events(dut) -> None:
    """Load-use, MUL-use, serialization and redirect events with one-cycle
    responses; each hazard below costs exactly one bubble."""
    csrrw, csrrs = 1, 2

    def write(csr: int, value: int) -> list[int]:
        return load_immediate(1, value) + [encode_csr(csr, 1, csrrw, 0)]

    words = (write(0x323, 5) + write(0x324, 69) + write(0x325, 68) + write(0x326, 9))
    words += [
        encode_i(0, 0, 2, 2, opcode=0x03),    # lw x2
        encode_r(0, 2, 2, 0, 3),              # add x3, x2, x2: load-use
        encode_r(1, 3, 3, 0, 4),              # mul x4, x3, x3
        encode_r(0, 4, 4, 0, 5),              # add x5, x4, x4: MUL-use
        encode_i(0, 0, 2, 6, opcode=0x03),    # lw x6
        encode_i(0, 0, 0, 0),
        encode_r(0, 6, 6, 0, 7),              # distance 2: no stall
        encode_r(1, 7, 7, 0, 8),              # mul x8
        encode_i(1, 0, 0, 9),
        encode_r(0, 8, 8, 0, 10),             # distance 2: no stall
        encode_b(8, 0, 0, 0),                 # taken
        encode_i(0, 0, 0, 0),
        encode_csr(0xB03, 0, csrrs, 11),
        encode_csr(0xB04, 0, csrrs, 12),
        encode_csr(0xB06, 0, csrrs, 13),
        encode_csr(0xB05, 0, csrrs, 14),
        0x0010_0073,
    ]
    initial = (7).to_bytes(4, "little")
    result = await _run(dut, words, stop_trap=True, max_cycles=400, initial_memory=initial)
    reads = {item.rd_address: item.rd_write_data for item in result.observations if item.rd_write}
    assert reads[11] == 1 and reads[12] == 1 and reads[13] == 1, reads
    # Each serialized reader waits for the stages behind it to drain.
    assert reads[14] >= 6, reads


async def _count_run(dut, body: list[int], events: tuple[int, int, int, int],
                     latency: int = 1, **kwargs) -> tuple[tuple[int, ...], RunResult]:
    """Run ``body`` behind selector writes for four events (the same number
    of instructions for every selection, so the timing is identical) and
    return the four counter reads (x11..x14) the body ends with, and the run."""
    csrrw = 1
    words: list[int] = []
    for index, event in enumerate(events):
        words += load_immediate(1, event) + [encode_csr(0x323 + index, 1, csrrw, 0)]
    result = await _run(dut, words + body, stop_cause=3, max_cycles=600,
                        instruction_latency=latency, **kwargs)
    reads = {item.rd_address: item.rd_write_data for item in result.observations if item.rd_write}
    return (reads[11], reads[12], reads[13], reads[14]), result


async def _count_events(dut, body: list[int], events: tuple[int, int, int, int],
                       latency: int) -> tuple[int, ...]:
    """Counter reads of :func:`_count_run`."""
    reads, _ = await _count_run(dut, body, events, latency)
    return reads


def _read_counters() -> list[int]:
    csrrs = 2
    return [encode_csr(0xB03 + index, 0, csrrs, 11 + index) for index in range(4)] + [pipeline_cases.EBREAK]


async def _check_prediction_counts(dut, name: str, body: list[int],
                                   transfers: tuple[int, int, int],
                                   bubbles: dict[int, tuple[int, int, int, int, int]]) -> None:
    """Exact prediction counts for ``body`` (ending in the counter reads):
    ``transfers`` is (BRANCH_PREDICT_TAKEN 78, BRANCH_MISPREDICT 79,
    BRANCH_REDIRECT 9) under every latency; ``bubbles[latency]`` is
    (IFETCH_PREDICT_BUBBLE 83, IFETCH_MISPREDICT_REFILL 84,
    IFETCH_REDIRECT_REFILL 85, FETCH_PREDICT_TAKEN 92, FETCH_PREDICT_STALE
    93). The IFETCH_WAIT causes sum to IFETCH_WAIT (3); under one-cycle
    responses there is no supply wait, so IFETCH_WAIT is exactly the refill
    clocks, and under three-cycle responses some supply waits remain."""
    for latency, expected in bubbles.items():
        predicted, mispredicted, taken, ifetch_wait = await _count_events(
            dut, body, (78, 79, 9, 3), latency)
        assert (predicted, mispredicted, taken) == transfers, (name, latency, predicted,
                                                                mispredicted, taken)
        causes = await _count_events(dut, body, (83, 84, 85, 86), latency)
        fetch = await _count_events(dut, body, (92, 93, 78, 79), latency)
        cocotb.log.info("%s latency=%d IFETCH_WAIT=%d causes=%s fetch_taken/stale=%s",
                        name, latency, ifetch_wait, causes, fetch[:2])
        assert causes[:3] + fetch[:2] == expected, (name, latency, causes, fetch)
        assert fetch[2:] == (predicted, mispredicted), (name, latency, fetch)
        assert sum(causes) == ifetch_wait, (name, latency, causes, ifetch_wait)
        assert (causes[3] == 0) == (latency == 1), (name, latency, causes)


async def _check_pmon_prediction_events(dut) -> None:
    """A three-iteration two-instruction loop, a forward taken branch, a
    JAL and a never-taken backward branch. Static prediction covers the
    first loop iteration and the JAL; the loop's second fetch of its branch
    under one-cycle responses comes on the edge its first commit trains the
    fetch BTB, which the read-first table does not show yet, so only the
    third iteration is predicted at fetch (and falls through); under
    three-cycle responses the second already is."""
    nop = encode_i(0, 0, 0, 0)
    body = [
        encode_i(3, 0, 0, 2),                 # x2 = 3
        encode_i(-1, 2, 0, 2),                # loop: x2 -= 1
        encode_b(-4, 0, 2, 1),                # bne x2, x0, loop: taken twice
        encode_b(8, 0, 0, 0),                 # beq x0, x0, +8: forward taken, not predicted
        nop,                                  # skipped
        encode_j(8, 0),                       # jal +8: predicted, taken
        nop,                                  # skipped
        encode_b(-8, 0, 2, 1),                # bne x2, x0, -8: predicted, never taken
    ] + _read_counters()
    # Predicted: three loop branches, the JAL and the last branch; wrong:
    # the forward branch, the loop exit and the last branch.
    await _check_prediction_counts(dut, "prediction", body, (5, 3, 4), {
        1: (3, 6, 0, 1, 0),
        3: (2, 6, 0, 2, 0),
    })


async def _check_pmon_fetch_btb_events(dut) -> None:
    """Back-to-back taken JALs and a loop branch, trained on the first of
    three iterations and predicted at fetch (no refill clock) on the
    others; the loop exit is the only mispredict."""
    nop = encode_i(0, 0, 0, 0)
    body = [
        encode_i(3, 0, 0, 2),                 # x2 = 3
        encode_j(4, 0),                       # loop: jal x0, +4
        encode_j(4, 0),                       # jal x0, +4
        encode_i(-1, 2, 0, 2),                # x2 -= 1
        nop,
        nop,
        encode_b(-20, 0, 2, 1),               # bne x2, x0, loop
    ] + _read_counters()
    # Iteration one: three static predictions (one refill clock each);
    # iterations two and three: six JALs and two branches predicted at
    # fetch, the last branch falling through (two refill clocks).
    await _check_prediction_counts(dut, "fetch_btb", body, (9, 1, 8), {
        1: (3, 2, 0, 6, 0),
        3: (3, 2, 0, 6, 0),
    })


async def _check_pmon_counter_btb_events(dut) -> None:
    """Two-bit counter hysteresis: a forward branch taken on the first two
    of five loop iterations and not taken on the last three is unpredicted
    (trained) on the first, predicted taken at fetch on the next three, so
    the third and fourth mispredict while each decrements the counter, and
    not predicted on the fifth, which is correct."""
    nop = encode_i(0, 0, 0, 0)
    body = [
        encode_i(5, 0, 0, 2),                 # x2 = 5
        encode_i(4, 0, 0, 3),                 # x3 = 4
        encode_b(8, 3, 2, 5),                 # loop: bge x2, x3, +8 (taken while x2 >= 4)
        nop,                                  # skipped when taken
        encode_i(-1, 2, 0, 2),                # x2 -= 1
        nop,
        nop,
        encode_b(-20, 0, 2, 1),               # bne x2, x0, loop
    ] + _read_counters()
    # Predicted: the forward branch on iterations 2..4 (fetch) and the loop
    # branch on all five (static once, then fetch); wrong: the forward
    # branch's first iteration and its two predicted not-taken ones, and
    # the loop exit.
    await _check_prediction_counts(dut, "counter_btb", body, (8, 4, 6), {
        1: (1, 8, 0, 7, 0),
        3: (1, 8, 0, 7, 0),
    })


async def _check_pmon_stale_btb_events(dut) -> None:
    """Fetch-BTB entries that do not match their word. A branch at B is
    trained by three calls (JAL x9, so the stack is untouched; its return
    JALR changes target every call and mispredicts). One, two and three
    alias spans above B (256, 512 and 768 KiB at 256 entries) share its
    index and partial tag: a non-transfer, a
    never-taken branch with another target and a JAL with another target.
    Each is predicted at fetch and rejected in Q101H (FETCH_PREDICT_STALE,
    one refill clock), retiring only the architectural path; a rejected
    entry trains as a miss, so the never-taken alias leaves B's entry
    intact and a fourth call to B is still predicted at fetch.

    The JAL hops reach only 1 MiB, so this exact-count program needs three
    alias spans within it: up to 256 entries (the n2d_rv32im_core_btb256
    target). Larger tables check stale rejection in the alias class of
    :func:`_check_btb_index_tag_boundaries`."""
    nop = encode_i(0, 0, 0, 0)
    selectors = 4 * 2  # four load_immediate + csrrw pairs precede the body
    main, second, b_word, report = selectors, 0x30, 0x40, 0x50
    entries = btb_entries(dut)
    if entries > 256:
        cocotb.log.info("stale_btb: BTB_ENTRIES=%d alias span beyond JAL reach; "
                        "covered by the index/tag alias class", entries)
        return
    span = pipeline_cases.btb_alias_span(entries) // 4
    aliases = [b_word + k * span for k in (1, 2, 3)]
    layout: dict[int, list[int]] = {
        main: [encode_j(4 * (b_word - main), 9),            # three calls to B
               encode_j(4 * (b_word - main - 1), 9),
               encode_j(4 * (b_word - main - 2), 9),
               encode_j(4 * (aliases[0] - main - 3), 0)],
        second: [encode_j(4 * (b_word - second), 9),        # a fourth call to B
                 encode_j(4 * (aliases[2] - second - 1), 0)],
        b_word: [encode_b(8, 0, 0, 0),                      # B: beq x0, x0, +8
                 nop,
                 encode_i(0, 9, 0, 0, 0x67)],               # jalr x0, 0(x9)
        report: _read_counters(),
        aliases[0]: [encode_i(5, 3, 0, 3),                  # non-transfer alias
                     encode_j(4 * (aliases[1] - aliases[0] - 1), 0)],
        aliases[1]: [encode_b(8, 0, 0, 1),                  # bne x0, x0, +8: never taken
                     encode_j(4 * (second - aliases[1] - 1), 0)],
        aliases[2]: [encode_j(4 * (report - aliases[2]), 0)],  # JAL alias
    }
    body = [nop] * (aliases[2] + 1 - selectors)
    for word_index, words in layout.items():
        body[word_index - selectors: word_index - selectors + len(words)] = words
    # Predicted taken: 9 JALs (static, the JAL alias after its rejection),
    # the last 3 B branches and the last 3 return JALRs (fetch). Wrong: B's
    # first (unpredicted) and all 4 return JALRs (unpredicted, then a stale
    # target each call). Refill clocks: the 9 static JALs and the two
    # rejected fetch redirects to pc + 4. Four rejections: the three aliases
    # of B, and the hop JAL after the never-taken alias, which aliases the
    # hop after the non-transfer (trained, another target).
    await _check_prediction_counts(dut, "stale_btb", body, (15, 5, 17), {
        1: (11, 10, 0, 6, 4),
    })


async def _check_pmon_trap_refill_events(dut) -> None:
    """An ECALL and its MRET are late redirects: two IFETCH_REDIRECT_REFILL
    (85) clocks each, never a mispredict; the only transfer, a JAL, is
    predicted statically once."""
    csrrw, csrrs = 1, 2
    nop = encode_i(0, 0, 0, 0)
    selectors = 4 * 2
    # Words: li x5 (padded to two), csrrw, ecall, j, then the handler,
    # which advances mepc past the ECALL and returns to the JAL.
    li_handler = load_immediate(5, 4 * (selectors + 5))
    body = li_handler + [nop] * (2 - len(li_handler)) + [
        encode_csr(0x305, 5, csrrw, 0),       # mtvec = handler
        0x0000_0073,                          # ecall
        encode_j(4 * 5, 0),                   # after MRET: jump to the reads
        encode_csr(0x341, 0, csrrs, 6),       # handler: x6 = mepc
        encode_i(4, 6, 0, 6),                 # x6 += 4
        encode_csr(0x341, 6, csrrw, 0),       # mepc = x6
        0x3020_0073,                          # mret
        nop,
    ] + _read_counters()
    await _check_prediction_counts(dut, "trap_refill", body, (1, 0, 1), {
        1: (1, 0, 4, 0, 0),
        3: (1, 0, 4, 0, 0),
    })


async def _check_pmon_return_events(dut) -> None:
    """A loop calling a leaf that only returns. The first iteration predicts
    the call, the return (stack) and the loop branch statically; later
    calls and loop branches are predicted at fetch. Each return is fetched
    on the edge its call pushes the stack, so it is predicted statically
    (one refill clock). The loop exit is the only mispredict."""
    body = [
        encode_i(4, 0, 0, 2),                 # x2 = 4
        encode_j(16, 1),                      # loop: jal ra, leaf
        encode_i(-1, 2, 0, 2),                # x2 -= 1
        encode_b(-8, 0, 2, 1),                # bne x2, x0, loop: exits by mispredict
        encode_j(8, 0),                       # jal x0, +8
        pipeline_cases.RET,                   # leaf: ret
    ] + _read_counters()
    # 4 calls + 4 returns + 4 loop branches + 1 jump predicted; the last loop
    # branch falls through; every transfer but that one is taken.
    await _check_prediction_counts(dut, "return", body, (13, 1, 12), {
        1: (7, 2, 0, 6, 0),
        3: (7, 2, 0, 6, 0),
    })


async def _check_pmon_btb_events(dut) -> None:
    """A four-iteration loop around an always-taken forward branch and an
    indirect call (JALR ra, 4(x7)) to a leaf that returns: the first
    iteration's forward branch and call are unpredicted (mispredicts) and
    train the fetch BTB; later iterations predict both, and the loop branch,
    at fetch. Returns follow their call's push too closely to be predicted
    at fetch and are predicted from the stack in Q101H."""
    body = [
        encode_i(4, 0, 0, 2),                 # 0: x2 = 4
        encode_u(0, 7, 0x17),                 # 1: x7 = pc of word 1
        encode_i(4 * 9 - 4 - 4, 7, 0, 7),     # 2: x7 = leaf (word 9) - 4
        encode_b(8, 0, 0, 0),                 # 3: loop: beq x0, x0, +8
        encode_i(0, 0, 0, 0),                 # 4: skipped
        encode_i(4, 7, 0, 1, 0x67),           # 5: jalr ra, 4(x7) (pushes)
        encode_i(-1, 2, 0, 2),                # 6: x2 -= 1
        encode_b(-16, 0, 2, 1),               # 7: bne x2, x0, loop: exits by mispredict
        encode_j(8, 0),                       # 8: jal x0, +8
        pipeline_cases.RET,                   # 9: leaf: ret
    ] + _read_counters()
    # Predicted: 4 returns, 4 loop branches, 3 + 3 fetch-predicted forward
    # branches and calls, 1 jump. Wrong path: the two first-iteration
    # transfers and the loop exit.
    await _check_prediction_counts(dut, "btb", body, (15, 3, 16), {
        1: (6, 6, 0, 9, 0),
        3: (6, 6, 0, 9, 0),
    })


# Fetch-BTB capacity checks (core/DV.md "Fetch BTB capacity"). Events:
# FETCH_PREDICT_TAKEN 92, FETCH_PREDICT_STALE 93, BRANCH_PREDICT_TAKEN 78,
# BRANCH_MISPREDICT 79.
_BTB_SITE_EVENTS = (92, 93, 78, 79)
_BTB_SELECTOR_WORDS = 4 * 2  # four load_immediate + csrrw pairs precede the body


def _btb_site_program(calls: list[int], sites: list[int]) -> list[int]:
    """Body (after the selectors) calling each address in ``calls`` with
    JALR ra and reading the counters. Each site is an always-taken forward
    branch (BEQ x0, x0, +8; not statically predicted), a skipped poison
    word and a return predicted from the stack."""
    poison = encode_s(0x5FC, 1, 0, 2)
    main: list[int] = []
    for address in calls:
        load = load_immediate(5, address)
        main += load + [encode_i(0, 0, 0, 0)] * (2 - len(load))
        main.append(encode_i(0, 5, 0, 1, 0x67))      # jalr ra, 0(x5)
    main += _read_counters()
    end = max(sites) + 12
    assert 4 * (_BTB_SELECTOR_WORDS + len(main)) <= min(sites)
    body = main + [encode_i(0, 0, 0, 0)] * ((end - 4 * _BTB_SELECTOR_WORDS) // 4 - len(main))
    for site in sites:
        index = site // 4 - _BTB_SELECTOR_WORDS
        body[index: index + 3] = [encode_b(8, 0, 0, 0), poison, pipeline_cases.RET]
    return body


async def _btb_pair_counts(dut, first: int, distance: int) -> tuple[int, ...]:
    """Train a site twice, run a second site ``distance`` bytes above it,
    then the first again; return the four event counts."""
    second = first + distance
    body = _btb_site_program([first, first, second, first], [first, second])
    reads, result = await _count_run(dut, body, _BTB_SITE_EVENTS)
    assert _word(result.memory, 0x5FC) == 0, "speculative poison store retired"
    return reads


async def _check_btb_index_tag_boundaries(dut) -> None:
    """Index and partial-tag width at the core's BTB size N: a second site
    at the top index bit (2N bytes) or with an alias span added (1024N + 2N)
    has its own entry, like an adjacent site; one at the lowest or highest
    tag bit (4N, 512N, 1024N + 4N) evicts the first site's entries; one a
    full alias span above (1024N) shares their index and tag, so its fetch
    predictions are rejected (stale) and it retrains the first site's
    entry. Relative to separate entries, an eviction costs the first site's
    final fetch predictions (branch and return: 92 -2) and makes its branch
    a mispredict (78 -1, 79 +1); an alias predicts the second site's return
    at fetch (92 +1) and rejects two fetch predictions (93 +2), the first
    site's final branch mispredicting (92 -1, 78 -1, 79 +1). With no fetch
    BTB (N = 0) every placement behaves alike and nothing is predicted at
    fetch."""
    entries = btb_entries(dut)
    unit = entries or 256  # no fetch BTB: place the sites as 256 entries would
    assert unit >= 64, "sites must clear the calling code"
    first = 4 * unit
    span = pipeline_cases.btb_alias_span(unit)
    separate = await _btb_pair_counts(dut, first, 16)
    cases = {
        "top index bit": (2 * unit, "separate"),
        "alias span + top index bit": (span + 2 * unit, "separate"),
        "lowest tag bit": (4 * unit, "conflict"),
        "highest tag bit": (span // 2, "conflict"),
        "alias span + lowest tag bit": (span + 4 * unit, "conflict"),
        "alias span": (span, "alias"),
    }
    if entries == 0:
        expected = {"separate": separate, "conflict": separate, "alias": separate}
        assert separate[:2] == (0, 0), separate
    else:
        expected = {
            "separate": separate,
            "conflict": (separate[0] - 2, separate[1], separate[2] - 1, separate[3] + 1),
            "alias": (separate[0], separate[1] + 2, separate[2] - 1, separate[3] + 1),
        }
    for name, (distance, kind) in cases.items():
        counts = await _btb_pair_counts(dut, first, distance)
        cocotb.log.info("BTB_ENTRIES=%d %s (+0x%x): %s %s", entries, name, distance, kind, counts)
        assert counts == expected[kind], (entries, name, hex(distance), kind, counts, expected[kind])


async def _check_btb_reset_sweep(dut) -> None:
    """The reset sweep clears the whole table and no lookup hits before it
    ends (core/HAS.md "Reset sweep"). Sites at index N - 1 and index 0 are
    trained (their second calls predicted at fetch); after a reset and the
    N-clock sweep the same program sees cold entries again. A loop at the
    top indices is predicted at fetch after the sweep; run again from reset
    with no settle clocks, it finishes inside the sweep, before its trained
    entries are cleared, and is never predicted at fetch."""
    entries = btb_entries(dut)
    if entries == 0:
        return
    top = 4 * entries + 4 * (entries - 1)         # index N - 1
    bottom = 4 * entries * 3                      # index 0
    body = _btb_site_program([top, top, bottom, bottom], [top, bottom])
    cold, _ = await _count_run(dut, body, _BTB_SITE_EVENTS)
    assert cold[0] >= 2, ("sites not trained", cold)
    again, _ = await _count_run(dut, body, _BTB_SITE_EVENTS)
    assert again == cold, ("entries survived the reset sweep", entries, cold, again)

    # The loop sits at indices N - 3 .. N - 1, which the sweep clears last,
    # so the trained entries of the swept run are still in the table while
    # the run that starts at reset executes it.
    start = entries - 3 - _BTB_SELECTOR_WORDS
    loop = [encode_j(4 * start, 0)] + [encode_i(0, 0, 0, 0)] * (start - 1) + [
        encode_i(8, 0, 0, 2),                 # x2 = 8
        encode_i(-1, 2, 0, 2),                # loop: x2 -= 1
        encode_b(-4, 0, 2, 1),                # bne x2, x0, loop
    ] + _read_counters()
    swept, _ = await _count_run(dut, loop, _BTB_SITE_EVENTS)
    assert swept[0] > 0, swept
    during, result = await _count_run(dut, loop, _BTB_SITE_EVENTS, settle_cycles=0)
    if result.cycles < entries:
        assert during[:2] == (0, 0), ("lookup hit during the reset sweep", entries, during)
    cocotb.log.info("BTB_ENTRIES=%d sweep: cold %s again %s; loop swept %s, during %s (%d clocks)",
                    entries, cold, again, swept, during, result.cycles)


def _configure_window(elf_path: Path) -> tuple[int, int]:
    """Fetch range of pmon_configure_under_test before its inhibit restore."""
    objdump = shutil.which(os.environ.get("N2D_RISCV_OBJDUMP", "riscv64-unknown-elf-objdump"))
    assert objdump is not None, "riscv64-unknown-elf-objdump (or N2D_RISCV_OBJDUMP) required"
    listing = subprocess.run([objdump, "-d", str(elf_path)], check=True,
                             capture_output=True, text=True, timeout=20).stdout
    start = None
    restores = []
    for line in listing.splitlines():
        symbol = re.match(r"([0-9a-f]+) <(\w+)>:", line)
        if symbol:
            if start is not None and symbol.group(2) != "pmon_configure_under_test":
                break
            if symbol.group(2) == "pmon_configure_under_test":
                start = int(symbol.group(1), 16)
            continue
        instruction = re.match(r"\s+([0-9a-f]+):\s+[0-9a-f]+\s+(.*)", line)
        if start is not None and instruction and re.search(
                r"csrw\s+(mcountinhibit|0x320),", instruction.group(2)):
            restores.append(int(instruction.group(1), 16))
    assert start is not None and len(restores) == 2, (start, restores)
    return start, restores[-1]


async def _check_pmon_accessor(dut) -> None:
    """n2d_pmon.h configure stops the counter across its selector and half
    writes, then restores the previous inhibit mask."""
    sw_dir = Path(__file__).resolve().parents[2] / "sw" / "runtime"
    words, provenance, elf_path = compile_c_program(
        "c_pmon_configure", "pmon_configure", (sw_dir,))
    low, high = _configure_window(elf_path)
    cocotb.log.info("PMON accessor image words=%d window=[%#x,%#x) provenance=%s",
                    len(words), low, high, provenance)
    result = await _run(dut, words, stop_cause=3, max_cycles=2048,
                        platform_fetch_window=(low, high, 1 << 12))
    out = [_word(result.memory, 0x80 + 4 * index) for index in range(7)]
    assert out[6] > 0, out          # counter 3 had a count to clear
    assert out[0:4] == [0, 0, 12, 0], out
    assert out[4] == 0x10 and out[5] == 0, out


async def _check_variable_latency_halt_drain(dut) -> None:
    words = (
        encode_i(0x55, 0, 0, 1),
        encode_s(0, 1, 0, 2),
        encode_i(1, 0, 0, 2),
        0x0010_0073,
    )
    result = await _run(
        dut,
        words,
        stop_cause=3,
        max_cycles=192,
        instruction_latency=3,
        data_latency=5,
        instruction_ready_pattern=(0, 0, 1, 0, 1),
        data_ready_pattern=(0, 0, 0, 1),
        halt_on_first_data=True,
    )
    assert result.halt_drain_seen
    assert _word(result.memory, 0) == 0x55
    assert any(item.pc == 8 and item.rd_write_data == 1 for item in result.observations)


@cocotb.test(timeout_time=100, timeout_unit="ms")
async def rv32im_architecture_contract(dut) -> None:
    assert int(os.environ["DV_CLOCK_HZ"]) == CLOCK_HZ
    seed = int(os.environ["DV_SEED"])
    maximum = int(os.environ["DV_MAX_CYCLES"])
    assert maximum >= 4096
    cocotb.log.info(
        "RV32IM architectural regression core=%s seed=%d max_cycles=%d", dut._name, seed, maximum
    )
    cocotb.start_soon(Clock(dut.clk_i, CLOCK_PERIOD_NS, unit="ns").start())
    rng = random.Random(seed)
    await _check_m_boundaries(dut, rng)
    await _check_integer_operations(dut)
    await _check_control_memory_system(dut)
    await _check_csr_trap_state(dut)
    await _check_pmon(dut)
    await _check_pmon_accessor(dut)
    await _check_pmon_pipeline_events(dut)
    await _check_pmon_prediction_events(dut)
    await _check_pmon_fetch_btb_events(dut)
    await _check_pmon_counter_btb_events(dut)
    await _check_pmon_stale_btb_events(dut)
    await _check_pmon_trap_refill_events(dut)
    await _check_pmon_return_events(dut)
    await _check_pmon_btb_events(dut)
    await _check_generated_signature(dut)
    await _check_compiled_c_signature(dut)
    await _check_variable_latency_halt_drain(dut)
    await pipeline_cases.check_reset_and_halt_ports(dut)
    await pipeline_cases.check_divide_halt_and_reset(dut, rng)
    await pipeline_cases.check_raw_distances(dut, rng)
    await pipeline_cases.check_divide_under_hold(dut, rng)
    await pipeline_cases.check_multiply_under_memory_hold(dut, rng)
    await pipeline_cases.check_m_corners(dut, rng)
    await pipeline_cases.check_divide_pairs(dut, rng)
    await pipeline_cases.check_divide_killed_by_older_trap(dut, rng)
    await pipeline_cases.check_flush_suppresses_younger_store(dut, rng)
    await pipeline_cases.check_system_serialization(dut, rng)
    await pipeline_cases.check_data_error_precedes_younger_work(dut, rng)
    await pipeline_cases.check_static_prediction(dut, rng)
    await pipeline_cases.check_return_prediction(dut, rng)
    await pipeline_cases.check_btb_aliasing(dut, rng)
    await _check_btb_index_tag_boundaries(dut)
    await _check_btb_reset_sweep(dut)
    await pipeline_cases.check_halt_sweep(dut, rng, stride=1)
    await pipeline_cases.check_reset_pc(dut, rng)
    await pipeline_cases.check_random_streams(dut, seed, 12)
    await _report_ipc(dut)
    cocotb.log.info("RV32IM architectural regression PASS core=%s seed=%d", dut._name, seed)


@cocotb.test(timeout_time=60, timeout_unit="ms")
async def btb_capacity_contract(dut) -> None:
    """Fetch-BTB size boundaries for one BTB_ENTRIES configuration: index
    and partial-tag boundaries, the alias span, the reset sweep, and the
    size-scaled alias recovery and halt sweep under random port timing."""
    assert int(os.environ["DV_CLOCK_HZ"]) == CLOCK_HZ
    seed = int(os.environ["DV_SEED"])
    entries = btb_entries(dut)
    cocotb.log.info("fetch BTB capacity contract BTB_ENTRIES=%d seed=%d", entries, seed)
    cocotb.start_soon(Clock(dut.clk_i, CLOCK_PERIOD_NS, unit="ns").start())
    rng = random.Random(seed)
    await _check_btb_index_tag_boundaries(dut)
    await _check_btb_reset_sweep(dut)
    await pipeline_cases.check_btb_aliasing(dut, rng)
    await pipeline_cases.check_static_prediction(dut, rng)
    await pipeline_cases.check_return_prediction(dut, rng)
    await pipeline_cases.check_halt_sweep(dut, rng, stride=1)
    await pipeline_cases.check_random_streams(dut, seed, 4)
    cocotb.log.info("fetch BTB capacity contract PASS BTB_ENTRIES=%d seed=%d", entries, seed)


async def _report_ipc(dut) -> None:
    words, _ = compile_signature_program_cached()
    await pipeline_cases.measure_ipc(dut, "c_signature", words, stop_cause=3, max_cycles=1024)
    await pipeline_cases.measure_ipc(
        dut, "generated_signature", c_like_signature_program(), stop_trap=True, max_cycles=256
    )
    kernel_memory = bytes(random.Random(0xD00).getrandbits(8) for _ in range(0x800))
    await pipeline_cases.measure_ipc(
        dut, "doom_like_kernels", pipeline_cases.kernel_program(), stop_cause=3,
        initial_memory=kernel_memory, max_cycles=8000,
    )
    await _measure_doom_math(dut)
    program, _, _ = pipeline_cases.random_program(random.Random(0x1D0), length=200)
    await pipeline_cases.measure_ipc(dut, "random_stream", program, stop_cause=3, max_cycles=8000)


async def _measure_doom_math(dut) -> None:
    """Compiled Doom fixed-point kernel (FixedMul, FixedDiv through libgcc's
    64-bit divide, SlopeDiv), model-checked, with its IPC reported."""
    words, provenance, _ = compile_c_program(
        "doom_math", "doom_math", start="doom_math_start", optimization="-Os", libgcc=True,
    )
    cocotb.log.info("Doom fixed-point image words=%d provenance=%s", len(words), provenance)
    image = b"".join(item.to_bytes(4, "little") for item in words)
    result = await pipeline_cases.measure_ipc(
        dut, "doom_fixed_math", words, stop_cause=3,
        initial_memory=image + bytes(0x2000 - len(image)), max_cycles=40000,
    )
    signature = _word(result.memory, 0x80)
    assert signature == DOOM_MATH_SIGNATURE, f"doom_math signature 0x{signature:08x}"
