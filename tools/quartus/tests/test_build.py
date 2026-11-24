"""Staging and running: the generated project, the timing audit script, output
confinement, the build lock and the Quartus process (status, timeout, identity)."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.quartus import build, manifest  # noqa: E402
from tools.quartus.tests import fixture  # noqa: E402


class BuildTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = fixture.make_checkout(Path(self.tmp.name))
        self.output = self.root / "workdir" / "quartus" / "demo"
        self.config = manifest.load(self.root, fixture.MANIFEST)

    def test_qsf_orders_device_macros_files_parameters_then_pins(self):
        config = manifest.load(self.root, fixture.MANIFEST, profile_path=fixture.PROFILE)
        lines = build.qsf(config).splitlines()
        self.assertEqual(lines[:6], [
            'set_global_assignment -name FAMILY "MAX 10"', "set_global_assignment -name DEVICE 10M50DAF484C7G",
            "set_global_assignment -name TOP_LEVEL_ENTITY demo_top",
            'set_global_assignment -name VERILOG_MACRO "SYNTHESIS=1"',
            'set_global_assignment -name VERILOG_MACRO "DISABLE_ASSERTIONS=1"', 'source "src/demo/fpga/demo.qsf"'])
        self.assertIn("set_parameter -name BIG 1", lines)
        self.assertIn("set_location_assignment PIN_B8 -to RESET_N", lines)
        self.assertNotIn("-to RESET_N", "\n".join(line for line in lines if "IO_STANDARD" in line))
        self.assertIn('set_instance_assignment -name IO_STANDARD "2.5 V" -to LED[3]', lines)
        self.assertLess(lines.index("set_parameter -name BIG 1"), lines.index("set_location_assignment PIN_P11 -to CLK_50"))

    def test_audit_reports_fmax_and_each_path_group_per_corner(self):
        tcl = build.audit_tcl(self.config)
        self.assertTrue(tcl.startswith("project_open demo_image\n"))
        self.assertIn("report_clock_fmax_summary -file output_files/fmax_${label}.rpt", tcl)
        self.assertIn("report_timing -$check -from [get_ports {DRAM_DQ[*]}] -npaths 1 -detail full_path "
                      "-file output_files/sdram_read_${label}_${check}.rpt", tcl)
        self.assertIn("-to_clock [get_clocks sdram_clock]", tcl)
        self.assertEqual(build.corner_label("Slow 1200mV 85C"), "slow_85")
        self.assertEqual(build.corner_label("Fast 1200mV 0C"), "fast_0")

    def test_stage_copies_inputs_and_records_their_hashes(self):
        build.stage(self.root, self.output, self.config)
        inputs = json.loads((self.output / "inputs.json").read_text())
        self.assertEqual(inputs["config"], json.loads(json.dumps(self.config)))
        self.assertEqual(set(inputs["sha256"]), set(build.staged_files(self.config)))
        self.assertEqual((self.output / "src/demo/fpga/demo.sdc").read_text(),
                         (self.root / "src/demo/fpga/demo.sdc").read_text())
        self.assertEqual((self.output / "demo_image.qsf").read_text(), build.qsf(self.config))
        self.assertTrue((self.output / "audit.tcl").is_file())

    def test_output_is_confined_below_workdir_without_links(self):
        with self.assertRaisesRegex(ValueError, "below workdir"):
            build.confined(self.root, self.root / "src")
        (self.root / "elsewhere").mkdir()
        os.symlink(self.root / "elsewhere", self.root / "workdir" / "link")
        with self.assertRaisesRegex(ValueError, "below workdir"):
            build.confined(self.root, self.root / "workdir" / "link" / "x")
        os.symlink(self.root / "workdir" / "quartus", self.root / "workdir" / "inner")
        (self.root / "workdir" / "quartus").mkdir()
        with self.assertRaisesRegex(ValueError, "symlink"):
            build.confined(self.root, self.root / "workdir" / "inner" / "x")

    def test_concurrent_build_is_refused_before_touching_output(self):
        with build.build_lock(self.root, self.output):
            with self.assertRaises(FileExistsError):
                with build.build_lock(self.root, self.output):
                    pass
        self.assertFalse(self.output.with_suffix(".lock").exists())

    def test_run_records_the_child_and_fails_on_status_and_timeout(self):
        self.output.mkdir(parents=True)
        build.run([sys.executable, "-c", "print('ok')"], self.output, "ok.log", 30)
        self.assertEqual((self.output / "ok.log").read_text(), "ok\n")
        owner = json.loads((self.output / "ok.log.owner.json").read_text())["launcher_child"]
        self.assertEqual(owner["timeout_seconds"], 30)
        with self.assertRaisesRegex(RuntimeError, "Quartus exited 3"):
            build.run([sys.executable, "-c", "raise SystemExit(3)"], self.output, "bad.log", 30)
        with self.assertRaises(subprocess.TimeoutExpired):
            build.run([sys.executable, "-c", "import time; time.sleep(30)"], self.output, "slow.log", 1)


if __name__ == "__main__":
    unittest.main()
