"""RV32IM and Zicsr mnemonic encoder for hand-written DV test programs.

Each mnemonic returns one 32-bit instruction word and takes its operands in
assembly order, so a program line reads as its assembly:

    addi(X5, X0, 7)       addi  x5, x0, 7
    lw(X4, 0, X3)         lw    x4, 0(x3)
    sw(X6, 8, X5)         sw    x6, 8(x5)
    lui(X5, 0x20001)      lui   x5, 0x20001    (the 20-bit upper immediate)
    beq(X6, X0, -4)       beq   x6, x0, -4     (a byte offset from this word)
    csrrw(X0, MTVEC, X9)  csrrw x0, mtvec, x9

A branch or jump target may also be a label name; the mnemonic then returns a
deferred word that ``assemble`` resolves against the program's labels.
Pseudo-instructions (``nop``, ``j``, ``ret``, ``csrr``, ``csrw``, ``li``,
``la`` ...) expand as the RISC-V assembler expands them. The format encoders
(``encode_r`` ...) stay public for programs that choose a funct3 or opcode at
run time, such as random instruction streams.
"""

from __future__ import annotations

from typing import Callable, Union

MASK32 = 0xFFFF_FFFF

X0, X1, X2, X3, X4, X5, X6, X7, X8, X9, X10, X11, X12, X13, X14, X15 = range(16)
X16, X17, X18, X19, X20, X21, X22, X23, X24, X25, X26, X27, X28, X29, X30, X31 = range(16, 32)
ZERO, RA, SP = X0, X1, X2

# Ref: RISC-V Unprivileged ISA, chapter "RV32/64G Instruction Set Listings": major opcodes.
OPCODE_LOAD = 0x03
OPCODE_OP_IMM = 0x13
OPCODE_AUIPC = 0x17
OPCODE_STORE = 0x23
OPCODE_OP = 0x33
OPCODE_LUI = 0x37
OPCODE_BRANCH = 0x63
OPCODE_JALR = 0x67
OPCODE_JAL = 0x6F
OPCODE_SYSTEM = 0x73

# Ref: RISC-V Privileged ISA, "Control and Status Registers": machine CSR addresses.
MSTATUS = 0x300
MISA = 0x301
MTVEC = 0x305
MCOUNTINHIBIT = 0x320
MHPMEVENT3, MHPMEVENT4, MHPMEVENT5, MHPMEVENT6 = range(0x323, 0x327)
MSCRATCH = 0x340
MEPC = 0x341
MCAUSE = 0x342
MTVAL = 0x343
MCYCLE = 0xB00
MINSTRET = 0xB02
MHPMCOUNTER3, MHPMCOUNTER4, MHPMCOUNTER5, MHPMCOUNTER6 = range(0xB03, 0xB07)
MCYCLEH = 0xB80
MINSTRETH = 0xB82
MHPMCOUNTER3H, MHPMCOUNTER4H, MHPMCOUNTER5H, MHPMCOUNTER6H = range(0xB83, 0xB87)

Deferred = Callable[[int, dict], int]
Target = Union[int, str]


def _check_signed(imm: int, bits: int, what: str) -> None:
    """Refuse an immediate outside the field's signed range, as GNU as does,
    instead of masking it to a different value."""
    limit = 1 << (bits - 1)
    assert -limit <= imm < limit, f"{what} immediate {imm} outside {-limit}..{limit - 1}"


def encode_r(funct7: int, rs2: int, rs1: int, funct3: int, rd: int) -> int:
    return (
        ((funct7 & 0x7F) << 25)
        | ((rs2 & 31) << 20)
        | ((rs1 & 31) << 15)
        | ((funct3 & 7) << 12)
        | ((rd & 31) << 7)
        | OPCODE_OP
    )


def encode_i(imm: int, rs1: int, funct3: int, rd: int, opcode: int = OPCODE_OP_IMM) -> int:
    _check_signed(imm, 12, "I-type")
    return ((imm & 0xFFF) << 20) | ((rs1 & 31) << 15) | ((funct3 & 7) << 12) | ((rd & 31) << 7) | opcode


def encode_s(imm: int, rs2: int, rs1: int, funct3: int) -> int:
    _check_signed(imm, 12, "S-type")
    value = imm & 0xFFF
    return (
        (((value >> 5) & 0x7F) << 25)
        | ((rs2 & 31) << 20)
        | ((rs1 & 31) << 15)
        | ((funct3 & 7) << 12)
        | ((value & 31) << 7)
        | OPCODE_STORE
    )


def encode_b(imm: int, rs2: int, rs1: int, funct3: int) -> int:
    _check_signed(imm, 13, "branch")
    assert imm % 2 == 0
    value = imm & 0x1FFF
    return (
        (((value >> 12) & 1) << 31)
        | (((value >> 5) & 0x3F) << 25)
        | ((rs2 & 31) << 20)
        | ((rs1 & 31) << 15)
        | ((funct3 & 7) << 12)
        | (((value >> 1) & 15) << 8)
        | (((value >> 11) & 1) << 7)
        | OPCODE_BRANCH
    )


def encode_u(imm: int, rd: int, opcode: int) -> int:
    """``imm`` is the full 32-bit value whose upper 20 bits are encoded."""
    return (imm & 0xFFFFF000) | ((rd & 31) << 7) | opcode


def encode_j(imm: int, rd: int) -> int:
    _check_signed(imm, 21, "jal")
    assert imm % 2 == 0
    value = imm & 0x1FFFFF
    return (
        (((value >> 20) & 1) << 31)
        | (((value >> 1) & 0x3FF) << 21)
        | (((value >> 11) & 1) << 20)
        | (((value >> 12) & 0xFF) << 12)
        | ((rd & 31) << 7)
        | OPCODE_JAL
    )


def encode_csr(csr: int, operand: int, funct3: int, rd: int) -> int:
    """``operand`` is rs1, or the 5-bit immediate of the ``*i`` forms."""
    return ((csr & 0xFFF) << 20) | ((operand & 31) << 15) | ((funct3 & 7) << 12) | ((rd & 31) << 7) | OPCODE_SYSTEM


def _signed32(value: int) -> int:
    value &= MASK32
    return value - (1 << 32) if value & 0x8000_0000 else value


def _r(funct7: int, funct3: int) -> Callable[[int, int, int], int]:
    return lambda rd, rs1, rs2: encode_r(funct7, rs2, rs1, funct3, rd)


def _op_imm(funct3: int) -> Callable[[int, int, int], int]:
    return lambda rd, rs1, imm: encode_i(imm, rs1, funct3, rd)


def _shift_imm(funct3: int, high: int) -> Callable[[int, int, int], int]:
    def shift(rd: int, rs1: int, shamt: int) -> int:
        assert 0 <= shamt < 32
        return encode_i(high | shamt, rs1, funct3, rd)
    return shift


def _load(funct3: int) -> Callable[[int, int, int], int]:
    return lambda rd, offset, rs1: encode_i(offset, rs1, funct3, rd, OPCODE_LOAD)


def _store(funct3: int) -> Callable[[int, int, int], int]:
    return lambda rs2, offset, rs1: encode_s(offset, rs2, rs1, funct3)


def _branch(funct3: int) -> Callable[[int, int, Target], Union[int, Deferred]]:
    def branch(rs1: int, rs2: int, target: Target):
        if isinstance(target, str):
            return lambda pc, labels: encode_b(labels[target] - pc, rs2, rs1, funct3)
        return encode_b(target, rs2, rs1, funct3)
    return branch


def _csr(funct3: int) -> Callable[[int, int, int], int]:
    return lambda rd, csr, operand: encode_csr(csr, operand, funct3, rd)


add, sll, slt, sltu, xor, srl, or_, and_ = (_r(0x00, funct3) for funct3 in range(8))
sub, sra = _r(0x20, 0b000), _r(0x20, 0b101)
mul, mulh, mulhsu, mulhu, div, divu, rem, remu = (_r(0x01, funct3) for funct3 in range(8))

addi, slti, sltiu, xori, ori, andi = (_op_imm(funct3) for funct3 in (0, 2, 3, 4, 6, 7))
slli, srli, srai = _shift_imm(0b001, 0), _shift_imm(0b101, 0), _shift_imm(0b101, 0x400)

lb, lh, lw, lbu, lhu = (_load(funct3) for funct3 in (0, 1, 2, 4, 5))
sb, sh, sw = (_store(funct3) for funct3 in (0, 1, 2))

beq, bne, blt, bge, bltu, bgeu = (_branch(funct3) for funct3 in (0, 1, 4, 5, 6, 7))

csrrw, csrrs, csrrc = (_csr(funct3) for funct3 in (1, 2, 3))
csrrwi, csrrsi, csrrci = (_csr(funct3) for funct3 in (5, 6, 7))


def lui(rd: int, imm20: int) -> int:
    assert 0 <= imm20 < 1 << 20
    return encode_u(imm20 << 12, rd, OPCODE_LUI)


def auipc(rd: int, imm20: int) -> int:
    assert 0 <= imm20 < 1 << 20
    return encode_u(imm20 << 12, rd, OPCODE_AUIPC)


def jal(rd: int, target: Target):
    if isinstance(target, str):
        return lambda pc, labels: encode_j(labels[target] - pc, rd)
    return encode_j(target, rd)


def jalr(rd: int, offset: int, rs1: int) -> int:
    return encode_i(offset, rs1, 0b000, rd, OPCODE_JALR)


ECALL = encode_i(0, 0, 0, 0, OPCODE_SYSTEM)
EBREAK = encode_i(1, 0, 0, 0, OPCODE_SYSTEM)
MRET = encode_i(0x302, 0, 0, 0, OPCODE_SYSTEM)
NOP = addi(X0, X0, 0)
RET = jalr(X0, 0, RA)


def j(target: Target):
    return jal(X0, target)


def call(target: Target):
    return jal(RA, target)


def beqz(rs1: int, target: Target):
    return beq(rs1, X0, target)


def bnez(rs1: int, target: Target):
    return bne(rs1, X0, target)


def csrr(rd: int, csr: int) -> int:
    return csrrs(rd, csr, X0)


def csrw(csr: int, rs1: int) -> int:
    return csrrw(X0, csr, rs1)


def _split_hi_lo(value: int) -> tuple[int, int]:
    """The ``%hi``/``%lo`` split of a 32-bit value: the upper part rounds up
    when the low 12 bits are negative, so ``upper + lower`` is ``value``."""
    value &= MASK32
    upper = (value + 0x800) & 0xFFFFF000
    return upper, _signed32(value - upper)


def li(rd: int, value: int) -> list[int]:
    """``addi`` alone when the value fits 12 signed bits, else ``lui`` + ``addi``."""
    upper, lower = _split_hi_lo(value)
    if upper == 0:
        return [addi(rd, X0, lower)]
    return [lui(rd, upper >> 12), addi(rd, rd, lower)]


def la(rd: int, label: str) -> list[Deferred]:
    """rd = the address of ``label``: ``auipc`` + ``addi``, both relative to the
    ``auipc``, split with ``%pcrel_hi``/``%pcrel_lo`` as GNU as splits it."""
    return [lambda pc, labels: auipc(rd, _split_hi_lo(labels[label] - pc)[0] >> 12),
            lambda pc, labels: addi(rd, rd, _split_hi_lo(labels[label] - (pc - 4))[1])]


def assemble(lines: list, base: int = 0) -> list[int]:
    """Two-pass label assembler: a str ending in ':' defines a label, a
    deferred word (a callable of pc and labels) is resolved, an int is a word."""
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
