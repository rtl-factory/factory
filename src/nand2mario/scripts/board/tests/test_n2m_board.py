"""DE10-Lite manifest, source selection and host-tool behavior, without Quartus or a board."""
import contextlib
import hashlib
import copy
import io
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[5]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from src.nand2mario.scripts.tests import target_view  # noqa: E402
from src.nand2mario.scripts.board import n2m_board
from src.nand2mario.scripts.board import n2m_quartus  # noqa: E402
from src.common.scripts.jtag import n2a_jtag_keyboard as jtag  # noqa: E402

CONFIG = "src/nand2mario/cfg/n2m_quartus.json"
TOP = ROOT / "src/nand2mario/fpga/de10_lite/n2m_de10_lite.sv"
PAIRS = (("src/nand2mario/fpga/de10_lite/n2m_de10_lite_clock_portable.sv", "src/nand2mario/fpga/de10_lite/n2m_de10_lite_clock_intel.sv"),
         ("src/common/rtl/memory/n2a_sdp_ram_portable.sv", "src/common/rtl/memory/n2a_sdp_ram_intel.sv"))


def setUpModule():
    """Temporary directories live below workdir/, which a fresh checkout lacks."""
    (ROOT / "workdir").mkdir(exist_ok=True)


def top_ports(path):
    """Every bit of the board top's port list, as the pin map names them."""
    source = re.sub(r"//[^\n]*", "", path.read_text())
    ports = source.split(") (", 1)[1].split(");", 1)[0]
    expected = set()
    for declaration in ports.split(","):
        declaration = declaration.strip()
        match = re.search(r"\[(\d+):0\]", declaration)
        name = declaration.split()[-1]
        expected.update([name] if match is None else [f"{name}[{i}]" for i in range(int(match[1]) + 1)])
    return expected


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.config = n2m_quartus.load_config(ROOT, CONFIG)

    def test_every_top_port_bit_has_exactly_one_pin(self):
        self.assertEqual(top_ports(TOP), set(self.config["pins"]))
        self.assertEqual(len(set(self.config["pins"].values())), len(self.config["pins"]))

    @unittest.skipUnless((ROOT / "src/nand2doom/cfg/n2d_quartus.json").exists(), "nand2doom is not ported")
    def test_board_pins_match_the_doom_de10_lite_manifest(self):
        doom = json.loads((ROOT / "src/nand2doom/cfg/n2d_quartus.json").read_text())["pins"]
        shared = set(doom) & set(self.config["pins"])
        self.assertGreaterEqual(len(shared), 60)   # Why: 60 is the clock, VGA, UART and every SDRAM pin
        for port in shared:
            self.assertEqual(doom[port], self.config["pins"][port], port)
        self.assertEqual(doom["RESET_N"], self.config["pins"]["KEY[0]"])
        self.assertTrue({p for p in doom if p.startswith(("DRAM_", "UART_"))} <= set(self.config["pins"]))

    def test_quartus_list_selects_only_intel_backends(self):
        sources = self.config["sources"]
        for portable, intel in PAIRS:
            self.assertIn(intel, sources)
            self.assertNotIn(portable, sources)
        self.assertIn("src/common/rtl/jtag/n2a_jtag_virtual.sv", sources)
        self.assertFalse([s for s in sources if "/dv/" in s or s.endswith(".v")])
        self.assertEqual("src/nand2mario/fpga/de10_lite/n2m_de10_lite.sv", sources[-1])

    def test_simulation_target_selects_only_portable_backends(self):
        targets = target_view.load_targets()
        board = targets["n2m_de10_lite"]
        for portable, intel in PAIRS:
            self.assertIn(portable, board.sources)
            self.assertNotIn(intel, board.sources)
        # Why: the vendor primitive exists only in Quartus, so simulation adds its dv/
        # stand-in; the common Virtual JTAG wrapper is the same in both lists.
        self.assertIn("src/common/rtl/jtag/n2a_jtag_virtual.sv", board.sources)
        self.assertIn("src/common/dv/jtag/sld_virtual_jtag_stub.sv", board.sources)
        self.assertEqual(("n2m_de10_lite_fixture", "board_smoke", 97), (board.top, board.testcase, board.default_seed))
        self.assertEqual((("HEARTBEAT_BIT", 20), ("UART_CLKS_PER_BIT", 8)), tuple(sorted(board.parameters)))
        self.assertIn("src/nand2mario/dv/storage/n2m_sdram_model.sv", board.sources)
        swapped = {intel: portable for portable, intel in PAIRS}
        quartus = [swapped.get(s, s) for s in self.config["sources"]]
        self.assertEqual(quartus, [s for s in board.sources if "/dv/" not in s])

    def test_manifest_rejections(self):
        cases = {
            "clock": lambda c: c.update(clock_hz=50_000_000),
            "duplicate pin": lambda c: c["pins"].update({"SW[0]": c["pins"]["SW[1]"]}),
            "escaping source": lambda c: c["sources"].append("../outside.sv"),
            "unknown key": lambda c: c.update(extra=1),
            "bad parameter": lambda c: c["parameters"].update(lower=1),
            "timeout": lambda c: c.update(timeout_seconds=0),
        }
        original = json.loads((ROOT / CONFIG).read_text())
        for label, mutate in cases.items():
            with self.subTest(case=label), tempfile.TemporaryDirectory(dir=ROOT / "workdir") as temporary:
                config = copy.deepcopy(original)
                mutate(config)
                path = Path(temporary) / "config.json"
                path.write_text(json.dumps(config))
                with self.assertRaises((ValueError, KeyError)):
                    n2m_quartus.load_config(ROOT, str(path.relative_to(ROOT)))

    def test_plan_writes_nothing(self):
        output = ROOT / "workdir/quartus/n2m_de10_lite"
        before = output.exists() and sorted(output.iterdir())
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            self.assertEqual(0, n2m_quartus.main(["--plan"]))
        self.assertIn("10M50DAF484C7G, 25000000 Hz", stream.getvalue())
        self.assertEqual(before, output.exists() and sorted(output.iterdir()))

    def test_stage_emits_parameters_pins_and_synthesis_macro(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "workdir") as temporary:
            output = Path(temporary) / "project"
            n2m_quartus.stage(ROOT, output, self.config)
            qsf = (output / "n2m_de10_lite.qsf").read_text()
        self.assertIn('VERILOG_MACRO "SYNTHESIS=1"', qsf)
        self.assertNotIn("set_parameter", qsf)   # Why: the board top's defaults (115200 baud, 0.67 s heartbeat) must apply unchanged
        self.assertIn("set_location_assignment PIN_L14 -to DRAM_CLK", qsf)
        self.assertIn("set_location_assignment PIN_AB5 -to UART_RXD", qsf)
        self.assertIn("set_location_assignment PIN_P11 -to MAX10_CLK1_50", qsf)
        self.assertNotIn('IO_STANDARD "3.3-V LVTTL" -to KEY[0]', qsf)   # Why: an LVTTL assignment here would override the fragment's Schmitt trigger on KEY[0]

    def test_output_confined_to_workdir(self):
        with self.assertRaises(ValueError):
            n2m_quartus.confined(ROOT, ROOT / "src")


class Args:
    def __init__(self, **values):
        defaults = dict(cable=None, device=None, quartus_bin=None, tck_hz=6000000,
                        clock_mode="fixed-original-6mhz", cable_index=None, seconds=20.0,
                        yes=False, mode="tap", hold=0.0, names=["start"], sof=n2m_board.DEFAULT_SOF)
        self.__dict__.update(defaults, **values)


class BoardToolTests(unittest.TestCase):
    def test_button_translation_matches_the_board_decoder(self):
        expected = {"right": "e0 74", "left": "e0 6b", "up": "e0 75", "down": "e0 72",
                    "a": "22", "b": "1a", "select": "29", "start": "5a"}
        for name, make in expected.items():
            with self.subTest(button=name):
                (pressed, released), = n2m_board.button_strokes([name], jtag)
                self.assertEqual(make, pressed.hex(" "))
                code = make.split()[-1]
                self.assertEqual(("e0 f0 " if name in ("right", "left", "up", "down") else "f0 ") + code,
                                 released.hex(" "))

    def test_tap_breaks_in_reverse_order(self):
        steps = n2m_board.key_steps(["start", "up"], "tap", jtag)
        self.assertEqual(["5a e0 75", "e0 f0 75 f0 5a"], [s.hex(" ") for s in steps])
        self.assertEqual(["5a e0 75"], [s.hex(" ") for s in n2m_board.key_steps(["start", "up"], "press", jtag)])

    def test_joypad_dry_run_opens_nothing(self):
        class NoOpen:
            key_strokes = staticmethod(jtag.key_strokes)
            settings = staticmethod(jtag.settings)

            @staticmethod
            def open_transport(_):
                raise AssertionError("dry run opened a transport")
        code, result = n2m_board.run_joypad(Args(names=["a", "b"]), jtag=NoOpen)
        self.assertEqual(0, code)
        self.assertIn("dry_run", result)
        self.assertEqual(6, result["bytes"])   # Ref: Set-2 make codes 22 1a, then breaks in reverse: f0 1a f0 22

    def test_missing_device_is_resolved_by_the_board_idcode(self):
        asked = []
        def resolve(cable, quartus_bin, idcode):
            asked.append((cable, idcode))
            return f"@1: part (0x{idcode})"
        for board, idcode in (("de10_lite", "031050DD"), ("de2_115", "020F70DD")):
            with self.subTest(board=board):
                code, result = n2m_board.run_joypad(Args(cable="USB-Blaster [1-1]", board=board),
                                                    jtag=jtag, resolve=resolve)
                self.assertEqual((0, f"@1: part (0x{idcode})"), (code, result["jtag_device"]))
        self.assertEqual([("USB-Blaster [1-1]", "031050DD"), ("USB-Blaster [1-1]", "020F70DD")], asked)

    def test_unknown_button_and_missing_cable_refused(self):
        with self.assertRaises(ValueError):
            n2m_board.run_joypad(Args(names=["turbo"]), jtag=jtag)
        with self.assertRaises(ValueError):
            n2m_board.run_joypad(Args(yes=True), jtag=jtag)

    def test_program_dry_run_and_refusals(self):
        def runner(*_, **__):
            raise AssertionError("dry run executed the programmer")
        code, result = n2m_board.run_program(Args(), runner=runner)
        self.assertEqual(0, code)
        self.assertIn("quartus_pgm -c '<cable>' -m jtag -o", result["command"])
        self.assertIn("dry_run", result)
        with self.assertRaises(ValueError):
            n2m_board.run_program(Args(yes=True), runner=runner)
        with self.assertRaises(ValueError):
            n2m_board.run_program(Args(sof="src/nand2mario/README.md"), runner=runner)

    def test_program_with_yes_runs_exact_command(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "workdir") as temporary:
            sof = Path(temporary) / "image.sof"
            sof.write_bytes(b"sof")
            from src.nand2mario.scripts.board.tests.sof_identity_fixture import companion_bytes
            inputs, evidence = companion_bytes(n2m_quartus.load_config(ROOT))
            sof.with_suffix(".inputs.json").write_bytes(inputs)
            sof.with_suffix(".evidence.json").write_bytes(evidence)
            sof.with_suffix(".json").write_text(json.dumps({
                "schema": "n2m-sof/1", "source_sha": "a" * 40, "quartus_version": "Quartus test version",
                "device": "10M50DAF484C7G", "project": "n2m_de10_lite",
                "sha256": hashlib.sha256(b"sof").hexdigest(),
                "inputs_sha256": hashlib.sha256(inputs).hexdigest(),
                "evidence_sha256": hashlib.sha256(evidence).hexdigest()}))
            calls = []

            class Done:
                returncode = 0

            def runner(command, **kwargs):
                calls.append((command, kwargs))
                return Done()
            code, result = n2m_board.run_program(
                Args(yes=True, cable="USB-Blaster [1-1]", sof=str(sof.relative_to(ROOT)), quartus_bin="/q/bin"),
                runner=runner, which=lambda name: name)
            self.assertEqual(0, code)
        self.assertEqual(["/q/bin/quartus_pgm", "-c", "USB-Blaster [1-1]", "-m", "jtag", "-o", f"p;{sof}@1"],
                         calls[0][0])

    def test_load_dry_run_writes_frames_only(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "workdir") as temporary:
            image = Path(temporary) / "image.gb"
            image.write_bytes(bytes(range(256)) * 3)
            dump = Path(temporary) / "frames.bin"
            args = Args(image=str(image), port=None, baud=115200, dump=str(dump.relative_to(ROOT)),
                        no_verify=False, yes=False)
            with contextlib.redirect_stdout(io.StringIO()):
                code, result = n2m_board.run_load(args)
            self.assertEqual(0, code)
            self.assertIn("dry_run", result)
            frames = dump.read_bytes()
        from src.nand2mario.scripts.board import cart_loader
        self.assertEqual(cart_loader.SYNC, frames[0])
        self.assertEqual(cart_loader.CMD_HOLD, frames[1])
        self.assertEqual(3, sum(1 for i in range(len(frames)) if frames[i:i + 2] == bytes([0xA5, cart_loader.CMD_WRITE])))
        self.assertEqual(0, sum(1 for i in range(len(frames)) if frames[i:i + 2] == bytes([0xA5, cart_loader.CMD_READ])))
        with self.assertRaises(ValueError):
            n2m_board.run_load(Args(image=str(image), port=None, baud=115200, dump="x", no_verify=False, yes=True))

    def test_snapshot_dry_run_writes_plan_only(self):
        from src.nand2mario.scripts.board import frame_snapshot
        with tempfile.TemporaryDirectory(dir=ROOT / "workdir") as temporary:
            plan = Path(temporary) / "plan.bin"
            args = Args(port=None, baud=115200, out="workdir/n2m_board/snapshot",
                        plan_file=str(plan.relative_to(ROOT)), yes=False)
            code, result = n2m_board.run_snapshot(args)
            self.assertEqual(0, code)
            self.assertIn("dry_run", result)
            self.assertEqual(plan.read_bytes(), b"".join(frame_snapshot.plan_frames()))
            self.assertEqual(27, result["frames"])
        with self.assertRaises(ValueError):
            n2m_board.run_snapshot(Args(port=None, baud=115200, out="workdir/x", plan_file="x", yes=False))
        with self.assertRaises(ValueError):
            n2m_board.run_snapshot(Args(port=None, baud=115200, out="workdir/x", plan_file="workdir/p", yes=True))
        with self.assertRaises(ValueError):
            n2m_board.run_snapshot(Args(port="/dev/x", baud=115200, out="/tmp/x", plan_file="workdir/p", yes=True))

        class Tool:
            calls = []

            @staticmethod
            def main(argv):
                Tool.calls.append(argv)
                return 1

        code, result = n2m_board.run_snapshot(Args(port="/dev/ttyUSB9", baud=115200, out="workdir/n2m_board/s",
                                                   plan_file="workdir/p", yes=True), snapshot_tool=Tool)
        self.assertEqual((1, "error"), (code, result["status"]))
        self.assertEqual(["capture", "--port", "/dev/ttyUSB9", "--baud", "115200", "--out",
                          str((ROOT / "workdir/n2m_board/s").resolve())], Tool.calls[0])

    def test_cli_help(self):
        for action in ("compile", "program", "load", "snapshot", "joypad"):
            with self.subTest(action=action), contextlib.redirect_stdout(io.StringIO()), \
                    self.assertRaises(SystemExit) as exit_:
                n2m_board.main([action, "--help"])
            self.assertEqual(0, exit_.exception.code)



SYSTEM_CLOCK = "u_clock|u_pll|auto_generated|pll1|clk[0]"
# Ref: a sanitized Quartus 25.1 Clocks table with its actual column order.
CLOCKS = """; Clock Name ; Type ; Period ; Frequency ; Rise ; Fall ; Duty Cycle ; Divide by ; Multiply by ; Phase ; Offset ; Edge List ; Edge Shift ; Inverted ; Master ; Source ; Targets ;
; reference ; Base ; 20.000 ; 50.0 MHz ; 0.000 ; 10.000 ; ; ; ; ; ; ; ; ; ; ; { MAX10_CLK1_50 } ;
; jtag_tck ; Base ; 100.000 ; 10.0 MHz ; 0.000 ; 50.000 ; ; ; ; ; ; ; ; ; ; ; { altera_reserved_tck } ;
; sdram_clock ; Generated ; 40.000 ; 25.0 MHz ; 20.000 ; 40.000 ; ; 1 ; 1 ; ; ; ; ; true ; SYSTEM ; SYSTEM ; { DRAM_CLK } ;
; SYSTEM ; Generated ; 40.000 ; 25.0 MHz ; 0.000 ; 20.000 ; 50.00 ; 2 ; 1 ; ; ; ; ; false ; reference ; u_clock|u_pll|auto_generated|pll1|inclk[0] ; { SYSTEM } ;
""".replace("SYSTEM", SYSTEM_CLOCK)


def sdram_report(config, corner, direction, kind, slack="5.000", **override):
    """One sanitized single-path report in the shape the audit writes."""
    capture = config["sdram_capture"] + "[3]"
    rows = {"From Node": "DRAM_DQ[3]" if direction == "read" else config["sdram_controller"] + "state.phase.SDRAM_WRITE",
            "To Node": capture if direction == "read" else "DRAM_ADDR[4]",
            "Launch Clock": "sdram_clock" if direction == "read" else SYSTEM_CLOCK,
            "Latch Clock": SYSTEM_CLOCK if direction == "read" else "sdram_clock",
            "Slack": slack}
    rows.update(override)
    return (f"Report Timing: Found 1 {kind} paths (0 violated).  Worst case slack is {rows['Slack']}\n"
            f"Delay Model: {corner} Model\n"
            f"Path #1: {kind.title()} slack is {rows['Slack']}\n" +
            "".join(f"; {key} ; {value} ;\n" for key, value in rows.items()))


class ReportGateTests(unittest.TestCase):
    def setUp(self):
        self.config = n2m_quartus.load_config(ROOT, CONFIG)

    def test_clock_relationships(self):
        clocks = n2m_quartus.fitted_clocks(CLOCKS, SYSTEM_CLOCK, self.config)
        self.assertEqual({"reference", "jtag_tck", "sdram_clock", SYSTEM_CLOCK}, set(clocks))
        for label, broken in (("not inverted", CLOCKS.replace("; true ;", "; false ;")),
                              ("wrong pin", CLOCKS.replace("{ DRAM_CLK }", "{ VGA_HS }")),
                              ("missing", "\n".join(l for l in CLOCKS.splitlines() if "sdram_clock" not in l))):
            with self.subTest(case=label), self.assertRaises(RuntimeError):
                n2m_quartus.fitted_clocks(broken, SYSTEM_CLOCK, self.config)

    def write_reports(self, directory, **override):
        for corner in self.config["timing_corners"]:
            label = corner.split()[0].lower() + "_" + corner.split()[-1].removesuffix("C")
            for direction in ("read", "write"):
                for kind in ("setup", "hold"):
                    (directory / f"sdram_{direction}_{label}_{kind}.rpt").write_text(
                        sdram_report(self.config, corner, direction, kind,
                                     **(override if (direction, kind) == ("read", "setup") else {})))

    def test_sdram_paths(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "workdir") as temporary:
            directory = Path(temporary)
            self.write_reports(directory)
            result = n2m_quartus.sdram_timing(directory, self.config, SYSTEM_CLOCK)
            self.assertEqual(12, len(result["paths"]))
            self.assertEqual(5.0, result["worst_margin_ns"]["read_setup"])
            for label, override in (("negative", {"slack": "-0.100"}),
                                    ("wrong capture", {"To Node": "u_other|q[3]"}),
                                    ("wrong latch", {"Latch Clock": "sdram_clock"})):
                with self.subTest(case=label), self.assertRaises(RuntimeError):
                    self.write_reports(directory, **override)
                    n2m_quartus.sdram_timing(directory, self.config, SYSTEM_CLOCK)
            (directory / "sdram_write_fast_0_hold.rpt").unlink()
            with self.assertRaises(RuntimeError):
                n2m_quartus.sdram_timing(directory, self.config, SYSTEM_CLOCK)

    def test_audit_requests_sdram_paths(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "workdir") as temporary:
            output = Path(temporary) / "project"
            n2m_quartus.stage(ROOT, output, self.config)
            audit = (output / "audit.tcl").read_text()
        self.assertIn("-from [get_ports {DRAM_DQ[*]}]", audit)
        self.assertIn("-to_clock [get_clocks sdram_clock]", audit)
        self.assertIn("report_clock_fmax_summary", audit)


if __name__ == "__main__":
    unittest.main()
