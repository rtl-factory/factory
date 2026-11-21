"""Hardware-free tests of the one-command play flow: board check, exclusive-use
refusal, digest-skip streaming load against the independent stream device
model, interrupt recovery, and the dispatch of the controls."""
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[5]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from src.nand2doom.scripts.sw_build import n2d_image as image  # noqa: E402
from src.nand2doom.scripts.host import n2d_play as play  # noqa: E402
from src.nand2doom.scripts.host import n2d_uart as host  # noqa: E402
from src.nand2doom.scripts.host import n2d_uart_debug_host as wire  # noqa: E402
from src.nand2doom.scripts.host.tests.test_n2d_uart_stream import StreamDevice, make_client  # noqa: E402

JTAGCONFIG = "1) USB-Blaster [1-1]\n  020F70DD   10CL120(Y|Z)/EP3C120/EP4CE115\n"
DEVICE = "@1: 10CL120(Y|Z)/EP3C120/.. (0x020F70DD)"   # Why: this is the name quartus_stp prints on the board host
STP_LISTING = "Info: Running Quartus Prime Signal Tap\nN2A_DEVICE " + DEVICE + "\n"
PROGRAMMED = "Info: Quartus Prime Programmer was successful. 0 errors, 0 warnings\n"
MANIFEST = host.Manifest(0x100, (
    host.Segment(0x0100, bytes((i * 7) & 0xFF for i in range(2500)), "program.bin"),
    host.Segment(0x2000, bytes((i * 13 + 1) & 0xFF for i in range(4099)), "data.wad"),
    host.Segment(0x8000, bytes((i * 3 + 5) & 0xFF for i in range(1500)), "extra.bin"),
))


def store(device, *segments):
    for segment in segments:
        device.memory[segment.address:segment.address + len(segment.data)] = segment.data


class DigestFaultDevice(StreamDevice):
    """Digest requests numbered in `fail` (1-based) answer a status error or nothing at all.
    The first digest is the program segment's post-load verification."""

    def __init__(self, *, fail=(2,), mode="status", **kwargs):
        super().__init__(**kwargs)
        self.fail, self.mode, self.digests = set(fail), mode, 0

    def write(self, packet):
        if packet[0] == wire.STREAM_SOF and packet[1] == wire.STREAM_DIGEST:
            self.digests += 1
            if self.digests in self.fail:
                if self.mode == "status":
                    from src.nand2doom.scripts.host.tests.test_n2d_uart_stream import reply
                    self.pending += reply(wire.STREAM_DIGEST, int.from_bytes(packet[2:4], "little"),
                                          status=wire.ST_MEMORY)
                return len(packet)
        return super().write(packet)


class InterruptingDevice(StreamDevice):
    """Raises KeyboardInterrupt (as Ctrl-C would) on the third write frame."""

    def write(self, packet):
        if packet[0] == wire.STREAM_SOF and packet[1] == wire.STREAM_WRITE and self.writes == 2:
            self.writes += 1
            raise KeyboardInterrupt
        return super().write(packet)


def written_addresses(device):
    return device.write_addresses


class Recording(StreamDevice):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.write_addresses = []

    def write(self, packet):
        if packet[0] == wire.STREAM_SOF and packet[1] == wire.STREAM_WRITE:
            self.write_addresses.append(int.from_bytes(packet[4:8], "little"))
        return super().write(packet)


class LoadTests(unittest.TestCase):
    def load(self, device, **kwargs):
        client = make_client(device)
        lines = []
        results = play.load_image(client, MANIFEST, baud=3_000_000, report=lines.append, **kwargs)
        return client, results, lines

    def assert_image(self, device):
        for segment in MANIFEST.segments:
            self.assertEqual(bytes(device.memory[segment.address:segment.address + len(segment.data)]),
                             segment.data)

    def actions(self, results):
        return [(r.name, r.action) for r in results]

    def test_matching_segments_are_skipped_program_always_loaded(self):
        device = Recording()
        store(device, *MANIFEST.segments)
        _, results, lines = self.load(device)
        self.assertEqual(self.actions(results), [("program.bin", "loaded"), ("data.wad", "skipped"),
                                                 ("extra.bin", "skipped")])
        self.assertTrue(all(0x100 <= a < 0x100 + 2500 for a in device.write_addresses))
        self.assertEqual(len(device.write_addresses), 3)
        self.assertIn("program segment, always loaded", results[0].reason)
        self.assertTrue(any("data.wad: skipped" in line for line in lines))
        self.assertEqual(device.legacy, [wire.CMD_HALT])  # Why: loading must not reset the PC or Resume before start()
        self.assert_image(device)

    def test_mismatch_loads_the_segment(self):
        device = Recording()
        _, results, _ = self.load(device)
        self.assertEqual(self.actions(results), [("program.bin", "loaded"), ("data.wad", "loaded"),
                                                 ("extra.bin", "loaded")])
        self.assertIn("!=", results[1].reason)
        self.assert_image(device)

    def test_partial_match_loads_only_the_changed_segment(self):
        device = Recording()
        store(device, MANIFEST.segments[1])
        stale = MANIFEST.segments[2]
        device.memory[stale.address:stale.address + len(stale.data)] = stale.data
        device.memory[stale.address + 100] ^= 0xFF
        _, results, _ = self.load(device)
        self.assertEqual(self.actions(results), [("program.bin", "loaded"), ("data.wad", "skipped"),
                                                 ("extra.bin", "loaded")])
        self.assertFalse(any(0x2000 <= a < 0x3004 for a in device.write_addresses))
        self.assert_image(device)

    def test_digest_request_failure_falls_back_to_a_full_segment_load(self):
        for mode in ("status", "silent"):
            with self.subTest(mode=mode):
                device = DigestFaultDevice(mode=mode)
                store(device, *MANIFEST.segments)
                _, results, _ = self.load(device)
                self.assertEqual(self.actions(results), [("program.bin", "loaded"), ("data.wad", "loaded"),
                                                         ("extra.bin", "skipped")])
                self.assertIn("digest request failed", results[1].reason)
                self.assert_image(device)
        device = DigestFaultDevice(fail=(1,))  # Why: the program's verification is never skipped, even on a digest fault
        with self.assertRaisesRegex(host.StreamError, "remains halted"):
            self.load(device)

    def test_force_full_sends_no_pre_digest(self):
        device = DigestFaultDevice(fail=())
        store(device, *MANIFEST.segments)
        _, results, _ = self.load(device, force_full=True)
        self.assertEqual([r.action for r in results], ["loaded"] * 3)
        self.assertEqual(device.digests, 3)  # Why: a forced full load skips the pre-load digests; only post-load verifications run

    def test_post_load_digest_mismatch_stops_halted(self):
        device = StreamDevice(corrupt_memory=0x150)
        client = make_client(device)
        with self.assertRaisesRegex(host.StreamError, "remains halted"):
            play.load_image(client, MANIFEST, baud=3_000_000, report=lambda m: None, force_full=True)
        self.assertTrue(client.failed)
        self.assertNotIn(wire.CMD_RESUME, device.legacy)

    def test_interrupt_ends_the_stream_session_and_never_resumes(self):
        device = InterruptingDevice()
        client = make_client(device)
        with self.assertRaises(play.LoadInterrupted) as stop:
            play.load_image(client, MANIFEST, baud=3_000_000, report=lambda m: None)
        self.assertTrue(stop.exception.ended)
        self.assertEqual(device.frames[-1], wire.STREAM_END)
        self.assertEqual(device.legacy, [wire.CMD_HALT])
        self.assertTrue(client.failed)

    def test_start_resets_pc_then_resumes(self):
        device = StreamDevice()
        client = make_client(device)
        client.command(wire.CMD_HALT)
        play.start(client, MANIFEST)
        self.assertEqual(device.legacy, [wire.CMD_HALT, wire.CMD_RESET_PC, wire.CMD_RESUME])


class BoardTests(unittest.TestCase):
    def test_holders_reports_uart_and_jtag_clients_but_not_self_or_jtagd(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc, device = Path(tmp) / "proc", Path(tmp) / "ttyUSB0"
            device.touch()
            for pid, comm, fds in ((10, "python3", [device]), (11, "quartus_stp", []), (12, "jtagd", []),
                                   (13, "bash", [Path(tmp)]), (99, "python3", [device])):
                (proc / str(pid) / "fd").mkdir(parents=True)
                (proc / str(pid) / "comm").write_text(comm + "\n")
                for index, target in enumerate(fds):
                    os.symlink(target, proc / str(pid) / "fd" / str(index))
            (proc / "self").mkdir()
            found = play.holders(str(device), proc=proc, own=99)
            self.assertEqual(sorted(found), [(10, "python3", f"UART {device}"),
                                             (11, "quartus_stp", "USB-Blaster (JTAG client)")])
            self.assertEqual(play.holders(str(device), jtag=False, proc=proc, own=99),
                             [(10, "python3", f"UART {device}")])


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sof = Path(self.tmp.name) / "n2d_de2_115.sof"
        self.sof.write_bytes(b"sof")
        self.calls, self.lines, self.controls = [], [], []

    def tearDown(self):
        self.tmp.cleanup()

    def runner(self, argv, timeout):
        self.calls.append(argv)
        if argv[0].endswith("quartus_stp"):
            return 0, self.listing
        return (0, JTAGCONFIG) if argv[0].endswith("jtagconfig") else (0, PROGRAMMED)

    listing = STP_LISTING

    def args(self, *extra):
        args = play.parser().parse_args(["--port", "/dev/fake", "--sof", str(self.sof), *extra])
        args.skip_program = args.skip_program or args.skip_load
        args.baud = 3_000_000
        return args

    def flow(self, args, device, **kwargs):
        kwargs.setdefault("find_holders", lambda port, jtag: [])
        return play.play(args, play.BOARDS["de2_115"], MANIFEST, self.sof, runner=self.runner,
                         opener=lambda port, timeout, baud: device,
                         launch_controls=lambda argv: self.controls.append(argv) or 0,
                         out=lambda line, **_: self.lines.append(line), **kwargs)

    def test_full_flow_programs_loads_starts_and_opens_jtag_controls(self):
        device = StreamDevice()
        store(device, MANIFEST.segments[1])
        self.assertEqual(self.flow(self.args("--transport", "jtag"), device), 0)
        self.assertEqual(self.calls[0], ["jtagconfig"])
        self.assertEqual(self.calls[1][0::3], ["quartus_stp", "USB-Blaster [1-1]"])
        self.assertEqual(self.calls[2], ["quartus_pgm", "-c", "USB-Blaster [1-1]", "-m", "jtag",
                                         "-o", f"p;{self.sof}@1"])
        self.assertEqual(device.legacy, [wire.CMD_PING, wire.CMD_HALT, wire.CMD_RESET_PC, wire.CMD_RESUME])
        self.assertTrue(device.closed)
        self.assertEqual(self.controls, [["--transport", "jtag", "--baud", "3000000", "--connect",
                                          "--timeout", "1.0", "--cable", "USB-Blaster [1-1]",
                                          "--device", DEVICE]])
        text = "\n".join(self.lines)
        self.assertIn(f"JTAG device {DEVICE!r}", text)
        self.assertIn("data.wad: skipped", text)
        self.assertIn("[7/7] controls over JTAG", text)

    def test_jtag_device_override_skips_the_listing(self):
        self.assertEqual(self.flow(self.args("--skip-load", "--transport", "jtag", "--jtag-device", "@1: X"),
                                   StreamDevice()), 0)
        self.assertEqual(self.calls, [["jtagconfig"]])
        self.assertEqual(self.controls[0][-2:], ["--device", "@1: X"])

    def test_unresolved_jtag_device_stops_before_programming(self):
        for listing in ("Info: banner\n", STP_LISTING + "N2A_DEVICE @2: EP4CE115 (0x020F70DD)\n"):
            with self.subTest(listing=listing):
                self.calls, self.listing = [], listing
                with self.assertRaisesRegex(play.PlayError, "exactly one JTAG device with IDCODE 0x020F70DD"):
                    self.flow(self.args("--transport", "jtag"), StreamDevice())
                self.assertEqual([c[0] for c in self.calls], ["jtagconfig", "quartus_stp"])
                self.assertEqual(self.controls, [])

    def test_refuses_when_held_and_never_runs_tools(self):
        busy = lambda port, jtag: [(4242, "n2l_console", "UART /dev/ttyUSB0")]  # noqa: E731
        self.assertEqual(self.flow(self.args(), StreamDevice(), find_holders=busy), play.EXIT_BUSY)
        self.assertEqual(self.calls, [])
        self.assertIn("PID 4242", "\n".join(self.lines))

    def test_skip_load_reconnects_without_tools_or_halt(self):
        device = StreamDevice()
        self.assertEqual(self.flow(self.args("--skip-load"), device), 0)
        self.assertEqual(self.calls, [])
        self.assertEqual(device.legacy, [wire.CMD_PING])
        self.assertEqual(self.controls[0][-2:], ["--port", "/dev/fake"])

    def test_skip_load_refuses_a_halted_cpu(self):
        device = StreamDevice()
        device.halted = True
        with self.assertRaisesRegex(play.PlayError, "halted"):
            self.flow(self.args("--skip-load"), device)

    def test_skip_program_keeps_the_image_and_reloads(self):
        device = StreamDevice()
        self.assertEqual(self.flow(self.args("--skip-program", "--controls", "none"), device), 0)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.controls, [])
        self.assertIn(wire.CMD_RESUME, device.legacy)

    def test_programmer_failure_stops_before_the_uart(self):
        def runner(argv, timeout):
            return (0, JTAGCONFIG) if argv[0] == "jtagconfig" else (1, "Error: programming failed")
        self.runner = runner
        opener = Mock()
        with self.assertRaisesRegex(play.PlayError, "quartus_pgm exited 1"):
            play.play(self.args(), play.BOARDS["de2_115"], MANIFEST, self.sof, runner=runner, opener=opener,
                      find_holders=lambda port, jtag: [], out=lambda *a, **k: None)
        opener.assert_not_called()

    def test_interrupted_load_reports_recovery(self):
        device = InterruptingDevice()
        self.assertEqual(self.flow(self.args("--skip-program"), device), play.EXIT_INTERRUPTED)
        text = "\n".join(self.lines)
        self.assertIn("stream session ended cleanly", text)
        self.assertIn("--skip-program", text)
        self.assertTrue(device.closed)
        self.assertEqual(self.controls, [])


class PlanTests(unittest.TestCase):
    def test_plan_opens_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = b"\x13\x00\x00\x00" * 4
            (Path(tmp) / "program.bin").write_bytes(data)
            manifest = Path(tmp) / "manifest.json"
            manifest.write_text(json.dumps({
                "schema_version": 1, "architecture": image.ARCH, "abi": image.ABI, "endianness": "little",
                "memory_map_revision": image.MEMORY_MAP_REVISION, "entry": 0,
                "segments": [{"address": 0, "byte_length": len(data), "path": "program.bin",
                              "sha256": hashlib.sha256(data).hexdigest()}]}))
            sof = Path(tmp) / "n2d_de2_115.sof"
            sof.write_bytes(b"sof")
            opener, runner = Mock(), Mock()
            with redirect_stdout(io.StringIO()) as stream:
                code = play.main(["--port", "/dev/fake", "--sof", str(sof), "--manifest", str(manifest), "--plan"],
                                 opener=opener, runner=runner)
            self.assertEqual(code, 0)
            opener.assert_not_called()
            runner.assert_not_called()
            text = stream.getvalue()
            self.assertIn("always loaded (program)", text)
            self.assertIn("quartus_pgm -c", text)
            with redirect_stdout(io.StringIO()) as stream:
                play.main(["--port", "/dev/fake", "--sof", str(sof), "--manifest", str(manifest), "--plan",
                           "--transport", "jtag"], opener=opener, runner=runner)
            runner.assert_not_called()
            self.assertIn("--device <resolved by IDCODE 0x020F70DD at the board check>", stream.getvalue())

    def test_missing_inputs_are_argument_errors(self):
        for argv in (["--sof", "/nonexistent.sof", "--port", "/dev/fake"],
                     ["--skip-program", "--port", "/dev/fake", "--manifest", "/nonexistent.json"],
                     ["--skip-program", "--manifest", "/nonexistent.json"]):
            with self.subTest(argv=argv), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exit_:
                play.main(argv)
            self.assertEqual(exit_.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
