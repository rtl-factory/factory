"""Project target compatibility and product source-closure checks."""
from pathlib import Path
import importlib.util
import sys
import unittest

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))
from src.nand2doom.scripts.tests import target_view  # noqa: E402

_spec = importlib.util.spec_from_file_location("quartus", REPO_ROOT / "src/nand2doom/scripts/board/quartus.py")
quartus = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(quartus)


class ProjectTargetTests(unittest.TestCase):
    def test_product_rtl_layout_and_prefix_correct(self):
        project = REPO_ROOT / "src/nand2doom"
        rtl = project / "rtl"
        categories = {rtl / unit for unit in ("core", "memory", "io", "video", "platform")}
        self.assertEqual(categories, {path for path in rtl.iterdir()})
        files = sorted(rtl.rglob("*.sv"))
        self.assertTrue(files)
        self.assertEqual(files, sorted(path for path in rtl.rglob("*") if path.is_file()))
        for source in files:
            with self.subTest(source=source):
                self.assertIn(source.parent, categories)
                self.assertTrue(source.name.startswith("n2d_"))
                self.assertEqual(".sv", source.suffix)
        boards = {project / "fpga" / board for board in ("de10_lite", "de2_115")}
        board_files = sorted((project / "fpga").rglob("*.sv"))
        self.assertTrue(board_files)
        for source in board_files:
            self.assertIn(source.parent, boards)
        files += board_files
        targets = target_view.load_targets()
        consumers = {source for target in targets.values() for source in target.sources}
        # Why: Intel backends are consumed only by a board's Quartus source list, so
        # that list counts as their consumer.
        quartus_sources = {source for board in quartus.BOARDS for source in quartus.load_config(
            REPO_ROOT, "src/nand2doom/cfg/n2d_quartus.json", board)["sources"]}
        for source in files:
            relative = source.relative_to(REPO_ROOT).as_posix()
            if relative.endswith("_intel.sv"):
                self.assertIn(relative, quartus_sources)
                self.assertNotIn(relative, consumers)
            else:
                self.assertIn(relative, consumers)

    def test_verilator_targets_select_portable_backends_only(self):
        # Why: Verilator targets must simulate the portable backends, so none lists an
        # Intel backend and every board-top target lists exactly one clock backend.
        targets = target_view.load_targets()
        for board in ("de10_lite", "de2_115"):
            other = f"src/nand2doom/fpga/{board}"
            clock_files = (f"{other}/n2d_{board}_clock_portable.sv",
                           f"{other}/n2d_{board}_clock_intel.sv")
            board_targets = 0
            for target in targets.values():
                if not target.name.startswith("n2d_"):
                    continue  # Why: common stub targets deliberately lint Intel wiring
                with self.subTest(board=board, target=target.name):
                    self.assertFalse([s for s in target.sources if s.endswith("_intel.sv")])
                    if f"{other}/n2d_{board}.sv" in target.sources:
                        board_targets += 1
                        self.assertEqual([clock_files[0]],
                                         [s for s in target.sources if s in clock_files])
            self.assertGreaterEqual(board_targets, 1)

    def test_project_targets(self):
        targets = target_view.load_targets()
        self.temp_path = REPO_ROOT / "workdir/unit-tests"
        self.assertEqual(2_400_000, targets["n2d_c_runtime_system"].max_cycles)
        self.assertEqual(2_400_000, targets["n2d_integrated_system"].max_cycles)
        clock = targets["n2d_doom_clock"]
        self.assertEqual(targets["n2d_rv32im_core"].sources, clock.sources)
        self.assertEqual("n2d_rv32im_core", clock.top)
        self.assertEqual(300_000, clock.max_cycles)
        self.assertEqual(67, clock.default_seed)
        self.assertEqual(90, clock.timeout_seconds)
        self.assertEqual("compiled_doom_clock_contract", clock.testcase)
        observer = targets["n2d_doom_observer"]
        self.assertEqual("n2d_retirement_fixture", observer.top)
        self.assertEqual(128, observer.max_cycles)
        self.assertEqual("retirement_observer_contract", observer.testcase)
        product = targets["n2d_system_product"]
        self.assertEqual("n2d_system", product.top)
        self.assertFalse(
            any("preload" in source or "fast" in source for source in product.sources)
        )
        product_source = "\n".join(
            (REPO_ROOT / source).read_text(encoding="utf-8").lower()
            for source in product.sources
        )
        for simulation_only_term in ("preload", "backdoor", "force_error"):
            self.assertNotIn(simulation_only_term, product_source)
        synthesis_product = targets["n2d_system_product_synthesis"]
        self.assertEqual(product.sources, synthesis_product.sources)
        self.assertEqual(("SYNTHESIS",), synthesis_product.defines)
        self.assertEqual("cocotb", targets["n2d_rv32_regfile"].kind)
        self.assertEqual(192, targets["n2d_rv32_regfile"].max_cycles)
        self.assertEqual(
            "src.common.dv.riscv.test_rv32_regfile",
            targets["n2d_rv32_regfile"].test_module,
        )
        # Why: the Doom cores instantiate the common register file directly, so the
        # Doom-named target keeps its oracle, seed and bound on that module.
        self.assertEqual("n2a_rv32_regfile", targets["n2d_rv32_regfile"].top)
        self.assertEqual(
            ("src/common/rtl/riscv/n2a_rv32_regfile.sv",),
            targets["n2d_rv32_regfile"].sources,
        )
        for name in ("n2d_rv32i_core_step", "n2d_rv32i_program_smoke"):
            self.assertIn("src/common/rtl/riscv/n2a_rv32_regfile.sv", targets[name].sources)
            self.assertFalse([s for s in targets[name].sources if "n2d_rv32_regfile" in s])
        self.assertEqual("cocotb", targets["n2d_rv32i_decode_alu"].kind)
        self.assertEqual(25, targets["n2d_rv32i_decode_alu"].default_seed)
        self.assertEqual(4_608, targets["n2d_rv32i_decode_alu"].max_cycles)
        self.assertEqual(
            "src.nand2doom.dv.core.test_n2d_rv32i_decode_alu",
            targets["n2d_rv32i_decode_alu"].test_module,
        )
        self.assertEqual("cocotb", targets["n2d_rv32i_core_step"].kind)
        self.assertEqual(96, targets["n2d_rv32i_core_step"].max_cycles)
        self.assertEqual(
            "src.nand2doom.dv.core.test_n2d_rv32i_core_step",
            targets["n2d_rv32i_core_step"].test_module,
        )
        self.assertEqual(
            "src/common/rtl/riscv/n2a_rv32_regfile.sv",
            targets["n2d_rv32i_core_step"].sources[0],
        )
        self.assertEqual("cocotb", targets["n2d_rv32i_program_smoke"].kind)
        self.assertEqual(80, targets["n2d_rv32i_program_smoke"].max_cycles)
        self.assertEqual(
            "src.nand2doom.dv.core.test_n2d_rv32i_program_smoke",
            targets["n2d_rv32i_program_smoke"].test_module,
        )
        self.assertIn(
            "src/common/rtl/riscv/n2a_rv32_regfile.sv",
            targets["n2d_rv32i_program_smoke"].sources,
        )
        self.assertEqual("cocotb", targets["n2d_framebuffer_vga"].kind)
        self.assertEqual(32, targets["n2d_framebuffer_vga"].default_seed)
        self.assertEqual(128, targets["n2d_framebuffer_vga"].max_cycles)
        self.assertEqual(
            "framebuffer_cpu_palette_contract",
            targets["n2d_framebuffer_vga"].testcase,
        )
        self.assertEqual(
            2_300_000, targets["n2d_framebuffer_vga_frame"].max_cycles
        )
        self.assertEqual(
            "framebuffer_actual_frame_crc_contract",
            targets["n2d_framebuffer_vga_frame"].testcase,
        )
        self.assertEqual("cocotb", targets["n2d_uart_debug"].kind)
        self.assertEqual(33, targets["n2d_uart_debug"].default_seed)
        self.assertEqual(220_000, targets["n2d_uart_debug"].max_cycles)
        self.assertEqual(
            "src.nand2doom.dv.io.test_n2d_uart_debug",
            targets["n2d_uart_debug"].test_module,
        )
        self.assertIn(
            "src/common/rtl/uart/n2a_uart_rx.sv",
            targets["n2d_uart_debug"].sources,
        )
        self.assertEqual("cocotb", targets["n2d_ps2_keyboard"].kind)
        self.assertEqual(34, targets["n2d_ps2_keyboard"].default_seed)
        self.assertEqual(50_000, targets["n2d_ps2_keyboard"].max_cycles)
        self.assertEqual(
            "src/common/rtl/sync/n2a_cdc_bit_sync.sv",
            targets["n2d_ps2_keyboard"].sources[0],
        )
        self.assertEqual("cocotb", targets["n2d_rv32im_core"].kind)
        self.assertEqual(29, targets["n2d_rv32im_core"].default_seed)
        self.assertEqual(4_096, targets["n2d_rv32im_core"].max_cycles)
        self.assertEqual(
            "src.nand2doom.dv.core.test_n2d_rv32im_core",
            targets["n2d_rv32im_core"].test_module,
        )
        self.assertIn(
            "src/common/rtl/riscv/n2a_rv32_regfile.sv",
            targets["n2d_rv32im_core"].sources,
        )
