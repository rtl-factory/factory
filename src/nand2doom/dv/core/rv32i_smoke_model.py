from __future__ import annotations

from dataclasses import dataclass, field

from .rv32_asm import X0, X1, X2, X3, X4, X5, X6, add, addi, beq, bne, lw, sub, sw


MASK32 = 0xFFFF_FFFF


def _signed(value: int) -> int:
    value &= MASK32
    return value - (1 << 32) if value & (1 << 31) else value


def _sign_extend(value: int, width: int) -> int:
    sign = 1 << (width - 1)
    return ((value & (sign - 1)) - (value & sign)) & MASK32


def smoke_program() -> tuple[int, ...]:
    return (
        # Why: the signature 36 at memory[1] needs every step (x1 = 7, x2 = 5, x3 =
        # x4 = 12, x5 = 7, x6 = 36), so a wrong ALU result, a broken load-use, a
        # wrong branch or a retired poison word all change it.
        addi(X1, X0, 7),
        addi(X2, X0, 5),
        add(X3, X1, X2),
        sw(X3, 0, X0),
        lw(X4, 0, X0),
        beq(X4, X3, 8),
        addi(X5, X0, -1),
        sub(X5, X4, X2),
        bne(X5, X1, 8),
        addi(X6, X5, 29),
        sw(X6, 4, X0),
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
