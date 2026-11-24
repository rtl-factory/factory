"""The command line: plan, explicit skip without Quartus, a full compile with a
stand-in Quartus, the clean-source rule, verify-only and elaboration errors."""
from contextlib import redirect_stderr, redirect_stdout
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.quartus import build, image, manifest, quartus  # noqa: E402
from tools.quartus.tests import fixture  # noqa: E402

SOURCE = {"source_sha": "b" * 40, "source_dirty": True, "quartus_version": "Version 25.1.0 Lite Edition"}


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = fixture.make_checkout(Path(self.tmp.name))
        self.calls = []

    def main(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            status = quartus.main(["--manifest", fixture.MANIFEST, *args], root=self.root)
        return status, out.getvalue(), err.getvalue()

    def fake_run(self, command, output, log, timeout):
        """Stand in for Quartus: the compile leaves a passing build's reports."""
        self.calls.append((Path(command[0]).name, log, timeout))
        if log == "compile.log":
            config = json.loads((output / "inputs.json").read_text())["config"]
            fixture.write_build(self.root, output, config)
        elif log == "elaborate.log":
            (output / log).write_text("Error (10161): Verilog HDL error at demo_top.sv(3): object \"x\" is not declared\n")
            raise RuntimeError("Quartus exited 2")

    def test_plan_writes_nothing_and_needs_no_quartus(self):
        status, out, _ = self.main("--plan", "--board", "demo_wide", "--quartus", "/missing/quartus_sh")
        self.assertEqual(status, 0)
        self.assertIn("demo_wide demo_wide: EP4CE115F29C7, 25000000 Hz", out)
        self.assertEqual(list((self.root / "workdir").iterdir()), [])

    def test_elaboration_skips_explicitly_without_quartus(self):
        status, _, err = self.main("--elaborate-only", "--quartus", "/missing/quartus_sh")
        self.assertEqual(status, quartus.SKIPPED)
        self.assertIn("SKIPPED", err)
        status, _, err = self.main("--quartus", "/missing/quartus_sh")
        self.assertEqual(status, 1)
        self.assertIn("Quartus not found", err)

    def test_elaboration_failure_reports_the_quartus_error_lines(self):
        with mock.patch.object(build, "run", self.fake_run):
            status, _, err = self.main("--elaborate-only", "--quartus", sys.executable)
        self.assertEqual(status, 1)
        self.assertIn('Error (10161): Verilog HDL error at demo_top.sv(3): object "x" is not declared', err)
        self.assertEqual(self.calls, [("quartus_map", "elaborate.log", 60)])

    def test_full_compile_gates_and_binds_the_image(self):
        with mock.patch.object(build, "run", self.fake_run), \
                mock.patch.object(build, "source_identity", return_value=SOURCE):
            status, out, err = self.main("--quartus", sys.executable)
        self.assertEqual((status, err), (0, ""))
        self.assertIn("Full hardware gate passed", out)
        self.assertIn("M9Ks 6/182, minimum slack 0.125 ns", out)
        self.assertEqual([call[1] for call in self.calls], ["compile.log", "audit.log"])
        self.assertEqual(self.calls[1][0], "quartus_sta")
        output = self.root / "workdir" / "quartus" / "demo_image"
        config = manifest.load(self.root, fixture.MANIFEST)
        identity = image.verify_image(output / "output_files" / "demo_image.sof", config)
        self.assertEqual(identity["source_sha"], "b" * 40)
        self.assertFalse(output.with_suffix(".lock").exists())

        status, out, _ = self.main("--verify-only")
        self.assertEqual(status, 0)
        (output / "src/demo/fpga/demo.sdc").write_text("changed\n")
        status, _, err = self.main("--verify-only")
        self.assertEqual(status, 1)
        self.assertIn("staged input changed after compilation", err)

    def test_profile_reaches_the_build_and_the_board_timeout_reaches_each_tool(self):
        with mock.patch.object(build, "run", self.fake_run), \
                mock.patch.object(build, "source_identity", return_value=SOURCE):
            status, _, err = self.main("--profile", fixture.PROFILE, "--quartus", sys.executable)
            self.assertEqual((status, err), (0, ""))
            self.assertTrue((self.root / "workdir/quartus/demo_image_big/output_files/demo_image_big.json").is_file())
            self.calls.clear()
            status, _, _ = self.main("--board", "demo_wide", "--quartus", sys.executable)
        self.assertEqual(status, 0)
        self.assertEqual([call[2] for call in self.calls], [90, 90])

    def test_clean_source_is_required_when_the_manifest_says_so(self):
        data = copy.deepcopy(fixture.MANIFEST_DATA)
        data["require_clean_source"] = True
        fixture.make_checkout(self.root, data)
        with mock.patch.object(build, "run", self.fake_run), \
                mock.patch.object(build, "source_identity", return_value=SOURCE):
            status, _, err = self.main("--quartus", sys.executable)
        self.assertEqual(status, 1)
        self.assertIn("require_clean_source", err)
        self.assertEqual(self.calls, [])

    def test_output_outside_workdir_is_refused(self):
        status, _, err = self.main("--plan", "--output", str(self.root / "src"))
        self.assertEqual(status, 1)
        self.assertIn("below workdir", err)


if __name__ == "__main__":
    unittest.main()
