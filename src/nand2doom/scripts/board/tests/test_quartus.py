"""Hardware planner failures and board manifest coverage, without Quartus."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[5]
spec = importlib.util.spec_from_file_location("quartus", ROOT / "src/nand2doom/scripts/board/quartus.py")
quartus = importlib.util.module_from_spec(spec)
spec.loader.exec_module(quartus)
SYSTEM_CLOCK = "u_clock|u_pll|auto_generated|pll1|clk[0]"

# Ref: Sanitized Quartus 25.1 Clocks table, retaining its actual field ordering.
CLOCKS = """; Clock Name ; Type ; Period ; Frequency ; Rise ; Fall ; Duty Cycle ; Divide by ; Multiply by ; Phase ; Offset ; Edge List ; Edge Shift ; Inverted ; Master ; Source ; Targets ;
; reference ; Base ; 20.000 ; 50.0 MHz ; 0.000 ; 10.000 ; ; ; ; ; ; ; ; ; ; ; { MAX10_CLK1_50 } ;
; jtag_tck ; Base ; 100.000 ; 10.0 MHz ; 0.000 ; 50.000 ; ; ; ; ; ; ; ; ; ; ; { altera_reserved_tck } ;
; sdram_clock ; Generated ; 40.000 ; 25.0 MHz ; 20.000 ; 40.000 ; ; 1 ; 1 ; ; ; ; ; true ; SYSTEM ; SYSTEM ; { DRAM_CLK } ;
; SYSTEM ; Generated ; 40.000 ; 25.0 MHz ; 0.000 ; 20.000 ; 50.00 ; 2 ; 1 ; ; ; ; ; false ; reference ; u_clock|u_pll|auto_generated|pll1|inclk[0] ; { SYSTEM } ;
""".replace("SYSTEM", SYSTEM_CLOCK)


# Ref: Sanitized observed Quartus connectivity schema and complete unused-port set.
JTAG_HIERARCHY = "n2a_jtag_virtual:u_jtag_virtual|sld_virtual_jtag:u_vjtag"
UNUSED_JTAG_PORTS = """virtual_state_e1dr virtual_state_pdr virtual_state_e2dr virtual_state_cir tms
jtag_state_tlr jtag_state_rti jtag_state_sdrs jtag_state_cdr jtag_state_sdr
jtag_state_e1dr jtag_state_pdr jtag_state_e2dr jtag_state_udr jtag_state_sirs
jtag_state_cir jtag_state_sir jtag_state_e1ir jtag_state_pir jtag_state_e2ir jtag_state_uir""".split()
CONNECTIVITY_DETAIL = "Declared by entity but not connected by instance. Logic that only feeds a dangling port will be removed."
CONNECTIVITY_BORDER = "+-----+------+----------+--------+\n"
CONNECTIVITY = (f'; Port Connectivity Checks: "{JTAG_HIERARCHY}" ;\n' + CONNECTIVITY_BORDER +
                '; Port ; Type ; Severity ; Details ;\n' + CONNECTIVITY_BORDER +
                '; ir_out ; Input ; Info ; Stuck at GND ;\n' +
                "".join(f'; {port} ; Output ; Warning ; {CONNECTIVITY_DETAIL} ;\n'
                        for port in UNUSED_JTAG_PORTS) + CONNECTIVITY_BORDER)
AGGREGATE = "Warning (12241): 1 hierarchies have connectivity warnings - see the Connectivity Checks report folder"


def warning_fixture(config):
    """Synthetic diagnostics for the board-spec report gate, with movable source lines."""
    top = config["top"]
    source = f"/staged/src/nand2doom/fpga/{top.removeprefix('n2d_')}/{top}.sv"
    warnings = []
    for item in config["reviewed_unused_objects"]:
        warnings.append(f'Warning (10036): Verilog HDL or VHDL warning at {Path(item["source"]).name}(65): '
                        f'object "{item["object"]}" assigned a value but never read File: '
                        f'/staged/{item["source"]} Line: 65')
    constants = {}
    if config["board"] == "de2_115":
        constants.update({"DRAM_DQM[2]": "VCC", "DRAM_DQM[3]": "VCC", "VGA_BLANK_N": "VCC", "VGA_SYNC_N": "GND"})
        constants.update({f"HEX{h}[{i}]": "VCC" for h in range(8) for i in range(7)})
        constants.update({f"LEDR[{i}]": "GND" for i in range(4, 18)})
        constants.update({f"LEDG[{i}]": "GND" for i in range(9)})
    warnings.append(AGGREGATE)
    if constants:
        warnings.append("Warning (13024): Output pins are stuck at VCC or GND")
    warnings.extend(f'Warning (13410): Pin "{pin}" is stuck at {value} File: {source} Line: 99'
                    for pin, value in constants.items())
    warnings.extend(["Warning (292013): Feature LogicLock is only available with a valid subscription license. "
                     "You can purchase a software subscription to gain full access to this feature."])
    pin = "VGA_CLK" if config["board"] == "de2_115" else "DRAM_CLK"
    warnings.append(f'Warning (15064): PLL "{config["clock_module"]}:u_clock|altpll:u_pll|altpll_example:auto_generated|pll1" '
                    f'output port clk[0] feeds output pin "{pin}~output" via non-dedicated routing -- '
                    'jitter performance depends on switching rate of other design elements. Use PLL '
                    'dedicated clock outputs to ensure jitter performance File: /staged/db/altpll_example.tdf Line: 31')
    ports = [f"DRAM_DQ[{i}]" for i in range(16)]
    ports += (["CLOCK_50", "UART_RX", "PS2_CLK", "PS2_DAT"] if config["board"] == "de2_115" else
              ["MAX10_CLK1_50", "RESET_N", "UART_RXD", "PS2_CLK", "PS2_DAT"])
    advisory = (f"Warning (169177): {len(ports)} pins must meet Intel FPGA requirements for 3.3-, 3.0-, and 2.5-V "
                f"interfaces. For more information, refer to AN 447: Interfacing {config['family']} Devices "
                "with 3.3/3.0/2.5-V LVTTL/LVCMOS I/O Systems.")
    warnings.append(advisory)
    details = []
    for pin in ports:
        standard = "3.3 V SCHMITT TRIGGER" if pin == "RESET_N" else "3.3-V LVTTL"
        details.append(f"    Info (169178): Pin {pin} uses I/O standard {standard} at {config['pins'][pin]} "
                       f"File: {source} Line: 123")
    return warnings, advisory + "\n" + "\n".join(details) + "\n"


def clocks_table(config):
    """The sanitized Clocks table for a board: one inverted row per forwarded pin."""
    if config["board"] == "de10_lite":
        return CLOCKS
    row = next(line for line in CLOCKS.splitlines() if line.startswith("; sdram_clock ;"))
    extra = [row.replace("sdram_clock", name).replace("DRAM_CLK", port)
             for name, port in config["forwarded_clocks"].items() if name != "sdram_clock"]
    return CLOCKS.replace("MAX10_CLK1_50", config["reference_port"]) + "\n".join(extra) + "\n"


def top_ports(path):
    """Every bit of a board top's port list, as the pin map names them."""
    source = re.sub(r"//[^\n]*", "", path.read_text())
    ports = source.split("(", 1)[1].split(");", 1)[0]
    expected, bus = set(), None
    for declaration in ports.split(","):
        declaration = declaration.strip()
        if re.match(r"(?:input|output|inout)\b", declaration):
            match = re.search(r"\[(\d+):0\]", declaration)
            bus = int(match[1]) + 1 if match else None
        name = declaration.split()[-1]
        expected.update([name] if bus is None else [f"{name}[{i}]" for i in range(bus)])
    return expected



def simulation_sources(target: str) -> list:
    """A nand2doom simulation target's resolved source list."""
    resolver = quartus.hdl_libraries(ROOT)
    targets = json.loads((ROOT / "src/nand2doom/cfg/targets.json").read_text())["targets"]
    return resolver.resolve_target(targets[target], resolver.load(ROOT))["sources"]

class QuartusTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "workdir").mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT / "workdir")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.output = self.root / "workdir" / "quartus" / "test"
        self.config = quartus.load_config(ROOT, "src/nand2doom/cfg/n2d_quartus.json")

    def test_board_timeout_reaches_compile_and_audit(self):
        for board, seconds in (("de10_lite", 1800), ("de2_115", 5400)):
            with self.subTest(board=board):
                config = quartus.load_config(ROOT, "src/nand2doom/cfg/n2d_quartus.json", board)
                self.assertEqual(config["timeout_seconds"], seconds)
                with patch.object(quartus, "ROOT", self.root), \
                        patch.object(quartus, "load_config", return_value=config), \
                        patch.object(quartus, "stage"), patch.object(quartus, "run") as run, \
                        patch.object(quartus, "verify_reports", return_value={"minimum_slack_ns": 1, "warning_summary": {"occurrences": 0, "by_category": {}}}), \
                        patch.object(quartus.shutil, "which", return_value="/tools/quartus_sh"), \
                        contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(quartus.main(["--board", board]), 0)
                self.assertEqual([call.args[2:] for call in run.call_args_list],
                                 [("compile.log", seconds), ("audit.log", seconds)])

    def test_timeout_requires_positive_integer_on_each_board(self):
        raw = json.loads((ROOT / "src/nand2doom/cfg/n2d_quartus.json").read_text())
        for board in quartus.BOARDS:
            for invalid in (0, -1, True, False, None, "5400", 1.5):
                with self.subTest(board=board, invalid=invalid):
                    config = json.loads(json.dumps(raw))
                    selected = config if board == "de10_lite" else config["boards"][board]
                    selected["timeout_seconds"] = invalid
                    path = self.root / "timeout.json"
                    path.write_text(json.dumps(config))
                    with self.assertRaisesRegex(ValueError, "timeout_seconds"):
                        quartus.load_config(ROOT, str(path), board)
        # Why: removing the override must inherit the base bound, not drop it.
        raw["boards"]["de2_115"].pop("timeout_seconds")
        path.write_text(json.dumps(raw))
        self.assertEqual(quartus.load_config(ROOT, str(path), "de2_115")["timeout_seconds"], 1800)

    def test_connectivity_accepts_exact_observed_port_contract_on_both_boards(self):
        for board in quartus.BOARDS:
            config = quartus.load_config(ROOT, "src/nand2doom/cfg/n2d_quartus.json", board)
            rows = quartus.connectivity_warnings(
                '115. Port Connectivity Checks: "TOC prose"\n' + CONNECTIVITY, config)
            self.assertEqual(len(rows), 21)
            self.assertEqual({row["port"] for row in rows}, set(UNUSED_JTAG_PORTS))
            self.assertEqual({row["hierarchy"] for row in rows}, {JTAG_HIERARCHY})

    def test_connectivity_rejects_same_count_changed_contract_and_extra_rows(self):
        changes = [CONNECTIVITY.replace(JTAG_HIERARCHY, JTAG_HIERARCHY + "_other"),
                   CONNECTIVITY + '; Port Connectivity Checks: unquoted_other ;\n'
                   '; Port ; Type ; Severity ; Details ;\n'
                   f'; tms ; Output ; Warning ; {CONNECTIVITY_DETAIL} ;\n',
                   CONNECTIVITY.replace("; tms ;", "; tdo ;"),
                   CONNECTIVITY.replace("; tms ; Output", "; tms ; Input"),
                   CONNECTIVITY.replace("; tms ; Output ; Warning", "; tms ; Output ; Critical Warning"),
                   CONNECTIVITY.replace("; tms ; Output ; Warning", "; tms ; Output ; Info"),
                   CONNECTIVITY.replace(CONNECTIVITY_DETAIL, "Connected to dangling logic.", 1),
                   CONNECTIVITY.replace("; tms ; Output ; Warning", "; tms ; Output ; Warning ; extra"),
                   CONNECTIVITY.replace("; tms ; Output ; Warning ; " + CONNECTIVITY_DETAIL + " ;\n", ""),
                   CONNECTIVITY[:-len(CONNECTIVITY_BORDER)] + f'; tms ; Output ; Warning ; {CONNECTIVITY_DETAIL} ;\n' + CONNECTIVITY_BORDER,
                   CONNECTIVITY[:-len(CONNECTIVITY_BORDER)] + f'; unknown ; Output ; Warning ; {CONNECTIVITY_DETAIL} ;\n' + CONNECTIVITY_BORDER,
                   CONNECTIVITY + '; Port Connectivity Checks: "other:instance" ;\n'
                   '; Port ; Type ; Severity ; Details ;\n'
                   f'; tms ; Output ; Warning ; {CONNECTIVITY_DETAIL} ;\n']
        for report in changes:
            with self.subTest(report=report), self.assertRaisesRegex(RuntimeError, "connectivity"):
                quartus.connectivity_warnings(report, self.config)

    def test_aggregate_requires_complete_matching_report_and_exact_diagnostic(self):
        reports = self.reports()
        path = reports / f'{self.config["project"]}.map.rpt'
        path.write_text(CONNECTIVITY)
        log = self.output / "compile.log"
        log.write_text("\n".join(warning_fixture(self.config)[0]) + "\n")
        self.assertEqual(len(quartus.verify_reports(self.output, self.config)["connectivity_warnings"]), 21)
        for report, diagnostic in (("", AGGREGATE), (CONNECTIVITY, ""),
                                   (CONNECTIVITY, AGGREGATE + "\n" + AGGREGATE),
                                   (CONNECTIVITY, AGGREGATE.replace("1 hierarchies", "2 hierarchies")),
                                   (CONNECTIVITY, AGGREGATE.replace("Warning", "Critical Warning", 1))):
            path.write_text(report)
            log.write_text(diagnostic)
            with self.subTest(report=report, diagnostic=diagnostic), self.assertRaises(RuntimeError):
                quartus.verify_reports(self.output, self.config)

    def test_full_gate_rejects_malformed_rows_without_losing_table_ownership(self):
        reports = self.reports()
        path = reports / f'{self.config["project"]}.map.rpt'
        (self.output / "compile.log").write_text("\n".join(warning_fixture(self.config)[0]) + "\n")
        path.write_text(CONNECTIVITY)
        self.assertEqual(len(quartus.verify_reports(self.output, self.config)["connectivity_warnings"]), 21)
        needle = next(line for line in CONNECTIVITY.splitlines() if line.startswith("; jtag_state_uir "))
        malformed = [needle + "\n; unexpected ;",
                     needle + "\n; unexpected ;\n; unknown ; Output ; Warning ; new unowned diagnostic ;",
                     needle + "\n unknown ; Output ; Warning ; new unowned diagnostic ;",
                     needle + " unparsed trailing diagnostic"]
        for row in malformed:
            path.write_text(CONNECTIVITY.replace(needle, row))
            with self.subTest(row=row), self.assertRaisesRegex(RuntimeError, "connectivity"):
                quartus.verify_reports(self.output, self.config)
        path.write_text(CONNECTIVITY)
        self.assertEqual(len(quartus.verify_reports(self.output, self.config)["connectivity_warnings"]), 21)

    def test_removed_btb_pass_through_warning_is_never_reviewed(self):
        warning = ('Warning (276020): Inferred RAM node "n2d_system:u_system|n2d_rv32im_core:u_core|'
                   'n2d_if:u_if|g_btb.tag_q_rtl_0" from synchronous design logic.  Pass-through '
                   'logic has been added to match the read-during-write behavior of the original design.')
        for board in quartus.BOARDS:
            config = quartus.load_config(ROOT, "src/nand2doom/cfg/n2d_quartus.json", board)
            matches = lambda line: any(re.fullmatch(rule["pattern"], line) for rule in quartus.warning_rules(config))
            self.assertFalse(matches(warning))
            for changed in (warning.replace("tag_q_rtl_0", "target_q_rtl_0"),
                            warning.replace("tag_q_rtl_0", "tag_q_rtl_1"),
                            warning.replace("u_if|g_btb", "u_if|g_ras"),
                            warning.replace("Warning", "Critical Warning", 1)):
                self.assertFalse(matches(changed), changed)

    def test_unused_reduction_warning_matches_only_exact_object_and_file(self):
        for board in quartus.BOARDS:
            config = quartus.load_config(ROOT, "src/nand2doom/cfg/n2d_quartus.json", board)
            for area, module, obj in (("core", "exe", "unused_base"), ("core", "wb", "unused_wb"),
                                      ("core", "if", "unused_update_bits"), ("core", "muldiv", "unused_mul_full"),
                                      ("ls", "dcache", "data_request_held_held_prev"),
                                      ("ls", "dcache", "line_request_held_held_prev")):
                warning = (f'Warning (10036): Verilog HDL or VHDL warning at n2d_{module}.sv(65): '
                           f'object "{obj}" assigned a value but never read File: '
                           f'/staged/src/nand2doom/rtl/{area}/n2d_{module}.sv Line: 65')
                matches = lambda line: any(re.fullmatch(rule["pattern"], line) for rule in quartus.warning_rules(config))
                expected = {"source": f"src/nand2doom/rtl/{area}/n2d_{module}.sv", "object": obj}
                self.assertEqual(matches(warning), expected in config["reviewed_unused_objects"])
                if expected not in config["reviewed_unused_objects"]:
                    map_path = self.root / f"{board}.map.rpt"
                    map_path.write_text("No connectivity diagnostics.\n")
                    with self.assertRaisesRegex(RuntimeError, "unreviewed"):
                        quartus.reviewed_warnings([warning], map_path, config, elaboration=True)
                for changed in (warning.replace(obj, obj + "_other"),
                                warning.replace(f"/{area}/", "/other/"),
                                warning.replace("Warning", "Critical Warning", 1),
                                warning.replace("never read", "read by multiple drivers")):
                    self.assertFalse(matches(changed), changed)

    def test_warning_contract_counts_and_semantic_identities_on_both_boards(self):
        for board, constants, electrical in (("de10_lite", 0, 21), ("de2_115", 83, 20)):
            config = quartus.load_config(ROOT, "src/nand2doom/cfg/n2d_quartus.json", board)
            warnings, fit = warning_fixture(config)
            reports = self.output / board
            reports.mkdir(parents=True)
            map_path, fit_path = reports / "test.map.rpt", reports / "test.fit.rpt"
            map_path.write_text(CONNECTIVITY)
            fit_path.write_text(fit)
            result = quartus.reviewed_warnings(warnings, map_path, config, fit_report=fit_path)
            summary = result["warning_summary"]
            summary_count = 1 if constants else 0
            self.assertEqual(summary["occurrences"], constants + summary_count + 4 + len(config["reviewed_unused_objects"]))
            expected_codes = {"12241": 1, "292013": 1, "15064": 1, "169177": 1}
            if constants:
                expected_codes.update({"13024": 1, "13410": constants})
            if config["reviewed_unused_objects"]:
                expected_codes["10036"] = len(config["reviewed_unused_objects"])
            self.assertEqual(summary["by_code"], expected_codes)
            self.assertEqual(summary["by_category"].get("constant_outputs", 0), constants + summary_count)
            self.assertEqual(len(result["electrical_advisory"]), electrical)
            self.assertEqual(len(result["connectivity_warnings"]), 21)
            self.assertIn(f"{summary['occurrences']} reviewed warning occurrences", quartus.warning_summary_text(result))
            # Why: the staging root and line numbers move between runs, so neither may
            # change a warning's identity.
            moved = [line.replace("/staged/", "/new/root/").replace("(65)", "(876)")
                     .replace("Line: 65", "Line: 876").replace("Line: 99", "Line: 1234") for line in warnings]
            self.assertEqual(result, quartus.reviewed_warnings(moved, map_path, config, fit_report=fit_path))

    def test_every_full_warning_identity_requires_its_exact_occurrence_count(self):
        config = quartus.load_config(ROOT, "src/nand2doom/cfg/n2d_quartus.json", "de2_115")
        reports = self.reports(config)
        map_path, fit_path = (reports / f'{config["project"]}.{kind}.rpt' for kind in ("map", "fit"))
        warnings, _ = warning_fixture(config)
        for index, warning in enumerate(warnings):
            for kind, changed in (("missing", warnings[:index] + warnings[index + 1:]),
                                  ("duplicate", warnings + [warning])):
                with self.subTest(kind=kind, warning=warning), self.assertRaises(RuntimeError):
                    quartus.reviewed_warnings(changed, map_path, config, fit_report=fit_path)
        with self.assertRaises(RuntimeError):
            quartus.reviewed_warnings([], map_path, config, fit_report=fit_path)

    def test_warning_identity_changes_fail_even_with_unchanged_total(self):
        config = quartus.load_config(ROOT, "src/nand2doom/cfg/n2d_quartus.json", "de2_115")
        reports = self.reports(config)
        map_path, fit_path = (reports / f'{config["project"]}.{kind}.rpt' for kind in ("map", "fit"))
        warnings, _ = warning_fixture(config)
        text = "\n".join(warnings)
        changes = [('Pin "HEX0[0]"', 'Pin "HEX0[1]"'), ('Pin "HEX0[0]"', 'Pin "DRAM_ADDR[0]"'),
                   ('Pin "HEX0[0]" is stuck at VCC', 'Pin "HEX0[0]" is stuck at GND'),
                   ('/de2_115/n2d_de2_115.sv', '/other/another_board.sv'),
                   ('"VGA_CLK~output"', '"DRAM_CLK~output"'),
                   ('output port clk[0]', 'output port clk[1]'),
                   ('/db/altpll_example.tdf', '/db/altpll_other.tdf'),
                   ('Cyclone IV E Devices', 'MAX 10 Devices'),
                   ('20 pins must meet', '19 pins must meet'),
                   ('Warning (13410)', 'Critical Warning (13410)'),
                   ('gain full access to this feature.', 'gain full access to another feature.')]
        for old, new in changes:
            self.assertIn(old, text)
            with self.subTest(change=(old, new)), self.assertRaises(RuntimeError):
                quartus.reviewed_warnings(text.replace(old, new, 1).splitlines(), map_path, config, fit_report=fit_path)

    def test_electrical_advisory_requires_owned_complete_pin_pad_standard_details(self):
        for board in quartus.BOARDS:
            config = quartus.load_config(ROOT, "src/nand2doom/cfg/n2d_quartus.json", board)
            _, original = warning_fixture(config)
            lines = original.splitlines()
            first = lines[1]
            changed = ["\n".join(lines[:-1]), original + first + "\n", original.replace('Pin DRAM_DQ[0]', 'Pin DRAM_DQ[1]', 1),
                       original.replace('Pin DRAM_DQ[0]', 'Pin DRAM_ADDR[0]', 1),
                       original.replace(f'at {config["pins"]["DRAM_DQ[0]"]} File:', 'at Z99 File:', 1),
                       original.replace('I/O standard 3.3-V LVTTL', 'I/O standard 2.5 V', 1),
                       original.replace('/fpga/', '/rtl/core/', 1),
                       original.replace('Info (169178)', 'Warning (169178)', 1),
                       original.replace('Info (169178)', 'Info (169178): bad', 1),
                       "\n".join(lines[1:]), original + original,
                       lines[0] + '\nInfo (1): unrelated\n' + '\n'.join(lines[1:]),
                       original.replace('Devices with', 'Device with', 1)]
            for text in changed:
                with self.subTest(board=board, text=text), self.assertRaisesRegex(RuntimeError, 'electrical'):
                    quartus.electrical_warnings(text, config)
            result = quartus.electrical_warnings(original, config)
            if board == "de10_lite":
                reset = next(row for row in result if row["pin"] == "RESET_N")
                self.assertEqual((reset["pad"], reset["io_standard"]), ("B8", "3.3 V SCHMITT TRIGGER"))

    def test_electrical_advisory_standard_ignores_report_case_and_spacing(self):
        config = quartus.load_config(ROOT, "src/nand2doom/cfg/n2d_quartus.json", "de10_lite")
        _, original = warning_fixture(config)
        reported = "standard 3.3 V SCHMITT TRIGGER at"
        self.assertIn(reported, original)
        # Ref: Quartus 25.1std.0 prints the configured standard in mixed case.
        for spelling in ("3.3 V Schmitt Trigger", "3.3  V  schmitt\ttrigger"):
            with self.subTest(spelling=spelling):
                text = original.replace(reported, f"standard {spelling} at")
                reset = next(row for row in quartus.electrical_warnings(text, config) if row["pin"] == "RESET_N")
                self.assertEqual(reset["io_standard"], "3.3 V SCHMITT TRIGGER")
        for wrong in ("3.3-V LVTTL", "3.3 V Schmitt", "2.5 V Schmitt Trigger"):
            with self.subTest(wrong=wrong), self.assertRaisesRegex(RuntimeError, 'electrical'):
                quartus.electrical_warnings(original.replace(reported, f"standard {wrong} at"), config)

    def test_full_gate_keeps_raw_order_and_detects_cross_log_duplicates(self):
        reports = self.reports()
        warnings, _ = warning_fixture(self.config)
        original = (self.output / "compile.log").read_bytes()
        result = quartus.verify_reports(self.output, self.config)
        self.assertEqual(result["reviewed_warnings"], warnings)
        self.assertEqual(result["warning_summary"]["occurrences"], len(warnings))
        self.assertEqual((self.output / "compile.log").read_bytes(), original)
        extra = next(line for line in warnings if '(292013)' in line)
        (self.output / "audit.log").write_text(extra + "\n")
        with self.assertRaisesRegex(RuntimeError, 'occurrence mismatch'):
            quartus.verify_reports(self.output, self.config)
        (self.output / "audit.log").write_text("Warning: malformed new diagnostic\n")
        with self.assertRaisesRegex(RuntimeError, 'unreviewed'):
            quartus.verify_reports(self.output, self.config)
        (self.output / "audit.log").write_text("")
        fit_path = reports / f'{self.config["project"]}.fit.rpt'
        fit_path.write_text(fit_path.read_text().replace('at Y21 File:', 'at Z99 File:', 1))
        with self.assertRaisesRegex(RuntimeError, 'electrical'):
            quartus.verify_reports(self.output, self.config)

    def test_elaboration_has_separate_bounded_frontend_contract(self):
        self.elaboration(self.config)
        map_path = self.output / "output_files" / f'{self.config["project"]}.map.rpt'
        map_path.write_text("Analysis and elaboration, no connectivity diagnostics.\n")
        self.assertEqual(quartus.verify_elaboration(self.output, self.config)["warning_summary"]["occurrences"], 0)
        warnings, _ = warning_fixture(self.config)
        for warning in warnings:
            code = int(re.search(r'\((\d+)\)', warning)[1])
            if code in (13024, 13410, 15064, 169177):
                with self.subTest(code=code), self.assertRaisesRegex(RuntimeError, 'unreviewed'):
                    quartus.reviewed_warnings([warning], map_path, self.config, elaboration=True)
        license_warning = next(line for line in warnings if '(292013)' in line)
        with self.assertRaisesRegex(RuntimeError, 'occurrence mismatch'):
            quartus.reviewed_warnings([license_warning, license_warning], map_path, self.config, elaboration=True)
        for unused in (line for line in warnings if '(10036)' in line):
            with self.assertRaisesRegex(RuntimeError, 'unreviewed'):
                quartus.reviewed_warnings([unused.replace('Line: 65', 'Line: 66')], map_path, self.config, elaboration=True)

    def generated_fixture(self, entity="altpll_example"):
        """Synthetic generated-file metadata, never vendor source content."""
        reports = self.output / "output_files"
        reports.mkdir(parents=True, exist_ok=True)
        path = self.output / "db" / f"{entity}.tdf"
        path.parent.mkdir(exist_ok=True)
        family = entity.split("_")[0]
        primitive = {"decode": "lpm_decode", "mux": "lpm_mux"}.get(family, family)
        path.write_text(f'--{primitive} TEST_ONLY=1\nSUBDESIGN {entity}\n()\n')
        map_path = reports / "generated.map.rpt"
        row = f'; db/{entity}.tdf ; yes ; Auto-Generated Megafunction ; /staged/db/{entity}.tdf ; ;\n'
        with map_path.open('a') as stream:
            stream.write(row)
        warning = (f'Warning (12125): Using design file db/{entity}.tdf, which is not specified as a design file '
                   'for the current project, but contains definitions for 1 design units and 1 entities in project')
        return warning, map_path, path

    def test_generated_discovery_binds_file_identity_provenance_and_multiplicity(self):
        warnings = []
        for entity in ('altpll_example', 'altsyncram_example', 'decode_example', 'mux_example'):
            warning, map_path, _ = self.generated_fixture(entity)
            warnings.append(warning)
        result = quartus.reviewed_warnings(warnings, map_path, self.config, elaboration=True)
        self.assertEqual(result['warning_summary']['by_category']['generated_ip'], 4)
        self.assertEqual(result['warning_summary']['by_code']['12125'], 4)
        self.assertEqual({row['identity']['file'] for row in result['warning_summary']['identities'] if row['code'] == 12125},
                         {'db/altpll_example.tdf', 'db/altsyncram_example.tdf', 'db/decode_example.tdf', 'db/mux_example.tdf'})
        for extra in (warnings[0], warnings[0].replace('altpll_example', 'altpll_absent'),
                      warnings[0].replace('altpll_example', 'other_example'),
                      warnings[0].replace('1 design units', '2 design units')):
            with self.subTest(extra=extra), self.assertRaises(RuntimeError):
                quartus.reviewed_warnings(warnings + [extra], map_path, self.config, elaboration=True)
        warning, _, _ = self.generated_fixture('altpll_second')
        with self.assertRaisesRegex(RuntimeError, 'too many generated IP'):
            quartus.reviewed_warnings(warnings + [warning], map_path, self.config, elaboration=True)

    def test_generated_discovery_rejects_missing_or_misidentified_evidence(self):
        warning, map_path, path = self.generated_fixture()
        original_map, original_file = map_path.read_text(), path.read_text()
        cases = [(original_map.replace('Auto-Generated Megafunction', 'User AHDL File'), original_file),
                 (original_map.replace('; yes ;', '; no ;'), original_file),
                 (original_map + original_map, original_file),
                 (original_map.replace('/staged/db/', '/staged/elsewhere/'), original_file),
                 (original_map, original_file.replace('SUBDESIGN altpll_example', 'SUBDESIGN altpll_other')),
                 (original_map, original_file.replace('--altpll ', '--altsyncram ')),
                 (original_map, '')]
        for report, data in cases:
            map_path.write_text(report)
            path.write_text(data)
            with self.subTest(report=report, data=data), self.assertRaisesRegex(RuntimeError, 'generated IP'):
                quartus.reviewed_warnings([warning], map_path, self.config, elaboration=True)
        map_path.write_text(original_map)
        path.unlink()
        with self.assertRaisesRegex(RuntimeError, 'generated IP'):
            quartus.reviewed_warnings([warning], map_path, self.config, elaboration=True)

    def test_board_pins_cover_every_bit_once(self):
        expected = top_ports(ROOT / "src/nand2doom/fpga/de10_lite/n2d_de10_lite.sv")
        self.assertEqual(set(self.config["pins"]), expected)
        self.assertEqual(len(set(self.config["pins"].values())), len(expected))
        self.assertEqual(self.config["device"], "10M50DAF484C7G")
        self.assertFalse(any("fast" in s or "preload" in s or "/dv/" in s for s in self.config["sources"]))
        simulation = simulation_sources("n2d_de10_lite")
        # Why: Quartus must build what the board simulation checked, except that each
        # common memory contract uses its Intel backend in place of the portable one
        # (never both files of a pair) and the dv/ primitive stub is left out; the
        # common Virtual JTAG wrapper is in both lists.
        intel_for = {"src/common/rtl/memory/n2a_sync_ram.sv": "src/common/rtl/memory/n2a_sync_ram_intel.sv",
                     "src/common/rtl/memory/n2a_sdp_ram_portable.sv": "src/common/rtl/memory/n2a_sdp_ram_intel.sv",
                     "src/common/rtl/memory/n2a_tdp_ram_portable.sv": "src/common/rtl/memory/n2a_tdp_ram_intel.sv",
                     "src/common/fpga/de10_lite/n2a_de10_lite_clock_portable.sv":
                         "src/common/fpga/de10_lite/n2a_de10_lite_clock_intel.sv"}
        # Why: lint-waiver companions and the dv/ primitive stub exist only for
        # Verilator, so Quartus never sees them.
        expected_sources = {intel_for.get(source, source) for source in simulation
                            if "/dv/" not in source}
        self.assertIn("src/common/rtl/jtag/n2a_jtag_virtual.sv", self.config["sources"])
        self.assertEqual(set(self.config["sources"]), expected_sources)
        for portable, intel in intel_for.items():
            self.assertIn(intel, self.config["sources"])
            self.assertNotIn(portable, self.config["sources"])
            self.assertIn(portable, simulation)
            self.assertNotIn(intel, simulation)
        for requirement in self.config["ram_requirements"]:
            self.assertIn(requirement["single_port"], (True, False))

    def test_de10_lite_resolution_is_the_unchanged_top_level(self):
        raw = quartus.resolve_sources(json.loads((ROOT / "src/nand2doom/cfg/n2d_quartus.json").read_text()), ROOT)
        raw.pop("boards")
        resolved = {key: value for key, value in self.config.items()
                    if key != "board" and key not in quartus.BOARD_DEFAULTS}
        self.assertEqual(resolved, raw)
        self.assertEqual(self.config["board"], "de10_lite")
        output = self.root / "workdir" / "de10"
        quartus.stage(ROOT, output, self.config)
        qsf = (output / "n2d_de10_lite.qsf").read_text()
        # Why: KEY0 (RESET_N) takes its standard from the settings fragment, so the QSF
        # sets 3.3-V LVTTL on every other pin only.
        standards = re.findall(r'IO_STANDARD "([^"]+)" -to (\S+)', qsf)
        self.assertEqual({port for _, port in standards}, set(self.config["pins"]) - {"RESET_N"})
        self.assertEqual({standard for standard, _ in standards}, {"3.3-V LVTTL"})

    @unittest.skipUnless((ROOT / 'src/nand2linux/fpga/n2l_de2_115.qsf').exists(),
                         'nand2linux (the DE2-115 pin authority) is not ported')
    def test_de2_115_pins_cover_every_bit_and_match_the_nand2linux_board(self):
        config = quartus.load_config(ROOT, "src/nand2doom/cfg/n2d_quartus.json", "de2_115")
        self.assertEqual(set(config["pins"]), top_ports(ROOT / "src/nand2doom/fpga/de2_115/n2d_de2_115.sv"))
        self.assertEqual((config["family"], config["device"]), ("Cyclone IV E", "EP4CE115F29C7"))
        # Ref: The nand2linux DE2-115 QSF is the cross-checked board pin authority
        # (DE2-115 manual tables); every shared name must agree on pin and standard.
        n2l = (ROOT / "src/nand2linux/fpga/n2l_de2_115.qsf").read_text()
        locations = {port: pin for pin, port in re.findall(r"set_location_assignment PIN_(\w+) -to (\S+)", n2l)}
        standards = {port: value for value, port in
                     re.findall(r'set_instance_assignment -name IO_STANDARD "([^"]+)" -to (\S+)', n2l)}
        for port, pin in config["pins"].items():
            with self.subTest(port=port):
                self.assertEqual(locations[port], pin)
                expected = standards.get(port, standards.get(port.split("[")[0] + "[*]"))
                self.assertEqual(quartus.io_standard(config, port), expected)
        # Ref: DE2-115 manual: JP5 UART (GPIO[0] RX, GPIO[1] TX) and the on-board PS/2 connector.
        self.assertEqual([config["pins"][p] for p in ("UART_RX", "UART_TX", "PS2_CLK", "PS2_DAT")],
                         ["AB22", "AC15", "G6", "H5"])

    def test_de2_115_closure_swaps_only_the_board_files(self):
        config = quartus.load_config(ROOT, "src/nand2doom/cfg/n2d_quartus.json", "de2_115")
        de10, de2 = "src/nand2doom/fpga/de10_lite", "src/nand2doom/fpga/de2_115"
        clocks = "src/common/fpga"
        swapped = {f"{clocks}/de10_lite/n2a_de10_lite_clock_intel.sv": f"{clocks}/de2_115/n2a_de2_115_clock_intel.sv",
                   f"{de10}/n2d_de10_lite.sv": f"{de2}/n2d_de2_115.sv"}
        self.assertEqual(config["sources"], [swapped.get(name, name) for name in self.config["sources"]])
        # Why: the DE2-115 framebuffer is full width, so its RAM requirements count two
        # full-width banks (eight CPU and eight scan lane copies) besides the four-way
        # D-cache (four data arrays and a 63-bit per-set metadata word in four M9Ks).
        def de2_115_ram(r):
            prefix = r.get("prefix", "")
            if "u_set_ram" in r.get("name", ""):
                return dict(r, width=63, m9ks_each=4)
            return dict(r, count=8 if "g_framebuffer" in prefix else
                        4 if "u_dcache" in prefix else r["count"])
        expected = [de2_115_ram(r) for r in self.config["ram_requirements"]]
        self.assertEqual(config["ram_requirements"], expected)
        self.assertEqual(sum(r["count"] * r["m9ks_each"] for r in expected), 318)
        simulation = simulation_sources("n2d_de2_115")
        intel_for = {"src/common/rtl/memory/n2a_sync_ram.sv": "src/common/rtl/memory/n2a_sync_ram_intel.sv",
                     "src/common/rtl/memory/n2a_sdp_ram_portable.sv": "src/common/rtl/memory/n2a_sdp_ram_intel.sv",
                     "src/common/rtl/memory/n2a_tdp_ram_portable.sv": "src/common/rtl/memory/n2a_tdp_ram_intel.sv",
                     f"{clocks}/de2_115/n2a_de2_115_clock_portable.sv": f"{clocks}/de2_115/n2a_de2_115_clock_intel.sv"}
        self.assertEqual(set(config["sources"]), {intel_for.get(name, name) for name in simulation
                                                  if "/dv/" not in name})

    def test_de2_115_plan_and_generated_project(self):
        with patch.object(quartus, "stage") as stage, patch.object(quartus.shutil, "which") as which:
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(quartus.main(["--plan", "--board", "de2_115"]), 0)
            stage.assert_not_called()
            which.assert_not_called()
        self.assertIn("de2_115 n2d_de2_115: EP4CE115F29C7, 25000000 Hz", stdout.getvalue())
        self.assertIn("workdir/quartus/n2d_de2_115", stdout.getvalue())
        self.assertIn("timeout 5400 s per invocation", stdout.getvalue())
        config = quartus.load_config(ROOT, "src/nand2doom/cfg/n2d_quartus.json", "de2_115")
        output = self.root / "workdir" / "de2_115"
        quartus.stage(ROOT, output, config)
        qsf = (output / "n2d_de2_115.qsf").read_text()
        for line in ('set_global_assignment -name FAMILY "Cyclone IV E"',
                     "set_global_assignment -name DEVICE EP4CE115F29C7",
                     "set_global_assignment -name TOP_LEVEL_ENTITY n2d_de2_115",
                     'set_global_assignment -name VERILOG_MACRO "SYNTHESIS=1"',
                     'set_global_assignment -name VERILOG_MACRO "DISABLE_ASSERTIONS=1"',
                     'source "src/nand2doom/fpga/de2_115/n2d_de2_115.qsf"',
                     'set_global_assignment -name SDC_FILE "src/nand2doom/fpga/de2_115/n2d_de2_115.sdc"',
                     "set_location_assignment PIN_Y2 -to CLOCK_50",
                     'set_instance_assignment -name IO_STANDARD "2.5 V" -to KEY[0]',
                     'set_instance_assignment -name IO_STANDARD "2.5 V" -to HEX3[1]',
                     'set_instance_assignment -name IO_STANDARD "3.3-V LVTTL" -to HEX3[2]',
                     'set_instance_assignment -name IO_STANDARD "3.3-V LVTTL" -to UART_RX'):
            self.assertIn(line, qsf)
        self.assertEqual(len(re.findall("IO_STANDARD", qsf)), len(config["pins"]))
        self.assertEqual((output / "n2d_de2_115.qpf").read_text(), 'PROJECT_REVISION = "n2d_de2_115"\n')
        hardware = (output / "src/common/fpga/de2_115/n2a_de2_115_clock_intel.sv").read_text()
        self.assertIn('.intended_device_family("Cyclone IV E")', hardware)
        self.assertNotIn("de10_lite", "\n".join(path.name for path in output.rglob("*.sv")))
        self.assertFalse((output / "src/common/fpga/de2_115/n2a_de2_115_clock_portable.sv").exists())
        settings = (output / config["settings"]).read_text()
        for port in ("UART_RX", "PS2_CLK", "PS2_DAT"):
            self.assertIn(f"WEAK_PULL_UP_RESISTOR ON -to {port}", settings)
        for pattern, strength in (("DRAM*", "8MA"), ("VGA_*", "8MA"), ("LEDR*", "4MA"),
                                  ("LEDG*", "4MA"), ("HEX*", "4MA"), ("UART_TX", "4MA")):
            self.assertIn(f'CURRENT_STRENGTH_NEW "{strength}" -to {pattern}', settings)
        sdc = (output / config["constraints"]).read_text()
        self.assertIn("create_generated_clock -name vga_dac_clock -source $pll_target -invert $vga_clock_port", sdc)
        self.assertIn("[get_ports CLOCK_50]", sdc)

    def test_board_selection_rejects_unknown_boards_and_overrides(self):
        raw = json.loads((ROOT / "src/nand2doom/cfg/n2d_quartus.json").read_text())
        with self.assertRaises(ValueError):
            quartus.select_board(dict(raw), "de0_nano")
        for change in ({"clock_hz": 50_000_000}, {"source_replacements": {"missing.sv": "x.sv"}},
                       {"io_standards": {"NOT_A_PIN": "2.5 V"}},
                       {"forwarded_clocks": {"vga_dac_clock": "VGA_CLK"}}):
            config = json.loads(json.dumps(raw))
            config["boards"]["de2_115"].update(change)
            path = self.root / "board.json"
            path.write_text(json.dumps(config))
            with self.subTest(change=change), self.assertRaises((ValueError, KeyError)):
                quartus.load_config(ROOT, str(path), "de2_115")

    def test_de2_115_report_gate_requires_the_forwarded_vga_clock(self):
        config = quartus.load_config(ROOT, "src/nand2doom/cfg/n2d_quartus.json", "de2_115")
        reports = self.reports(config)
        result = quartus.verify_reports(self.output, config)
        self.assertEqual(set(result["clock_relationships"]),
                         {"reference", SYSTEM_CLOCK, "jtag_tck", "sdram_clock", "vga_dac_clock"})
        self.assertEqual(result["clock_relationships"]["vga_dac_clock"]["Targets"], "{ VGA_CLK }")
        table = reports / "n2d_de2_115.sta.rpt"
        check = reports / "check_timing.rpt"
        summary = reports / "n2d_de2_115.sta.summary"
        originals = {path: path.read_text() for path in (table, check, summary)}
        vga_row = next(line for line in originals[table].splitlines() if line.startswith("; vga_dac_clock ;"))
        for path, bad in ((table, originals[table].replace(vga_row + "\n", "")),
                          (table, originals[table].replace("{ VGA_CLK }", "{ VGA_HS }")),
                          (table, originals[table].replace("CLOCK_50", "MAX10_CLK1_50")),
                          (check, originals[check].replace("; VGA_CLK ;", "; VGA_HS ;")),
                          (check, originals[check].replace("n2a_de2_115_clock:", "n2a_de10_lite_clock:")),
                          (summary, originals[summary].replace("Setup 'vga_dac_clock'", "Setup 'other'"))):
            path.write_text(bad)
            with self.subTest(path=path.name), self.assertRaises(RuntimeError):
                quartus.verify_reports(self.output, config)
            path.write_text(originals[path])

    def elaboration(self, config, log="", status="Successful - Wed Oct  7 07:54:33 2026"):
        """A sanitized analysis-and-elaboration output tree for one board."""
        reports = self.output / "output_files"
        reports.mkdir(parents=True, exist_ok=True)
        (reports / f'{config["project"]}.map.summary').write_text(
            f"Analysis & Elaboration Status : {status}\nTop-level Entity Name : {config['top']}\n")
        (reports / f'{config["project"]}.map.rpt').write_text(CONNECTIVITY)
        (self.output / "elaborate.log").write_text(log)

    def test_elaborate_only_runs_only_analysis_and_elaboration_per_board(self):
        for board, seconds in (("de10_lite", 1800), ("de2_115", 5400)):
            with self.subTest(board=board):
                config = quartus.load_config(ROOT, "src/nand2doom/cfg/n2d_quartus.json", board)
                with patch.object(quartus, "ROOT", self.root), \
                        patch.object(quartus, "load_config", return_value=config), \
                        patch.object(quartus, "stage") as stage, patch.object(quartus, "run") as run, \
                        patch.object(quartus, "verify_elaboration",
                                     return_value={"reviewed_warnings": [], "warning_summary": {"occurrences": 0, "by_category": {}}}) as verify, \
                        patch.object(quartus, "verify_reports") as full, \
                        patch.object(quartus.shutil, "which", return_value="/tools/quartus_sh"), \
                        contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(quartus.main(["--board", board, "--elaborate-only"]), 0)
                output = self.root / "workdir" / "quartus" / f'{config["project"]}-elaborate'
                self.assertEqual(stage.call_args.args[1], output)
                self.assertEqual([call.args for call in run.call_args_list],
                                 [(["/tools/quartus_map", config["project"], "--analysis_and_elaboration"],
                                   output, "elaborate.log", seconds)])
                verify.assert_called_once_with(output, config)
                full.assert_not_called()

    def test_elaborate_only_skips_explicitly_without_quartus(self):
        stderr = io.StringIO()
        with patch.object(quartus, "ROOT", self.root), patch.object(quartus, "stage") as stage, \
                patch.object(quartus, "load_config", return_value=self.config), \
                patch.object(quartus.shutil, "which", return_value=None), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(stderr):
            self.assertEqual(quartus.main(["--elaborate-only"]), 77)
            # Why: the full gate must never pass without evidence, so a missing tool fails it.
            self.assertEqual(quartus.main([]), 1)
        stage.assert_not_called()
        self.assertIn("SKIPPED: Quartus not found", stderr.getvalue())
        self.assertFalse((self.root / "workdir").exists())

    def test_elaborate_only_output_stays_below_workdir(self):
        with patch.object(quartus, "ROOT", self.root), patch.object(quartus, "stage") as stage, \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(quartus.main(["--elaborate-only", "--output", str(self.root / "elsewhere")]), 1)
        stage.assert_not_called()

    def test_elaboration_gate_accepts_success_and_reviewed_warnings_on_both_boards(self):
        for board in quartus.BOARDS:
            config = quartus.load_config(ROOT, "src/nand2doom/cfg/n2d_quartus.json", board)
            self.elaboration(config, f"Info (1): ok\n{AGGREGATE}\n")
            with self.subTest(board=board):
                result = quartus.verify_elaboration(self.output, config)
                self.assertEqual(result["reviewed_warnings"], [AGGREGATE])
                self.assertEqual(len(result["connectivity_warnings"]), 21)
                self.assertTrue((self.output / "evidence.json").is_file())

    def test_elaboration_gate_rejects_errors_failure_and_unreviewed_warnings(self):
        config = self.config
        # Why: these are the two Quartus Std rejections that reached main (#271); the
        # gate must catch both.
        cases = (("Error (10170): Verilog HDL syntax error at n2d_if.sv(260) near text: \"if\";  "
                  "expecting \"endmodule\".\n", "Successful", "elaboration errors"),
                 ("Error (10170): Verilog HDL syntax error at n2d_dcache.sv(84) near text: \"$error\";  "
                  "expecting \"end\".\n", "Successful", "elaboration errors"),
                 ("", "Failed - Wed Oct  7 07:54:33 2026", "did not succeed"),
                 ("Warning (10230): Verilog HDL assignment warning at n2d_dcache.sv(90): truncated value\n" + AGGREGATE,
                  "Successful", "unreviewed Quartus warnings"),
                 ("Critical Warning (12345): something\n" + AGGREGATE, "Successful", "unreviewed Quartus warnings"),
                 (AGGREGATE + "\n" + AGGREGATE + "\n", "Successful", "connectivity"))
        for log, status, message in cases:
            self.elaboration(config, log, status)
            with self.subTest(log=log, status=status), self.assertRaisesRegex(RuntimeError, message):
                quartus.verify_elaboration(self.output, config)
        for name in ("map.summary", "map.rpt"):
            self.elaboration(config)
            (self.output / "output_files" / f'{config["project"]}.{name}').unlink()
            with self.subTest(missing=name), self.assertRaisesRegex(RuntimeError, "missing analysis"):
                quartus.verify_elaboration(self.output, config)

    def test_elaboration_failure_reports_the_quartus_error_lines(self):
        def failing(command, output, log, timeout):
            (output / log).write_text("Info (1): start\n  Error (10170): syntax error at n2d_if.sv(260)\n")
            raise RuntimeError(f"Quartus exited 3; see {output / log}")
        stderr = io.StringIO()
        with patch.object(quartus, "ROOT", self.root), patch.object(quartus, "load_config", return_value=self.config), \
                patch.object(quartus, "stage", side_effect=lambda root, output, config: output.mkdir(parents=True)), \
                patch.object(quartus, "run", side_effect=failing), \
                patch.object(quartus.shutil, "which", return_value="/tools/quartus_sh"), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(stderr):
            self.assertEqual(quartus.main(["--elaborate-only"]), 1)
        self.assertIn("Quartus exited 3", stderr.getvalue())
        self.assertIn("Error (10170): syntax error at n2d_if.sv(260)", stderr.getvalue())

    def test_plan_writes_nothing_and_needs_no_tool(self):
        with patch.object(quartus, "stage") as stage, patch.object(quartus.shutil, "which") as which:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(quartus.main(["--plan"]), 0)
            stage.assert_not_called()
            which.assert_not_called()

    def test_generated_project_selects_hardware_clock(self):
        output = ROOT / "workdir" / Path(self.temporary.name).name / "workdir" / "project"
        quartus.stage(ROOT, output, self.config)
        qsf = (output / f'{self.config["project"]}.qsf').read_text()
        self.assertIn('set_global_assignment -name VERILOG_MACRO "SYNTHESIS=1"', qsf)
        self.assertIn('set_global_assignment -name VERILOG_MACRO "DISABLE_ASSERTIONS=1"', qsf)
        registry = json.loads((ROOT / "src/nand2doom/cfg/targets.json").read_text())
        for target in registry["targets"].values():
            self.assertNotIn("DISABLE_ASSERTIONS", target.get("defines", []))
        hardware = (output / "src/common/fpga/de10_lite/n2a_de10_lite_clock_intel.sv").read_text()
        self.assertIn("altpll #(", hardware)
        self.assertIn(") u_pll (", hardware)
        self.assertNotIn("clock_q", hardware)
        self.assertNotIn("SYNTHESIS", hardware)
        self.assertFalse((output / "src/common/fpga/de10_lite/n2a_de10_lite_clock_portable.sv").exists())
        settings = (output / self.config["settings"]).read_text()
        for pattern, strength in (("DRAM*", "8MA"), ("VGA_*", "8MA"),
                                  ("LEDR*", "4MA"), ("UART_TXD", "4MA")):
            self.assertIn(f'CURRENT_STRENGTH_NEW "{strength}" -to {pattern}', settings)
        self.assertFalse(any("model" in name or "preload" in name for name in self.config["sources"]))

    def test_output_escape_and_symlink_rejected(self):
        with self.assertRaises(ValueError):
            quartus.confined(self.root, self.root / "outside")
        self.output.parent.mkdir(parents=True)
        outside = self.root / "outside"
        outside.mkdir()
        self.output.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            quartus.confined(self.root, self.output)

    def test_missing_source_and_duplicate_pin_rejected(self):
        for change in ("missing", "pins"):
            config = dict(self.config)
            if change == "missing":
                config["sources"] = ["missing.sv"]
            else:
                config["pins"] = {"a": "P11", "b": "P11"}
            path = self.root / "config.json"
            # Why: the config may live outside the repo, but its sources must still resolve
            # against ROOT.
            path.write_text(json.dumps(config))
            with self.assertRaises(ValueError):
                quartus.load_config(ROOT, str(path))

    def test_nonzero_tool_status_fails(self):
        self.output.mkdir(parents=True)
        with self.assertRaises(RuntimeError):
            quartus.run(["/bin/false"], self.output, "failure.log", 2)

    def test_concurrent_build_rejected_before_touching_output(self):
        with quartus.build_lock(self.root, self.output):
            with self.assertRaises(FileExistsError):
                with quartus.build_lock(self.root, self.output):
                    self.fail("concurrent entry")
        self.assertFalse(self.output.with_suffix(".lock").exists())

    def test_native_tool_timeout_fails(self):
        self.output.mkdir(parents=True)
        with self.assertRaises(subprocess.TimeoutExpired):
            quartus.run([sys.executable, "-c", "import time; time.sleep(10)"],
                        self.output, "timeout.log", 1)

    def test_run_records_launcher_child_identity_in_sidecar(self):
        self.output.mkdir(parents=True)
        # Why: the child writes immediately and heavily, so any launcher write to the
        # same stream could interleave; identity must live in its own file and the log
        # hold tool output only.
        quartus.run([sys.executable, "-c",
                     "import sys; sys.stdout.write('tool output\\n' * 2000); sys.stdout.flush()"],
                    self.output, "normal.log", 10)
        log = (self.output / "normal.log").read_text()
        self.assertEqual(log, "tool output\n" * 2000)
        record = json.loads((self.output / "normal.log.owner.json").read_text())["launcher_child"]
        self.assertGreater(record["pid"], 0)
        self.assertEqual(record["pgid"], record["pid"])
        self.assertEqual(record["timeout_seconds"], 10)
        self.assertRegex(record["started_utc"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00$")

    def test_run_failed_identity_record_still_waits_and_reports_status(self):
        self.output.mkdir(parents=True)
        with patch.object(quartus, "record_launcher_child", side_effect=OSError("sidecar denied")):
            with contextlib.redirect_stderr(io.StringIO()) as stderr:
                with self.assertRaisesRegex(RuntimeError, "exited 3"):
                    quartus.run([sys.executable, "-c", "print('ran'); raise SystemExit(3)"],
                                self.output, "guarded.log", 10)
        self.assertIn("could not record launcher child identity", stderr.getvalue())
        self.assertIn("ran", (self.output / "guarded.log").read_text())
        self.assertFalse((self.output / "guarded.log.owner.json").exists())

    def test_run_timeout_after_identity_record_still_kills_child(self):
        self.output.mkdir(parents=True)
        with self.assertRaises(subprocess.TimeoutExpired):
            quartus.run([sys.executable, "-c", "import time; time.sleep(10)"],
                        self.output, "timeout-sidecar.log", 1)
        record = json.loads((self.output / "timeout-sidecar.log.owner.json").read_text())["launcher_child"]
        with self.assertRaises(ProcessLookupError):
            import os
            os.killpg(record["pgid"], 0)

    def reports(self, config=None):
        config = config or self.config
        reports = self.output / "output_files"
        reports.mkdir(parents=True)
        for name in ("map.rpt", "fit.rpt", "sta.rpt", "sta.summary", "asm.rpt", "sof"):
            (reports / f'{config["project"]}.{name}').write_text("fixture\n")
        timing = []
        for corner in config["timing_corners"]:
            for clock, kinds in (("u_clock|u_pll|auto_generated|pll1|clk[0]",
                                   ("Setup", "Hold", "Minimum Pulse Width")),
                                  ("jtag_tck", ("Recovery", "Removal")),
                                  *((name, ("Setup", "Hold")) for name in config["forwarded_clocks"]),
                                  ("reference", ("Minimum Pulse Width",))):
                for kind in kinds:
                    timing.append(f"Type : {corner} Model {kind} '{clock}'\nSlack : 0.125\nTNS : 0.000\n")
        (reports / f'{config["project"]}.sta.summary').write_text("\n".join(timing))
        (reports / f'{config["project"]}.sta.rpt').write_text(clocks_table(config))
        for corner, label in (("Slow 1200mV 85C", "slow_85"),
                              ("Slow 1200mV 0C", "slow_0"), ("Fast 1200mV 0C", "fast_0")):
            for direction in ("read", "write"):
                for kind in ("setup", "hold"):
                    controller = "n2d_memory_controller:u_memory|n2a_sdram_ctrl:u_sdram|"
                    source, target, launch, latch = (
                        ("DRAM_DQ[14]", controller + "regs_q.read_data[78]", "sdram_clock", SYSTEM_CLOCK)
                        if direction == "read" else
                        (controller + "beat_q[2]", "DRAM_LDQM" if "DRAM_LDQM" in config["pins"]
                         else "DRAM_DQM[0]", SYSTEM_CLOCK, "sdram_clock"))
                    # Ref: Actual report sections/rows with irrelevant path-delay rows omitted.
                    (reports / f"sdram_{direction}_{label}_{kind}.rpt").write_text(
                        f"Report Timing: Found 1 {kind} paths (0 violated). Worst case slack is 3.870\n"
                        f"Delay Model:\n {corner} Model\n"
                        f"Path #1: {kind.title()} slack is 3.870\n"
                        f"; Path Summary ;\n; Property ; Value ;\n"
                        f"; From Node ; {source} ;\n; To Node ; {target} ;\n"
                        f"; Launch Clock ; {launch} ;\n; Latch Clock ; {latch} ;\n"
                        "; Slack ; 3.870 ;\n")
        m9ks = sum(r["count"] * r["m9ks_each"] for r in config["ram_requirements"])
        blocks = 432 if config["board"] == "de2_115" else 182
        fit = ["; Total logic elements ; 12345 / 49,760 ;", f"; M9Ks ; {m9ks} / {blocks} ;",
               "; Total PLLs ; 1 / 4 ;",
               "; Name ; Type ; Mode ; Clock Mode ; Port A Depth ; Port A Width ; Port B Depth ; Port B Width ; M9Ks ; Mixed Width RDW Mode ; Port A RDW Mode ; Port B RDW Mode ;"]
        for requirement in config["ram_requirements"]:
            for index in range(requirement["count"]):
                name = (requirement["name"] if "name" in requirement else
                        f'{requirement["prefix"]}[{index}].{requirement["array"]}')
                single = requirement["single_port"]
                mode = "Single Port" if single else "Simple Dual Port"
                port_b = "-- ; --" if single else f'{requirement["depth"]} ; {requirement["width"]}'
                rdw = "Don't care ; Old data" if single else "Old data ; New data with NBE Read"
                fit.append(f'; {name} ; AUTO ; {mode} ; Single Clock ; '
                           f'{requirement["depth"]} ; {requirement["width"]} ; {port_b} ; '
                           f'{requirement["m9ks_each"]} ; {rdw} ; New data with NBE Read ;')
        warnings, electrical = warning_fixture(config)
        (reports / f'{config["project"]}.fit.rpt').write_text("\n".join(fit) + "\n" + electrical)
        (reports / f'{config["project"]}.map.rpt').write_text(CONNECTIVITY)
        categories = ("Illegal Clocks", "Unconstrained Clocks", "Unconstrained Input Ports",
                      "Unconstrained Input Port Paths", "Unconstrained Output Ports",
                      "Unconstrained Output Port Paths")
        (reports / "unconstrained.rpt").write_text("\n".join(f"; {c} ; 0 ; 0 ;" for c in categories))
        (reports / "clocks.tsv").write_text(f"reference\t20\njtag_tck\t100\n{SYSTEM_CLOCK}\t40\n" + "".join(
            f"{name}\t40\n" for name in config["forwarded_clocks"]))
        for name in ("setup.rpt", "hold.rpt"):
            (reports / name).write_text("fixture\n")
        checks = """no_clock multiple_clock pos_neg_clock_domain generated_clock virtual_clock
            no_input_delay no_output_delay partial_input_delay partial_output_delay
            io_min_max_delay_consistency reference_pin generated_io_delay latency_override
            partial_multicycle multicycle_consistency loops latches pll_cross_check uncertainty
            partial_min_max_delay clock_assignments_on_output_ports input_delay_assigned_to_clock""".split()
        forwarded = list(config["forwarded_clocks"].values())
        counts = {"no_clock": 1, "virtual_clock": 1, "no_output_delay": len(forwarded)}
        (reports / "check_timing.rpt").write_text("\n".join(
            f'; {name} ; {counts.get(name, 0)} ;' for name in checks) + "\n" + "".join(
            f'; {port} ; No output delay was set on output port. This port has clock assignments. ;\n'
            for port in forwarded) +
            f"; {config['clock_module']}:u_clock|altpll:u_pll|altpll_example:auto_generated|pll_lock_sync ; No clock feeds this register's clock port. ;\n"
            '; ; No virtual clock was found. ;\n')
        (reports / "ignored.rpt").write_text("-------------------\n; SDC Assignments ;\n"
                                             "-------------------\nNo constraints were ignored.\n")
        (self.output / "compile.log").write_text("\n".join(warnings) + "\n")
        (self.output / "audit.log").write_text("")
        return reports

    def test_fitter_gate_requires_every_bank_and_real_pll(self):
        reports = self.reports()
        report = (reports / f'{self.config["project"]}.fit.rpt').read_text()
        result = quartus.fitted_resources(report, self.config)
        self.assertEqual(result["M9Ks"]["remaining"], 10)
        self.assertEqual(len(result["ram_summary"]), 16)
        for bad in (report.replace("g_framebuffer[3].u_scan_ram", "missing_bank"),
                    report.replace("Total PLLs ; 1 / 4", "Total PLLs ; 0 / 4"),
                    report.replace("172 / 182", "183 / 182"),
                    report.replace("; 16384 ; 8 ;", "; 8192 ; 8 ;")):
            with self.assertRaises(RuntimeError):
                quartus.fitted_resources(bad, self.config)

    def test_fitter_auto_type_real_row_shape(self):
        # Ref: Sanitized Quartus 25.1 fitted row schema/data, not a generated artifact.
        # Type=AUTO is the inference request; the positive M9Ks is the result.
        report = """; Total logic elements ; 34960 / 49760 ;
; M9Ks ; 8 / 182 ;
; Total PLLs ; 1 / 4 ;
; Name ; Type ; Mode ; Clock Mode ; Port A Depth ; Port A Width ; Port B Depth ; Port B Width ; Port A Input Registers ; Port A Output Registers ; Port B Input Registers ; Port B Output Registers ; Size ; Implementation Port A Depth ; Implementation Port A Width ; Implementation Port B Depth ; Implementation Port B Width ; Implementation Bits ; M9Ks ; MIF ; Location ; Mixed Width RDW Mode ; Port A RDW Mode ; Port B RDW Mode ; ECC Mode ; ECC Pipeline Registers ; Fits in MLABs ;
; n2d_system:u_system|n2d_dcache:u_dcache|altsyncram:g_way[0].data_ram[0][127]__3|altsyncram_example:auto_generated|ALTSYNCRAM ; AUTO ; Simple Dual Port ; Single Clock ; 512 ; 128 ; 512 ; 128 ; yes ; no ; yes ; no ; 65536 ; 512 ; 128 ; 512 ; 128 ; 65536 ; 8 ; None ; M9K_X53_Y17_N0 ; Old data ; New data with NBE Read ; New data with NBE Read ; Off ; No ; No - Unknown ;
"""
        # Why: the sanitized row uses an inferred RAM name, not an explicit shared RAM
        # instance, so match it by that name to keep the schema check honest.
        config = {"ram_requirements": [{"name": "u_dcache|altsyncram:g_way[0].data_ram",
                                        "single_port": False, "count": 1, "depth": 512,
                                        "width": 128, "m9ks_each": 8}]}
        result = quartus.fitted_resources(report, config)
        self.assertEqual(result["M9Ks"]["used"], 8)
        self.assertEqual(result["ram_summary"][0]["Type"], "AUTO")
        for bad in (report.replace('; 8 ; None ;', '; 0 ; None ;'),
                    report.replace('; 512 ; 128 ;', '; 256 ; 128 ;'),
                    report.replace('g_way[0]', 'g_way[1]')):
            with self.assertRaises(RuntimeError):
                quartus.fitted_resources(bad, config)

    def test_every_ram_role_requires_its_used_old_data_port_and_single_clock(self):
        reports = self.reports()
        original = (reports / f'{self.config["project"]}.fit.rpt').read_text()
        for requirement in self.config["ram_requirements"]:
            name = (requirement["name"] if "name" in requirement else
                    f'{requirement["prefix"]}[0].{requirement["array"]}')
            row = next(line for line in original.splitlines() if name in line)
            for bad_row in (row.replace("Old data", "New data"),
                            row.replace("Old data", "Don't care"),
                            row.replace("Single Clock", "Dual Clock"),
                            row.replace("Single Port", "ROM").replace("Simple Dual Port", "True Dual Port")):
                with self.subTest(name=name, row=bad_row), self.assertRaisesRegex(RuntimeError, "RAM timing/mode"):
                    quartus.fitted_resources(original.replace(row, bad_row), self.config)
            cells = row.split(";")
            cells[7] = " 257 "  # Why: no requirement uses depth 257; the unused single-port B side is rejected too.
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, "RAM geometry"):
                quartus.fitted_resources(original.replace(row, ";".join(cells)), self.config)

    def test_all_twelve_sdram_reports_are_required_and_have_valid_paths(self):
        reports = self.reports()
        result = quartus.verify_reports(self.output, self.config)
        self.assertEqual(len(result["sdram_timing"]["paths"]), 12)
        self.assertEqual(result["sdram_timing"]["worst_margin_ns"],
                         dict.fromkeys(("read_setup", "read_hold", "write_setup", "write_hold"), 3.870))
        for file in sorted(reports.glob("sdram_*.rpt")):
            original = file.read_text()
            file.unlink()
            with self.subTest(file=file.name, case="missing"), self.assertRaisesRegex(RuntimeError, "SDRAM"):
                quartus.verify_reports(self.output, self.config)
            for bad in ("", original.replace("Found 1", "Found 0"),
                        original.replace("(0 violated)", "(1 violated)"),
                        original.replace("3.870", "-0.001"),
                        original.replace("; Slack ; 3.870", "; Slack ; 0.001"),
                        original.replace("Delay Model:", "Missing model:"),
                        original.replace("1200mV", "1100mV"),
                        original.replace("; From Node ;", "; Missing Node ;"),
                        re.sub(r"; To Node ; [^\n]+", "; To Node ; UART_TXD ;", original),
                        re.sub(r"; Launch Clock ; [^\n]+", "; Launch Clock ; reference ;", original),
                        re.sub(r"; Latch Clock ; [^\n]+", "; Latch Clock ; reference ;", original),
                        original.replace("Path #1:", "Path #2:")):
                file.write_text(bad)
                with self.subTest(file=file.name, content=bad), self.assertRaisesRegex(RuntimeError, "SDRAM"):
                    quartus.verify_reports(self.output, self.config)
            file.write_text(original)

    def test_generated_clock_requires_phase_source_master_and_pin(self):
        reports = self.reports()
        file = reports / f'{self.config["project"]}.sta.rpt'
        row = next(line for line in CLOCKS.splitlines() if line.startswith("; sdram_clock ;"))
        # Why: positions follow the sanitized real table above, so a wrong value in any
        # field fails, not only a clock-name mismatch.
        for index, value in ((2, "Base"), (3, "20.000"), (5, "0.000"), (6, "20.000"),
                             (8, "2"), (9, "2"), (14, "false"), (15, "reference"),
                             (16, "reference"), (17, "{ UART_TXD }")):
            cells = row.split(";")
            cells[index] = f" {value} "
            file.write_text(CLOCKS.replace(row, ";".join(cells)))
            with self.subTest(field=index), self.assertRaisesRegex(RuntimeError, "fitted clock"):
                quartus.verify_reports(self.output, self.config)
        for bad in (CLOCKS.replace(row, ""), CLOCKS + row + "\n",
                    CLOCKS.replace("|inclk[0]", "|inclk[1]"),
                    CLOCKS.replace("MAX10_CLK1_50", "UART_RXD"),
                    CLOCKS.replace("; jtag_tck ;", "; other_tck ;"),
                    CLOCKS.replace("{ altera_reserved_tck }", "{ UART_RXD }"),
                    CLOCKS.replace("; jtag_tck ; Base ; 100.000", "; jtag_tck ; Base ; 40.000")):
            file.write_text(bad)
            with self.assertRaisesRegex(RuntimeError, "fitted clock"):
                quartus.verify_reports(self.output, self.config)

    def test_sdram_read_endpoint_requires_exact_struct_field_and_bit_range(self):
        reports = self.reports()
        name = "sdram_read_slow_85_setup.rpt"
        original = (reports / name).read_text()
        good = "n2d_memory_controller:u_memory|n2a_sdram_ctrl:u_sdram|regs_q.read_data[78]"
        self.assertIn(good, original)
        for bad in ("n2d_memory_controller:u_memory|n2a_sdram_ctrl:u_sdram|read_data_q[78]",
                    "n2d_memory_controller:u_memory|n2a_sdram_ctrl:u_sdram|regs_q.write_data[78]",
                    "n2d_memory_controller:u_memory|n2a_sdram_ctrl:u_sdramx|regs_q.read_data[78]",
                    "n2d_memory_controller:u_memory|regs_q.read_data[78]",
                    "n2d_memory_controller:u_memory|n2a_sdram_ctrl:u_sdram|regs_q.read_data[128]",
                    "n2d_memory_controller:u_memory|n2a_sdram_ctrl:u_sdram|regs_q.read_data[7a]"):
            with self.subTest(bad=bad):
                (reports / name).write_text(original.replace(good, bad))
                with self.assertRaisesRegex(RuntimeError, "unexpected SDRAM timing endpoints/clocks"):
                    quartus.sdram_timing(reports, self.config, SYSTEM_CLOCK)
        (reports / name).write_text(original)
        result = quartus.sdram_timing(reports, self.config, SYSTEM_CLOCK)
        self.assertEqual(12, len(result["paths"]))

    def test_timing_gate_rejects_missing_corners_checks_and_ignored_constraints(self):
        reports = self.reports()
        timing = reports / f'{self.config["project"]}.sta.summary'
        original = timing.read_text()
        for bad in (original.replace('Fast 1200mV 0C', 'Fast 1200mV 85C'),
                    re.sub(r"Type : [^\n]+ Model Hold [\s\S]*?TNS : 0.000\n", '', original),
                    original.rsplit('TNS', 1)[0], 'Slack : 1.0\nSlack : 2.0\n'):
            timing.write_text(bad)
            with self.assertRaises(RuntimeError):
                quartus.verify_reports(self.output, self.config)
        timing.write_text(original)
        checks = reports / 'check_timing.rpt'
        original_check = checks.read_text()
        for bad in (original_check.replace('; no_clock ; 1 ;', '; no_clock ; 2 ;'),
                    original_check.replace('pll_lock_sync', 'product_state'),
                    original_check.replace('DRAM_CLK', 'DRAM_DQ[0]'), ''):
            checks.write_text(bad)
            with self.assertRaisesRegex(RuntimeError, 'check_timing'):
                quartus.verify_reports(self.output, self.config)
        checks.write_text(original_check)
        for bad in ('', '; set_false_path ; Ignored ;\n',
                    'No constraints were ignored.\n; set_output_delay ; Ignored ;\n'):
            (reports / 'ignored.rpt').write_text(bad)
            with self.assertRaisesRegex(RuntimeError, 'SDC constraints'):
                quartus.verify_reports(self.output, self.config)

    def test_hardware_gate_requires_artifacts_timing_and_reviewed_warnings(self):
        reports = self.reports()
        self.assertEqual(quartus.verify_reports(self.output, self.config)["minimum_slack_ns"], 0.125)
        timing = reports / f'{self.config["project"]}.sta.summary'
        original = timing.read_text()
        timing.write_text(original.replace('Slack : 0.125', 'Slack : -0.001', 1))
        with self.assertRaisesRegex(RuntimeError, "timing slack"):
            quartus.verify_reports(self.output, self.config)
        timing.write_text(original)
        (self.output / "compile.log").write_text("\n".join(warning_fixture(self.config)[0]) + "\n    Critical Warning (123): an unreviewed problem\n")
        with self.assertRaisesRegex(RuntimeError, "unreviewed"):
            quartus.verify_reports(self.output, self.config)
        (self.output / "compile.log").write_text("")
        (reports / "unconstrained.rpt").write_text("; Unconstrained Clocks ; 1 ; 1 ;")
        with self.assertRaisesRegex(RuntimeError, "timing audit"):
            quartus.verify_reports(self.output, self.config)
        (reports / f'{self.config["project"]}.sof').unlink()
        with self.assertRaisesRegex(RuntimeError, "missing Quartus artifact"):
            quartus.verify_reports(self.output, self.config)

    def test_stale_image_cannot_satisfy_successful_tool_exit(self):
        output = self.root / "workdir" / "quartus" / self.config["project"]
        output.mkdir(parents=True)
        old = output / "stale.sof"
        old.write_text("old")
        with patch.object(quartus, "ROOT", self.root), patch.object(quartus, "load_config", return_value=self.config), \
                patch.object(quartus, "stage"), patch.object(quartus, "run"), \
                patch.object(quartus.shutil, "which", return_value="quartus_sh"), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(quartus.main([]), 1)
        self.assertFalse(old.exists())

    def test_backend_pairs_select_exactly_one_file(self):
        pairs = (("src/common/rtl/memory/n2a_sync_ram.sv", "src/common/rtl/memory/n2a_sync_ram_intel.sv"),
                 ("src/common/rtl/memory/n2a_sdp_ram_portable.sv", "src/common/rtl/memory/n2a_sdp_ram_intel.sv"),
                 ("src/common/fpga/de10_lite/n2a_de10_lite_clock_portable.sv",
                  "src/common/fpga/de10_lite/n2a_de10_lite_clock_intel.sv"),
                 ("src/common/rtl/memory/n2a_tdp_ram_portable.sv", "src/common/rtl/memory/n2a_tdp_ram_intel.sv"))
        sources = self.config["sources"]
        for portable, intel in pairs:
            with self.subTest(pair=intel):
                self.assertEqual(1, sum(source in sources for source in (portable, intel)))
                self.assertIn(intel, sources)
                self.assertEqual(1, sum(1 for path in (ROOT / portable, ROOT / intel) if path.is_file()) - 1)
        for bad in (sources + ["src/common/fpga/de10_lite/n2a_de10_lite_clock_portable.sv"],
                    [source for source in sources if "n2a_de10_lite_clock" not in source]):
            with self.subTest(bad=len(bad)):
                self.assertNotEqual(1, sum(source in bad for source in pairs[2]))

    @unittest.skipUnless(shutil.which("verilator"), "verilator not installed")
    def test_virtual_jtag_wrapper_lints_against_vendor_port_stub(self):
        # Why: the board tops bind the common wrapper; linting it against the common
        # sld_virtual_jtag stub checks its vendor wiring without Quartus.
        sources = ["src/common/dv/jtag/sld_virtual_jtag_stub.sv",
                   "src/common/rtl/jtag/n2a_jtag_virtual.sv"]
        with tempfile.TemporaryDirectory() as directory:
            # Why: only the stub's file name differs from its vendor module name, so
            # DECLFILENAME is the one rule waived.
            waiver = Path(directory) / "stub.vlt"
            waiver.write_text('`verilator_config\n'
                              'lint_off -rule DECLFILENAME -file "*/sld_virtual_jtag_stub.sv"\n')
            result = subprocess.run(
                ["verilator", "--lint-only", "-Wall", "--top-module", "n2a_jtag_virtual",
                 str(waiver), *sources],
                cwd=ROOT, capture_output=True, text=True, timeout=120, check=False)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertNotIn("%Warning", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
