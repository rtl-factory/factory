from __future__ import annotations

from dataclasses import dataclass, field


MASK32 = 0xFFFF_FFFF


def _signed(value: int) -> int:
    value &= MASK32
    return value - (1 << 32) if value & (1 << 31) else value


def _sign_extend(value: int, width: int) -> int:
    sign = 1 << (width - 1)
    return ((value & (sign - 1)) - (value & sign)) & MASK32


def encode_i(immediate: int, rs1: int, funct3: int, rd: int, opcode: int = 0x13) -> int:
    return (
        ((immediate & 0xFFF) << 20)
        | ((rs1 & 0x1F) << 15)
        | ((funct3 & 0x7) << 12)
        | ((rd & 0x1F) << 7)
        | opcode
    )


def encode_r(funct7: int, rs2: int, rs1: int, funct3: int, rd: int) -> int:
    return (
        ((funct7 & 0x7F) << 25)
        | ((rs2 & 0x1F) << 20)
        | ((rs1 & 0x1F) << 15)
        | ((funct3 & 0x7) << 12)
        | ((rd & 0x1F) << 7)
        | 0x33
    )


def encode_s(immediate: int, rs2: int, rs1: int, funct3: int = 0b010) -> int:
    encoded = immediate & 0xFFF
    return (
        (((encoded >> 5) & 0x7F) << 25)
        | ((rs2 & 0x1F) << 20)
        | ((rs1 & 0x1F) << 15)
        | ((funct3 & 0x7) << 12)
        | ((encoded & 0x1F) << 7)
        | 0x23
    )


def encode_b(immediate: int, rs2: int, rs1: int, funct3: int) -> int:
    assert immediate % 2 == 0
    encoded = immediate & 0x1FFF
    return (
        (((encoded >> 12) & 1) << 31)
        | (((encoded >> 5) & 0x3F) << 25)
        | ((rs2 & 0x1F) << 20)
        | ((rs1 & 0x1F) << 15)
        | ((funct3 & 0x7) << 12)
        | (((encoded >> 1) & 0xF) << 8)
        | (((encoded >> 11) & 1) << 7)
        | 0x63
    )


def smoke_program() -> tuple[int, ...]:
    return (
        encode_i(7, 0, 0b000, 1),              # x1 = 7
        encode_i(5, 0, 0b000, 2),              # x2 = 5
        encode_r(0, 2, 1, 0b000, 3),           # x3 = 12
        encode_s(0, 3, 0),                     # memory[0] = 12
        encode_i(0, 0, 0b010, 4, opcode=0x03), # x4 = memory[0]
        encode_b(8, 3, 4, 0b000),              # taken, skip poison
        encode_i(-1, 0, 0b000, 5),             # must not retire
        encode_r(0x20, 2, 4, 0b000, 5),        # x5 = 7
        encode_b(8, 1, 5, 0b001),              # not taken
        encode_i(29, 5, 0b000, 6),             # x6 = 36
        encode_s(4, 6, 0),                     # signature memory[1] = 36
    )


@dataclass(frozen=True)
class Observation:
    order: int
    pc: int
    instruction: int
    next_pc: int
    rd_address: int = 0
    rd_write: int = 0
    rd_write_data: int = 0
    memory_valid: int = 0
    memory_write: int = 0
    memory_address: int = 0
    memory_read_data: int = 0
    memory_write_data: int = 0
    memory_read_mask: int = 0
    memory_write_mask: int = 0
    trap: int = 0
    cause: int = 0
    trap_value: int = 0


@dataclass
class Machine:
    pc: int = 0
    order: int = 0
    registers: list[int] = field(default_factory=lambda: [0] * 32)
    memory: dict[int, int] = field(default_factory=dict)


def step(machine: Machine, instruction: int) -> Observation:
    pc = machine.pc
    opcode = instruction & 0x7F
    rd = (instruction >> 7) & 0x1F
    funct3 = (instruction >> 12) & 0x7
    rs1 = (instruction >> 15) & 0x1F
    rs2 = (instruction >> 20) & 0x1F
    funct7 = (instruction >> 25) & 0x7F
    a = machine.registers[rs1]
    b = machine.registers[rs2]
    next_pc = (pc + 4) & MASK32
    kwargs: dict[str, int] = {}

    if opcode == 0x13 and funct3 == 0b000:
        result = (a + _sign_extend(instruction >> 20, 12)) & MASK32
        if rd:
            machine.registers[rd] = result
            kwargs = {"rd_address": rd, "rd_write": 1, "rd_write_data": result}
    elif opcode == 0x33 and funct3 == 0b000 and funct7 in (0, 0x20):
        result = (a + b if funct7 == 0 else a - b) & MASK32
        if rd:
            machine.registers[rd] = result
            kwargs = {"rd_address": rd, "rd_write": 1, "rd_write_data": result}
    elif opcode == 0x63 and funct3 in (0b000, 0b001):
        raw = (
            (((instruction >> 31) & 1) << 12)
            | (((instruction >> 7) & 1) << 11)
            | (((instruction >> 25) & 0x3F) << 5)
            | (((instruction >> 8) & 0xF) << 1)
        )
        taken = (a == b) if funct3 == 0 else (a != b)
        if taken:
            next_pc = (pc + _sign_extend(raw, 13)) & MASK32
    elif opcode in (0x03, 0x23) and funct3 == 0b010:
        if opcode == 0x03:
            immediate = _sign_extend(instruction >> 20, 12)
            address = (a + immediate) & MASK32
            data = machine.memory.get(address >> 2, 0)
            if rd:
                machine.registers[rd] = data
            kwargs = {
                "rd_address": rd,
                "rd_write": int(rd != 0),
                "rd_write_data": data if rd else 0,
                "memory_valid": 1,
                "memory_address": address,
                "memory_read_data": data,
                "memory_read_mask": 0xF,
            }
        else:
            raw = ((instruction >> 25) << 5) | ((instruction >> 7) & 0x1F)
            address = (a + _sign_extend(raw, 12)) & MASK32
            machine.memory[address >> 2] = b
            kwargs = {
                "memory_valid": 1,
                "memory_write": 1,
                "memory_address": address,
                "memory_write_data": b,
                "memory_write_mask": 0xF,
            }
    else:
        raise ValueError(f"unsupported model instruction pc=0x{pc:08x} insn=0x{instruction:08x}")

    observation = Observation(machine.order, pc, instruction, next_pc, **kwargs)
    machine.pc = next_pc
    machine.order += 1
    machine.registers[0] = 0
    return observation


def expected_smoke() -> tuple[Observation, ...]:
    program = smoke_program()
    machine = Machine()
    observations: list[Observation] = []
    while len(observations) < 32:
        instruction = program[machine.pc >> 2]
        observations.append(step(machine, instruction))
        if machine.memory.get(1) == 36:
            assert machine.registers[6] == 36
            return tuple(observations)
    raise AssertionError("model did not produce the signature")
