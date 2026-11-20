import os
import random

import cocotb
from cocotb.triggers import Timer


MASK32 = 0xFFFF_FFFF
OPCODE_OP = 0x33
OPCODE_OP_IMM = 0x13
DIRECTED_OPERANDS = (
    0x0000_0000,
    0x0000_0001,
    0x7FFF_FFFF,
    0x8000_0000,
    0xFFFF_FFFF,
    0xAAAA_AAAA,
    0x5555_5555,
    0xDEAD_BEEF,
)
DIRECTED_IMMEDIATES = (-2048, -1, 0, 1, 31, 32, 2047)
R_OPERATIONS = {
    "add": (0b000, 0b0000000),
    "sub": (0b000, 0b0100000),
    "sll": (0b001, 0b0000000),
    "slt": (0b010, 0b0000000),
    "sltu": (0b011, 0b0000000),
    "xor": (0b100, 0b0000000),
    "srl": (0b101, 0b0000000),
    "sra": (0b101, 0b0100000),
    "or": (0b110, 0b0000000),
    "and": (0b111, 0b0000000),
}
I_OPERATIONS = {
    "addi": 0b000,
    "slti": 0b010,
    "sltiu": 0b011,
    "xori": 0b100,
    "ori": 0b110,
    "andi": 0b111,
}
I_SHIFT_OPERATIONS = {
    "slli": (0b001, 0b0000000),
    "srli": (0b101, 0b0000000),
    "srai": (0b101, 0b0100000),
}
R_SHIFT_NAMES = ("sll", "srl", "sra")
RANDOM_TRANSACTIONS = 256
EXPECTED_TRANSACTIONS = 4174


def _signed32(value: int) -> int:
    value &= MASK32
    return value - (1 << 32) if value & (1 << 31) else value


def _sign_extend_12(value: int) -> int:
    value &= 0xFFF
    return (value | 0xFFFF_F000) if value & 0x800 else value


def _operation_result(name: str, operand_a: int, operand_b: int) -> int:
    operand_a &= MASK32
    operand_b &= MASK32
    shift = operand_b & 0x1F
    if name in {"add", "addi"}:
        return (operand_a + operand_b) & MASK32
    if name == "sub":
        return (operand_a - operand_b) & MASK32
    if name in {"sll", "slli"}:
        return (operand_a << shift) & MASK32
    if name in {"slt", "slti"}:
        return int(_signed32(operand_a) < _signed32(operand_b))
    if name in {"sltu", "sltiu"}:
        return int(operand_a < operand_b)
    if name in {"xor", "xori"}:
        return operand_a ^ operand_b
    if name in {"srl", "srli"}:
        return operand_a >> shift
    if name in {"sra", "srai"}:
        return (_signed32(operand_a) >> shift) & MASK32
    if name in {"or", "ori"}:
        return operand_a | operand_b
    if name in {"and", "andi"}:
        return operand_a & operand_b
    raise AssertionError(f"unknown model operation: {name}")


def _decode_reference(instruction: int, rs1_data: int, rs2_data: int) -> tuple[int, ...]:
    opcode = instruction & 0x7F
    rd_index = (instruction >> 7) & 0x1F
    funct3 = (instruction >> 12) & 0x7
    rs1_index = (instruction >> 15) & 0x1F
    rs2_index = (instruction >> 20) & 0x1F
    funct7 = (instruction >> 25) & 0x7F
    immediate = _sign_extend_12(instruction >> 20)
    name = None
    uses_rs2 = 0
    selected_operand = 0
    published_immediate = 0

    if opcode == OPCODE_OP:
        uses_rs2 = 1
        for operation_name, encoding in R_OPERATIONS.items():
            if encoding == (funct3, funct7):
                name = operation_name
                selected_operand = rs2_data
                break
    elif opcode == OPCODE_OP_IMM:
        published_immediate = immediate
        for operation_name, operation_funct3 in I_OPERATIONS.items():
            if funct3 == operation_funct3:
                name = operation_name
                selected_operand = immediate
                break
        if name is None:
            for operation_name, encoding in I_SHIFT_OPERATIONS.items():
                if encoding == (funct3, funct7):
                    name = operation_name
                    selected_operand = instruction >> 20
                    break

    if name is None:
        return (
            rs1_index,
            rs2_index,
            rd_index,
            0,
            0,
            0,
            0,
            0,
            1,
        )

    return (
        rs1_index,
        rs2_index,
        rd_index,
        1,
        uses_rs2,
        int(rd_index != 0),
        published_immediate,
        _operation_result(name, rs1_data, selected_operand),
        0,
    )


def _encode_r(name: str, rs1: int, rs2: int, rd: int) -> int:
    funct3, funct7 = R_OPERATIONS[name]
    return (
        (funct7 << 25)
        | (rs2 << 20)
        | (rs1 << 15)
        | (funct3 << 12)
        | (rd << 7)
        | OPCODE_OP
    )


def _encode_i(name: str, rs1: int, rd: int, immediate: int) -> int:
    if name in I_SHIFT_OPERATIONS:
        funct3, funct7 = I_SHIFT_OPERATIONS[name]
        immediate_field = (funct7 << 5) | (immediate & 0x1F)
    else:
        funct3 = I_OPERATIONS[name]
        immediate_field = immediate & 0xFFF
    return (
        (immediate_field << 20)
        | (rs1 << 15)
        | (funct3 << 12)
        | (rd << 7)
        | OPCODE_OP_IMM
    )


@cocotb.test(timeout_time=20, timeout_unit="us")
async def decode_alu_contract(dut) -> None:
    seed = int(os.environ["DV_SEED"])
    clock_hz = int(os.environ["DV_CLOCK_HZ"])
    max_transactions = int(os.environ["DV_MAX_CYCLES"])
    assert seed >= 0, f"seed must be non-negative: {seed}"
    assert clock_hz == 25_000_000, f"unexpected clock contract: {clock_hz}"
    assert max_transactions >= EXPECTED_TRANSACTIONS, (
        f"transaction budget too small: {max_transactions}"
    )
    rng = random.Random(seed)
    transaction = 0

    async def check(instruction: int, operand_a: int, operand_b: int, label: str) -> None:
        nonlocal transaction
        transaction += 1
        assert transaction <= max_transactions
        dut.instruction_i.value = instruction & MASK32
        dut.rs1_data_i.value = operand_a & MASK32
        dut.rs2_data_i.value = operand_b & MASK32
        await Timer(1, unit="ns")
        observed = (
            int(dut.rs1_index_o.value),
            int(dut.rs2_index_o.value),
            int(dut.rd_index_o.value),
            int(dut.rs1_read_o.value),
            int(dut.rs2_read_o.value),
            int(dut.rd_write_o.value),
            int(dut.immediate_o.value),
            int(dut.alu_result_o.value),
            int(dut.illegal_o.value),
        )
        expected = _decode_reference(instruction, operand_a, operand_b)
        assert observed == expected, (
            f"transaction={transaction} seed={seed} label={label} "
            f"instruction=0x{instruction:08x} a=0x{operand_a:08x} "
            f"b=0x{operand_b:08x} expected={expected} observed={observed}"
        )

    for operation_index, name in enumerate(R_OPERATIONS):
        for operand_index, operand_a in enumerate(DIRECTED_OPERANDS):
            for operand_b in DIRECTED_OPERANDS:
                instruction = _encode_r(
                    name,
                    rs1=(operation_index + operand_index) & 0x1F,
                    rs2=(operation_index + 11) & 0x1F,
                    rd=(transaction + 1) & 0x1F,
                )
                await check(instruction, operand_a, operand_b, name)

    for operation_index, name in enumerate(I_OPERATIONS):
        for operand_index, operand_a in enumerate(DIRECTED_OPERANDS):
            for immediate in DIRECTED_IMMEDIATES:
                instruction = _encode_i(
                    name,
                    rs1=(operation_index + operand_index) & 0x1F,
                    rd=(transaction + 1) & 0x1F,
                    immediate=immediate,
                )
                await check(instruction, operand_a, 0xCAFE_BABE, name)

    for name in R_SHIFT_NAMES:
        for operand_a in DIRECTED_OPERANDS:
            for shift in range(32):
                instruction = _encode_r(name, rs1=3, rs2=4, rd=5)
                await check(instruction, operand_a, shift, f"{name}-shift-{shift}")

    for name in I_SHIFT_OPERATIONS:
        for operand_a in DIRECTED_OPERANDS:
            for shift in range(32):
                instruction = _encode_i(name, rs1=6, rd=7, immediate=shift)
                await check(instruction, operand_a, 0, f"{name}-shift-{shift}")

    operation_names = tuple(R_OPERATIONS) + tuple(I_OPERATIONS) + tuple(I_SHIFT_OPERATIONS)
    for random_index in range(RANDOM_TRANSACTIONS):
        name = rng.choice(operation_names)
        rs1 = rng.randrange(32)
        rd = rng.randrange(32)
        operand_a = rng.getrandbits(32)
        operand_b = rng.getrandbits(32)
        if name in R_OPERATIONS:
            instruction = _encode_r(name, rs1, rng.randrange(32), rd)
        elif name in I_SHIFT_OPERATIONS:
            instruction = _encode_i(name, rs1, rd, rng.randrange(32))
        else:
            instruction = _encode_i(name, rs1, rd, rng.randrange(-2048, 2048))
        await check(instruction, operand_a, operand_b, f"random-{random_index}-{name}")

    # Every other seven-bit opcode is unsupported by this bounded slice.
    for opcode in range(128):
        if opcode not in (OPCODE_OP, OPCODE_OP_IMM):
            instruction = (0xA55A_5A00 | opcode) & MASK32
            await check(instruction, 0x8000_0000, 0xFFFF_FFFF, f"opcode-{opcode}")

    # Exhaust all OP funct3/funct7 combinations, including RV32M funct7=1.
    for funct3 in range(8):
        for funct7 in range(128):
            instruction = (
                (funct7 << 25)
                | (9 << 20)
                | (8 << 15)
                | (funct3 << 12)
                | (10 << 7)
                | OPCODE_OP
            )
            await check(instruction, 0x8000_0001, 0xFFFF_FFFF, "op-encoding-space")

    # Exhaust shift-immediate high bits; only base and arithmetic-right forms pass.
    for funct3 in (0b001, 0b101):
        for funct7 in range(128):
            instruction = (
                (funct7 << 25)
                | (31 << 20)
                | (12 << 15)
                | (funct3 << 12)
                | (13 << 7)
                | OPCODE_OP_IMM
            )
            await check(instruction, 0x8000_0000, 0, "op-imm-shift-space")

    assert transaction == EXPECTED_TRANSACTIONS
    cocotb.log.info(
        "decode/ALU contract passed seed=%d transactions=%d",
        seed,
        transaction,
    )
