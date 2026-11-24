"""Original games build reproducibly from committed source with recorded hashes."""
import hashlib
import json
import unittest
from unittest.mock import patch
from pathlib import Path

from src.nand2mario.scripts.sw_build import cpu_sw, sw_games
from tools.bazel import scratch

ROOT = Path(__file__).resolve().parents[5]


class GamesTests(unittest.TestCase):
    def test_each_game_matches_recorded_sha256(self):
        games = sw_games.load()
        self.assertEqual(sorted(games), ['menu', 'springtrail', 'stackdrop'])
        # Why: the game sources and the output are confined to the module ROOTs.
        root = scratch.checkout(self)
        with patch.object(sw_games, 'ROOT', root), patch.object(cpu_sw, 'ROOT', root):
            for name, game in games.items():
                sw_games.build(name, root / 'workdir' / name)
                image = (root / 'workdir' / name / 'program.gb').read_bytes()
                self.assertEqual(hashlib.sha256(image).hexdigest(), game['image_sha256'], name)
                self.assertEqual(image[0x134:0x134 + len(game['title'])], game['title'].encode())

    def test_every_ported_file_has_a_manifest_entry(self):
        manifest = json.loads((ROOT / 'src/nand2mario/cfg/sw-import.json').read_text())
        listed = {item['destination'] for item in manifest['files']}
        ported = {str(p.relative_to(ROOT)) for p in (ROOT / 'src/nand2mario/sw/games').rglob('*') if p.is_file()}
        self.assertEqual(ported - listed, set())

    def test_scenarios_press_start_and_right(self):
        for name, game in sw_games.load().items():
            if name == 'menu':
                continue  # Why: the library scenario owns navigation and selection.
            labels = {label: mask for label, mask, _ in game['scenario']}
            self.assertEqual((labels['start'], labels['right']), (0x80, 0x01), name)

    def test_named_games_run_once_without_duplicate_menu(self):
        with patch.object(sw_games, 'simulate', return_value=0) as simulate:
            self.assertEqual(sw_games.main(['run', 'springtrail', 'stackdrop', '--output', 'workdir/test']), 0)
        self.assertEqual([call.args[0] for call in simulate.call_args_list], ['springtrail', 'stackdrop'])
        with patch.object(sw_games, 'simulate', return_value=0) as simulate:
            self.assertEqual(sw_games.main(['run', 'all', '--output', 'workdir/test']), 0)
        self.assertEqual([call.args[0] for call in simulate.call_args_list], ['menu', 'springtrail', 'stackdrop'])

    def test_unknown_game_is_rejected(self):
        with self.assertRaises(ValueError):
            sw_games.build('unknown', ROOT / 'workdir/never', dry_run=True)


if __name__ == '__main__':
    unittest.main()
