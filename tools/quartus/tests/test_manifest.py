"""Manifest resolution: board pin maps, pin specs and aliases, board overrides,
profiles, source closures and the refusals of malformed manifests."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.quartus import manifest  # noqa: E402
from tools.quartus.tests import fixture  # noqa: E402


class ManifestTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = fixture.make_checkout(Path(self.tmp.name))

    def load(self, data=None, board=None, profile=None, profile_data=None):
        if data is not None or profile_data is not None:
            fixture.make_checkout(self.root, data, profile_data)
        return manifest.load(self.root, fixture.MANIFEST, board, profile)

    def edited(self, edit):
        data = copy.deepcopy(fixture.MANIFEST_DATA)
        edit(data)
        return data

    def test_pin_specs_expand_lsb_first(self):
        self.assertEqual(manifest.expand("LED[3:0]"), ["LED[0]", "LED[1]", "LED[2]", "LED[3]"])
        self.assertEqual(manifest.expand("KEY[1]"), ["KEY[1]"])
        self.assertEqual(manifest.expand("CLK_50"), ["CLK_50"])
        for bad in ("LED[0:3]", "LED[", "1LED", "LED[a]"):
            with self.assertRaises(ValueError, msg=bad):
                manifest.expand(bad)

    def test_base_board_resolves_pins_aliases_and_standards(self):
        config = self.load()
        self.assertEqual(config["board"], "demo_board")
        self.assertEqual((config["family"], config["device"]), ("MAX 10", "10M50DAF484C7G"))
        self.assertEqual(config["pins"]["LED[2]"], "A10")
        self.assertEqual(config["pins"]["RESET_N"], "B8")
        self.assertEqual(config["pins"]["UART_RX"], "V10")
        self.assertEqual(config["io_standards"]["LED[0]"], "2.5 V")
        self.assertIsNone(config["io_standards"]["RESET_N"])
        self.assertEqual(config["io_standards"]["DRAM_DQ[3]"], "3.3-V LVTTL")
        self.assertEqual(config["sources"], ["src/demo/fpga/demo_clock_intel.sv", "src/demo/fpga/demo_top.sv"])

    def test_board_override_merges_one_level_and_replaces_lists(self):
        config = self.load(board="demo_wide")
        self.assertEqual((config["board"], config["project"], config["top"]), ("demo_wide", "demo_wide", "demo_wide"))
        self.assertEqual(config["device"], "EP4CE115F29C7")
        self.assertEqual(config["timeout_seconds"], 90)
        self.assertIn("src/demo/fpga/demo_wide.sv", config["sources"])
        self.assertNotIn("src/demo/fpga/demo_top.sv", config["sources"])
        self.assertNotIn("RESET_N", config["pins"])
        self.assertEqual(config["io_standards"]["KEY[0]"], "2.5 V")
        self.assertEqual(config["forwarded_clocks"], {"sdram_clock": "DRAM_CLK", "vga_dac_clock": "VGA_CLK"})
        self.assertEqual(config["reviewed_warnings"]["logiclock_count"], 1)
        self.assertEqual(config["reviewed_warnings"]["constant_outputs"], {})

    def test_unknown_boards_and_override_keys_are_refused(self):
        with self.assertRaisesRegex(ValueError, "no board"):
            self.load(board="demo_missing")
        with self.assertRaisesRegex(ValueError, "unsupported board override keys"):
            self.load(self.edited(lambda d: d["boards"]["demo_wide"].update(libraries=[])), board="demo_wide")
        with self.assertRaisesRegex(ValueError, "unknown board"):
            self.load(self.edited(lambda d: d.update(board="demo_none")))

    def test_profile_extends_lists_merges_dicts_and_requires_its_board(self):
        base = self.load()
        config = self.load(profile=fixture.PROFILE)
        self.assertEqual(config["project"], "demo_image_big")
        self.assertEqual(config["parameters"], {"BIG": 1})
        self.assertEqual(len(config["ram_requirements"]), len(base["ram_requirements"]) + 1)
        self.assertEqual(config["reviewed_warnings"]["patterns"][:1], base["reviewed_warnings"]["patterns"])
        self.assertEqual(config["pins"], base["pins"])
        with self.assertRaisesRegex(ValueError, "requires --board demo_board"):
            self.load(board="demo_wide", profile=fixture.PROFILE)
        with self.assertRaisesRegex(ValueError, "a profile holds"):
            self.load(profile=fixture.PROFILE, profile_data={**fixture.PROFILE_DATA, "device": "X"})

    def test_pin_refusals(self):
        cases = {"has no pin": lambda d: d["pins"].append("LED[7]"),
                 "duplicate pin": lambda d: d["pins"].append("LED[0]"),
                 "duplicate physical pin": lambda d: d["pins"].append("SPARE=LED[0]"),
                 "invalid pin spec": lambda d: d["pins"].append("A=B=C"),
                 "reference clock": lambda d: d["pins"].remove("CLK_50")}
        for message, edit in cases.items():
            with self.subTest(message), self.assertRaisesRegex(ValueError, message):
                self.load(self.edited(edit))

    def test_manifest_refusals(self):
        cases = {"needs": lambda d: d.update(family="MAX 10"),
                 "timeout_seconds": lambda d: d.update(timeout_seconds=0),
                 "divide": lambda d: d.update(clock_hz=30_000_000),
                 "operating conditions": lambda d: d.update(timing_corners=["Typical"]),
                 "invalid Verilog macro": lambda d: d.update(verilog_macros=["A B"]),
                 "missing or out-of-tree source": lambda d: d.update(settings="src/demo/fpga/none.qsf"),
                 "invalid source path": lambda d: d.update(constraints="../outside.sdc"),
                 "invalid forwarded clocks": lambda d: d.update(forwarded_clocks={"x": "LED[9]"}),
                 "invalid RAM requirement": lambda d: d["ram_requirements"][0].update(mode="Dual"),
                 "require_clean_source": lambda d: d.update(require_clean_source="yes"),
                 "reviewed_warnings keys": lambda d: d["reviewed_warnings"].update(other=1),
                 "invalid reviewed warning pattern": lambda d: d["reviewed_warnings"]["patterns"].append("(")}
        for message, edit in cases.items():
            with self.subTest(message), self.assertRaisesRegex(ValueError, message):
                self.load(self.edited(edit))

    def test_electrical_contract_must_agree_with_pin_standards(self):
        edit = self.edited(lambda d: d["reviewed_warnings"]["electrical_pins"].update(RESET_N="3.3-V LVTTL"))
        with self.assertRaisesRegex(ValueError, "disagrees with pin assignment: RESET_N"):
            self.load(edit)

    def test_board_pin_map_refusals(self):
        board = json.loads(json.dumps(fixture.BOARDS["demo_board"]))
        board["pins"]["SPARE"] = "P11"
        path = self.root / "src/common/fpga/demo_board/pins.json"
        path.write_text(json.dumps(board))
        with self.assertRaisesRegex(ValueError, "duplicate physical pin"):
            manifest.load_board(self.root, "demo_board")
        board = json.loads(json.dumps(fixture.BOARDS["demo_board"]))
        board["extra"] = 1
        path.write_text(json.dumps(board))
        with self.assertRaisesRegex(ValueError, "keys must be exactly"):
            manifest.load_board(self.root, "demo_board")
        with self.assertRaisesRegex(ValueError, "invalid board"):
            manifest.load_board(self.root, "../demo_board")


if __name__ == "__main__":
    unittest.main()
