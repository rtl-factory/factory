"""Deterministic catalogue bytes, strict profile/slot bounds and donor imports."""
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import zlib

from src.nand2mario.scripts.sw_build import game_library as library
from src.nand2mario.scripts.sw_build.asm import assembler, linker
from src.nand2mario.scripts.sw_build.asm.expressions import AssemblyError
from src.nand2mario.dv.menu import fixture, timing

ROOT = Path(__file__).resolve().parents[5]


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.menu = fixture.game_image(16, 'GAME MENU')
        self.game = fixture.game_image(0, 'GAME')

    def test_catalogue_is_donor_encoding(self):
        banked = fixture.mbc1_image()
        packed, rows = library.pack({0: (1, self.game), 5: (3, banked), 16: (2, self.menu)})
        self.assertEqual(len(packed), 1024 * 1024)
        for slot in (0, 5, 16):
            address = 0x88000 + slot * 32
            self.assertEqual(packed[address:address + 32], fixture.pack_entry(rows[slot]))
            self.assertEqual(rows[slot]['crc32'], zlib.crc32(packed[slot * 32768:slot * 32768 + rows[slot]['length']]))
        self.assertEqual(packed[5 * 32768:7 * 32768], banked)
        self.assertEqual(packed[0x880c0:0x880e0], bytes(32))  # second half has no separate entry
        self.assertEqual(packed[0x88220:0x88400], bytes(480))
        self.assertEqual(rows[0]['title'], self.game[0x134:0x144])
        self.assertEqual(library.pack({16: (2, self.menu), 5: (3, banked), 0: (1, self.game)})[0], packed)

    def test_malformed_images_and_overlapping_slots_fail(self):
        cases = [
            {}, {16: (1, self.menu)}, {16: (2, b'')}, {16: (2, self.menu), 17: (1, self.game)},
            {16: (2, self.menu), 0: (0, self.game)}, {16: (2, self.menu), 0: (3, self.game)},
            {16: (2, self.menu), 15: (3, fixture.mbc1_image())},
            {16: (2, self.menu), 5: (3, fixture.mbc1_image()), 6: (1, self.game)},
        ]
        for case in cases:
            with self.subTest(slots=list(case)), self.assertRaises(ValueError):
                library.pack(case)

    def test_imported_assembly_assets_match_source_hashes(self):
        manifest = json.loads((ROOT / 'src/nand2mario/cfg/sw-import.json').read_text())
        rows = [r for r in manifest['files'] if r['source'].startswith('src/sw/menu/')]
        self.assertEqual(len(rows), 8)
        for row in rows:
            if row['action'] == 'owner-copy: unchanged':
                self.assertEqual(hashlib.sha256((ROOT / row['destination']).read_bytes()).hexdigest(), row['sha256'])

    def test_native_copy_safety_bounds(self):
        self.assertEqual(timing.BYTE_CLOCKS, {'onchip': 2, 'sdram': 16})
        self.assertEqual(timing.frame_wait_ns('onchip'), 5 * timing.FRAME_NS)
        self.assertAlmostEqual(timing.frame_wait_ns('sdram') - timing.frame_wait_ns('onchip'),
                               73400320)
        self.assertEqual(timing.reset_wait_ns('onchip'), (65568 * 2 + 8192) * 40 + 4000)
        self.assertEqual(timing.reset_wait_ns('sdram'), (65568 * 16 + 8192) * 40 + 4000)
        self.assertLess(timing.reset_wait_ns('sdram'), timing.frame_wait_ns('sdram'))

    def test_regression_adapter_routes_workdir(self):
        with patch.object(library, 'simulate', return_value=0) as simulate:
            self.assertEqual(library.main(['--workdir', 'workdir/build', 'run', '--output', 'workdir/out']), 0)
        simulate.assert_called_once_with('workdir/out', 'workdir/build', 'onchip')

    def test_loader_profile_rejects_rom1_placement(self):
        source = ROOT / 'src/nand2mario/sw/games/menu/main.asm'
        from src.nand2mario.scripts.sw_build import cpu_sw
        from src.nand2mario.scripts.sw_build import sw_games
        spec = sw_games.load()['menu']
        assets = cpu_sw.parse_assets([f"{name}={source.parent / row['source']}" for name, row in spec['assets'].items()])
        obj = assembler.assemble(source, ROOT, ROOT / 'src/nand2mario/sw/cpu/interfaces.inc', assets)
        layout = json.loads(source.with_name('layout.json').read_text())
        layout['sections'][0].update(region='ROM1', address=16384)
        with self.assertRaises(AssemblyError) as raised:
            linker.link([('main', obj)], layout, {'unit': 'main', 'symbol': 'Start'}, 'dmg-loader-v1')
        self.assertEqual(raised.exception.diagnostic['code'], 'LAYOUT_REGION')


if __name__ == '__main__':
    unittest.main()
