from pathlib import Path
import sys
import copy
import contextlib
import io
import hashlib
import json
from unittest.mock import patch
import tempfile
import unittest

REPO_ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(REPO_ROOT))
from src.nand2doom.scripts.perf import n2d_perf  # noqa: E402
from src.nand2doom.scripts.host.n2d_uart_debug_host import (  # noqa: E402
    CMD_RESET_PC, CMD_RESUME, input_request, request,
)

REPORT = """start backend=fast frames=1 max_cycles=1000
resumed cycle=100 retired=1
marker cycle=10 value=0xd00d3701 retired=5
frame index=0 start=100 end=200 retired_start=10 retired_end=40 crc=0x0000000a ihit=1 imiss=1 dhit=1 dmiss=1 after_scene=0
scene cycle=210 retired=50 crc=0x9da80229 boot_seen=1 ihit=90 imiss=10 dhit=40 dmiss=10
vga label=scene crc=0x516470a8 cycle=300
frame index=1 start=900 end=1000 retired_start=100 retired_end=250 crc=0x0000000b ihit=2 imiss=0 dhit=2 dmiss=0 after_scene=1 wait=60 spin=10
frame index=2 start=1900 end=2000 retired_start=300 retired_end=450 crc=0x0000000c ihit=2 imiss=0 dhit=2 dmiss=0 after_scene=1 wait=40 spin=0
swap geometry=full count=3 wait=150 spin=10 guard_violations=0
done status=pass cycle=2100 run_cycles=2000 retired=500 ihit=300 imiss=100 dhit=150 dmiss=50
"""


class N2dPerfTests(unittest.TestCase):
    def test_harness_builds_through_tools_sim_as_a_compile_only_target(self):
        sys.path.insert(0, str(REPO_ROOT / "tools/sim"))
        import sim  # noqa: E402
        targets = sim.load_targets(REPO_ROOT)
        for name in ("n2d_perf_fast", "n2d_perf_sdram", "n2d_perf_fast_half", "n2d_perf_sdram_half"):
            with self.subTest(target=name):
                self.assertEqual("compile", targets[name]["regression_action"])
                self.assertEqual([sys.executable, "tools/sim/sim.py", "test", name],
                                 n2d_perf.build_command(name))

    def test_uart_tokens_are_reset_pc_then_resume_with_gaps(self) -> None:
        tokens = n2d_perf.uart_tokens()
        first = list(request(CMD_RESET_PC, 1, address=0))
        second = list(request(CMD_RESUME, 2))
        self.assertEqual(tokens, first + [0x100] + second + [0x100])

    def test_input_events_are_queued_between_reset_pc_and_resume(self) -> None:
        tokens = n2d_perf.uart_tokens((0x016, 0x375))
        expected = (list(request(CMD_RESET_PC, 1, address=0)) + [0x100]
                    + list(input_request(2, 0x016)) + [0x100]
                    + list(input_request(3, 0x375)) + [0x100]
                    + list(request(CMD_RESUME, 4)) + [0x100])
        self.assertEqual(tokens, expected)
        for bad in ((0x400,), (-1,), (0x16,) * 9):
            with self.assertRaises(ValueError):
                n2d_perf.uart_tokens(bad)

    def test_report_parsing_and_summary(self) -> None:
        result = n2d_perf.parse_report(REPORT)
        self.assertEqual(result["resumed"], 100)
        self.assertEqual(result["scene"]["crc"], "0x9da80229")
        self.assertEqual(result["vga"]["scene"], "0x516470a8")
        self.assertEqual(len(result["frames"]), 3)
        summary = n2d_perf.summarize(result)
        self.assertEqual(summary["gameplay_frames"], 2)
        self.assertEqual(summary["cycles_per_frame"], 900)
        self.assertAlmostEqual(summary["gameplay_ipc"], 410 / 1800, places=4)
        self.assertEqual(summary["icache_hit_ratio"], 0.75)
        self.assertEqual(summary["dcache_hit_ratio"], 0.75)
        self.assertEqual(n2d_perf.check(result, "9da80229", "516470a8"), [])
        self.assertEqual(summary["swap_wait_cycles_per_frame"], 50)
        self.assertEqual(summary["swap_spin_cycles_per_frame"], 5)
        self.assertEqual(summary["swap"]["count"], 3)

    def test_check_rejects_writes_while_swap_pending(self) -> None:
        result = n2d_perf.parse_report(REPORT.replace("guard_violations=0", "guard_violations=1"))
        self.assertTrue(any("swap was pending" in e for e in n2d_perf.check(result, None, None)))

    def test_oracle_and_pins(self) -> None:
        result = n2d_perf.parse_report(REPORT + "vga label=frame-001 crc=0x00000011 cycle=1100\n"
                                       "vga label=frame-002 crc=0x00000012 cycle=2050\n")
        observed = n2d_perf.oracle(result)
        self.assertEqual(observed, {
            "scene": "0x9da80229", "scene_vga": "0x516470a8",
            "gameplay": ["0x0000000b", "0x0000000c"],
            "gameplay_vga": ["0x00000011", "0x00000012"]})
        pins = {"scene": "9da80229", "scene_vga": "516470a8",
                "gameplay": ["0b", "0c"], "gameplay_vga": ["11", "12"]}
        self.assertEqual(n2d_perf.check_pins(observed, pins), [])
        wrong = dict(pins, gameplay=["0b", "0d"])
        self.assertEqual(n2d_perf.check_pins(observed, wrong),
                         ["gameplay[1] CRC 0x0000000c != pinned 0d"])
        one_frame = dict(observed, gameplay=observed["gameplay"][:1],
                         gameplay_vga=observed["gameplay_vga"][:1])
        self.assertEqual(n2d_perf.check_pins(one_frame, pins), [])
        for key in ("scene", "scene_vga"):
            self.assertNotIn("PENDING", n2d_perf.DETERMINISTIC_PINS[key])

    def test_production_variant_rejects_pin_check(self) -> None:
        with self.assertRaises(SystemExit):
            n2d_perf.main(["--variant", "production", "--check-pins", "--dry-run"])

    def test_pin_check_rejects_injected_input(self) -> None:
        # Why: the pinned CRCs describe a run with no injected input, so the
        # combination is a usage error before any build, in either flag order.
        for flags in (["--check-pins", "--input", "0x16"],
                      ["--input", "0x16", "--input", "0x1c", "--check-pins"]):
            with self.subTest(flags=flags), \
                    contextlib.redirect_stderr(io.StringIO()) as stderr, \
                    self.assertRaises(SystemExit) as raised:
                n2d_perf.main([*flags, "--dry-run"])
            self.assertEqual(raised.exception.code, 2)
            self.assertIn("pins require no input", stderr.getvalue())
        for flags, pins, events in ((["--check-pins"], True, []),
                                    (["--input", "0x16"], False, [0x16])):
            with self.subTest(flags=flags), contextlib.redirect_stdout(io.StringIO()) as stdout:
                self.assertEqual(n2d_perf.main([*flags, "--dry-run"]), 0)
            plan = json.loads(stdout.getvalue())
            self.assertEqual((plan["check_pins"], plan["input"]), (pins, events))

    def test_pmon_and_cycle_lines(self) -> None:
        events = ("e2=100 e3=20 e4=60 e12=200 e13=2 e15=40 e16=4 e17=1 e19=1 e29=2 e30=5 "
                  "e34=70 e64=10 e65=3 e66=1 e67=6 e80=4 e81=3 e82=1 e94=14 e95=4 e99=7")
        classes = ("retire=100 divide=10 uncached_wait=6 dmiss_wait=54 load_use=100 "
                   "mul_use=100 data_admission=0 serialize=5 ifetch_wait=20 halted=0 other=5")
        frame_classes = classes.replace("retire=100", "retire=50")
        report = (REPORT
                  + f"pmon scope=gameplay index=1 {events}\ncycles scope=gameplay index=1 {frame_classes}\n"
                  + f"pmon scope=frame index=0 {events}\ncycles scope=frame index=0 {classes}\n"
                  + f"pmon scope=total index=0 {events}\ncycles scope=total index=0 {classes}\n")
        result = n2d_perf.parse_report(report)
        total = result["pmon"]["total"]
        self.assertEqual(total["events"]["DCACHE_WRITE_BUFFER_PENDING"], 14)
        self.assertEqual(total["events"]["DCACHE_WRITE_BUFFER_WAIT"], 4)
        self.assertEqual(total["events"]["INSTRET"], 100)
        self.assertEqual(total["events"]["EVENT_99"], 7)
        self.assertEqual(total["cycles"]["other"], 5)
        self.assertEqual([(i["index"], i["gameplay"]) for i in result["pmon"]["intervals"]],
                         [(1, True), (0, False)])
        summary = n2d_perf.summarize(result)
        self.assertEqual(list(summary)[:4],
                         ["overall_ipc", "scene_ipc", "gameplay_ipc", "gameplay_frame_ipc"])
        stack = summary["pmon_total"]
        self.assertEqual(list(stack)[0], "ipc")
        self.assertEqual(stack["ipc"], 0.25)
        self.assertEqual(summary["overall_ipc"], 0.25)
        self.assertEqual(stack["cycles"], 400)
        self.assertEqual(stack["coverage_percent"], 98.75)
        self.assertEqual(stack["cycle_percent"]["dmiss_wait"], 13.5)
        self.assertEqual(sum(stack["cycle_percent"].values()), 100.0)
        self.assertEqual(stack["icache_hit_ratio"], 0.99)
        self.assertEqual(stack["dcache_hit_ratio"], 0.9)
        self.assertEqual(stack["dmiss_penalty_cycles"], 10.8)
        self.assertEqual(stack["imiss_penalty_cycles"], 10.0)
        self.assertEqual(stack["line_read_latency_cycles"], 10.0)
        self.assertEqual(stack["uncached_wait_per_access"], 1.5)
        self.assertEqual(total["events"]["DCACHE_PREFETCH_DROP"], 1)
        self.assertEqual(stack["dcache_prefetch_accuracy"], 0.75)
        gameplay = summary["pmon_gameplay"]
        self.assertEqual(gameplay["frames"], 1)
        self.assertEqual(gameplay["cycles"], 350)
        self.assertEqual(summary["gameplay_frame_ipc"], [round(100 / 350, 4)])

    def test_stall_split_lines_and_self_check(self) -> None:
        classes = ("retire=100 divide=0 uncached_wait=0 dmiss_wait=40 load_use=0 "
                   "mul_use=0 data_admission=0 serialize=0 ifetch_wait=60 halted=0 other=0")
        split = ("ifetch_predict=10 ifetch_mispredict=20 ifetch_redirect=2 ifetch_miss=27 "
                 "ifetch_other=1 dmiss_service=20 dmiss_fetch=4 dmiss_prefetch=3 "
                 "dmiss_writeback=2 dmiss_debug=0 dmiss_refresh=1 dmiss_cache=10")
        events = "e2=100 e83=10 e84=20 e85=2 e86=27 e87=9 e88=30 e89=8 e90=3 e91=0"
        report = (REPORT
                  + f"pmon scope=gameplay index=1 {events}\ncycles scope=gameplay index=1 {classes}\n"
                  + f"split scope=gameplay index=1 {split}\n"
                  + f"pmon scope=total index=0 {events}\ncycles scope=total index=0 {classes}\n"
                  + f"split scope=total index=0 {split}\n")
        result = n2d_perf.parse_report(report)
        self.assertEqual(result["pmon"]["total"]["events"]["IFETCH_SUPPLY_WAIT"], 27)
        self.assertEqual(result["pmon"]["total"]["events"]["MEM_OWNER_DATA_READ"], 30)
        self.assertEqual(n2d_perf.check(result, None, None), [])
        split_report = n2d_perf.summarize(result)["pmon_gameplay"]["stall_split"]
        ifetch = split_report["ifetch_wait"]
        self.assertEqual(ifetch["cycles"], 60)
        self.assertEqual(ifetch["classes"]["ifetch_miss"],
                         {"cycles": 27, "cycle_percent": 13.5, "parent_percent": 45.0})
        dmiss = split_report["dmiss_wait"]["classes"]
        self.assertEqual(sum(c["cycles"] for c in dmiss.values()), 40)
        self.assertEqual(dmiss["dmiss_service"]["parent_percent"], 50.0)
        broken = n2d_perf.parse_report(report.replace("dmiss_cache=10", "dmiss_cache=9"))
        errors = n2d_perf.check(broken, None, None)
        self.assertEqual(len(errors), 2)  # Why: the broken split shows in both the gameplay interval and the total
        self.assertTrue(all("dmiss_wait split sums to 39, class is 40" in e for e in errors))

    def test_fetch_supply_wait_prefers_the_exact_event(self) -> None:
        # Why: with the fetch BTB a taken transfer may cost no refill clock, so the
        # exact IFETCH_SUPPLY_WAIT replaces the two-per-redirect estimate.
        events = {"IFETCH_WAIT": 100, "BRANCH_REDIRECT": 60, "IFETCH_SUPPLY_WAIT": 30}
        self.assertEqual(n2d_perf.fetch_supply_wait(events), 30)
        del events["IFETCH_SUPPLY_WAIT"]
        self.assertEqual(n2d_perf.fetch_supply_wait(events), 0)
        events["BRANCH_REDIRECT"] = 20
        self.assertEqual(n2d_perf.fetch_supply_wait(events), 60)

    def test_report_without_pmon_lines_has_no_stack(self) -> None:
        summary = n2d_perf.summarize(n2d_perf.parse_report(REPORT))
        self.assertNotIn("pmon_total", summary)
        self.assertNotIn("pmon_gameplay", summary)

    def test_check_rejects_crc_mismatch_trap_and_failure(self) -> None:
        result = n2d_perf.parse_report(REPORT.replace("crc=0x9da80229", "crc=0x00000001"))
        self.assertTrue(any("scene CRC" in e for e in n2d_perf.check(result, "9da80229", None)))
        failed = n2d_perf.parse_report(REPORT.replace("status=pass", "status=cycle-limit")
                                       + "trap cycle=5 pc=0x00000010 cause=0x00000002 value=0x0\n")
        errors = n2d_perf.check(failed, None, None)
        self.assertTrue(any(e.startswith("trap") for e in errors))
        self.assertTrue(any("did not pass" in e for e in errors))

    def test_fst_window_end_is_a_successful_run(self) -> None:
        result = n2d_perf.parse_report(
            "fst on cycle=200000\nfst off cycle=200500\n"
            "done status=fst-window cycle=200500 run_cycles=70013 retired=1 ihit=1 imiss=0 dhit=0 dmiss=0\n")
        self.assertEqual(n2d_perf.check(result, None, None), [])
        self.assertTrue(n2d_perf.check(result, "9da80229", None))

    def test_preload_lines_verify_segment_sizes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "a.bin").write_bytes(b"1234")
            manifest = {"segments": [{"path": "a.bin", "byte_length": 4, "address": 0x2000000}]}
            self.assertEqual(n2d_perf.preload_lines(root, manifest),
                             [f"2000000 {(root / 'a.bin').resolve()}"])
            manifest["segments"][0]["byte_length"] = 5
            with self.assertRaises(ValueError):
                n2d_perf.preload_lines(root, manifest)

    def test_dry_run_rejects_output_outside_workdir(self) -> None:
        with self.assertRaises(SystemExit):
            n2d_perf.main(["--dry-run", "--output", "../outside"])
        self.assertEqual(n2d_perf.main(["--dry-run", "--backend", "sdram"]), 0)
        self.assertEqual(n2d_perf.main(["--dry-run", "--geometry", "half"]), 0)
        with self.assertRaises(SystemExit):
            n2d_perf.main(["--dry-run", "--geometry", "half", "--fst", "1:2"])

    def test_compress_stream_replaces_raw_file(self) -> None:
        import gzip
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "stream.bin"
            raw.write_bytes(b"N2DMST01" + bytes(range(40)))
            target = n2d_perf.compress_stream(raw)
            self.assertFalse(raw.exists())
            self.assertEqual(target.name, "stream.bin.gz")
            with gzip.open(target) as handle:
                self.assertEqual(handle.read(), b"N2DMST01" + bytes(range(40)))




class GameStateTests(unittest.TestCase):
    def test_state_report_keeps_each_frame_and_scene(self):
        states = ("state label=frame index=41 cycle=10 retired=20 gametic=1 maketic=2 "
                  "ticdup=1 leveltime=1 prndindex=12 rndindex=13 fuzzpos=0\n"
                  "state label=scene index=42 cycle=11 retired=21 gametic=1 maketic=2 "
                  "ticdup=1 leveltime=1 prndindex=12 rndindex=13 fuzzpos=0\n")
        result = n2d_perf.parse_report(states)
        self.assertEqual(["frame", "scene"], [s["label"] for s in result["states"]])
        self.assertEqual([41, 42], [s["index"] for s in result["states"]])
        self.assertEqual(12, result["states"][0]["prndindex"])

    def test_all_state_symbols_require_unique_aligned_bss_objects(self):
        from src.nand2doom.dv.software.doom_gameplay import parse_symbols
        text = "".join(f"{0x100+4*i:08x} 00000004 {('b' if name == 'maketic' else 'B')} {name}\n"
                       for i, name in enumerate(n2d_perf.GAME_STATE_SYMBOLS))
        symbols = parse_symbols(text, n2d_perf.GAME_STATE_SYMBOLS)
        self.assertEqual(set(n2d_perf.GAME_STATE_SYMBOLS), set(symbols))
        for invalid in (text + text, text.replace("00000104", "00000100"),
                        text.replace("00000004", "00000001"),
                        text.replace(" B fuzzpos", " D fuzzpos")):
            with self.assertRaises(AssertionError):
                parse_symbols(invalid, n2d_perf.GAME_STATE_SYMBOLS)


class RenderOracleTests(unittest.TestCase):
    def sample(self, root):
        fixture = copy.deepcopy(n2d_perf.render_oracle())
        result = n2d_perf.parse_report(REPORT)
        result["frames"][1]["crc"], result["frames"][2]["crc"] = fixture["crcs"]["gameplay"]
        result["vga"].update({"frame-001": fixture["crcs"]["gameplay_vga"][0],
                              "frame-002": fixture["crcs"]["gameplay_vga"][1]})
        result["states"] = [dict(state, label="scene" if i == 0 else "frame", index=i)
                            for i, state in enumerate(fixture["states"])]
        result["single_tic"] = [dict(display=i, tick_calls=i+1,
                                     since_previous=2 if i == 1 else 1, mode=1)
                                for i in range(1, 5)]
        for group in ("framebuffer_sha256", "vga_ppm_sha256"):
            for name in fixture[group]:
                data = name.encode()
                (root / name).write_bytes(data)
                fixture[group][name] = hashlib.sha256(data).hexdigest()
        return result, fixture

    def test_state_call_crc_and_raw_pixel_oracle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result, fixture = self.sample(root)
            self.assertEqual(n2d_perf.check_render(result, root, fixture), [])
            mutations = [
                lambda r: r["states"][1].update(maketic=9),
                lambda r: r["states"].append(r["states"][1]),
                lambda r: r["single_tic"][0].update(mode=0),
                lambda r: r["single_tic"][1].update(since_previous=2),
                lambda r: r["single_tic"].clear(),
                lambda r: r["frames"].pop(),
                lambda r: r["frames"][1].update(crc="0x0"),
                lambda r: r["vga"].pop("frame-002"),
                lambda r: r["done"].update(status="fst-window"),
                lambda r: r.update(trap={"pc": "0x0"}),
                lambda r: r["swap"].update(guard_violations=1),
                lambda r: r["swap"].update(geometry="half"),
                lambda r: r["swap"].pop("guard_violations"),
                lambda r: r.update(swap=None),
            ]
            for mutate in mutations:
                altered = copy.deepcopy(result)
                mutate(altered)
                with self.subTest(mutate=mutate):
                    self.assertTrue(n2d_perf.check_render(altered, root, fixture))
            pixel = root / "doom-frame-002-vga.ppm"
            pixel.write_bytes(b"corrupted")
            self.assertTrue(n2d_perf.check_render(result, root, fixture))
            pixel.unlink()
            self.assertTrue(n2d_perf.check_render(result, root, fixture))

    def test_render_cli_is_explicit_and_no_input_only(self):
        for flags, workload in (([], "deterministic"), (["--variant", "render-verification"], "render-verification")):
            with contextlib.redirect_stdout(io.StringIO()) as stdout:
                self.assertEqual(n2d_perf.main([*flags, "--dry-run"]), 0)
            plan = json.loads(stdout.getvalue())
            self.assertEqual(plan["workload"], workload)
            self.assertEqual(plan["check_pins"], workload == "render-verification")
            self.assertEqual(plan["game_state"], workload == "render-verification")
            if workload == "render-verification":
                self.assertTrue(plan["output"].endswith("fast-render-verification"))
        for flags in (["--input", "0x16"], ["--frames", "1"],
                      ["--fst", "1:2"]):
            with self.subTest(flags=flags), self.assertRaises(SystemExit):
                n2d_perf.main(["--variant", "render-verification", "--dry-run", *flags])

    def test_half_geometry_selects_shared_states_and_independent_pixels(self):
        full, half = n2d_perf.render_oracle(), n2d_perf.render_oracle("half")
        self.assertEqual(half["states"], full["states"])
        self.assertEqual(half["provenance"], full["provenance"])
        self.assertNotEqual(half["crcs"], full["crcs"])
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(n2d_perf.main(["--variant", "render-verification", "--geometry", "half", "--dry-run"]), 0)
        self.assertEqual(json.loads(stdout.getvalue())["target"], "n2d_perf_fast_half")
        with self.assertRaises(SystemExit):
            n2d_perf.main(["--geometry", "half", "--check-pins", "--dry-run"])
        with tempfile.TemporaryDirectory() as tmp:
            result, fixture = self.sample(Path(tmp))
            fixture["geometry"] = "half"
            result["swap"]["geometry"] = "half"
            self.assertEqual(n2d_perf.check_render(result, Path(tmp), fixture), [])
            fixture["geometry"] = "full"
            self.assertTrue(n2d_perf.check_render(result, Path(tmp), fixture))
        with self.assertRaises(ValueError):
            n2d_perf.render_oracle("unknown")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "half.json"
            metadata = json.loads(n2d_perf.RENDER_HALF_ORACLE_PATH.read_text())
            metadata["full_oracle"]["sha256"] = "00" * 32
            path.write_text(json.dumps(metadata))
            with patch.object(n2d_perf, "RENDER_HALF_ORACLE_PATH", path), self.assertRaises(ValueError):
                n2d_perf.render_oracle("half")

    def test_legacy_pin_image_checks_bytes_elf_and_layout(self):
        from src.nand2doom.scripts.sw_build import n2d_doom
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("program.bin", "wad.bin", "program.elf"):
                (root / name).write_bytes(name.encode())
            digest = lambda name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            manifest = dict(architecture="rv32im", abi="ilp32", endianness="little", entry=0,
                            variant=dict(name="deterministic", **{k: v for k, v in
                                n2d_doom.VARIANTS["deterministic"].items() if k != "output"}),
                            capture={"elf": {"path": "program.elf"}},
                            segments=[dict(path=name, address=address, byte_length=len(name), sha256=digest(name))
                                      for name, address in (("program.bin", 0), ("wad.bin", 0x2000000))])
            with patch.object(n2d_perf, "LEGACY_IMAGE_SHA256", digest("program.bin")), \
                 patch.object(n2d_perf, "LEGACY_ELF_SHA256", digest("program.elf")), \
                 patch.object(n2d_doom, "WAD_SHA256", digest("wad.bin")):
                n2d_perf.validate_pin_image(root, manifest, "deterministic")
                altered = copy.deepcopy(manifest)
                altered["segments"][1]["address"] += 4
                with self.assertRaisesRegex(ValueError, "configuration"):
                    n2d_perf.validate_pin_image(root, altered, "deterministic")
                (root / "program.elf").write_bytes(b"different")
                with self.assertRaisesRegex(ValueError, "reference ELF"):
                    n2d_perf.validate_pin_image(root, manifest, "deterministic")
                (root / "program.bin").write_bytes(b"different")
                with self.assertRaisesRegex(ValueError, "segment bytes"):
                    n2d_perf.validate_pin_image(root, manifest, "deterministic")

    def test_render_image_identity_binds_sources_defines_elf_and_binary(self):
        from src.nand2doom.scripts.sw_build import n2d_doom
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sources = {"src/nand2doom/sw/doom/platform.c": b"typed singletics port",
                       "src/nand2doom/scripts/sw_build/n2d_doom.py": b"verification defines"}
            for name, data in sources.items():
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_bytes(data)
            inputs = dict(variant_defines=n2d_doom.VARIANTS["render-verification"]["defines"],
                          sources={name: hashlib.sha256(data).hexdigest() for name, data in sources.items()})
            identity = hashlib.sha256(json.dumps(inputs, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            for name, data in (("program.bin", bytes.fromhex(identity)), ("wad.bin", b"wad"), ("program.elf", b"elf")):
                (root / name).write_bytes(data)
            digest = lambda name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            manifest = dict(architecture="rv32im", abi="ilp32", endianness="little", entry=0,
                            variant=dict(name="render-verification", **{k: v for k, v in
                                n2d_doom.VARIANTS["render-verification"].items() if k != "output"}),
                            engine={"revision": n2d_perf.render_oracle()["provenance"]["engine_revision"]},
                            capture=dict(identity_inputs=inputs, identity=identity, identity_address=0,
                                         elf=dict(path="program.elf", sha256=digest("program.elf"))),
                            segments=[dict(path=name, address=address, byte_length=(root/name).stat().st_size, sha256=digest(name))
                                      for name, address in (("program.bin", 0), ("wad.bin", 0x2000000))])
            with patch.object(n2d_perf, "REPO_ROOT", root), patch.object(n2d_doom, "WAD_SHA256", digest("wad.bin")):
                n2d_perf.validate_pin_image(root, manifest, "render-verification")
                for mutate in (lambda m: m["capture"]["identity_inputs"].update(variant_defines=[]),
                               lambda m: m["capture"].update(identity="00"*32),
                               lambda m: m["capture"].update(identity_address=1),
                               lambda m: m["capture"]["elf"].update(sha256="00"*32)):
                    altered = copy.deepcopy(manifest)
                    mutate(altered)
                    with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                        n2d_perf.validate_pin_image(root, altered, "render-verification")
                (root / next(iter(sources))).write_bytes(b"changed port")
                with self.assertRaisesRegex(ValueError, "source differs"):
                    n2d_perf.validate_pin_image(root, manifest, "render-verification")

    def test_compiled_call_symbol_validation(self):
        from src.nand2doom.dv.software.doom_gameplay import parse_call_symbols
        text = "00000100 00000010 T G_Ticker\n00000200 00000010 T D_Display\n00000300 00000004 B singletics\n"
        self.assertEqual(set(parse_call_symbols(text)), {"G_Ticker", "D_Display", "singletics"})
        for invalid in (text + text, text.replace(" B singletics", " D singletics"),
                        text.replace("00000200", "00000100"), text.replace("00000300", "00000301")):
            with self.assertRaises(AssertionError):
                parse_call_symbols(invalid)


if __name__ == "__main__":
    unittest.main()
