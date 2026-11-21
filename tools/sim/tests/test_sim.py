"""The simulation runner's target registry views, without a simulator."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools/sim"))
import sim  # noqa: E402


class TargetViewTests(unittest.TestCase):
    def test_views_fill_defaults_and_freeze_collections(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = Path(tmp) / "src/demo/cfg/targets.json"
            registry.parent.mkdir(parents=True)
            registry.write_text(json.dumps({"targets": {"demo_top": {
                "top": "demo_top", "sources": ["a.sv", "b.sv"], "parameters": {"W": 8}}}}))
            view = sim.target_views(Path(tmp))["demo_top"]
        self.assertEqual(view.name, "demo_top")
        self.assertEqual(view.project, "demo")
        self.assertEqual(view.kind, "cocotb")
        self.assertEqual(view.sources, ("a.sv", "b.sv"))
        self.assertEqual(view.parameters, (("W", 8),))
        self.assertEqual(view.defines, ())
        self.assertIsNone(view.testcase)

    def test_views_cover_every_registered_target(self):
        self.assertEqual(set(sim.target_views()), set(sim.load_targets()))


if __name__ == "__main__":
    unittest.main()
