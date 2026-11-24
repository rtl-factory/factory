"""The RV32 mnemonic encoder against GNU as, and the hand-encoded programs
it replaced, word for word.

The expected words of ``GNU_AS`` were assembled once by riscv64-unknown-elf-as
13.2 (``-march=rv32im_zicsr``, no compressed instructions) from the listed
text. The program goldens are the literal words the DV programs held before
they were written with the encoder; they must not change.
"""
import unittest

from src.nand2doom.dv.core.rv32_asm import (
    EBREAK, ECALL, MCAUSE, MCOUNTINHIBIT, MCYCLE, MCYCLEH, MEPC, MHPMCOUNTER3H,
    MHPMCOUNTER6, MHPMCOUNTER6H, MHPMEVENT3, MHPMEVENT6, MINSTRET, MINSTRETH, MISA, MRET,
    MSCRATCH, MSTATUS, MTVAL, MTVEC, NOP, RET, X0, X1, X2, X3, X4, X5, X6, X7, X8, X9, X10,
    X11, X15, X16, X29, X30, X31, add, addi, and_, andi, assemble, auipc, beq, beqz, bge,
    bgeu, blt, bltu, bne, bnez, call, csrr, csrrc, csrrci, csrrs, csrrsi, csrrw, csrrwi,
    csrw, div, divu, j, jal, jalr, la, lb, lbu, lh, lhu, li, lui, lw, mul, mulh, mulhsu,
    mulhu, or_, ori, rem, remu, sb, sh, sll, slli, slt, slti, sltiu, sltu, sra, srai, srl,
    srli, sub, sw, xor, xori,
)
from src.nand2doom.dv.core.rv32i_smoke_model import smoke_program
from src.nand2doom.dv.core.rv32im_model import c_like_signature_program

GNU_AS = [
    ('add x3, x1, x2', add(X3, X1, X2), 0x0020_81B3),
    ('sub x5, x4, x2', sub(X5, X4, X2), 0x4022_02B3),
    ('sll x1, x2, x3', sll(X1, X2, X3), 0x0031_10B3),
    ('slt x1, x2, x3', slt(X1, X2, X3), 0x0031_20B3),
    ('sltu x1, x2, x3', sltu(X1, X2, X3), 0x0031_30B3),
    ('xor x1, x2, x3', xor(X1, X2, X3), 0x0031_40B3),
    ('srl x1, x2, x3', srl(X1, X2, X3), 0x0031_50B3),
    ('sra x1, x2, x3', sra(X1, X2, X3), 0x4031_50B3),
    ('or x1, x2, x3', or_(X1, X2, X3), 0x0031_60B3),
    ('and x31, x30, x29', and_(X31, X30, X29), 0x01DF_7FB3),
    ('mul x5, x1, x2', mul(X5, X1, X2), 0x0220_82B3),
    ('mulh x5, x1, x1', mulh(X5, X1, X1), 0x0210_92B3),
    ('mulhsu x5, x1, x2', mulhsu(X5, X1, X2), 0x0220_A2B3),
    ('mulhu x5, x1, x2', mulhu(X5, X1, X2), 0x0220_B2B3),
    ('div x4, x1, x2', div(X4, X1, X2), 0x0220_C233),
    ('divu x3, x5, x1', divu(X3, X5, X1), 0x0212_D1B3),
    ('rem x6, x5, x4', rem(X6, X5, X4), 0x0242_E333),
    ('remu x6, x5, x4', remu(X6, X5, X4), 0x0242_F333),
    ('addi x5, x0, 7', addi(X5, X0, 7), 0x0070_0293),
    ('addi x1, x1, -1', addi(X1, X1, -1), 0xFFF0_8093),
    ('addi x2, x0, 2047', addi(X2, X0, 2047), 0x7FF0_0113),
    ('addi x2, x0, -2048', addi(X2, X0, -2048), 0x8000_0113),
    ('slti x1, x2, -5', slti(X1, X2, -5), 0xFFB1_2093),
    ('sltiu x1, x2, 5', sltiu(X1, X2, 5), 0x0051_3093),
    ('xori x1, x2, -1', xori(X1, X2, -1), 0xFFF1_4093),
    ('ori x1, x2, 0x7f', ori(X1, X2, 0x7F), 0x07F1_6093),
    ('andi x15, x15, 127', andi(X15, X15, 127), 0x07F7_F793),
    ('slli x1, x2, 31', slli(X1, X2, 31), 0x01F1_1093),
    ('srli x1, x2, 1', srli(X1, X2, 1), 0x0011_5093),
    ('srai x15, x11, 16', srai(X15, X11, 16), 0x4105_D793),
    ('lb x5, 1(x3)', lb(X5, 1, X3), 0x0011_8283),
    ('lh x5, -2(x3)', lh(X5, -2, X3), 0xFFE1_9283),
    ('lw x4, 0(x3)', lw(X4, 0, X3), 0x0001_A203),
    ('lbu x16, 0(x15)', lbu(X16, 0, X15), 0x0007_C803),
    ('lhu x5, 6(x3)', lhu(X5, 6, X3), 0x0061_D283),
    ('sb x7, 9(x5)', sb(X7, 9, X5), 0x0072_84A3),
    ('sh x7, -6(x5)', sh(X7, -6, X5), 0xFE72_9D23),
    ('sw x6, 8(x5)', sw(X6, 8, X5), 0x0062_A423),
    ('sw x1, 0x5fc(x0)', sw(X1, 0x5FC, X0), 0x5E10_2E23),
    ('sw x10, -2048(x2)', sw(X10, -2048, X2), 0x80A1_2023),
    ('beq x6, x0, .-4', beq(X6, X0, -4), 0xFE03_0EE3),
    ('bne x4, x0, .+8', bne(X4, X0, 8), 0x0002_1463),
    ('blt x1, x2, .-4096', blt(X1, X2, -4096), 0x8020_C063),
    ('bge x2, x3, .+8', bge(X2, X3, 8), 0x0031_5463),
    ('bltu x1, x2, .+4094', bltu(X1, X2, 4094), 0x7E20_EFE3),
    ('bgeu x1, x2, .-2', bgeu(X1, X2, -2), 0xFE20_FFE3),
    ('lui x5, 0x20001', lui(X5, 0x20001), 0x2000_12B7),
    ('lui x5, 0xfffff', lui(X5, 0xFFFFF), 0xFFFF_F2B7),
    ('auipc x8, 0', auipc(X8, 0), 0x0000_0417),
    ('auipc x8, 0x12345', auipc(X8, 0x12345), 0x1234_5417),
    ('jal x1, .-12', jal(X1, -12), 0xFF5F_F0EF),
    ('jal x0, .+1048574', jal(X0, 1048574), 0x7FFF_F06F),
    ('jal x5, .-1048576', jal(X5, -1048576), 0x8000_02EF),
    ('jalr x1, 4(x7)', jalr(X1, 4, X7), 0x0043_80E7),
    ('jalr x0, -8(x9)', jalr(X0, -8, X9), 0xFF84_8067),
    ('csrrw x0, mtvec, x9', csrrw(X0, MTVEC, X9), 0x3054_9073),
    ('csrrs x9, mepc, x0', csrrs(X9, MEPC, X0), 0x3410_24F3),
    ('csrrc x3, mstatus, x4', csrrc(X3, MSTATUS, X4), 0x3002_31F3),
    ('csrrwi x3, mscratch, 31', csrrwi(X3, MSCRATCH, 31), 0x340F_D1F3),
    ('csrrsi x3, mcountinhibit, 1', csrrsi(X3, MCOUNTINHIBIT, 1), 0x3200_E1F3),
    ('csrrci x3, mhpmcounter3h, 2', csrrci(X3, MHPMCOUNTER3H, 2), 0xB831_71F3),
    ('csrr x5, mcause', csrr(X5, MCAUSE), 0x3420_22F3),
    ('csrr x5, mtval', csrr(X5, MTVAL), 0x3430_22F3),
    ('csrr x5, misa', csrr(X5, MISA), 0x3010_22F3),
    ('csrr x5, mcycle', csrr(X5, MCYCLE), 0xB000_22F3),
    ('csrr x5, minstret', csrr(X5, MINSTRET), 0xB020_22F3),
    ('csrr x5, mcycleh', csrr(X5, MCYCLEH), 0xB800_22F3),
    ('csrr x5, minstreth', csrr(X5, MINSTRETH), 0xB820_22F3),
    ('csrr x5, mhpmcounter6', csrr(X5, MHPMCOUNTER6), 0xB060_22F3),
    ('csrr x5, mhpmcounter6h', csrr(X5, MHPMCOUNTER6H), 0xB860_22F3),
    ('csrw mhpmevent3, x1', csrw(MHPMEVENT3, X1), 0x3230_9073),
    ('csrw mhpmevent6, x1', csrw(MHPMEVENT6, X1), 0x3260_9073),
    ('ecall', ECALL, 0x0000_0073),
    ('ebreak', EBREAK, 0x0010_0073),
    ('mret', MRET, 0x3020_0073),
    ('nop', NOP, 0x0000_0013),
    ('ret', RET, 0x0000_8067),
    ('j .-8', j(-8), 0xFF9F_F06F),
    ('jal ra, .+16', call(16), 0x0100_00EF),
    ('beqz x5, .+12', beqz(X5, 12), 0x0002_8663),
    ('bnez x10, .-4', bnez(X10, -4), 0xFE05_1EE3),
]

PROGRAMS = {
    "uart_mem_board": (
        0x1001_03B7, 0x56C0_0313, 0x0063_A023, 0x1000_02B7, 0x0403_0337, 0x2013_0313,
        0x0062_A023, 0x0807_0337, 0x6053_0313, 0x0062_A223, 0x2000_1437, 0x0030_0493,
        0x0094_2423, 0x0015_0513, 0x10A0_2023, 0x04A2_A023, 0xFF5F_F06F, 0x0000_0013,
    ),
    "de2_115": (
        0x2000_12B7, 0x0010_0313, 0x0062_A023, 0x0062_A423, 0x2000_22B7, 0x0002_A303,
        0xFE03_0EE3, 0x1060_2023, 0x0000_006F,
    ),
    "video_control": (
        0x1002_02B7, 0x0140_0313, 0x3053_1073, 0x0002_A383, 0x0002_A023, 0x2000_12B7,
        0x0280_0313, 0x3053_1073, 0x0030_0393, 0x0072_84A3, 0x0340_0313, 0x3053_1073,
        0x0072_A423, 0x0000_006F,
    ),
    "smoke": (
        0x0070_0093, 0x0050_0113, 0x0020_81B3, 0x0030_2023, 0x0000_2203, 0x0032_0463,
        0xFFF0_0293, 0x4022_02B3, 0x0012_9463, 0x01D2_8313, 0x0060_2223,
    ),
    "c_like_signature": (
        0x0050_0093, 0x0010_0113, 0x0211_0133, 0xFFF0_8093, 0xFE00_9CE3, 0x0071_0193,
        0x0030_2023, 0x0000_2203, 0x0010_0073,
    ),
}


def _program(name):
    if name == "smoke":
        return smoke_program()
    if name == "c_like_signature":
        return c_like_signature_program()
    if name == "uart_mem_board":
        from src.nand2doom.dv.fpga.uart_mem_board import PROGRAM
    elif name == "de2_115":
        from src.nand2doom.dv.fpga.test_n2d_de2_115 import PROGRAM
    else:
        from src.nand2doom.dv.platform.test_n2d_video_control import PROGRAM
    return PROGRAM


class GnuAsTests(unittest.TestCase):
    def test_every_mnemonic_matches_gnu_as(self):
        for text, word, expected in GNU_AS:
            with self.subTest(text):
                self.assertEqual(f"{word:08x}", f"{expected:08x}")


class PseudoInstructionTests(unittest.TestCase):
    def test_li_is_addi_when_the_value_fits_twelve_signed_bits(self):
        self.assertEqual(li(X5, -2048), [addi(X5, X0, -2048)])
        self.assertEqual(li(X5, 2047), [addi(X5, X0, 2047)])

    def test_li_rounds_the_upper_part_for_a_negative_low_part(self):
        self.assertEqual(li(X6, 0x0807_0605), [lui(X6, 0x08070), addi(X6, X6, 0x605)])
        self.assertEqual(li(X6, 0x1234_5FFF), [lui(X6, 0x12346), addi(X6, X6, -1)])
        self.assertEqual(li(X6, 0x8000_0000), [lui(X6, 0x80000), addi(X6, X6, 0)])

    def test_labels_resolve_to_byte_offsets_from_each_word(self):
        words = assemble([
            "top:",
            addi(X1, X1, -1),
            bnez(X1, "top"),
            j("end"),
            NOP,
            "end:",
            call("top"),
        ])
        self.assertEqual(words, [addi(X1, X1, -1), bne(X1, X0, -4), jal(X0, 8), NOP, jal(X1, -16)])

    def test_la_is_auipc_plus_addi_relative_to_the_auipc(self):
        words = assemble([NOP, *la(X8, "data"), NOP, "data:", 0], base=0x100)
        self.assertEqual(words[1:3], [auipc(X8, 0), addi(X8, X8, 12)])

    def test_la_splits_a_far_offset_as_gnu_as_does(self):
        # Why: the words are GNU as's ``la`` (``.option norelax``) to a label
        # this many bytes from the auipc, so the auipc must carry the rounded
        # upper part and the addi the signed low part.
        for offset, expected in (
            (2408, (0x0000_1417, 0x9684_0413)),
            (2044, (0x0000_0417, 0x7FC4_0413)),
            (2048, (0x0000_1417, 0x8004_0413)),
            (-2048, (0x0000_0417, 0x8004_0413)),
            (-2052, (0xFFFF_F417, 0x7FC4_0413)),
            (0x5_0800, (0x0005_1417, 0x8004_0413)),
        ):
            with self.subTest(offset):
                if offset > 0:
                    lines = [*la(X8, "data"), *[0] * ((offset - 8) // 4), "data:", 0]
                    words = assemble(lines)[:2]
                else:
                    lines = ["data:", *[0] * (-offset // 4), *la(X8, "data")]
                    words = assemble(lines)[-2:]
                self.assertEqual([f"{w:08x}" for w in words], [f"{w:08x}" for w in expected])

    def test_out_of_range_immediates_are_refused(self):
        refused = {
            "addi 2048": lambda: addi(X1, X1, 2048),
            "lw -2049": lambda: lw(X1, -2049, X2),
            "sw 2048": lambda: sw(X1, 2048, X2),
            "beq 8192": lambda: beq(X1, X2, 8192),
            "beq 4096": lambda: beq(X1, X2, 4096),
            "bne -4098": lambda: bne(X1, X2, -4098),
            "jal 1 MiB": lambda: jal(X1, 1 << 20),
            "jal -1 MiB - 2": lambda: jal(X1, -(1 << 20) - 2),
            "label branch": lambda: assemble([beqz(X1, "far"), *[NOP] * 1024, "far:"]),
            "label jump": lambda: assemble(["top:", *[NOP] * ((1 << 18) + 1), j("top")]),
        }
        for name, encode in refused.items():
            with self.subTest(name), self.assertRaises(AssertionError):
                encode()

    def test_out_of_range_operands_are_refused(self):
        with self.assertRaises(AssertionError):
            lui(X1, 1 << 20)
        with self.assertRaises(AssertionError):
            slli(X1, X1, 32)
        with self.assertRaises(AssertionError):
            beq(X1, X2, 3)


class ProgramGoldenTests(unittest.TestCase):
    def test_programs_encode_the_words_they_held_as_literals(self):
        for name, expected in PROGRAMS.items():
            with self.subTest(name):
                self.assertEqual([f"{w:08x}" for w in _program(name)],
                                 [f"{w:08x}" for w in expected])


if __name__ == "__main__":
    unittest.main()
