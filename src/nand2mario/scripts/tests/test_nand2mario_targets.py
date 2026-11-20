"""Project target compatibility and product source-closure checks."""
from pathlib import Path
import sys
import unittest

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))
from src.nand2mario.scripts.tests import target_view  # noqa: E402


class ProjectTargetTests(unittest.TestCase):
    def assert_source_order(self, sources, required):
        """Each required source occurs once and precedes its consumer."""
        for source in required:
            self.assertEqual(sources.count(source), 1, source)
        positions = [sources.index(source) for source in required]
        self.assertEqual(positions, sorted(positions), required)

    def test_project_targets(self):
        targets = target_view.load_targets()
        self.temp_path = REPO_ROOT / "workdir/unit-tests"
        self.assertEqual("cocotb", targets["n2m_timebase"].kind)
        self.assertEqual(390_649, targets["n2m_timebase"].max_cycles)
        self.assertEqual(
            "src.nand2mario.dv.core.test_timebase",
            targets["n2m_timebase"].test_module,
        )
        self.assertEqual("cocotb", targets["n2m_cpu_alu"].kind)
        self.assertEqual(23, targets["n2m_cpu_alu"].default_seed)
        self.assertEqual(5_338, targets["n2m_cpu_alu"].max_cycles)
        self.assertEqual(
            "src.nand2mario.dv.core.test_cpu_alu",
            targets["n2m_cpu_alu"].test_module,
        )

    def test_video_and_system_targets(self):
        targets = target_view.load_targets()
        ppu = targets["n2m_ppu"]
        self.assertEqual(("cocotb", "n2m_ppu_fixture", 71), (ppu.kind, ppu.top, ppu.default_seed))
        self.assertEqual("src.nand2mario.dv.video.test_ppu", ppu.test_module)
        self.assertIn("src/nand2mario/rtl/core/n2m_timebase.sv", ppu.sources)
        self.assertEqual("src/nand2mario/dv/video/n2m_ppu_fixture.sv", ppu.sources[-1])
        video = [s for s in ppu.sources if s.startswith("src/nand2mario/rtl/video/")]
        self.assertEqual(8, len(video))
        objects = targets["n2m_ppu_objects"]
        self.assertEqual(("cocotb", "n2m_ppu_fixture", 73), (objects.kind, objects.top, objects.default_seed))
        self.assertEqual("src.nand2mario.dv.video.test_ppu_objects", objects.test_module)
        self.assertEqual(ppu.sources, objects.sources)
        window = targets["n2m_ppu_window"]
        self.assertEqual(("n2m_ppu_fixture", 83, "ppu_window_contract"), (window.top, window.default_seed, window.testcase))
        self.assertEqual("src.nand2mario.dv.video.test_ppu_window", window.test_module)
        self.assertEqual(ppu.sources, window.sources)
        dma = targets["n2m_dma"]
        self.assertEqual(("cocotb", "n2m_dma_fixture", 79, "dma_contract"), (dma.kind, dma.top, dma.default_seed, dma.testcase))
        self.assertEqual("src.nand2mario.dv.dma.test_dma", dma.test_module)
        self.assertIn("src/nand2mario/rtl/memory/n2m_memory.sv", dma.sources)
        self.assertEqual("src/nand2mario/dv/dma/n2m_dma_fixture.sv", dma.sources[-1])
        display = targets["n2m_display"]
        self.assertEqual(("cocotb", "n2m_display", 83), (display.kind, display.top, display.default_seed))
        self.assertEqual("src.nand2mario.dv.video.test_display", display.test_module)
        self.assertEqual("display_contract", display.testcase)
        self.assertEqual(("src/common/rtl/vga/n2a_vga_timing.sv", "src/common/rtl/memory/n2a_sdp_ram_portable.sv"),
                         display.sources[:2])
        self.assertEqual("src/nand2mario/rtl/video/n2m_display.sv", display.sources[-1])
        display_video = [s for s in display.sources if s.startswith("src/nand2mario/rtl/video/")]
        self.assertEqual(7, len(display_video))
        snapshot = targets["n2m_display_snapshot"]
        self.assertEqual((display.top, display.sources), (snapshot.top, snapshot.sources))
        self.assertEqual(("src.nand2mario.dv.video.test_display_snapshot", "display_snapshot"),
                         (snapshot.test_module, snapshot.testcase))
        for name in ("n2m_system", "n2m_system_background", "n2m_system_objects", "n2m_system_vga",
                     "n2m_system_snapshot"):
            with self.subTest(target=name):
                target = targets[name]
                self.assertEqual("n2m_system", target.top)
                self.assertEqual(42, target.default_seed)
                self.assertEqual(video + display_video,
                                 [s for s in target.sources if s.startswith("src/nand2mario/rtl/video/")])
                for source in display.sources:
                    self.assertIn(source, target.sources)
                self.assertIn("src/nand2mario/rtl/dma/n2m_dma.sv", target.sources)
                self.assertEqual("src/nand2mario/rtl/system/n2m_system.sv", target.sources[-1])
        self.assertEqual("system_background_frame", targets["n2m_system_background"].testcase)
        self.assertEqual("system_objects_frame", targets["n2m_system_objects"].testcase)
        self.assertEqual("src.nand2mario.dv.system.test_system_vga", targets["n2m_system_vga"].test_module)
        self.assertEqual("system_vga_frame", targets["n2m_system_vga"].testcase)
        self.assertEqual(targets["n2m_system_background"].sources, targets["n2m_system_vga"].sources)
        self.assertEqual(targets["n2m_system_vga"].sources, targets["n2m_system_snapshot"].sources)
        self.assertEqual(("src.nand2mario.dv.system.test_system_snapshot", "system_frame_snapshot"),
                         (targets["n2m_system_snapshot"].test_module, targets["n2m_system_snapshot"].testcase))

    def test_interfaces_and_ram_targets(self):
        targets = target_view.load_targets()
        self.assertEqual("native", targets["n2m_interfaces"].kind)
        self.assertEqual("n2m_interfaces_fixture", targets["n2m_interfaces"].top)
        self.assertEqual(
            ("src/nand2mario/rtl/core/n2m_interfaces_pkg.sv",
             "src/nand2mario/dv/core/n2m_interfaces_fixture.sv"),
            targets["n2m_interfaces"].sources,
        )
        self.assertEqual("cocotb", targets["n2m_dual_port_ram_single"].kind)
        self.assertEqual(41, targets["n2m_dual_port_ram_single"].default_seed)
        self.assertEqual(20, targets["n2m_dual_port_ram_dual"].max_cycles)
        self.assertEqual(
            "dual_clock_collision_is_rejected",
            targets["n2m_dual_port_ram_dual_collision_fail"].testcase,
        )
        self.assertEqual(
            "partial_same_port_collision_is_rejected",
            targets["n2m_partial_read_write_fail"].testcase,
        )
        for name in (
            "n2m_dual_port_ram_single", "n2m_dual_port_ram_dual",
            "n2m_dual_port_ram_dual_collision_fail", "n2m_partial_read_write_fail",
            "n2m_dual_port_ram_reset",
        ):
            with self.subTest(target=name):
                target = targets[name]
                self.assertEqual("src.nand2mario.dv.memory.test_dual_port_ram",
                                 target.test_module)
                self.assertEqual("src/nand2mario/rtl/memory/n2m_dual_port_ram.sv",
                                 target.sources[0])
                self.assertTrue(Path(target.sources[1]).is_relative_to(
                    Path("src/nand2mario/dv/memory")))

    def test_cartridge_targets(self):
        targets = target_view.load_targets()
        mapper = targets["n2m_cartridge"]
        self.assertEqual(("cocotb", "n2m_cartridge", "mbc1_contract", 61),
                         (mapper.kind, mapper.top, mapper.testcase, mapper.default_seed))
        self.assertEqual("src.nand2mario.dv.cartridge.test_cartridge", mapper.test_module)
        self.assertEqual("src/nand2mario/rtl/cartridge/n2m_cartridge.sv", mapper.sources[-1])
        backing = targets["n2m_cartridge_backing"]
        negative = targets["n2m_cartridge_backing_rom_write_fail"]
        for target in (backing, negative):
            self.assertEqual(("n2m_cartridge_backing", "src.nand2mario.dv.cartridge.test_cartridge_backing"),
                             (target.top, target.test_module))
            self.assertEqual("src/nand2mario/rtl/cartridge/n2m_cartridge_backing.sv", target.sources[-1])
            self.assertIn("src/nand2mario/rtl/memory/n2m_dual_port_ram.sv", target.sources)
            self.assertEqual((("RAM_BYTES", 16384), ("ROM_BYTES", 65536)), tuple(sorted(target.parameters)))
        self.assertEqual(("backing_contract", "rom_write_is_rejected"), (backing.testcase, negative.testcase))
        cartridge = [s for s in mapper.sources if s.startswith("src/nand2mario/rtl/cartridge/")]
        for name, rom_bytes in (("n2m_system", 32768), ("n2m_system_background", 32768), ("n2m_system_mbc1", 1048576)):
            with self.subTest(target=name):
                target = targets[name]
                self.assertEqual("n2m_system", target.top)
                self.assertEqual(cartridge + ["src/nand2mario/rtl/cartridge/n2m_cartridge_backing.sv",
                                             "src/nand2mario/rtl/cartridge/n2m_library_pkg.sv",
                                             "src/nand2mario/rtl/cartridge/n2m_library.sv"],
                                 [s for s in target.sources if s.startswith("src/nand2mario/rtl/cartridge/")])
                self.assertEqual(rom_bytes, dict(target.parameters)["CART_ROM_BYTES"])
        self.assertEqual(("system_mbc1", "src.nand2mario.dv.system.test_system_mbc1"),
                         (targets["n2m_system_mbc1"].testcase, targets["n2m_system_mbc1"].test_module))

    def test_every_system_consumer_lists_library_implementation(self):
        targets = target_view.load_targets()
        system = "src/nand2mario/rtl/system/n2m_system.sv"
        package = "src/nand2mario/rtl/cartridge/n2m_library_pkg.sv"
        engine = "src/nand2mario/rtl/cartridge/n2m_library.sv"
        consumers = {name: target for name, target in targets.items() if system in target.sources}
        self.assertTrue(consumers)
        for name, target in consumers.items():
            with self.subTest(target=name):
                self.assert_source_order(target.sources, (package, engine, system))

    def test_every_mario_uart_consumer_lists_common_types(self):
        targets = target_view.load_targets()
        package = "src/common/rtl/pkg/n2a_types_pkg.sv"
        checked = set()
        for name, target in targets.items():
            if not name.startswith("n2m_"):
                continue
            for uart in ("src/common/rtl/uart/n2a_uart_rx.sv", "src/common/rtl/uart/n2a_uart_tx.sv"):
                if uart in target.sources:
                    with self.subTest(target=name, uart=uart):
                        self.assert_source_order(target.sources, (package, uart))
                    checked.add(name)
        self.assertTrue({"n2m_system_games", "n2m_system_menu", "n2m_system_menu_sdram"} <= checked)

    def test_source_closure_rejects_missing_duplicate_and_late_dependencies(self):
        targets = target_view.load_targets()
        sources = targets["n2m_system_games"].sources
        contracts = (
            ("src/common/rtl/pkg/n2a_types_pkg.sv", "src/common/rtl/uart/n2a_uart_rx.sv"),
            ("src/common/rtl/pkg/n2a_types_pkg.sv", "src/common/rtl/uart/n2a_uart_tx.sv"),
            ("src/nand2mario/rtl/cartridge/n2m_library_pkg.sv",
             "src/nand2mario/rtl/cartridge/n2m_library.sv",
             "src/nand2mario/rtl/system/n2m_system.sv"),
        )
        for required in contracts:
            for source in required:
                for kind, mutant in (
                    ("missing", tuple(s for s in sources if s != source)),
                    ("duplicate", (*sources, source)),
                ):
                    with self.subTest(contract=required, source=source, kind=kind):
                        with self.assertRaises(AssertionError):
                            self.assert_source_order(mutant, required)
            late = tuple(s for s in sources if s != required[0]) + (required[0],)
            with self.subTest(contract=required, kind="late"):
                with self.assertRaises(AssertionError):
                    self.assert_source_order(late, required)

    def test_licensed_games_keep_ordinary_cartridge_configuration(self):
        targets = target_view.load_targets()
        games = targets["n2m_system_games"]
        self.assertEqual(games.top, "n2m_system")
        self.assertEqual(dict(games.parameters).get("CART_LIBRARY", 0), 0)
        self.assertEqual(dict(games.parameters)["CART_ROM_BYTES"], 32768)
        self.assertEqual(games.testcase, "game_program")
        self.assertEqual(games.test_module, "src.nand2mario.dv.system.test_system_games")

    def test_enabled_menu_consumers(self):
        targets = target_view.load_targets()
        engine = targets['n2m_library']
        self.assertEqual((engine.top, engine.testcase), ('n2m_library_fixture', 'library_contract'))
        for name in ('n2m_system_menu', 'n2m_system_menu_sdram'):
            target = targets[name]
            self.assertEqual(dict(target.parameters)['CART_LIBRARY'], 1)
            self.assertEqual(target.testcase, 'system_menu')
            self.assertEqual(target.test_module, 'src.nand2mario.dv.menu.test_system_menu')
        self.assertEqual(targets['n2m_system_menu_sdram'].top, 'n2m_system_sdram_fixture')
        self.assertEqual(dict(targets['n2m_system_menu'].parameters)['CART_ROM_BYTES'], 1048576)

    def test_timer_interrupt_and_system_targets(self):
        targets = target_view.load_targets()
        self.assertEqual("src.nand2mario.dv.timer.test_timer", targets["n2m_timer"].test_module)
        self.assertEqual("timer_contract", targets["n2m_timer"].testcase)
        self.assertEqual(53, targets["n2m_timer"].default_seed)
        self.assertEqual("src.nand2mario.dv.interrupts.test_interrupts",
                         targets["n2m_interrupts"].test_module)
        self.assertEqual("interrupt_flags_contract", targets["n2m_interrupts"].testcase)
        for name in ("n2m_timer", "n2m_interrupts"):
            with self.subTest(target=name):
                self.assertEqual("cocotb", targets[name].kind)
                self.assertEqual(name, targets[name].top)
                self.assertEqual(f"src/nand2mario/rtl/{name[4:]}/{name}.sv", targets[name].sources[-1])
        system = targets["n2m_system"].sources
        self.assertIn("src/nand2mario/rtl/timer/n2m_timer.sv", system)
        self.assertIn("src/nand2mario/rtl/interrupts/n2m_interrupts.sv", system)
        self.assertEqual("src/nand2mario/rtl/system/n2m_system.sv", system[-1])

    def test_joypad_serial_and_system_targets(self):
        targets = target_view.load_targets()
        self.assertEqual("src.nand2mario.dv.joypad.test_joypad", targets["n2m_joypad"].test_module)
        self.assertEqual("joypad_contract", targets["n2m_joypad"].testcase)
        self.assertEqual(61, targets["n2m_joypad"].default_seed)
        self.assertEqual("src.nand2mario.dv.serial.test_serial", targets["n2m_serial"].test_module)
        self.assertEqual("serial_contract", targets["n2m_serial"].testcase)
        self.assertEqual(67, targets["n2m_serial"].default_seed)
        for name in ("n2m_joypad", "n2m_serial"):
            with self.subTest(target=name):
                self.assertEqual("cocotb", targets[name].kind)
                self.assertEqual(name, targets[name].top)
                self.assertEqual(f"src/nand2mario/rtl/{name[4:]}/{name}.sv", targets[name].sources[-1])
        system = targets["n2m_system"].sources
        self.assertIn("src/nand2mario/rtl/joypad/n2m_joypad.sv", system)
        self.assertIn("src/nand2mario/rtl/serial/n2m_serial.sv", system)
        self.assertEqual("src/nand2mario/rtl/system/n2m_system.sv", system[-1])

    def test_storage_and_loader_targets(self):
        targets = target_view.load_targets()
        ctrl, refresh = targets["n2m_sdram_ctrl"], targets["n2m_sdram_refresh_fail"]
        self.assertEqual(("cocotb", "n2m_sdram_ctrl_fixture", "sdram_ctrl_contract", 73),
                         (ctrl.kind, ctrl.top, ctrl.testcase, ctrl.default_seed))
        self.assertEqual("src.nand2mario.dv.storage.test_sdram_ctrl", ctrl.test_module)
        self.assertEqual(("src/nand2mario/rtl/storage/n2m_sdram_pkg.sv", "src/nand2mario/rtl/storage/n2m_sdram_ctrl.sv"),
                         ctrl.sources[:2])
        self.assertEqual("src/nand2mario/dv/storage/n2m_sdram_ctrl_fixture.sv", ctrl.sources[-1])
        self.assertEqual(ctrl.sources, refresh.sources)
        self.assertEqual((("REFRESH_DEADLINE_CLOCKS", 250), ("REFRESH_INTERVAL", 220)), refresh.parameters)
        self.assertEqual("refresh_deadline_is_enforced", refresh.testcase)
        backing, negative = targets["n2m_sdram_backing"], targets["n2m_sdram_backing_rom_write_fail"]
        for target in (backing, negative):
            self.assertEqual(("n2m_sdram_backing_fixture", "src.nand2mario.dv.storage.test_sdram_backing"),
                             (target.top, target.test_module))
            self.assertIn("src/nand2mario/rtl/storage/n2m_sdram_backing.sv", target.sources)
            self.assertIn("src/nand2mario/dv/storage/n2m_sdram_model.sv", target.sources)
        self.assertEqual(("backing_contract", "rom_write_is_rejected", 79), (backing.testcase, negative.testcase, backing.default_seed))
        loader = targets["n2m_uart_loader"]
        self.assertEqual(("n2m_uart_loader_fixture", "src.nand2mario.dv.uart.test_uart_loader", "loader_contract", 83),
                         (loader.top, loader.test_module, loader.testcase, loader.default_seed))
        self.assertEqual((("CLKS_PER_BIT", 8), ("RX_TIMEOUT_CLOCKS", 4000)), loader.parameters)
        for source in ("src/common/rtl/uart/n2a_uart_rx.sv", "src/common/rtl/uart/n2a_uart_tx.sv",
                       "src/nand2mario/rtl/memory/n2m_dual_port_ram.sv", "src/nand2mario/rtl/uart/n2m_loader_pkg.sv"):
            self.assertIn(source, loader.sources)
        self.assertEqual("src/nand2mario/dv/uart/n2m_uart_loader_fixture.sv", loader.sources[-1])
        sdram = targets["n2m_system_sdram"]
        self.assertEqual(("n2m_system_sdram_fixture", "src.nand2mario.dv.system.test_system_sdram", "system_sdram_boot", 42),
                         (sdram.top, sdram.test_module, sdram.testcase, sdram.default_seed))
        self.assertEqual((("FILL_SEED", 305419896), ("UART_CLKS_PER_BIT", 8)), sdram.parameters)
        self.assertEqual(targets["n2m_system"].sources, sdram.sources[:len(targets["n2m_system"].sources)])
        self.assertEqual("src/nand2mario/dv/system/n2m_system_sdram_fixture.sv", sdram.sources[-1])
        preload = targets["n2m_system_preload"]
        self.assertEqual(("n2m_system", "src.nand2mario.dv.system.test_system_preload", "system_direct_preload"),
                         (preload.top, preload.test_module, preload.testcase))
        self.assertEqual((("CART_RAM_BYTES", 32768), ("CART_ROM_BYTES", 131072), ("UART_CLKS_PER_BIT", 8)),
                         preload.parameters)
        self.assertEqual(targets["n2m_system"].sources, preload.sources)
        sdram_preload = targets["n2m_system_sdram_preload"]
        self.assertEqual(("n2m_system_sdram_fixture", "src.nand2mario.dv.system.test_system_preload",
                          "system_sdram_direct_preload"),
                         (sdram_preload.top, sdram_preload.test_module, sdram_preload.testcase))
        self.assertEqual((sdram.sources, sdram.parameters), (sdram_preload.sources, sdram_preload.parameters))
        # Every n2m_system composition carries the loader, the PHYs and both backings.
        for name in ("n2m_system", "n2m_system_background", "n2m_system_objects", "n2m_system_mbc1", "n2m_system_vga",
                     "n2m_system_preload"):
            with self.subTest(target=name):
                for source in ("src/nand2mario/rtl/uart/n2m_uart_loader.sv", "src/nand2mario/rtl/storage/n2m_sdram_backing.sv",
                               "src/common/rtl/sync/n2a_cdc_bit_sync.sv"):
                    self.assertIn(source, targets[name].sources)
