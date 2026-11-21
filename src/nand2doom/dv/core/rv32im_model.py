from __future__ import annotations

from .rv32i_smoke_model import Observation

MASK32 = 0xFFFF_FFFF


def u32(value: int) -> int:
    return value & MASK32


def signed(value: int) -> int:
    value &= MASK32
    return value - (1 << 32) if value & 0x8000_0000 else value


def encode_r(funct7: int, rs2: int, rs1: int, funct3: int, rd: int) -> int:
    return (
        ((funct7 & 0x7F) << 25)
        | ((rs2 & 31) << 20)
        | ((rs1 & 31) << 15)
        | ((funct3 & 7) << 12)
        | ((rd & 31) << 7)
        | 0x33
    )


def encode_i(imm: int, rs1: int, funct3: int, rd: int, opcode: int = 0x13) -> int:
    return ((imm & 0xFFF) << 20) | ((rs1 & 31) << 15) | ((funct3 & 7) << 12) | ((rd & 31) << 7) | opcode


def encode_s(imm: int, rs2: int, rs1: int, funct3: int) -> int:
    value = imm & 0xFFF
    return (((value >> 5) & 0x7F) << 25) | ((rs2 & 31) << 20) | ((rs1 & 31) << 15) | ((funct3 & 7) << 12) | ((value & 31) << 7) | 0x23


def encode_b(imm: int, rs2: int, rs1: int, funct3: int) -> int:
    assert imm % 2 == 0
    value = imm & 0x1FFF
    return (((value >> 12) & 1) << 31) | (((value >> 5) & 0x3F) << 25) | ((rs2 & 31) << 20) | ((rs1 & 31) << 15) | ((funct3 & 7) << 12) | (((value >> 1) & 15) << 8) | (((value >> 11) & 1) << 7) | 0x63


def encode_u(imm: int, rd: int, opcode: int) -> int:
    return (imm & 0xFFFFF000) | ((rd & 31) << 7) | opcode


def encode_j(imm: int, rd: int) -> int:
    assert imm % 2 == 0
    value = imm & 0x1FFFFF
    return (((value >> 20) & 1) << 31) | (((value >> 1) & 0x3FF) << 21) | (((value >> 11) & 1) << 20) | (((value >> 12) & 0xFF) << 12) | ((rd & 31) << 7) | 0x6F


def encode_csr(csr: int, operand: int, funct3: int, rd: int) -> int:
    return ((csr & 0xFFF) << 20) | ((operand & 31) << 15) | ((funct3 & 7) << 12) | ((rd & 31) << 7) | 0x73


def load_immediate(rd: int, value: int) -> list[int]:
    value = u32(value)
    upper = (value + 0x800) & 0xFFFFF000
    lower = signed(value - upper)
    if upper == 0:
        return [encode_i(lower, 0, 0, rd)]
    return [encode_u(upper, rd, 0x37), encode_i(lower, rd, 0, rd)]


def m_result(funct3: int, left: int, right: int) -> int:
    left = u32(left)
    right = u32(right)
    if funct3 == 0:
        return u32(left * right)
    if funct3 == 1:
        return u32((signed(left) * signed(right)) >> 32)
    if funct3 == 2:
        return u32((signed(left) * right) >> 32)
    if funct3 == 3:
        return u32((left * right) >> 32)
    if funct3 == 4:
        if right == 0:
            return MASK32
        if left == 0x8000_0000 and right == MASK32:
            return 0x8000_0000
        return u32(abs(signed(left)) // abs(signed(right)) * (-1 if (signed(left) < 0) != (signed(right) < 0) else 1))
    if funct3 == 5:
        return MASK32 if right == 0 else left // right
    if funct3 == 6:
        if right == 0:
            return left
        if left == 0x8000_0000 and right == MASK32:
            return 0
        quotient = abs(signed(left)) // abs(signed(right)) * (-1 if (signed(left) < 0) != (signed(right) < 0) else 1)
        return u32(signed(left) - quotient * signed(right))
    if funct3 == 7:
        return left if right == 0 else left % right
    raise ValueError(f"invalid M funct3 {funct3}")


def c_like_signature_program() -> tuple[int, ...]:
    """Generate: product=1; for (n=5;n;n--) product*=n; signature=product+7."""
    return (
        encode_i(5, 0, 0, 1),
        encode_i(1, 0, 0, 2),
        encode_r(1, 1, 2, 0, 2),
        encode_i(-1, 1, 0, 1),
        encode_b(-8, 0, 1, 1),
        encode_i(7, 2, 0, 3),
        encode_s(0, 3, 0, 2),
        encode_i(0, 0, 2, 4, 0x03),
        0x0010_0073,
    )


DEFAULT_INSTRUCTION = 0x0010_0073  # Why: fetches beyond the program image stop as EBREAK instead of running zero words
COUNTER_CYCLE = (0xB00, 0xB80)
COUNTER_INSTRET = (0xB02, 0xB82)
# Why: these counters depend on timing the model does not have, so reads adopt
# the observed value.
COUNTER_HPM = tuple(range(0xB03, 0xB07)) + tuple(range(0xB83, 0xB87))
COUNTER_OBSERVED = COUNTER_CYCLE + COUNTER_HPM
MCOUNTINHIBIT_MASK = 0x7D
PMON_LEGAL_EVENTS = frozenset(
    (1, 2, 3, 4, 5, 6, 8, 9, 10, 12, 13, 14, 15, 16, 17, 18, 19, 29, 30, 32, 33, 34,
     64, 65, 66, 67, 68, 69, 76, 77, 78, 79, 80, 81, 82, 83, 84, 85, 86, 87, 88, 89,
     90, 91, 92, 93, 94, 95, 96, 97)
)


def _sext(value: int, width: int) -> int:
    sign = 1 << (width - 1)
    return ((value & (sign - 1)) - (value & sign)) & MASK32


class Rv32imModel:
    """Independent machine-mode RV32IM reference for the n2d core contract.

    The model owns registers, PC, the bounded machine CSR set, a Harvard
    instruction image (out-of-image fetches return EBREAK), a byte-addressed
    data memory, and access-error address sets. ``step`` returns the expected
    retirement/trap observation. mcycle and event-counter reads cannot be
    predicted from the architecture, so the caller passes the observed
    destination value for an instruction that reads one of them. PMON
    selectors and mcountinhibit are modeled, including IR inhibit of minstret.
    """

    def __init__(
        self,
        words,
        *,
        memory_size: int = 0x1000,
        initial_memory: bytes | None = None,
        instruction_error_addresses=(),
        data_error_addresses=(),
    ) -> None:
        self.words = tuple(words)
        self.memory = bytearray(memory_size)
        if initial_memory is not None:
            self.memory[: len(initial_memory)] = initial_memory
        self.instruction_errors = set(instruction_error_addresses)
        self.data_errors = set(data_error_addresses)
        self.registers = [0] * 32
        self.pc = 0
        self.order = 0
        self.csr = {
            0x300: 0x0000_1800,
            0x305: 0,
            0x340: 0,
            0x341: 0,
            0x342: 0,
            0x343: 0,
        }
        self.minstret = 0
        self.mcountinhibit = 0
        self.mhpmevent = [0, 0, 0, 0]

    def fetch(self, pc: int) -> int:
        index = pc >> 2
        return self.words[index] if index < len(self.words) else DEFAULT_INSTRUCTION

    def data_ok(self, address: int) -> bool:
        return address not in self.data_errors and (address & ~3) + 4 <= len(self.memory)

    def word(self, address: int) -> int:
        base = address & ~3
        return int.from_bytes(self.memory[base : base + 4], "little")

    @staticmethod
    def reads_cycle_counter(instruction: int) -> bool:
        return (
            (instruction & 0x7F) == 0x73
            and ((instruction >> 12) & 7) not in (0, 4)
            and (instruction >> 20) in COUNTER_OBSERVED
            and ((instruction >> 7) & 31) != 0
        )

    def _trap(self, pc: int, instruction: int, cause: int, value: int) -> Observation:
        self.csr[0x341] = pc
        self.csr[0x342] = cause
        self.csr[0x343] = value
        status = self.csr[0x300]
        mie = (status >> 3) & 1
        status = (status & ~0x88) | (mie << 7) | 0x1800
        self.csr[0x300] = status
        next_pc = self.csr[0x305]
        observation = Observation(
            self.order, pc, instruction, next_pc, trap=1, cause=cause, trap_value=value
        )
        self.pc = next_pc
        self.order += 1
        return observation

    def _csr_read(self, address: int, cycle_value: int | None) -> int | None:
        if address == 0x300:
            return self.csr[0x300] | 0x1800
        if address == 0x301:
            return 0x4000_1100
        if address in self.csr:
            return self.csr[address]
        if address == 0xB02:
            return self.minstret & MASK32
        if address == 0xB82:
            return (self.minstret >> 32) & MASK32
        if address in COUNTER_OBSERVED:
            return 0 if cycle_value is None else cycle_value
        if address == 0x320:
            return self.mcountinhibit
        if 0x323 <= address <= 0x326:
            return self.mhpmevent[address - 0x323]
        return None

    def step(self, observed_cycle_value: int | None = None) -> Observation:
        pc = self.pc
        if pc in self.instruction_errors:
            return self._trap(pc, 0, 1, pc)
        insn = self.fetch(pc)
        opcode = insn & 0x7F
        rd = (insn >> 7) & 31
        funct3 = (insn >> 12) & 7
        rs1 = (insn >> 15) & 31
        rs2 = (insn >> 20) & 31
        funct7 = insn >> 25
        a = self.registers[rs1]
        b = self.registers[rs2]
        imm_i = _sext(insn >> 20, 12)
        imm_s = _sext(((insn >> 25) << 5) | ((insn >> 7) & 31), 12)
        imm_b = _sext(
            (((insn >> 31) & 1) << 12) | (((insn >> 7) & 1) << 11)
            | (((insn >> 25) & 0x3F) << 5) | (((insn >> 8) & 15) << 1),
            13,
        )
        imm_u = insn & 0xFFFF_F000
        imm_j = _sext(
            (((insn >> 31) & 1) << 20) | (((insn >> 12) & 0xFF) << 12)
            | (((insn >> 20) & 1) << 11) | (((insn >> 21) & 0x3FF) << 1),
            21,
        )
        next_pc = u32(pc + 4)
        result: int | None = None
        memory: dict[str, int] = {}
        suppress_instret = False
        new_inhibit: int | None = None
        illegal = False

        def target(value: int) -> Observation | None:
            nonlocal next_pc
            next_pc = u32(value)
            if next_pc & 3:
                return self._trap(pc, insn, 0, next_pc)
            return None

        if opcode == 0x37:
            result = imm_u
        elif opcode == 0x17:
            result = u32(pc + imm_u)
        elif opcode == 0x6F:
            trap = target(pc + imm_j)
            if trap:
                return trap
            result = u32(pc + 4)
        elif opcode == 0x67 and funct3 == 0:
            trap = target((a + imm_i) & 0xFFFF_FFFE)
            if trap:
                return trap
            result = u32(pc + 4)
        elif opcode == 0x63 and funct3 not in (2, 3):
            taken = {
                0: a == b,
                1: a != b,
                4: signed(a) < signed(b),
                5: signed(a) >= signed(b),
                6: a < b,
                7: a >= b,
            }[funct3]
            if taken:
                trap = target(pc + imm_b)
                if trap:
                    return trap
        elif opcode == 0x03 and funct3 in (0, 1, 2, 4, 5):
            address = u32(a + imm_i)
            size = 1 << (funct3 & 3)
            if address % size:
                return self._trap(pc, insn, 4, address)
            if not self.data_ok(address):
                return self._trap(pc, insn, 5, address)
            bus = self.word(address)
            lane = address & 3
            raw = (bus >> (8 * lane)) & ((1 << (8 * size)) - 1)
            result = raw if funct3 & 4 or size == 4 else _sext(raw, 8 * size)
            mask = ((1 << size) - 1) << lane
            memory = dict(memory_valid=1, memory_address=address,
                          memory_read_data=bus, memory_read_mask=mask)
        elif opcode == 0x23 and funct3 in (0, 1, 2):
            address = u32(a + imm_s)
            size = 1 << funct3
            if address % size:
                return self._trap(pc, insn, 6, address)
            if not self.data_ok(address):
                return self._trap(pc, insn, 7, address)
            lane = address & 3
            bus = u32(b << (8 * lane))
            mask = ((1 << size) - 1) << lane
            for index in range(4):
                if mask & (1 << index):
                    self.memory[(address & ~3) + index] = (bus >> (8 * index)) & 0xFF
            memory = dict(memory_valid=1, memory_write=1, memory_address=address,
                          memory_write_data=bus, memory_write_mask=mask)
        elif opcode == 0x13:
            shamt = (insn >> 20) & 31
            if funct3 == 0:
                result = u32(a + imm_i)
            elif funct3 == 1 and funct7 == 0:
                result = u32(a << shamt)
            elif funct3 == 2:
                result = int(signed(a) < signed(imm_i))
            elif funct3 == 3:
                result = int(a < imm_i)
            elif funct3 == 4:
                result = a ^ imm_i
            elif funct3 == 5 and funct7 == 0:
                result = a >> shamt
            elif funct3 == 5 and funct7 == 0x20:
                result = u32(signed(a) >> shamt)
            elif funct3 == 6:
                result = a | imm_i
            elif funct3 == 7:
                result = a & imm_i
            else:
                illegal = True
        elif opcode == 0x33:
            shamt = b & 31
            if funct7 == 1:
                result = m_result(funct3, a, b)
            elif (funct7, funct3) == (0, 0):
                result = u32(a + b)
            elif (funct7, funct3) == (0x20, 0):
                result = u32(a - b)
            elif (funct7, funct3) == (0, 1):
                result = u32(a << shamt)
            elif (funct7, funct3) == (0, 2):
                result = int(signed(a) < signed(b))
            elif (funct7, funct3) == (0, 3):
                result = int(a < b)
            elif (funct7, funct3) == (0, 4):
                result = a ^ b
            elif (funct7, funct3) == (0, 5):
                result = a >> shamt
            elif (funct7, funct3) == (0x20, 5):
                result = u32(signed(a) >> shamt)
            elif (funct7, funct3) == (0, 6):
                result = a | b
            elif (funct7, funct3) == (0, 7):
                result = a & b
            else:
                illegal = True
        elif opcode == 0x0F and funct3 == 0:
            pass
        elif opcode == 0x73 and funct3 == 0:
            if insn == 0x0000_0073:
                return self._trap(pc, insn, 11, 0)
            if insn == 0x0010_0073:
                return self._trap(pc, insn, 3, 0)
            if insn == 0x3020_0073:
                trap = target(self.csr[0x341])
                if trap:
                    return trap
                status = self.csr[0x300]
                mpie = (status >> 7) & 1
                self.csr[0x300] = (status & ~0x88) | (mpie << 3) | 0x80 | 0x1800
            else:
                illegal = True
        elif opcode == 0x73 and funct3 != 4:
            address = insn >> 20
            write = funct3 in (1, 5) or rs1 != 0
            operand = rs1 if funct3 & 4 else a
            old = self._csr_read(address, observed_cycle_value)
            if old is None or (address == 0x301 and write):
                illegal = True
            else:
                result = old
                if write:
                    new = {1: operand, 2: old | operand, 3: old & ~operand & MASK32}[funct3 & 3]
                    if address == 0x300:
                        self.csr[0x300] = (new & 0x88) | 0x1800
                    elif address in (0x305, 0x341):
                        self.csr[address] = new & 0xFFFF_FFFC
                    elif address in self.csr:
                        self.csr[address] = new
                    elif address == 0xB02:
                        self.minstret = (self.minstret & ~MASK32) | new
                        suppress_instret = True
                    elif address == 0xB82:
                        self.minstret = (self.minstret & MASK32) | (new << 32)
                        suppress_instret = True
                    elif address == 0x320:
                        new_inhibit = new & MCOUNTINHIBIT_MASK
                    elif 0x323 <= address <= 0x326:
                        self.mhpmevent[address - 0x323] = new if new in PMON_LEGAL_EVENTS else 0
        else:
            illegal = True

        if illegal:
            return self._trap(pc, insn, 2, insn)

        register: dict[str, int] = {}
        if result is not None and rd != 0:
            self.registers[rd] = u32(result)
            register = dict(rd_address=rd, rd_write=1, rd_write_data=u32(result))
        observation = Observation(self.order, pc, insn, next_pc, **register, **memory)
        self.pc = next_pc
        self.order += 1
        # Ref: core MAS "Performance monitor": the writer's own count uses the
        # controls before its write.
        if not suppress_instret and not self.mcountinhibit & 4:
            self.minstret += 1
        if new_inhibit is not None:
            self.mcountinhibit = new_inhibit
        return observation
