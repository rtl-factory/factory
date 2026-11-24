"""nand2mario board images through tools/quartus: the three manifests' pins,
closures, generated projects, path rules and the DE2 library profile."""
import contextlib
import io
import json
from pathlib import Path
import re
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[5]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from src.nand2mario.scripts.board import n2m_board as board  # noqa: E402
from tools.quartus import build, gate, manifest  # noqa: E402

GOLDEN = Path(__file__).resolve().parent / "qsf"
IMAGES = {"n2m_de10_lite": ("de10_lite", False), "n2m_de2_115": ("de2_115", False),
          "n2m_de2_115_library": ("de2_115", True)}


def top_ports(path: Path) -> set:
    """Every bit of a board top's port list, as the pin map names them."""
    source = re.sub(r"//[^\n]*", "", path.read_text())
    ports = source.split(") (", 1)[1].split(");", 1)[0]
    expected = set()
    for declaration in ports.split(","):
        declaration = declaration.strip()
        match = re.search(r"\[(\d+):0\]", declaration)
        name = declaration.split()[-1]
        expected.update([name] if match is None else [f"{name}[{i}]" for i in range(int(match[1]) + 1)])
    return expected


def simulation_sources(target: str) -> list:
    resolver = manifest.hdl_libraries(ROOT)
    targets = json.loads((ROOT / "src/nand2mario/cfg/targets.json").read_text())["targets"]
    return resolver.resolve_target(targets[target], resolver.load(ROOT))["sources"]


def removed_ram_warnings() -> list:
    """Independent spelling of the observed Quartus diagnostics of the library's removed save RAM."""
    headers = ["Warning (14284): Synthesized away the following node(s):",
               "Warning (14285): Synthesized away the following RAM node(s):"]
    return headers + [
        'Warning (14320): Synthesized away node "n2m_system:u_system|n2m_library:g_library.u_library|'
        "n2m_cartridge_backing:u_active|n2a_sync_ram_enabled:u_ram|n2a_sync_ram:u_ram|altsyncram:u_ram|"
        f'altsyncram_4g71:auto_generated|q_a[{i}]" File: /mock/db/altsyncram_4g71.tdf Line: {36 + 21 * i}'
        for i in range(8)]


class N2mQuartusTests(unittest.TestCase):
    def setUp(self):
        self.configs = {image: board.image_config(*selection) for image, selection in IMAGES.items()}

    def test_generated_projects_equal_the_reviewed_ones(self):
        # Why: the shared tool must hand Quartus the project the per-project script
        # did; Quartus ignores the order of pin assignments, so only that may differ.
        for image, config in self.configs.items():
            with self.subTest(image):
                generated = build.qsf(config).splitlines()
                golden = (GOLDEN / f"{image}.qsf").read_text().splitlines()
                self.assertEqual(sorted(generated), sorted(golden))
                pin = re.compile(r"set_(location|instance)_assignment ")
                self.assertEqual([line for line in generated if not pin.match(line)],
                                 [line for line in golden if not pin.match(line)])

    def test_pins_cover_every_top_port_bit_once(self):
        for image, config in self.configs.items():
            with self.subTest(image):
                top = ROOT / f'src/nand2mario/fpga/{config["board"]}/{config["top"]}.sv'
                self.assertEqual(top_ports(top), set(config["pins"]))
                self.assertEqual(len(set(config["pins"].values())), len(config["pins"]))
        de10, de2 = self.configs["n2m_de10_lite"], self.configs["n2m_de2_115"]
        self.assertEqual([de10["pins"][p] for p in ("MAX10_CLK1_50", "DRAM_CLK", "UART_RXD", "UART_TXD")],
                         ["P11", "L14", "AB5", "AB6"])
        self.assertIsNone(de10["io_standards"]["KEY[0]"])
        # Ref: Terasic DE2-115 manual (2013) table 4-1; JP7 supply = 2.5 V for this profile.
        for index, pin in enumerate(("AB28", "AC28", "AC27", "AD27", "AB27", "AC26", "AD26", "AB26")):
            self.assertEqual(de2["pins"][f"SW[{index}]"], pin)
            self.assertEqual(de2["io_standards"][f"SW[{index}]"], "2.5 V")
        self.assertEqual([de2["pins"][p] for p in ("UART_RXD", "UART_TXD")], ["AB22", "AC15"])
        settings = (ROOT / de2["settings"]).read_text()
        for bank in range(3):
            self.assertIn(f"n2a_sdp_ram:g_banks[{bank}].u_ram", settings)

    def test_quartus_closure_is_the_simulation_closure_with_intel_backends(self):
        for image, config in self.configs.items():
            with self.subTest(image):
                simulation = [s for s in simulation_sources(f'n2m_{config["board"]}') if "/dv/" not in s]
                # Why: n2a_sync_ram's portable file has no suffix.
                self.assertEqual([s.replace("n2a_sync_ram_intel.sv", "n2a_sync_ram.sv").replace("_intel.sv", "_portable.sv")
                                  for s in config["sources"]], simulation)
                self.assertTrue(any(s.endswith("_intel.sv") for s in config["sources"]))
                self.assertIn("src/common/rtl/jtag/n2a_jtag_virtual.sv", config["sources"])

    def test_sdram_and_dac_path_rules(self):
        for image, config in self.configs.items():
            with self.subTest(image):
                groups = config["timing_paths"]
                write = groups["sdram_write"]["endpoints"]

                def allowed(source, sink):
                    return any(re.fullmatch(e["from"], source) and re.fullmatch(e["to"], sink) for e in write)
                reset = "n2a_reset_sync:u_reset|reset_q"
                self.assertTrue(allowed(reset, "DRAM_CKE"))
                self.assertTrue(allowed(reset, "DRAM_CS_N"))
                self.assertFalse(allowed(reset, "DRAM_ADDR[4]"))
                self.assertFalse(allowed("u_other|q", "DRAM_CKE"))
                controller = "n2m_system:u_system|n2m_sdram_backing:g_sdram.u_backing|n2a_sdram_ctrl:u_ctrl|"
                self.assertTrue(allowed(controller + "state.phase", "DRAM_ADDR[4]"))
                self.assertFalse(allowed(controller + "state.phase", "DRAM_CLK"))
                read = groups["sdram_read"]["endpoints"][0]["to"]
                self.assertTrue(re.fullmatch(read, controller + "regs_q.read_data[15]"))
                self.assertFalse(re.fullmatch(read, controller + "regs_q.read_data[16]"))
                self.assertEqual(("sdram_clock", "system"), (groups["sdram_read"]["launch"],
                                                             groups["sdram_read"]["latch"]))
        dac = self.configs["n2m_de2_115"]["timing_paths"]["dac"]
        self.assertEqual(("system", "vga_dac_clock"), (dac["launch"], dac["latch"]))
        self.assertTrue(any(re.fullmatch(e["to"], "VGA_R[3]") for e in dac["endpoints"]))
        self.assertFalse(any(re.fullmatch(e["to"], "DRAM_DQ[3]") for e in dac["endpoints"]))
        self.assertEqual(self.configs["n2m_de2_115_library"]["timing_paths"],
                         self.configs["n2m_de2_115"]["timing_paths"])

    def test_library_profile_changes_only_the_cartridge(self):
        ordinary, library = self.configs["n2m_de2_115"], self.configs["n2m_de2_115_library"]
        self.assertEqual(library["parameters"], {"CART_LIBRARY": 1})
        for key in ordinary.keys() - {"project", "profile", "parameters", "ram_requirements", "reviewed_warnings"}:
            self.assertEqual(ordinary[key], library[key], key)
        self.assertEqual(library["ram_requirements"][:-1], ordinary["ram_requirements"])
        active = library["ram_requirements"][-1]
        self.assertEqual((active["count"], active["depth"], active["width"], active["m9ks_each"], active["mode"]),
                         (1, 65536, 8, 64, "True Dual Port"))
        source = (ROOT / "src/nand2mario/fpga/de2_115/n2m_de2_115.sv").read_text()
        self.assertIn("parameter int unsigned CART_LIBRARY = 0", source)

    def test_library_removed_ram_warnings_are_one_exact_set(self):
        library = self.configs["n2m_de2_115_library"]
        rules = [rule for rule in gate.warning_rules(library)
                 if rule["category"] == "reviewed_pattern" and rule["maximum"] == 1]
        self.assertEqual(len(rules), 10)

        def exact(lines):
            matches = [[rule for rule in rules if re.fullmatch(rule["pattern"], line)] for line in lines]
            return (all(len(found) == 1 for found in matches)
                    and sorted(id(found[0]) for found in matches) == sorted(id(rule) for rule in rules))
        good = removed_ram_warnings()
        self.assertTrue(exact(good))
        for label, lines in (("missing node", good[:-1]), ("duplicate node", good + [good[-1]]),
                             ("missing header", good[1:]),
                             ("other RAM", [w.replace(":u_active|", ":u_cartridge|") for w in good]),
                             ("wrong file", [w.replace("/db/altsyncram_4g71.tdf", "/db/altsyncram_x.tdf")
                                             for w in good])):
            with self.subTest(label):
                self.assertFalse(exact(lines))

    def test_compile_plans_through_the_tool_and_refuses_the_library_on_de10_lite(self):
        with contextlib.redirect_stdout(io.StringIO()) as stdout, contextlib.redirect_stderr(io.StringIO()), \
                patch.object(build, "run", side_effect=AssertionError("plan invoked Quartus")), \
                patch.object(build, "stage", side_effect=AssertionError("plan staged files")):
            self.assertEqual(board.main(["compile", "--plan"]), 0)
            self.assertEqual(board.main(["compile", "--board", "de2_115", "--library", "--plan"]), 0)
            self.assertNotEqual(board.main(["compile", "--board", "de10_lite", "--library", "--plan"]), 0)
            self.assertNotEqual(board.main(["program", "--board", "de10_lite", "--library"]), 0)
        self.assertIn("de10_lite n2m_de10_lite: 10M50DAF484C7G, 25000000 Hz", stdout.getvalue())
        self.assertIn("de2_115 n2m_de2_115: EP4CE115F29C7, 25000000 Hz, CART_LIBRARY=1", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
