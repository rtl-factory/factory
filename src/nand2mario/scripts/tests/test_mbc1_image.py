"""The MBC1 image expander: bank replication, per-bank identity and pattern,
header rewrite with valid checksums, and input validation."""
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.nand2mario.scripts import mbc1_image as expander
from src.nand2mario.scripts.sw.package import validate_image


def direct_image():
    """A fake direct image: $FF everywhere, a distinct bank-1 template and
    the bank id 1 at $7FFE, with a header the packager would accept."""
    image = bytearray([0xFF] * expander.DIRECT_BYTES)
    image[0x150:0x160] = range(16)
    image[0x4000:0x4010] = range(0x40, 0x50)
    image[0x7FFE] = 1
    image[0x100:0x150] = bytes(0x50)
    image[0x100:0x104] = bytes([0, 0xC3, 0x50, 0x01])
    image[0x134:0x144] = b'MBC1 TEST'.ljust(16, b'\0')
    image[0x14A] = 1
    image[0x14C] = 1
    image[0x14D] = (-sum(image[0x134:0x14D]) - 25) & 0xFF
    image[0x14E:0x150] = (sum(image) & 0xFFFF).to_bytes(2, 'big')
    return bytes(image)


class ExpanderTests(unittest.TestCase):
    def test_banks_header_and_checksums(self):
        image = expander.expand(direct_image(), 64)
        self.assertEqual(len(image), 64 * expander.BANK_BYTES)
        self.assertEqual(image[:0x4000], direct_image()[:0x4000][:0x147] + bytes([3, 5, 3]) + image[0x14A:0x4000])
        self.assertEqual(image[0x147:0x14A], bytes([0x03, 0x05, 0x03]))
        for bank in (1, 2, 33, 63):
            base = bank * expander.BANK_BYTES
            with self.subTest(bank=bank):
                self.assertEqual(image[base:base + 16], bytes(range(0x40, 0x50)), 'template code replicated')
                self.assertEqual(image[base + 0x3FFE], bank)
                self.assertEqual(image[base + 0x3FFF], 0xFF)
                self.assertEqual(image[base + 0x3F80], expander.bank_pattern(bank, 0x3F80))
                self.assertEqual(image[base + 0x3F00], (bank * 0x25) & 0xFF)
        self.assertEqual(image[0x14D], (-sum(image[0x134:0x14D]) - 25) & 0xFF)
        self.assertEqual(int.from_bytes(image[0x14E:0x150], 'big'), (sum(image[:0x14E]) + sum(image[0x150:])) & 0xFFFF)
        # The packager's strict validator rejects the direct profile size but
        # agrees on both checksums once the header is restored to its shape.
        with self.assertRaises(Exception):
            validate_image(image, 0x150, 'MBC1 TEST', 1)

    def test_sizes(self):
        for banks, code in ((2, 0), (4, 1), (16, 3), (128, 6)):
            with self.subTest(banks=banks):
                image = expander.expand(direct_image(), banks)
                self.assertEqual((len(image), image[0x148]), (banks * expander.BANK_BYTES, code))
        for banks in (1, 3, 256):
            with self.assertRaises(ValueError):
                expander.expand(direct_image(), banks)

    def test_rejects_bad_template(self):
        with self.assertRaises(ValueError):
            expander.expand(direct_image()[:100], 64)
        dirty = bytearray(direct_image())
        dirty[0x7F80] = 0
        with self.assertRaises(ValueError):
            expander.expand(bytes(dirty), 64)
        dirty = bytearray(direct_image())
        dirty[0x7FFE] = 2
        with self.assertRaises(ValueError):
            expander.expand(bytes(dirty), 64)

    def test_cli_confinement(self):
        with tempfile.TemporaryDirectory() as name, patch.object(expander, 'ROOT', Path(name)):
            workdir = Path(name) / 'workdir'
            workdir.mkdir()
            (workdir / 'program.gb').write_bytes(direct_image())
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(expander.main(['--input', str(workdir / 'program.gb'),
                                                '--output', str(workdir / 'out/cartridge.gb'), '--banks', '8']), 0)
            self.assertIn('8 banks', out.getvalue())
            self.assertEqual((workdir / 'out/cartridge.gb').stat().st_size, 8 * expander.BANK_BYTES)
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(expander.main(['--input', str(workdir / 'program.gb'), '--output', '/tmp/escape.gb']), 2)
                self.assertEqual(expander.main(['--input', str(workdir / 'missing.gb'), '--output', str(workdir / 'x.gb')]), 2)


if __name__ == '__main__':
    unittest.main()
