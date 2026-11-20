"""The Python MBC1 cartridge model (the oracle of the cartridge targets)
against Pan Docs facts that a shared mistake with the RTL would hide."""
import unittest

from src.nand2mario.dv.cartridge import mbc1_model as m


def cartridge(cartridge_type, rom_code, ram_code):
    c = m.Cartridge()
    c.load(m.HEADER_TYPE, cartridge_type)
    c.load(m.HEADER_ROM_SIZE, rom_code)
    c.load(m.HEADER_RAM_SIZE, ram_code)
    return c


class CartridgeModelTests(unittest.TestCase):
    def test_header_undefined_until_loaded(self):
        c = m.Cartridge()
        with self.assertRaises(m.UndefinedHeader):
            c.translate(0x4000, False)
        c.load_image(bytes(0x150))
        self.assertEqual(c.translate(0x4000, False), ('rom', 0x4000))

    def test_masks(self):
        self.assertEqual([m.rom_bank_mask(code) for code in range(8)], [1, 3, 7, 15, 31, 63, 127, 127])
        self.assertEqual(m.rom_bank_mask(0x52), 127)
        self.assertEqual([m.ram_bank_mask(code) for code in range(6)], [0, 0, 0, 3, 3, 3])
        self.assertEqual([m.has_ram_capacity(code) for code in range(7)],
                         [False, False, True, True, True, True, False])

    def test_ram_size_zero_means_no_ram(self):
        # Pan Docs $0149: $00 is no RAM even for a RAM-bearing type.
        for cartridge_type in (m.MBC1_RAM, m.MBC1_RAM_BATTERY):
            for code in (0x00, 0x01):
                c = cartridge(cartridge_type, 6, code)
                c.write(0x0000, 0x0A)
                self.assertTrue(c.registers[m.RAMG])
                self.assertFalse(c.ram_present)
                self.assertEqual(c.translate(0xA000, False), ('local', None))
                self.assertEqual(c.translate(0xA000, True), ('local', None))
                c.write(0xA000, 0x55)
                self.assertEqual((c.read(0xA000), c.defined_ram_bytes()), (0xFF, []))
        c = cartridge(m.MBC1_RAM, 6, 2)
        c.write(0x0000, 0x0A)
        self.assertEqual(c.translate(0xA000, False), ('ram', 0))

    def test_rom_only_header_ignores_stale_registers(self):
        c = cartridge(m.MBC1_RAM_BATTERY, 6, 3)
        c.write(0x2000, 2)
        c.write(0x4000, 3)
        c.write(0x6000, 1)
        c.write(0x0000, 0x0A)
        self.assertEqual((c.rom_bank(0x0000), c.rom_bank(0x4000), c.ram_bank()), (96, 98, 3))
        for cartridge_type in (m.ROM_ONLY, 0x13):
            c.load(m.HEADER_TYPE, cartridge_type)   # no reset: registers stay
            self.assertEqual(c.registers[m.BANK1], 2)
            self.assertEqual((c.rom_bank(0x0000), c.rom_bank(0x4000)), (0, 1))
            self.assertEqual(c.translate(0x4123, False), ('rom', 0x4123))
            self.assertEqual(c.translate(0xA000, False), ('local', None))
        c.load(m.HEADER_TYPE, m.MBC1_RAM_BATTERY)
        self.assertEqual((c.rom_bank(0x0000), c.rom_bank(0x4000), c.ram_bank()), (96, 98, 3))

    def test_rom_only_ignores_registers_and_has_no_ram(self):
        c = cartridge(m.ROM_ONLY, 0, 0)
        c.write(0x2000, 3)
        c.write(0x0000, 0x0A)
        self.assertEqual(c.translate(0x4000, False), ('rom', 0x4000))
        self.assertEqual(c.translate(0xA000, False), ('local', None))
        self.assertEqual(c.read(0xA000), 0xFF)
        c = cartridge(0x13, 5, 3)   # another mapper type behaves as ROM-only
        c.write(0x2000, 3)
        self.assertEqual(c.translate(0x7FFF, False), ('rom', 0x7FFF))

    def test_mbc1_banking(self):
        c = cartridge(m.MBC1_RAM_BATTERY, 6, 3)
        self.assertEqual(c.rom_bank(0x4000), 1)
        c.write(0x2000, 0)
        self.assertEqual(c.rom_bank(0x4000), 1, 'zero selects bank one')
        c.write(0x2000, 0x1F)
        c.write(0x4000, 0x03)
        self.assertEqual((c.rom_bank(0x7FFF), c.rom_bank(0x0000)), (127, 0))
        c.write(0x6000, 1)
        self.assertEqual((c.rom_bank(0x0000), c.rom_bank(0x4000)), (96, 127))
        self.assertEqual(c.translate(0x0123, False), ('rom', 96 * 16384 + 0x123))
        c.write(0x2000, 0x60)
        self.assertEqual(c.rom_bank(0x4000), 97, 'five register bits, zero check before masking')
        # RAM: disabled until a $xA nibble, bank 0 in mode 0, BANK2 in mode 1.
        self.assertEqual(c.translate(0xA000, False), ('local', None))
        c.write(0x0000, 0x1A)
        self.assertEqual(c.translate(0xA000, False), ('ram', 3 * 8192))
        c.write(0x6000, 0)
        self.assertEqual(c.translate(0xBFFF, True), ('ram', 0x1FFF))
        c.write(0x0000, 0xA0)
        self.assertEqual(c.translate(0xA000, True), ('local', None))
        c.write(0xA000, 0x55)
        c.write(0x0000, 0x0A)
        with self.assertRaises(m.UndefinedByte):
            c.read(0xA000)
        c.write(0xA000, 0x66)
        self.assertEqual((c.read(0xA000), c.defined_ram_bytes()), (0x66, [(0, 0x66)]))

    def test_small_rom_and_ram_masks(self):
        c = cartridge(m.MBC1_RAM, 3, 2)   # 256 KiB, 8 KiB
        c.write(0x2000, 0x10)
        self.assertEqual(c.rom_bank(0x4000), 0, 'bank 16 of a 16-bank ROM is bank 0')
        c.write(0x4000, 3)
        c.write(0x6000, 1)
        self.assertEqual(c.rom_bank(0x0000), 0)
        c.write(0x0000, 0x0A)
        self.assertEqual(c.translate(0xA000, False), ('ram', 0))
        c = cartridge(m.MBC1, 5, 0)   # 1 MiB without RAM
        c.write(0x0000, 0x0A)
        self.assertEqual(c.translate(0xA000, False), ('local', None))
        c.write(0x4000, 2)
        c.write(0x6000, 1)
        self.assertEqual(c.rom_bank(0x0000), 0, '64 & 63')

    def test_reset_keeps_header_and_stores(self):
        c = cartridge(m.MBC1_RAM_BATTERY, 5, 3)
        c.write(0x0000, 0x0A)
        c.write(0x6000, 1)
        c.write(0x4000, 1)
        c.write(0xA000, 0x42)
        c.reset()
        self.assertEqual(c.registers, {m.BANK1: 1, m.BANK2: 0, m.MODE: 0, m.RAMG: False})
        self.assertTrue(c.mbc1 and c.ram_present)
        c.write(0x0000, 0x0A)
        c.write(0x6000, 1)
        c.write(0x4000, 1)
        self.assertEqual(c.read(0xA000), 0x42)


if __name__ == '__main__':
    unittest.main()
