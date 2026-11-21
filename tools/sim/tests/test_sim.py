"""The simulation runner's target registry views, without a simulator."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import unittest.mock

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


class BazelCommandTests(unittest.TestCase):
    def test_prune_model_keeps_only_the_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            build = Path(tmp)
            (build / "demo_top").write_text("binary")
            (build / "Vtop.cpp").write_text("source")
            (build / "obj").mkdir()
            (build / "obj" / "x.o").write_text("object")
            sim.prune_model("demo", {"top": "demo_top"}, build)
            self.assertEqual(["demo_top"], sorted(p.name for p in build.iterdir()))

    def test_native_model_binary_is_named_after_the_target(self):
        self.assertEqual(Path("b/demo_sim"), sim.model_binary("demo", {"kind": "native", "top": "t"}, Path("b")))

    def test_prepend_tool_paths_puts_the_files_folders_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            tool = Path(tmp) / "bin" / "verilator"
            tool.parent.mkdir()
            tool.write_text("")
            with unittest.mock.patch.dict("os.environ", {"SIM_PATH_PREPEND": str(tool), "PATH": "/usr/bin"}):
                sim.prepend_tool_paths()
                self.assertEqual([str(tool.parent), "/usr/bin"], os.environ["PATH"].split(os.pathsep))


if __name__ == "__main__":
    unittest.main()
