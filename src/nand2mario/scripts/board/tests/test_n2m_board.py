"""Board host-tool behavior (program, load, snapshot, joypad), without Quartus or a board."""
import contextlib
import functools
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[5]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from src.nand2mario.scripts.board import n2m_board  # noqa: E402
from src.common.scripts.jtag import n2a_jtag_keyboard as jtag  # noqa: E402
from tools.bazel import scratch  # noqa: E402


class Args:
    def __init__(self, **values):
        defaults = dict(cable=None, device=None, quartus_bin=None, tck_hz=6000000,
                        clock_mode="fixed-original-6mhz", cable_index=None, seconds=20.0,
                        yes=False, mode="tap", hold=0.0, names=["start"], sof=n2m_board.DEFAULT_SOF)
        self.__dict__.update(defaults, **values)


class BoardToolTests(unittest.TestCase):
    @functools.cached_property
    def root(self):
        return scratch.checkout(self)

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
        code, result = n2m_board.run_program(Args(), runner=runner, root=self.root)
        self.assertEqual(0, code)
        self.assertIn("quartus_pgm -c '<cable>' -m jtag -o", result["command"])
        self.assertIn("dry_run", result)
        with self.assertRaises(ValueError):
            n2m_board.run_program(Args(yes=True), runner=runner, root=self.root)
        with self.assertRaises(ValueError):
            n2m_board.run_program(Args(sof="src/nand2mario/README.md"), runner=runner, root=self.root)

    def test_program_refuses_an_image_without_its_identity(self):
        workdir = self.root / "workdir"
        sof = workdir / "n2m_de10_lite.sof"
        sof.write_bytes(b"sof")
        with self.assertRaisesRegex(ValueError, "identity receipt"):
            n2m_board.run_program(Args(yes=True, cable="USB-Blaster [1-1]", sof=str(sof.relative_to(self.root))),
                                  runner=None, which=lambda name: name, root=self.root)

    def test_program_with_yes_verifies_the_image_then_runs_exact_command(self):
        workdir = self.root / "workdir"
        sof = workdir / "image.sof"
        sof.write_bytes(b"sof")
        calls, verified = [], []

        class Done:
            returncode = 0

        def runner(command, **kwargs):
            calls.append((command, kwargs))
            return Done()

        def verify(path, config):
            verified.append((path, config["project"], config["profile"]))
            return {"project": config["project"]}
        for board, library, project, profile in (
                ("de10_lite", False, "n2m_de10_lite", None),
                ("de2_115", True, "n2m_de2_115_library", n2m_board.LIBRARY_PROFILE)):
            with self.subTest(board=board), patch.object(n2m_board.image, "verify_image", side_effect=verify):
                code, result = n2m_board.run_program(
                    Args(yes=True, cable="USB-Blaster [1-1]", sof=str(sof.relative_to(self.root)),
                         quartus_bin="/q/bin", board=board, library=library),
                    runner=runner, which=lambda name: name, root=self.root)
                self.assertEqual((0, project), (code, result["identity"]["project"]))
                self.assertEqual((sof, project, profile), verified[-1])
        self.assertEqual(["/q/bin/quartus_pgm", "-c", "USB-Blaster [1-1]", "-m", "jtag", "-o", f"p;{sof}@1"],
                         calls[0][0])

    def test_de2_115_plans_and_target_mismatch(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(0, n2m_board.main(["program", "--board", "de2_115"], root=self.root))
            self.assertEqual(0, n2m_board.main(["program", "--board", "de2_115", "--library"], root=self.root))
            self.assertNotEqual(0, n2m_board.main(["program", "--board", "de2_115", "--sof", n2m_board.DEFAULT_SOF],
                                                  root=self.root))
            self.assertNotEqual(0, n2m_board.main(["joypad", "--board", "de2_115", "--device",
                                                   "@1: MAX10 (0x031050DD)", "start"], root=self.root))
            self.assertEqual(0, n2m_board.main(["joypad", "--board", "de2_115", "--cable", "USB-Blaster [1-1]",
                                                "--device", "@1: 10CL120(Y|Z)/EP3C120/.. (0x020F70DD)", "start"],
                                               root=self.root))

    def test_load_dry_run_writes_frames_only(self):
        workdir = self.root / "workdir"
        image = workdir / "image.gb"
        image.write_bytes(bytes(range(256)) * 3)
        dump = workdir / "frames.bin"
        args = Args(image=str(image), port=None, baud=115200, dump=str(dump.relative_to(self.root)),
                    no_verify=False, yes=False)
        with contextlib.redirect_stdout(io.StringIO()):
            code, result = n2m_board.run_load(args, root=self.root)
        self.assertEqual(0, code)
        self.assertIn("dry_run", result)
        frames = dump.read_bytes()
        from src.nand2mario.scripts.board import cart_loader
        self.assertEqual(cart_loader.SYNC, frames[0])
        self.assertEqual(cart_loader.CMD_HOLD, frames[1])
        self.assertEqual(3, sum(1 for i in range(len(frames)) if frames[i:i + 2] == bytes([0xA5, cart_loader.CMD_WRITE])))
        self.assertEqual(0, sum(1 for i in range(len(frames)) if frames[i:i + 2] == bytes([0xA5, cart_loader.CMD_READ])))
        with self.assertRaises(ValueError):
            n2m_board.run_load(Args(image=str(image), port=None, baud=115200, dump="x", no_verify=False, yes=True),
                               root=self.root)

    def test_snapshot_dry_run_writes_plan_only(self):
        from src.nand2mario.scripts.board import frame_snapshot
        workdir = self.root / "workdir"
        plan = workdir / "plan.bin"
        args = Args(port=None, baud=115200, out="workdir/n2m_board/snapshot",
                    plan_file=str(plan.relative_to(self.root)), yes=False)
        code, result = n2m_board.run_snapshot(args, root=self.root)
        self.assertEqual(0, code)
        self.assertIn("dry_run", result)
        self.assertEqual(plan.read_bytes(), b"".join(frame_snapshot.plan_frames()))
        self.assertEqual(27, result["frames"])
        with self.assertRaises(ValueError):
            n2m_board.run_snapshot(Args(port=None, baud=115200, out="workdir/x", plan_file="x", yes=False),
                                   root=self.root)
        with self.assertRaises(ValueError):
            n2m_board.run_snapshot(Args(port=None, baud=115200, out="workdir/x", plan_file="workdir/p", yes=True),
                                   root=self.root)
        with self.assertRaises(ValueError):
            n2m_board.run_snapshot(Args(port="/dev/x", baud=115200, out="/tmp/x", plan_file="workdir/p", yes=True),
                                   root=self.root)

        class Tool:
            calls = []

            @staticmethod
            def main(argv):
                Tool.calls.append(argv)
                return 1

        code, result = n2m_board.run_snapshot(Args(port="/dev/ttyUSB9", baud=115200, out="workdir/n2m_board/s",
                                                   plan_file="workdir/p", yes=True), snapshot_tool=Tool, root=self.root)
        self.assertEqual((1, "error"), (code, result["status"]))
        self.assertEqual(["capture", "--port", "/dev/ttyUSB9", "--baud", "115200", "--out",
                          str((self.root / "workdir/n2m_board/s").resolve())], Tool.calls[0])

    def test_cli_help(self):
        for action in ("compile", "program", "load", "snapshot", "joypad"):
            with self.subTest(action=action), contextlib.redirect_stdout(io.StringIO()), \
                    self.assertRaises(SystemExit) as exit_:
                n2m_board.main([action, "--help"], root=self.root)
            self.assertEqual(0, exit_.exception.code)




if __name__ == "__main__":
    unittest.main()
