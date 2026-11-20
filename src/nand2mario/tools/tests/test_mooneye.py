"""Mooneye pin validation, archive verification and verdict decoding."""
import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from src.nand2mario.tools import mooneye
from src.nand2mario.dv.conformance.test_mooneye import verdict


def digest(data):
    return hashlib.sha256(data).hexdigest()


class PinTests(unittest.TestCase):
    def test_committed_pins_are_valid(self):
        pins = mooneye.load()
        self.assertEqual(pins['release']['commit'][:7], '31510e1')
        roms = [i['rom'] for i in pins['selected']]
        self.assertIn('acceptance/oam_dma_start.gb', roms)
        self.assertFalse([r for r in roms if r.endswith(('-S.gb', '-dmg0.gb', '-mgb.gb', '-sgb.gb', '-sgb2.gb'))])

    def write(self, pins):
        path = Path(tempfile.mkdtemp()) / 'pins.json'
        path.write_text(json.dumps(pins))
        return path

    def test_rejects_bad_pins(self):
        base = {'schema_version': 1, 'excluded': []}
        for selected in ([{'rom': 'acceptance/a.gb', 'sha256': 'x' * 64}] * 2,
                         [{'rom': '../a.gb', 'sha256': 'x' * 64}],
                         [{'rom': 'acceptance/a.gb', 'sha256': 'short'}]):
            with self.assertRaises(ValueError):
                mooneye.load(self.write(dict(base, selected=selected)))
        with self.assertRaises(ValueError):
            mooneye.load(self.write(dict(base, selected=[{'rom': 'acceptance/a.gb', 'sha256': 'x' * 64}],
                                         excluded=[{'rom': 'acceptance/a.gb', 'reason': 'r'}])))


class ArchiveTests(unittest.TestCase):
    def archive(self, rom=b'\x00' * 16, licence=b'MIT'):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as zf:
            zf.writestr('rel/LICENSE', licence)
            zf.writestr('rel/acceptance/a.gb', rom)
        return buffer.getvalue()

    def pins(self, archive, rom=b'\x00' * 16, licence=b'MIT'):
        return {'release': {'name': 'rel', 'sha256': digest(archive), 'license_member': 'rel/LICENSE',
                            'license_sha256': digest(licence)},
                'selected': [{'rom': 'acceptance/a.gb', 'sha256': digest(rom)}]}

    def test_extracts_verified_roms(self):
        archive = self.archive()
        self.assertEqual(mooneye.extract(archive, self.pins(archive)), {'acceptance/a.gb': b'\x00' * 16})

    def test_rejects_changed_archive_licence_or_rom(self):
        archive = self.archive()
        pins = self.pins(archive)
        with self.assertRaises(ValueError):
            mooneye.extract(archive + b'x', pins)
        pins['selected'][0]['sha256'] = digest(b'other')
        with self.assertRaises(ValueError):
            mooneye.extract(archive, pins)
        pins = self.pins(archive)
        pins['release']['license_sha256'] = digest(b'GPL')
        with self.assertRaises(ValueError):
            mooneye.extract(archive, pins)

    def test_output_must_be_below_workdir(self):
        for path in ('/tmp/x', mooneye.ROOT / 'workdir'):
            with self.assertRaises(ValueError):
                mooneye.generated(path)


class VerdictTests(unittest.TestCase):
    def test_breakpoint_registers(self):
        self.assertEqual(verdict([3, 5, 8, 13, 21, 34]), 'PASS')
        self.assertEqual(verdict([0x42] * 6), 'FAIL')
        self.assertEqual(verdict([3, 5, 8, 13, 21, 0]), 'UNKNOWN')

    def test_table_counts(self):
        text = mooneye.table({'acceptance/a.gb': {'verdict': 'PASS', 'cycles': 1},
                              'acceptance/b.gb': {'verdict': 'TIMEOUT', 'cycles': 2}})
        self.assertIn('| `a.gb` | PASS | 1 |', text)
        self.assertIn('PASS 1, TIMEOUT 1', text)

    def test_known_failures_pin_the_passing_set(self):
        pins = {'known_failures': [{'rom': 'acceptance/b.gb', 'issue': 1}]}
        results = {'acceptance/a.gb': {'verdict': 'PASS'}, 'acceptance/b.gb': {'verdict': 'FAIL'},
                   'acceptance/c.gb': {'verdict': 'TIMEOUT'}}
        self.assertEqual(mooneye.regressions(results, pins), ['acceptance/c.gb'])
        self.assertEqual(mooneye.regressions({'acceptance/b.gb': {'verdict': 'TIMEOUT'}}, pins), [])

    def test_committed_known_failures_are_selected_and_owned(self):
        pins = mooneye.load()
        selected = {i['rom'] for i in pins['selected']}
        self.assertEqual(len(pins['known_failures']), 5)
        self.assertNotIn(497, {i['issue'] for i in pins['known_failures']})
        self.assertTrue(all(i['rom'] in selected and i['issue'] > 0 for i in pins['known_failures']))


if __name__ == '__main__':
    unittest.main()


class ProtocolRomTest(unittest.TestCase):
    def test_protocol_rom_layout(self):
        image = mooneye.protocol_rom((3, 5, 8, 13, 21, 34))
        self.assertEqual(len(image), 32768)
        self.assertEqual(image[0x100:0x104], bytes([0x00, 0xC3, 0x50, 0x01]))
        self.assertEqual(image[0x150:0x15F], bytes([6, 3, 0x0E, 5, 0x16, 8, 0x1E, 13, 0x26, 21, 0x2E, 34, 0x40, 0x18, 0xFE]))

