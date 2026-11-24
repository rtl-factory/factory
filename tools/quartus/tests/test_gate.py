"""Report gate: a passing synthetic build passes on every board and profile, and
each obligation fails alone when its report is missing, changed or extended."""
import copy
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.quartus import gate, manifest  # noqa: E402
from tools.quartus.tests import fixture  # noqa: E402


class GateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = fixture.make_checkout(Path(self.tmp.name))
        self.output = self.root / "workdir" / "quartus" / "demo"

    def built(self, board=None, profile=None):
        config = manifest.load(self.root, fixture.MANIFEST, board, profile)
        reports = fixture.write_build(self.root, self.output, config)
        return config, reports

    def edit(self, path: Path, old: str, new: str, count: int = 1):
        text = path.read_text()
        self.assertIn(old, text)
        path.write_text(text.replace(old, new, count))

    def test_passing_build_passes_on_every_board_and_profile(self):
        for board, profile in ((None, None), ("demo_wide", None), (None, fixture.PROFILE)):
            with self.subTest(board=board, profile=profile):
                config, _ = self.built(board, profile)
                result = gate.verify_reports(self.output, config)
                evidence = json.loads((self.output / "evidence.json").read_text())
                self.assertEqual(evidence, json.loads(json.dumps(result)))
                self.assertEqual((evidence["board"], evidence["project"]), (config["board"], config["project"]))
                self.assertEqual(evidence["minimum_slack_ns"], 0.125)
                self.assertEqual(set(evidence["timing_paths"]), set(config["timing_paths"]))
                self.assertEqual(set(evidence["fmax"]), set(config["timing_corners"]))
                gate.check_evidence(evidence, config)

    def test_each_report_obligation_fails_alone(self):
        config, reports = self.built()
        project = config["project"]
        fit, sta, summary = (reports / f"{project}.{name}" for name in ("fit.rpt", "sta.rpt", "sta.summary"))
        cases = [
            ("missing Quartus artifact: sof", lambda: (reports / f"{project}.sof").unlink()),
            ("missing timing audit: clocks.tsv", lambda: (reports / "clocks.tsv").unlink()),
            ("failing all-corner timing", lambda: self.edit(summary, "Slack : 0.125", "Slack : -0.010")),
            ("failing all-corner timing", lambda: self.edit(summary, "TNS : 0.000", "TNS : -1.000")),
            ("incomplete timing summary", lambda: summary.write_text(re.sub(
                r"Type : [^\n]*Removal[^\n]*\nSlack : [^\n]*\nTNS : [^\n]*\n\n?", "", summary.read_text()))),
            ("unreviewed check_timing", lambda: self.edit(reports / "check_timing.rpt", "; loops ; 0 ;", "; loops ; 1 ;")),
            ("ignored or incomplete SDC", lambda: self.edit(reports / "ignored.rpt", "No constraints were ignored.",
                                                            "1 constraint was ignored.")),
            ("timing audit failed: Unconstrained Input Port Paths", lambda: self.edit(
                reports / "unconstrained.rpt", "; Unconstrained Input Port Paths ; 0 ; 0 ;",
                "; Unconstrained Input Port Paths ; 2 ; 0 ;")),
            ("audit clocks differ", lambda: self.edit(reports / "clocks.tsv", "jtag_tck\t100", "jtag_tck\t50")),
            ("missing or unexpected fitted clocks", lambda: self.edit(
                sta, "; sdram_clock ;", "; other_clock ;")),
            ("restricted Fmax below 25 MHz", lambda: self.edit(
                reports / "fmax_slow_85.rpt", "; 27.5 MHz ; 27.5 MHz", "; 27.5 MHz ; 24.0 MHz")),
            ("missing Fmax report", lambda: (reports / "fmax_fast_0.rpt").unlink()),
            ("missing timing path report", lambda: (reports / "sdram_read_slow_85_hold.rpt").unlink()),
            ("unexpected timing path endpoints", lambda: self.edit(
                reports / "sdram_read_fast_0_setup.rpt", "read_data[2]", "read_data[4]")),
            ("unexpected timing path endpoints", lambda: self.edit(
                reports / "sdram_write_fast_0_setup.rpt", "; DRAM_ADDR[1] ;", "; DRAM_CLK ;")),
            ("failing or inconsistent timing margin", lambda: self.edit(
                reports / "sdram_write_slow_85_hold.rpt", "3.870", "-0.100", 3)),
            ("missing fitted resource: Total PLLs", lambda: self.edit(fit, "; Total PLLs ; 1 / 4 ;", "")),
            ("does not account for every M9K", lambda: self.edit(fit, "; M9Ks ; 6 / 182 ;", "; M9Ks ; 7 / 182 ;")),
            ("fitted RAM geometry mismatch", lambda: self.edit(fit, "Simple Dual Port ; Single Clock ; 256 ; 8",
                                                               "Simple Dual Port ; Single Clock ; 256 ; 16")),
            ("fitted RAM mode mismatch", lambda: self.edit(fit, "True Dual Port ; Single Clock",
                                                           "True Dual Port ; Dual Clock")),
            ("expected 1 fitted RAM", lambda: self.edit(fit, "n2a_tdp_ram:u_palette", "n2a_tdp_ram:u_other")),
            ("unreviewed Quartus warnings", lambda: self.edit(
                self.output / "compile.log", fixture.TRUNCATION, fixture.TRUNCATION + "\nWarning (99999): new")),
            ("unreviewed Quartus warnings", lambda: (self.output / "audit.log").write_text("Critical Warning (1): x\n")),
            ("occurrence mismatch: 292013", lambda: self.edit(self.output / "compile.log", fixture.LICENSE + "\n", "")),
            ("electrical advisory", lambda: self.edit(fit, "Pin UART_RX uses I/O standard 3.3-V LVTTL",
                                                      "Pin UART_RX uses I/O standard 2.5 V")),
            ("connectivity", lambda: self.edit(reports / f"{project}.map.rpt", "; tms ; Output", "; tdi ; Output")),
            ("staged input changed after compilation", lambda: self.edit(
                self.output / "src/demo/fpga/demo.sdc", "20.000", "10.000")),
        ]
        for message, corrupt in cases:
            with self.subTest(message):
                fixture.write_build(self.root, self.output, config)
                (self.output / "audit.log").write_text("")
                corrupt()
                with self.assertRaisesRegex(RuntimeError, re.escape(message)):
                    gate.verify_reports(self.output, config)

    def test_jtag_clock_is_required_when_the_manifest_sets_it(self):
        config, reports = self.built()
        self.assertTrue({(corner, kind, "jtag_tck") for corner in config["timing_corners"]
                         for kind in ("Setup", "Hold", "Minimum Pulse Width")}
                        <= gate.required_timing(config, fixture.SYSTEM_CLOCK, {}))
        sta = reports / f"{config['project']}.sta.rpt"
        sta.write_text("".join(line for line in sta.read_text().splitlines(keepends=True)
                               if not line.startswith("; jtag_tck ;")))
        self.edit(reports / "clocks.tsv", "jtag_tck\t100\n", "")
        with self.assertRaisesRegex(RuntimeError, "missing or unexpected fitted clocks"):
            gate.verify_reports(self.output, config)

    def test_stale_reports_of_another_config_are_refused(self):
        config, _ = self.built()
        changed = copy.deepcopy(config)
        changed["parameters"] = {"BIG": 2}
        with self.assertRaisesRegex(RuntimeError, "fresh compile required"):
            gate.verify_reports(self.output, changed)

    def test_exact_count_patterns_require_their_count(self):
        config, _ = self.built(profile=fixture.PROFILE)
        gate.verify_reports(self.output, config)
        self.edit(self.output / "compile.log", fixture.SWEPT, fixture.SWEPT + "\n" + fixture.SWEPT)
        with self.assertRaisesRegex(RuntimeError, "occurrence mismatch: 14284"):
            gate.verify_reports(self.output, config)

    def test_a_line_matching_two_rules_is_ambiguous(self):
        config, _ = self.built()
        config["reviewed_warnings"]["patterns"].append(r"Warning \(10230\): .*")
        fixture.write_build(self.root, self.output, config)
        with self.assertRaisesRegex(RuntimeError, "ambiguous reviewed warning"):
            gate.verify_reports(self.output, config)

    def test_forwarded_clock_of_the_wide_board_is_required(self):
        config, reports = self.built("demo_wide")
        self.edit(reports / f'{config["project"]}.sta.rpt', "; vga_dac_clock ;", "; vga_clock ;")
        with self.assertRaisesRegex(RuntimeError, "fitted clocks"):
            gate.verify_reports(self.output, config)

    def test_evidence_check_rejects_tampering(self):
        config, _ = self.built()
        evidence = json.loads(json.dumps(gate.verify_reports(self.output, config)))
        edits = {
            "fields": lambda e: e.pop("fmax"),
            "board identity": lambda e: e.update(board="demo_wide"),
            "negative": lambda e: e["timing"][0].update(slack_ns=-0.5),
            "timing minimum": lambda e: e.update(minimum_slack_ns=1.0),
            "Fmax": lambda e: e["fmax"]["Slow 1200mV 85C"].update(restricted_fmax_mhz=20.0),
            "resource capacity": lambda e: e["resources"]["M9Ks"].update(used=7),
            "missing sdram_read paths": lambda e: e["timing_paths"]["sdram_read"]["paths"].pop(),
            "path endpoints": lambda e: e["timing_paths"]["sdram_write"]["paths"][0].update({"To Node": "LED[0]"}),
            "unreviewed Quartus warnings": lambda e: e["reviewed_warnings"].append("Warning (1): new"),
            "connectivity warnings": lambda e: e["connectivity_warnings"].pop(),
            "check_timing": lambda e: e["check_timing"].update(loops="1"),
        }
        for message, tamper in edits.items():
            with self.subTest(message):
                changed = copy.deepcopy(evidence)
                tamper(changed)
                with self.assertRaisesRegex(ValueError, "invalid gate evidence.*" + re.escape(message)):
                    gate.check_evidence(changed, config)

    def test_generated_ip_warnings_need_their_tdf_identity(self):
        config, reports = self.built()
        name = "db/altsyncram_ab12.tdf"
        line = (f"Warning (12125): Using design file {name}, which is not specified as a design file "
                "for the current project, but contains definitions for 1 design units and 1 entities in project")
        log, report, tdf = self.output / "compile.log", reports / f'{config["project"]}.map.rpt', self.output / name
        log.write_text(log.read_text() + line + "\n")
        report.write_text(report.read_text() +
                          f"; {name} ; yes ; Auto-Generated Megafunction ; /staged/{name} ; ;\n")
        tdf.parent.mkdir(exist_ok=True)
        tdf.write_text("--altsyncram DEVICE_FAMILY=MAX10\nSUBDESIGN altsyncram_ab12\n")
        gate.verify_reports(self.output, config)
        tdf.write_text("--altsyncram DEVICE_FAMILY=MAX10\nSUBDESIGN altsyncram_zz99\n")
        with self.assertRaisesRegex(RuntimeError, "unreviewed generated IP identity"):
            gate.verify_reports(self.output, config)
        self.edit(report, f"; {name} ; yes ;", f"; {name} ; no ;")
        with self.assertRaisesRegex(RuntimeError, "unreviewed generated IP evidence"):
            gate.verify_reports(self.output, config)
        self.edit(report, f"; {name} ; no ;", f"; {name} ; yes ;")
        tdf.write_text("--altsyncram DEVICE_FAMILY=MAX10\nSUBDESIGN altsyncram_ab12\n")
        log.write_text(log.read_text() + line + "\n")
        with self.assertRaisesRegex(RuntimeError, "duplicate generated IP warning"):
            gate.verify_reports(self.output, config)

    def test_elaboration_gate(self):
        config = manifest.load(self.root, fixture.MANIFEST)
        reports = self.output / "output_files"
        reports.mkdir(parents=True)
        summary = reports / f'{config["project"]}.map.summary'
        summary.write_text("Analysis & Elaboration Status : Successful - Sun Nov 22 2026\n")
        (reports / f'{config["project"]}.map.rpt').write_text(fixture.CONNECTIVITY)
        log = self.output / "elaborate.log"
        log.write_text(fixture.TRUNCATION + "\n" + fixture.AGGREGATE + "\n")
        result = gate.verify_elaboration(self.output, config)
        self.assertEqual(result["stage"], "analysis_and_elaboration")
        self.assertIn("2 reviewed warning occurrences", gate.warning_summary_text(result))
        log.write_text("Error (10161): undeclared object\n")
        with self.assertRaisesRegex(RuntimeError, "elaboration errors"):
            gate.verify_elaboration(self.output, config)
        log.write_text(fixture.AGGREGATE + "\nWarning (10999): unreviewed\n")
        with self.assertRaisesRegex(RuntimeError, "unreviewed Quartus warnings"):
            gate.verify_elaboration(self.output, config)
        summary.write_text("Analysis & Elaboration Status : Failed\n")
        with self.assertRaisesRegex(RuntimeError, "did not succeed"):
            gate.verify_elaboration(self.output, config)


if __name__ == "__main__":
    unittest.main()
