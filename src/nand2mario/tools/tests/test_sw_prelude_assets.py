"""Generated DMG prelude facts, shade-asset encoding and the cpu_sw --asset/--title CLI."""
import hashlib
import json
import re
import tempfile
import unittest
from pathlib import Path

from src.nand2mario.tools import cpu_sw
from src.nand2mario.tools.sw import interfaces_inc
from src.nand2mario.tools.sw.assets import encode_shades, parse_shades
from src.nand2mario.tools.sw.expressions import AssemblyError

ROOT = Path(__file__).resolve().parents[4]


class PreludeTests(unittest.TestCase):
    def test_committed_prelude_is_fresh(self):
        self.assertEqual(interfaces_inc.PRELUDE.read_text(), interfaces_inc.render())

    def symbols(self):
        return {s['name']: s['value'] for s in interfaces_inc.load()['symbols']}

    def test_regions_match_rtl_memory_package(self):
        pkg = (ROOT / 'src/nand2mario/rtl/memory/n2m_memory_pkg.sv').read_text()
        rtl = {m[1]: int(m[2], 16) for m in re.finditer(r"localparam logic \[15:0\] (\w+)\s*= 16'h([0-9A-F]+);", pkg)}
        s = self.symbols()
        pairs = {'VRAM_START': 'GB_VRAM_START', 'VRAM_END': 'GB_VRAM_END',
                 'CART_RAM_START': 'GB_CART_RAM_START', 'CART_RAM_END': 'GB_CART_RAM_END',
                 'WRAM_START': 'GB_WRAM_START', 'WRAM_END': 'GB_WRAM_END',
                 'ECHO_START': 'GB_ECHO_START', 'ECHO_END': 'GB_ECHO_END',
                 'OAM_START': 'GB_OAM_START', 'OAM_END': 'GB_OAM_END',
                 'UNUSABLE_START': 'GB_UNUSABLE_START', 'UNUSABLE_END': 'GB_UNUSABLE_END',
                 'IO_START': 'GB_IO_START', 'IO_END': 'GB_IO_END',
                 'HRAM_START': 'GB_HRAM_START', 'HRAM_END': 'GB_HRAM_END', 'IE_ADDRESS': 'GB_IE_START'}
        for rtl_name, sw_name in pairs.items():
            self.assertEqual(s[sw_name], rtl[rtl_name], sw_name)
        self.assertEqual((s['GB_ROM0_START'], s['GB_ROM1_END']), (rtl['ROM_START'], rtl['ROM_END']))

    def test_dmg_register_spot_checks(self):
        s = self.symbols()
        self.assertEqual((s['GB_REG_JOYP'], s['GB_REG_IF'], s['GB_REG_NR52'], s['GB_REG_LCDC'],
                          s['GB_REG_DMA'], s['GB_REG_WX'], s['GB_REG_IE']),
                         (0xFF00, 0xFF0F, 0xFF26, 0xFF40, 0xFF46, 0xFF4B, 0xFFFF))
        self.assertEqual(s['VECTOR_VBLANK'], 0x40)
        self.assertEqual(s['BUTTON_START'], 0x80)
        self.assertNotIn('MBC1_ROM_BYTES', s)
        self.assertFalse([n for n in s if n.startswith(('HOST_', 'COMMAND_', 'WIRE_'))])
        self.assertEqual(s['LIBRARY_CATALOGUE_ADDRESS'], 0x88000)
        self.assertEqual(s['LIBRARY_CATALOGUE_ENTRIES'], 17)
        self.assertEqual(s['LIBRARY_WINDOW_BYTES'], 16384)
        self.assertEqual(s['PROFILE_JOYP_SELECT'], 0x30)

    def test_load_rejects_duplicates_and_bad_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            for symbols in ([{'name': 'A', 'value': 1}, {'name': 'A', 'value': 2}],
                            [{'name': 'lower', 'value': 1}], [{'name': 'A', 'value': -1}],
                            [{'name': 'A', 'value': True}]):
                path = Path(tmp) / 'facts.json'
                path.write_text(json.dumps({'schema_version': 1, 'symbols': symbols}))
                with self.assertRaises(ValueError):
                    interfaces_inc.load(path)


class AssetTests(unittest.TestCase):
    def shades(self, pixels, width=8, height=8):
        return {'schema_version': 1, 'width': width, 'height': height, 'pixels': pixels}

    def test_2bpp_planes_and_tile_order(self):
        rows = [[0, 1, 2, 3, 0, 1, 2, 3] + [3] * 8] + [[0] * 16] * 7
        data = encode_shades(self.shades(rows, width=16))
        self.assertEqual(len(data), 32)
        self.assertEqual(data[0:2], bytes([0b01010101, 0b00110011]))
        self.assertEqual(data[16:18], bytes([0xFF, 0xFF]))

    def test_rejections(self):
        for bad in (dict(schema_version=2, width=8, height=8, pixels=[[0] * 8] * 8),
                    dict(schema_version=1, width=7, height=8, pixels=[[0] * 7] * 8),
                    dict(schema_version=1, width=8, height=8, pixels=[[0] * 8] * 7),
                    dict(schema_version=1, width=8, height=8, pixels=[[4] + [0] * 7] + [[0] * 8] * 7),
                    dict(schema_version=1, width=8, height=8, pixels=[[0] * 8] * 8, extra=1)):
            with self.assertRaises(AssemblyError):
                encode_shades(bad)
        with self.assertRaises(AssemblyError):
            parse_shades(b'{"schema_version": 1, "schema_version": 1}')


class CliTests(unittest.TestCase):
    def setUp(self):
        (ROOT / 'workdir').mkdir(exist_ok=True)
        self.tmp = Path(tempfile.mkdtemp(dir=ROOT / 'workdir'))
        (self.tmp / 'art.json').write_text(json.dumps(
            {'schema_version': 1, 'width': 8, 'height': 8, 'pixels': [[3] * 8] * 8}))
        (self.tmp / 'prog.asm').write_text(
            'SECTION "code", ROM\nstart:\n  ldh a, [GB_REG_LY]\n  halt\nArt:\nASSET "Art"\n')
        (self.tmp / 'layout.json').write_text(json.dumps(
            {'schema_version': 1, 'sections': [{'unit': 'prog', 'section': 'code', 'region': 'ROM0', 'address': 336}]}))

    def build(self, *assets, title='CPU SMOKE'):
        return cpu_sw.build([self.tmp / 'prog.asm'], self.tmp / 'layout.json', 'prog::start',
                            self.tmp / 'out', assets=list(assets), title=title)

    def test_asset_and_title(self):
        plan = self.build(f'Art={self.tmp / "art.json"}', title='ART TEST')
        self.assertEqual(plan['assets'], {'Art': 16})
        image = (self.tmp / 'out/program.gb').read_bytes()
        self.assertEqual(image[0x134:0x13C], b'ART TEST')
        self.assertIn(bytes([0xFF] * 16), image[336:400])

    def test_undeclared_or_malformed_assets_fail(self):
        with self.assertRaises(AssemblyError):
            self.build()
        for bad in ('Art', '1x=a.json', f'Art={self.tmp / "missing.json"}'):
            with self.assertRaises(ValueError):
                self.build(bad)
        with self.assertRaises(ValueError):
            self.build(f'Art={self.tmp / "art.json"}', f'Art={self.tmp / "art.json"}')


if __name__ == '__main__':
    unittest.main()
