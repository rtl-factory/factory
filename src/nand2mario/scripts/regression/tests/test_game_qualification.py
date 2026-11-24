"""Reject changed downloads, incompatible headers and unsafe output paths."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.nand2mario.scripts.regression import game_qualification as game
from tools.bazel import scratch


def checksums(data):
    data[0x14d] = (-sum(data[0x134:0x14d]) - 25) & 255
    data[0x14e:0x150] = b'\0\0'
    data[0x14e:0x150] = (sum(data) & 65535).to_bytes(2, 'big')
    return data


def pin(data):
    return dict(size=len(data), sha256=hashlib.sha256(data).hexdigest())


class GameQualificationTests(unittest.TestCase):
    def setUp(self):
        # Why: every output is confined below the module ROOT's workdir/, so the test
        # points ROOT at a scratch checkout.
        self.enterContext(patch.object(game, 'ROOT', scratch.checkout(self)))

    def test_image_admission(self):
        image = checksums(bytearray(32768))
        game.check_image(image, pin(image))
        original = pin(image)
        image[1000] ^= 1
        with self.assertRaisesRegex(ValueError, 'SHA256'):
            game.check_image(image, original)
        with self.assertRaisesRegex(ValueError, 'global checksum'):
            game.check_image(image, pin(image))

    def test_header_contract_independently_of_digest(self):
        for offset, value, error in ((0x147, 1, 'ROM ONLY'), (0x148, 1, 'ROM ONLY'),
                                     (0x149, 1, 'ROM ONLY'), (0x143, 0xC0, 'DMG')):
            image = bytearray(32768)
            image[offset] = value
            checksums(image)
            with self.subTest(offset=offset), self.assertRaisesRegex(ValueError, error):
                game.check_image(image, pin(image))
        image = checksums(bytearray(32768))
        image[0x134] = 1
        with self.assertRaisesRegex(ValueError, 'header checksum'):
            game.check_image(image, pin(image))

    def test_fetch_rejects_bad_notice_before_binary(self):
        with tempfile.TemporaryDirectory(dir=game.ROOT / 'workdir') as temporary:
            output = Path(temporary) / 'assets'
            license_pin = dict(pin(b'license'), url='https://example.invalid/LICENSE')
            manifest = {'games': {'fixture': dict(notices={'LICENSE': license_pin})}}
            with patch.object(game, 'manifest', return_value=manifest), \
                    patch.object(game.urllib.request, 'urlopen', return_value=io.BytesIO(b'bad')) as fetch:
                with self.assertRaisesRegex(ValueError, 'SHA256'):
                    game.acquire('fixture', output)
                self.assertEqual(1, fetch.call_count)
                self.assertEqual([], list(output.iterdir()))

    def test_output_boundary(self):
        with self.assertRaises(ValueError):
            game.generated(game.ROOT / 'src' / 'output')
        with tempfile.TemporaryDirectory(dir=game.ROOT / 'workdir') as temporary:
            link = Path(temporary) / 'link'
            link.symlink_to(game.ROOT / 'workdir', target_is_directory=True)
            with self.assertRaises(ValueError):
                game.generated(link / 'output')

    def test_suite_preserves_failures_and_separates_game_evidence(self):
        with tempfile.TemporaryDirectory(dir=game.ROOT / 'workdir') as temporary:
            output = Path(temporary) / 'suite'

            def simulate(name, image, workdir):
                test = workdir / 'cocotb/n2m_system_games/test'
                self.assertFalse(test.exists(), 'prior game evidence leaked into next test')
                test.mkdir(parents=True)
                (test / 'game-result.json').write_text(json.dumps({'game': name}))
                return int(name == 'max-pirate')

            # Why: the scratch checkout is no git repository, so the source commit is stubbed.
            with patch.object(game, 'acquire', return_value=Path(temporary) / 'game.gb'), \
                    patch.object(game.subprocess, 'check_output', return_value='0' * 40), \
                    patch.object(game, 'run_one', side_effect=simulate) as execute:
                self.assertEqual(1, game.run('all', None, output))
                self.assertEqual(len(game.manifest()['games']), execute.call_count)
            summary = json.loads((output / 'summary.json').read_text())
            self.assertEqual(1, summary['games']['max-pirate']['exit_status'])
            for name in game.manifest()['games']:
                evidence = json.loads((output / 'results' / name / 'game-result.json').read_text())
                self.assertEqual(name, evidence['game'])
            with self.assertRaisesRegex(ValueError, 'fresh'):
                game.run('all', None, output)


if __name__ == '__main__':
    unittest.main()
