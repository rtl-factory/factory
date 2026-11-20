"""Timing-table helper: what-if SDC, record parsing, classification; no Quartus."""
import contextlib
import importlib.util
import io
from pathlib import Path
import re
import shutil
import tempfile
import unittest
from unittest import mock

try:
    from markdown_it import MarkdownIt
except ImportError:  # pragma: no cover - optional renderer
    MarkdownIt = None

ROOT = Path(__file__).resolve().parents[5]
spec = importlib.util.spec_from_file_location("n2d_timing", ROOT / "src/nand2doom/scripts/board/n2d_timing.py")
timing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(timing)
SDC = (ROOT / "src/nand2doom/fpga/de10_lite/de10_lite.sdc").read_text()
CLOCK = "u_clock|u_pll|auto_generated|pll1|clk[0]"
SLOW, FAST = "Slow 1200mV 85C Model", "Fast 1200mV 0C Model"


def record(*cells):
    return "\t".join(str(cell) for cell in cells)


def table_rows(markdown, header):
    """GFM table rows (cells split on unescaped pipes) following a header row."""
    lines = markdown.splitlines()
    start = lines.index(header)
    rows = []
    for line in lines[start + 2:]:
        if not line.startswith("|"):
            break
        cells = re.split(r"(?<!\\)\|", line.strip())[1:-1]
        rows.append([cell.strip().replace("\\|", "|") for cell in cells])
    return rows


CLOCK_25 = [record("clock", corner, CLOCK, "40.000") for corner in (SLOW, FAST)]
TSV = "\n".join(CLOCK_25 + [
    record("fmax", SLOW, CLOCK, "41.2", "41.2"),
    record("domain", SLOW, "setup", CLOCK, "-3.5", "-120.0"),
    record("domain", SLOW, "hold", CLOCK, "0.3", "0.0"),
    record("path", SLOW, 1, "-3.5", "n2d_system:u_system|n2d_rv32im_core:u_core|state_q.pc[3]",
           "n2d_system:u_system|n2d_icache:u_icache|regs_q.state[1]", CLOCK, CLOCK, "20.0", "23.1", "17",
           "u_system|u_core|x~1 u_system|u_icache|y~2"),
    record("path", SLOW, 2, "-1.0", "DRAM_DQ[3]",
           "n2d_memory_controller:u_memory|n2d_sdram_controller:u_sdram|regs_q.read_data[3]",
           "sdram_clock", CLOCK, "10.0", "8.0", "1", ""),
    record("corepath", SLOW, 1, "9.1", "n2d_system:u_system|n2d_rv32im_core:u_core|regs_q.a[0]",
           "n2d_system:u_system|n2d_rv32im_core:u_core|regs_q.pc[9]", CLOCK, CLOCK, "40.0", "30.5", "21",
           "u_system|u_core|lpm_mult:Mult0|mult_core~7"),
    record("fmax", FAST, CLOCK, "70.0", "70.0", ""),  # Quartus 25.1 appends the note cell
    record("domain", FAST, "setup", CLOCK, "4.0", "0.0"),
    record("path", FAST, 1, "4.0", "a", "b", CLOCK, CLOCK, "20.0", "12.0", "3", "a b"),
]) + "\n"


class TimingTests(unittest.TestCase):
    def test_what_if_redefines_only_the_pll_system_clock_period(self):
        text = timing.what_if_sdc(SDC, 50)
        self.assertTrue(text.startswith(SDC.split("derive_pll_clocks\n")[0] + "derive_pll_clocks\n"))
        self.assertTrue(text.endswith(SDC.split("derive_pll_clocks\n")[1]))
        self.assertIn("-multiply_by 50 -divide_by 50", text)
        self.assertEqual(1, text.count("create_generated_clock -name $what_if_name"))
        # The PLL input pin comes from the derived clock, unescaped: an escaped
        # get_pins pattern matched nothing and Quartus silently kept 25 MHz.
        self.assertIn("-source [get_pins [get_clock_info -master_clock_pin $what_if_clock]]", text)
        self.assertNotIn("\\[", text.split("derive_pll_clocks\n")[1].split("# JTAG")[0])
        # The shipped file itself is never the what-if.
        self.assertNotIn("what_if", SDC)

    def test_what_if_rejects_unknown_sdc_and_range(self):
        with self.assertRaises(ValueError):
            timing.what_if_sdc(SDC.replace("derive_pll_clocks\n", ""), 50)
        with self.assertRaises(ValueError):
            timing.what_if_sdc(SDC, 0)

    def test_classification_owner_and_order(self):
        cases = {
            ("DRAM_DQ[0]", "x|u_sdram|regs_q.read_data[0]"): "SDRAM controller",
            ("u_system|u_core|lpm_mult:Mult0|mult_core", "u_system|u_core|state_q.rd"): "M extension",
            ("u_system|u_core|state_q.pc[2]", "u_system|u_dcache|regs_q.state"): "cache front end",
            ("u_system|u_core|state_q.instr[2]", "u_system|u_core|state_q.pc[7]"): "pipelined core",
            ("u_system|u_framebuffer|x", "u_system|u_framebuffer|y"): "glue",
            ("u_system|u_keyboard|q", "u_system|u_keyboard|d"): "glue",
            ("unknown_a", "unknown_b"): "glue",
        }
        for (source, target), owner in cases.items():
            self.assertEqual(owner, timing.classify(source, target)[1], (source, target))
        # A multiplier between core registers is found through the path's nodes.
        self.assertEqual("Multiply", timing.classify(
            "u_system|u_core|regs_q.instruction[3]", "u_system|u_core|regs_q.pc[4]",
            "u_system|u_core|u_decode_alu|lpm_mult:Mult0|mult_core~12")[0])

    def test_parse_and_table_use_the_worst_setup_corner(self):
        result = timing.parse(TSV)
        self.assertEqual(SLOW, timing.worst_corner(result))
        self.assertEqual(3, len(result["paths"]))
        table = timing.markdown(result, 50)
        self.assertIn("| 1 | -3.500 | 17 | `u_system\\|u_core\\|state_q.pc[3]` | "
                      "`u_system\\|u_icache\\|regs_q.state[1]` | I-cache lookup to state | cache front end |", table)
        self.assertIn("| 2 | -1.000 | 1 | `DRAM_DQ[3]` |", table)
        self.assertNotIn("| `a` |", table)  # other corners stay in the JSON only
        escaped = CLOCK.replace("|", "\\|")
        self.assertIn(f"| {SLOW} | setup | `{escaped}` | -3.500 | -120.000 |", table)
        self.assertEqual(1, len(result["core_paths"]))
        self.assertEqual("Multiply", result["core_paths"][0]["class"])
        self.assertIn("launched and latched by the system clock", table)
        self.assertIn("| 1 | 9.100 | 21 | `u_system\\|u_core\\|regs_q.a[0]` |", table)

    def test_table_cells_survive_hierarchy_pipes(self):
        table = timing.markdown(timing.parse(TSV), 25)
        header = "| # | Slack (ns) | Levels | From | To | Class | Owner |"
        rows = table_rows(table, header)
        self.assertEqual(["1", "-3.500", "17", "`u_system|u_core|state_q.pc[3]`",
                          "`u_system|u_icache|regs_q.state[1]`", "I-cache lookup to state",
                          "cache front end"], rows[0])
        self.assertTrue(all(len(row) == 7 for row in rows))
        fmax = table_rows(table, "| Corner | Clock | Fmax (MHz) | Restricted Fmax (MHz) |")
        self.assertEqual([SLOW, f"`{CLOCK}`", "41.20", "41.20"], fmax[0])

    @unittest.skipUnless(MarkdownIt, "markdown-it-py is not installed")
    def test_rendered_gfm_table_cells(self):
        rendered = MarkdownIt().enable("table").render(timing.markdown(timing.parse(TSV), 25))
        self.assertIn(f"<td><code>{CLOCK}</code></td>\n<td style=\"text-align:right\">41.20</td>", rendered)
        self.assertIn("<td><code>u_system|u_icache|regs_q.state[1]</code></td>\n"
                      "<td>I-cache lookup to state</td>", rendered)

    def test_mas_table_rows_match_their_headers(self):
        # GFM splits table cells on every unescaped pipe, inside backticks too.
        mas = (ROOT / "wiki/src/nand2doom/fpga/de10_lite/board-spec.md").read_text().splitlines()
        width = None
        for line in mas:
            if not line.startswith("|"):
                width = None
                continue
            cells = len(re.split(r"(?<!\\)\|", line.strip())) - 2
            width = width or cells
            self.assertEqual(width, cells, line)

    def test_mas_tables_render_code_cells(self):
        if MarkdownIt is None:
            self.skipTest("markdown-it-py is not installed")
        mas = (ROOT / "wiki/src/nand2doom/fpga/de10_lite/board-spec.md").read_text()
        rendered = MarkdownIt().enable("table").render(mas)
        self.assertIn("<td>C. SDRAM DQ read capture</td>\n"
                      "<td><code>DRAM_DQ</code> input (7 ns external delay after inverted "
                      "<code>DRAM_CLK</code>) to <code>u_sdram</code> <code>read_data</code>, "
                      "a half-cycle relationship</td>", rendered)

    def test_sta_only_refuses_a_clock_other_than_the_analyzed_one(self):
        workdir = ROOT / "workdir"
        workdir.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=workdir) as scratch:
            output = Path(scratch) / "fitted"
            (output / "db").mkdir(parents=True)

            def fake_sta(command, cwd, log, timeout):
                (cwd / "output_files").mkdir(exist_ok=True)
                (cwd / "output_files" / "timing.tsv").write_text(TSV)

            config = {"project": "n2d_de2_115", "timeout_seconds": 10}
            with mock.patch.object(timing.quartus, "run", side_effect=fake_sta):
                # A 25 MHz (40 ns) database requested as the 50 MHz what-if.
                with self.assertRaisesRegex(ValueError, "analyzed system clock is 25 MHz"):
                    timing.analyze(config, output, 50, 20, "/q/quartus_sh", sta_only=True)
                self.assertFalse((output / "timing.json").exists())
                self.assertFalse((output / "timing.md").exists())
                result = timing.analyze(config, output, 25, 20, "/q/quartus_sh", sta_only=True)
            self.assertEqual({"name": CLOCK, "period_ns": 40.0}, result["clock"])
            self.assertTrue((output / "timing.md").read_text().startswith("System clock 25 MHz."))

    def test_parse_rejects_unknown_or_incomplete_records(self):
        with self.assertRaises(ValueError):
            timing.parse(TSV + record("bogus", SLOW, "x") + "\n")
        with self.assertRaises(ValueError):
            timing.parse(record("fmax", SLOW, CLOCK, "41.2", "41.2") + "\n")
        with self.assertRaises(ValueError):  # no analyzed clock record
            timing.parse("\n".join(TSV.splitlines()[len(CLOCK_25):]) + "\n")
        with self.assertRaises(ValueError):  # corners disagree on the clock
            timing.parse(TSV + record("clock", FAST, CLOCK, "20.000") + "\n")

    def test_plan_writes_nothing_and_needs_no_tool(self):
        before = sorted((ROOT / "workdir").rglob("timing.tcl")) if (ROOT / "workdir").exists() else []
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(0, timing.main(["--plan", "--quartus", "/nonexistent/quartus_sh"]))
        self.assertIn("50 MHz: workdir/timing/n2d_de10_lite_50mhz", stdout.getvalue())
        after = sorted((ROOT / "workdir").rglob("timing.tcl")) if (ROOT / "workdir").exists() else []
        self.assertEqual(before, after)

    def test_board_selection_and_build_dir(self):
        de2_115 = (ROOT / "src/nand2doom/fpga/de2_115/n2d_de2_115.sdc").read_text()
        self.assertIn("-multiply_by 50 -divide_by 50", timing.what_if_sdc(de2_115, 50))
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(0, timing.main(["--plan", "--board", "de2_115", "--mhz", "25"]))
        self.assertIn("25 MHz: workdir/timing/n2d_de2_115_25mhz", stdout.getvalue())
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(0, timing.main(["--plan", "--board", "de2_115", "--mhz", "25", "--sta-only",
                                             "--build-dir", "workdir/quartus/n2d_de2_115"]))
        self.assertIn("25 MHz: workdir/quartus/n2d_de2_115", stdout.getvalue())
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            # The build directory is analysis input only: one clock, no compile, inside workdir.
            self.assertEqual(1, timing.main(["--plan", "--board", "de2_115", "--sta-only",
                                             "--build-dir", "workdir/quartus/n2d_de2_115"]))
            self.assertEqual(1, timing.main(["--plan", "--mhz", "25",
                                             "--build-dir", "workdir/quartus/n2d_de2_115"]))
            self.assertEqual(1, timing.main(["--plan", "--mhz", "25", "--sta-only",
                                             "--build-dir", "src/nand2doom"]))


if __name__ == "__main__":
    unittest.main()
