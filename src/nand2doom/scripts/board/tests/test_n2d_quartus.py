"""nand2doom board images through tools/quartus: pins cover every top port bit,
the Quartus closure is the board simulation's with Intel backends, the
generated projects equal the reviewed ones and the SDRAM path rules hold."""
import contextlib
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[5]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.bazel import hdl_libraries  # noqa: E402
from tools.quartus import build, manifest, quartus  # noqa: E402

MANIFEST = "src/nand2doom/cfg/n2d_quartus.json"
GOLDEN = Path(__file__).resolve().parent / "qsf"
BOARDS = {"de10_lite": "n2d_de10_lite", "de2_115": "n2d_de2_115"}
INTEL_FOR = {"src/common/rtl/memory/n2a_sync_ram.sv": "src/common/rtl/memory/n2a_sync_ram_intel.sv",
             "src/common/rtl/memory/n2a_sdp_ram_portable.sv": "src/common/rtl/memory/n2a_sdp_ram_intel.sv",
             "src/common/rtl/memory/n2a_tdp_ram_portable.sv": "src/common/rtl/memory/n2a_tdp_ram_intel.sv"}


def top_ports(path: Path) -> set:
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
    targets = json.loads((ROOT / "src/nand2doom/cfg/targets.json").read_text())["targets"]
    return hdl_libraries.resolve_target(targets[target], hdl_libraries.load(ROOT))["sources"]


class N2dQuartusTests(unittest.TestCase):
    def config(self, board):
        return manifest.load(ROOT, MANIFEST, board)

    def test_generated_projects_equal_the_reviewed_ones(self):
        # Why: the shared tool must hand Quartus the same project the per-project
        # script did; the golden files are that script's output.
        for board, image in BOARDS.items():
            with self.subTest(board):
                self.assertEqual(build.qsf(self.config(board)), (GOLDEN / f"{image}.qsf").read_text())

    def test_pins_cover_every_top_port_bit_once(self):
        for board, image in BOARDS.items():
            with self.subTest(board):
                config = self.config(board)
                self.assertEqual(set(config["pins"]), top_ports(ROOT / f"src/nand2doom/fpga/{board}/{image}.sv"))
                self.assertEqual(len(set(config["pins"].values())), len(config["pins"]))
        de10, de2 = self.config("de10_lite"), self.config("de2_115")
        self.assertEqual([de10["pins"][p] for p in ("RESET_N", "UART_RXD", "UART_TXD", "PS2_CLK", "PS2_DAT")],
                         ["B8", "AB5", "AB6", "V10", "W10"])
        # Ref: DE2-115 manual: JP5 UART (GPIO[0] RX, GPIO[1] TX) and the on-board PS/2 connector.
        self.assertEqual([de2["pins"][p] for p in ("UART_RX", "UART_TX", "PS2_CLK", "PS2_DAT")],
                         ["AB22", "AC15", "G6", "H5"])
        self.assertIsNone(de10["io_standards"]["RESET_N"])
        self.assertEqual(de2["io_standards"]["HEX3[1]"], "2.5 V")
        self.assertEqual(de2["io_standards"]["HEX3[2]"], "3.3-V LVTTL")

    def test_quartus_closure_is_the_simulation_closure_with_intel_backends(self):
        for board, image in BOARDS.items():
            with self.subTest(board):
                config = self.config(board)
                clock = f"src/common/fpga/{board}/n2a_{board}_clock_"
                intel_for = {**INTEL_FOR, clock + "portable.sv": clock + "intel.sv"}
                simulation = simulation_sources(image)
                self.assertEqual(set(config["sources"]),
                                 {intel_for.get(s, s) for s in simulation if "/dv/" not in s})
                for portable, intel in intel_for.items():
                    self.assertIn(intel, config["sources"])
                    self.assertNotIn(portable, config["sources"])
                self.assertIn("src/common/rtl/jtag/n2a_jtag_virtual.sv", config["sources"])
                self.assertFalse(any("fast" in s or "preload" in s for s in config["sources"]))

    def test_de2_115_ram_budget_and_forwarded_dac_clock(self):
        config = self.config("de2_115")
        self.assertEqual(sum(r["count"] * r["m9ks_each"] for r in config["ram_requirements"]), 320)
        self.assertEqual(config["forwarded_clocks"], {"sdram_clock": "DRAM_CLK", "vga_dac_clock": "VGA_CLK"})
        self.assertEqual(config["timeout_seconds"], 5400)

    def test_sdram_path_rules(self):
        groups = self.config("de10_lite")["timing_paths"]
        read = groups["sdram_read"]["endpoints"][0]["to"]
        self.assertTrue(re.fullmatch(read, "n2d_memory_controller:u_memory|n2a_sdram_ctrl:u_sdram|regs_q.read_data[78]"))
        for bad in ("n2d_memory_controller:u_memory|n2a_sdram_ctrl:u_sdram|read_data_q[78]",
                    "n2d_memory_controller:u_memory|n2a_sdram_ctrl:u_sdram|regs_q.write_data[78]",
                    "n2d_memory_controller:u_memory|n2a_sdram_ctrl:u_sdramx|regs_q.read_data[78]",
                    "n2d_memory_controller:u_memory|n2a_sdram_ctrl:u_sdram|regs_q.read_data[128]"):
            self.assertFalse(re.fullmatch(read, bad), bad)
        write = groups["sdram_write"]["endpoints"]

        def allowed(source, sink):
            return any(re.fullmatch(e["from"], source) and re.fullmatch(e["to"], sink) for e in write)
        self.assertTrue(allowed("n2a_reset_sync:u_reset|sync_q[1]", "DRAM_CKE"))
        self.assertFalse(allowed("n2a_reset_sync:u_reset|sync_q[1]", "DRAM_ADDR[3]"))
        self.assertTrue(allowed("n2d_memory_controller:u_memory|n2a_sdram_ctrl:u_sdram|regs_q.addr[3]", "DRAM_ADDR[3]"))
        self.assertFalse(allowed("n2d_memory_controller:u_memory|n2a_sdram_ctrl:u_sdram|x", "DRAM_CLK"))

    def test_plan_through_the_tool(self):
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(quartus.main(["--manifest", MANIFEST, "--board", "de2_115", "--plan"]), 0)
        self.assertIn("de2_115 n2d_de2_115: EP4CE115F29C7, 25000000 Hz", stdout.getvalue())
        self.assertIn("timeout 5400 s per invocation; output workdir/quartus/n2d_de2_115", stdout.getvalue())

    @unittest.skipUnless(shutil.which("verilator"), "verilator not installed")
    def test_virtual_jtag_wrapper_lints_against_vendor_port_stub(self):
        # Why: the board tops bind the common wrapper; linting it against the common
        # sld_virtual_jtag stub checks its vendor wiring without Quartus.
        sources = ["src/common/dv/jtag/sld_virtual_jtag_stub.sv", "src/common/rtl/jtag/n2a_jtag_virtual.sv"]
        with tempfile.TemporaryDirectory() as directory:
            waiver = Path(directory) / "stub.vlt"
            waiver.write_text('`verilator_config\nlint_off -rule DECLFILENAME -file "*/sld_virtual_jtag_stub.sv"\n')
            result = subprocess.run(["verilator", "--lint-only", "-Wall", "--top-module", "n2a_jtag_virtual",
                                     str(waiver), *sources], cwd=ROOT, capture_output=True, text=True,
                                    timeout=120, check=False)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertNotIn("%Warning", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
