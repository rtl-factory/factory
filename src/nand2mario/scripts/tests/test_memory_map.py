"""The RTL memory-map package, the Python DV model and the software placement
facts must agree on every region bound; none of the three may drift alone."""
import json
from pathlib import Path
import re
import unittest

REPO_ROOT = Path(__file__).resolve().parents[4]
PACKAGE = REPO_ROOT / 'src/nand2mario/rtl/memory/n2m_memory_pkg.sv'
SOFTWARE = REPO_ROOT / 'src/nand2mario/cfg/cpu-software.json'


def package_constants():
    text = PACKAGE.read_text()
    values = {}
    for name, literal in re.findall(r"localparam\s+logic\s*\[15:0\]\s+(\w+)\s*=\s*16'h([0-9A-Fa-f]+);", text):
        values[name] = int(literal, 16)
    for name, literal in re.findall(r"localparam\s+int\s+unsigned\s+(\w+)\s*=\s*(\d+);", text):
        values[name] = int(literal)
    return values


class MemoryMapTests(unittest.TestCase):
    def test_package_matches_software_placement_facts(self):
        rtl = package_constants()
        sw = json.loads(SOFTWARE.read_text())
        pairs = {'ROM_START': 'GB_ROM0_START', 'ROM_END': 'GB_ROM1_END',
                 'VRAM_START': 'GB_VRAM_START', 'VRAM_END': 'GB_VRAM_END',
                 'WRAM_START': 'GB_WRAM_START', 'WRAM_END': 'GB_WRAM_END',
                 'OAM_START': 'GB_OAM_START', 'OAM_END': 'GB_OAM_END',
                 'IO_START': 'GB_IO_START', 'HRAM_START': 'GB_HRAM_START',
                 'HRAM_END': 'GB_HRAM_END', 'IE_ADDRESS': 'GB_IE_END'}
        for rtl_name, sw_name in pairs.items():
            with self.subTest(constant=rtl_name):
                self.assertEqual(rtl[rtl_name], sw[sw_name])
        self.assertEqual(rtl['ROM_BYTES'], sw['PROFILE_ROM_BYTES'])

    def test_package_matches_python_model(self):
        from src.nand2mario.dv.memory import memory_model
        rtl = package_constants()
        expected = {name: (first, last) for first, last, name in memory_model.REGIONS}
        self.assertEqual((rtl['ROM_START'], rtl['ROM_END']), expected['rom'])
        self.assertEqual((rtl['VRAM_START'], rtl['VRAM_END']), expected['vram'])
        self.assertEqual((rtl['CART_RAM_START'], rtl['CART_RAM_END']), expected['cart_ram'])
        self.assertEqual((rtl['WRAM_START'], rtl['WRAM_END']), expected['wram'])
        self.assertEqual((rtl['ECHO_START'], rtl['ECHO_END']), expected['echo'])
        self.assertEqual((rtl['OAM_START'], rtl['OAM_END']), expected['oam'])
        self.assertEqual((rtl['UNUSABLE_START'], rtl['UNUSABLE_END']), expected['unusable'])
        self.assertEqual((rtl['IO_START'], rtl['IO_END']), expected['io'])
        self.assertEqual((rtl['HRAM_START'], rtl['HRAM_END']), expected['hram'])
        self.assertEqual((rtl['IE_ADDRESS'], rtl['IE_ADDRESS']), expected['ie'])
        self.assertEqual(rtl['OAM_BYTES'], memory_model.OAM_BYTES)
        self.assertEqual(rtl['HRAM_BYTES'], memory_model.HRAM_BYTES)
        self.assertEqual(rtl['VRAM_BYTES'], memory_model.VRAM_BYTES)
        self.assertEqual(rtl['WRAM_BYTES'], memory_model.WRAM_BYTES)

    def test_model_tiles_and_aliases(self):
        from src.nand2mario.dv.memory import memory_model
        covered = sum(last - first + 1 for first, last, _ in memory_model.REGIONS)
        self.assertEqual(covered, 65536)
        self.assertEqual(memory_model.destination(0xE000), ('wram', 0))
        self.assertEqual(memory_model.destination(0xFDFF), ('wram', 0x1DFF))
        self.assertEqual(memory_model.destination(0xDE00), ('wram', 0x1E00))
        # A ROM-only header (type $00 at $0147) selects the ROM-only mapper:
        # ROM writes are ignored and cartridge RAM is absent.
        memory = memory_model.GameBoyMemory(b'\x12' + bytes(0x14F))
        memory[0x0000] = 0x34
        self.assertEqual(memory[0x0000], 0x12)
        self.assertEqual(memory[0xA000], 0xFF)
        self.assertEqual(memory[0xFEA0], 0xFF)
        self.assertEqual(memory[0xFF44], 0xFF)
        memory[0xFFFF] = 0xE5
        self.assertEqual((memory[0xFFFF], memory.interrupt_enable()), (0xE5, 0x05))


if __name__ == '__main__':
    unittest.main()
