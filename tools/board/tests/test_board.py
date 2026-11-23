"""Board console: project board configs, the program, load and keys commands
(plans without --yes, exact commands with it), and the key translation."""
from contextlib import redirect_stderr
import io
import os
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.board import board as console  # noqa: E402
from tools.board import keys  # noqa: E402
from tools.board.tests.test_jtag_chain import JTAGCONFIG, LISTING_EP4  # noqa: E402
from src.common.scripts.jtag import n2a_jtag_keyboard as jtag  # noqa: E402
from src.common.scripts.jtag.tests.test_n2a_jtag_keyboard import Device  # noqa: E402

CONFIG = {"schema": "n2a-board/1", "project": "demo",
          "program": {"label": "Image", "patterns": ["*.bin"], "default": ""},
          "load": ["{python}", "load.py", "{program}", "--port", "{port}", "--baud", "{baud}", "--board", "{board}"],
          "boards": {"de2_115": {"idcode": "020F70DD", "baud": 2000000, "sof": "workdir/x/demo.sof"}}}


def demo_board():
    return console.parse_config(CONFIG, "demo")[0]


class Recorder:
    def __init__(self, replies):
        self.replies, self.calls = replies, []

    def __call__(self, argv, timeout):
        self.calls.append(argv)
        return self.replies[Path(argv[0]).name]


def run(argv, **kwargs):
    lines = []
    with redirect_stderr(io.StringIO()) as err:
        code = console.main(argv, out=lines.append, **kwargs)
    return code, lines, err.getvalue()


class ConfigTests(unittest.TestCase):
    def test_every_project_declares_both_boards(self):
        boards = console.load_boards()
        for project in ("nand2doom", "nand2mario"):
            for name, idcode in (("de10_lite", "031050DD"), ("de2_115", "020F70DD")):
                with self.subTest(project=project, board=name):
                    board = boards[f"{project}/{name}"]
                    self.assertEqual(idcode, board.idcode)
                    self.assertTrue((ROOT / board.load[1]).is_file(), board.load)
                    self.assertTrue(Path(board.sof).name.endswith(f"_{name}.sof"), board.sof)

    def test_bad_configs_are_refused(self):
        for change in ({"schema": "x"}, {"load": []}, {"load": ["{nope}"]},
                       {"boards": {"b": {"idcode": "zz", "baud": 1, "sof": ""}}},
                       {"boards": {"b": {"idcode": "020F70DD", "baud": 50, "sof": ""}}}):
            with self.subTest(change=change), self.assertRaises(console.BoardError):
                console.parse_config({**CONFIG, **change}, "demo")

    def test_pick_needs_one_match_and_sof_names_its_board(self):
        boards = console.load_boards()
        self.assertEqual("nand2mario/de2_115", console.pick(boards, "nand2mario", "de2_115").key)
        with self.assertRaises(console.BoardError):
            console.pick(boards, None, "de2_115")
        self.assertEqual("nand2doom/de10_lite",
                         console.board_for_sof(boards, Path("/any/n2d_de10_lite.sof")).key)
        self.assertIsNone(console.board_for_sof(boards, Path("/any/other.sof")))


class WindowBoardTests(unittest.TestCase):
    def boards(self, root):
        two = {**CONFIG, "project": "other",
               "boards": {"de2_115": {"idcode": "020F70DD", "baud": 2000000, "sof": "workdir/y/other.sof"},
                          "de10_lite": {"idcode": "031050DD", "baud": 2000000, "sof": "workdir/y/lite.sof"}}}
        return {b.key: b for b in console.parse_config(CONFIG, "demo") + console.parse_config(two, "other")}

    def test_the_connected_idcode_selects_its_board_newest_sof_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, boards = Path(tmp), self.boards(Path(tmp))
            jtagconfig = Recorder({"jtagconfig": (0, JTAGCONFIG)})
            board, why = console.detect_board(boards, runner=jtagconfig, root=root)
            self.assertEqual(("demo/de2_115", 1), (board.key, len(jtagconfig.calls)))
            self.assertIn("IDCODE 020F70DD", why)
            for path, stamp in (("workdir/x/demo.sof", 100), ("workdir/y/other.sof", 200)):
                (root / path).parent.mkdir(parents=True, exist_ok=True)
                (root / path).write_bytes(b"sof")
                os.utime(root / path, (stamp, stamp))
            board, _ = console.detect_board(boards, runner=jtagconfig, root=root)
            self.assertEqual("other/de2_115", board.key)
            lite = Recorder({"jtagconfig": (0, "1) USB-Blaster [1-1]\n  031050DD   10M50DA\n")})
            self.assertEqual("other/de10_lite", console.detect_board(boards, runner=lite, root=root)[0].key)

    def test_no_cable_two_cables_or_unknown_device_fall_back_to_the_first_board(self):
        boards = self.boards(ROOT)
        two = JTAGCONFIG + "2) USB-Blaster [1-2]\n  031050DD   10M50DA\n"
        unknown = "1) USB-Blaster [1-1]\n  12345678   other\n"

        def missing(argv, timeout):
            raise FileNotFoundError("jtagconfig")

        shared = JTAGCONFIG.rstrip("\n") + "\n  031050DD   10M50DA\n"
        for runner in (Recorder({"jtagconfig": (0, "")}), Recorder({"jtagconfig": (0, two)}),
                       Recorder({"jtagconfig": (0, shared)}),
                       Recorder({"jtagconfig": (1, "")}), Recorder({"jtagconfig": (0, unknown)}), missing):
            with self.subTest(runner=runner):
                board, why = console.detect_board(boards, runner=runner)
                self.assertEqual("demo/de2_115", board.key)
                self.assertIn("opened on demo/de2_115; choose the board", why)

    def test_window_flags_pick_and_one_serial_port_is_prefilled(self):
        from tools.board import tui
        opened = []
        real_main, real_ports = tui.main, console.serial_ports
        tui.main = lambda boards, **kwargs: opened.append(kwargs) or 0
        console.serial_ports = lambda: ["/dev/ttyUSB0"]
        try:
            self.assertEqual(0, run(["tui", "--project", "nand2mario", "--board", "de2_115"])[0])
            console.serial_ports = lambda: ["/dev/ttyUSB0", "/dev/ttyUSB1"]
            self.assertEqual(0, run([], runner=Recorder({"jtagconfig": (0, JTAGCONFIG)}))[0])
        finally:
            tui.main, console.serial_ports = real_main, real_ports
        self.assertEqual((opened[0]["first"][0].key, opened[0]["port"]), ("nand2mario/de2_115", "/dev/ttyUSB0"))
        self.assertEqual((opened[1]["first"][0].idcode, opened[1]["port"]), ("020F70DD", ""))


class CheckChainTests(unittest.TestCase):
    def test_every_cable_state_gives_a_finding_and_a_hint(self):
        boards = console.load_boards()
        mario = boards["nand2mario/de2_115"]
        lite = "1) USB-Blaster [1-1]\n  031050DD   10M50DA\n"

        def missing(argv, timeout):
            raise FileNotFoundError("jtagconfig")

        cases = [(Recorder({"jtagconfig": (0, JTAGCONFIG)}), True, "matches nand2mario/de2_115", "press k"),
                 (Recorder({"jtagconfig": (0, lite)}), False, "expected 020F70DD", "press b until the board is one"),
                 (Recorder({"jtagconfig": (0, "")}), False, "no JTAG cable", "plug in"),
                 (Recorder({"jtagconfig": (0, JTAGCONFIG + "2) USB-Blaster [1-2]\n")}), False, "cables:", "unplug"),
                 (Recorder({"jtagconfig": (1, "1) USB-Blaster [1-1]\n  Unable to lock chain - Insufficient port permissions\n")}),
                  False, "not accessible", "udev rule"),
                 (Recorder({"jtagconfig": (0, "1) USB-Blaster [1-1]\n")}), False, "no device", "power the board"),
                 (Recorder({"jtagconfig": (0, "1) USB-Blaster [1-1]\n  Unable to lock chain - Chain in use - check SLD HUB CLIENT on 127.0.0.1\n")}),
                  False, "in use by another program", "close the other board console"),
                 (missing, False, "jtagconfig not found", "--quartus-bin")]
        for runner, ready, finding, hint in cases:
            with self.subTest(finding=finding):
                got = console.check_chain(boards, mario, runner=runner)
                self.assertEqual(got[0], ready)
                self.assertIn(finding, got[1])
                self.assertIn(hint, got[2])


class ProgramTests(unittest.TestCase):
    def test_dry_run_touches_nothing(self):
        def runner(argv, timeout):
            raise AssertionError("dry run ran " + argv[0])
        code, lines, _ = run(["program", "--project", "nand2mario", "--board", "de10_lite"], runner=runner)
        plan = json.loads("\n".join(lines))
        self.assertEqual(0, code)
        self.assertEqual(["quartus_pgm", "-c", "<cable>", "-m", "jtag"], plan["command"][:5])
        self.assertIn("031050DD", plan["cable"])

    def test_programs_through_the_one_matching_cable(self):
        with tempfile.NamedTemporaryFile(suffix=".sof") as sof:
            runner = Recorder({"jtagconfig": (0, JTAGCONFIG), "quartus_pgm": (0, "Programmer was successful.")})
            code, lines, err = run(["program", "--project", "nand2doom", "--board", "de2_115",
                                    "--sof", sof.name, "--yes"], runner=runner)
            self.assertEqual((0, ""), (code, err))
            self.assertEqual([["jtagconfig"],
                              ["quartus_pgm", "-c", "USB-Blaster [1-1]", "-m", "jtag", "-o", f"p;{sof.name}@1"]],
                             runner.calls)

    def test_wrong_board_missing_sof_and_failed_programmer_are_errors(self):
        with tempfile.NamedTemporaryFile(suffix=".sof") as sof:
            wrong = Recorder({"jtagconfig": (0, JTAGCONFIG)})
            code, _, err = run(["program", "--project", "nand2doom", "--board", "de10_lite",
                                "--sof", sof.name, "--yes"], runner=wrong)
            self.assertEqual(2, code)
            self.assertIn("expected the one device 031050DD", err)
            self.assertEqual([["jtagconfig"]], wrong.calls)
            failed = Recorder({"jtagconfig": (0, JTAGCONFIG), "quartus_pgm": (0, "Error (213019)")})
            self.assertEqual(2, run(["program", "--project", "nand2doom", "--board", "de2_115",
                                     "--sof", sof.name, "--yes"], runner=failed)[0])
        code, _, err = run(["program", "--project", "nand2doom", "--board", "de2_115",
                            "--sof", "/no/such.sof", "--yes"], runner=wrong)
        self.assertIn("SOF not found", err)


class FakeProcess:
    def __init__(self, argv, rc, **kwargs):
        self.argv, self.kwargs, self.returncode = argv, kwargs, rc
        self.stdout = iter(["segment 1 loaded\n", "done\n"])

    def wait(self):
        return self.returncode


class LoadTests(unittest.TestCase):
    def test_load_command_fills_every_field(self):
        argv = console.load_command(demo_board(), Path("/w/game.bin"), "/dev/ttyUSB0", python="py")
        self.assertEqual(["py", "load.py", "/w/game.bin", "--port", "/dev/ttyUSB0", "--baud", "2000000",
                          "--board", "de2_115"], argv)

    def test_runs_the_project_loader_and_streams_its_output(self):
        started = []
        def popen(argv, **kwargs):
            started.append(FakeProcess(argv, 0, **kwargs))
            return started[-1]
        with tempfile.NamedTemporaryFile(suffix=".gb") as image:
            code, lines, err = run(["load", "--project", "nand2mario", "--board", "de10_lite",
                                    "--program", image.name, "--port", "/dev/ttyUSB0", "--yes"], popen=popen)
        self.assertEqual((0, ""), (code, err))
        argv = started[0].argv
        self.assertEqual(["src/common/scripts/uart_mem/uart_mem_load.py", image.name, "--project", "n2m ",
                          "--port", "/dev/ttyUSB0", "--baud", "2000000", "--yes"], argv[1:])
        self.assertEqual(ROOT, started[0].kwargs["cwd"])
        self.assertEqual(["segment 1 loaded", "done"], lines[1:])

    def test_dry_run_missing_port_and_loader_failure(self):
        def never(argv, **kwargs):
            raise AssertionError("dry run started the loader")
        code, lines, _ = run(["load", "--project", "nand2doom", "--board", "de2_115"], popen=never)
        plan = json.loads("\n".join(lines))
        self.assertEqual(0, code)
        self.assertIn("--skip-program", plan["command"])
        with tempfile.NamedTemporaryFile() as image:
            self.assertIn("--port", run(["load", "--project", "nand2mario", "--board", "de2_115",
                                         "--program", image.name, "--yes"], popen=never)[2])
            failing = lambda argv, **kw: FakeProcess(argv, 3, **kw)
            self.assertIn("loader exited 3", run(["load", "--project", "nand2mario", "--board", "de2_115",
                                                  "--program", image.name, "--port", "p", "--yes"],
                                                 popen=failing)[2])
        self.assertIn("super-mario-land-rev1.gb",
                      "\n".join(run(["load", "--project", "nand2mario", "--board", "de2_115"])[1]),
                      "nand2mario's default cartridge is the tracked ROM")


class ClosingDevice(Device):
    def close(self):
        self.closed = True
        return "closed"


class FakeJtag:
    """The real common Session over the common test's wire-level device."""
    def __init__(self):
        self.device, self.opened = ClosingDevice(), []
        self.settings, self.Session, self.send_all, self.type_text = jtag.settings, jtag.Session, jtag.send_all, jtag.type_text

    def open_transport(self, data):
        self.opened.append(data)
        return self.device


class KeysCommandTests(unittest.TestCase):
    def test_types_through_the_board_device_found_by_idcode(self):
        fake = FakeJtag()
        runner = Recorder({"jtagconfig": (0, JTAGCONFIG), "quartus_stp": (0, LISTING_EP4)})
        code, lines, err = run(["keys", "--project", "nand2doom", "--board", "de2_115", "--yes", "ok"],
                               runner=runner, jtag=fake)
        self.assertEqual((0, ""), (code, err))
        self.assertEqual("@1: EP4CE115 (0x020F70DD)", fake.opened[0]["jtag_device"])
        self.assertEqual(bytes.fromhex("44 f0 44 42 f0 42"), bytes(fake.device.injected))

    def test_dry_run_and_wrong_device(self):
        code, lines, _ = run(["keys", "--project", "nand2mario", "--board", "de10_lite", "x"])
        self.assertEqual(0, code)
        self.assertIn("dry_run", "\n".join(lines))
        code, _, err = run(["keys", "--project", "nand2mario", "--board", "de10_lite", "--cable", "c",
                            "--device", "@1: EP4CE115 (0x020F70DD)", "--yes", "x"], jtag=FakeJtag())
        self.assertEqual(2, code)
        self.assertIn("is not the de10_lite", err)


class KeyStateTests(unittest.TestCase):
    def test_plain_and_extended_keys(self):
        self.assertEqual((b"\x1c", b"\xf0\x1c"), (keys.make("a"), keys.brk("a")))
        self.assertEqual((b"\xe0\x75", b"\xe0\xf0\x75"), (keys.make("Up"), keys.brk("Up")))
        self.assertEqual("1", keys.key_name("exclam"))
        self.assertEqual("a", keys.key_name("A"))
        self.assertIsNone(keys.key_name("XF86AudioMute"))

    def test_held_key_with_autorepeat_is_one_make_and_one_break(self):
        state = keys.KeyState(settle=0.03)
        self.assertEqual(b"\xe0\x6b", state.press("Left", 0.0))
        for t in (0.5, 0.53, 0.56):  # Why: X11 auto-repeat is release then press at the same instant
            self.assertEqual(b"", state.release("Left", t))
            self.assertEqual(b"", state.press("Left", t))
        self.assertEqual(b"", state.flush(1.0))
        state.release("Left", 1.0)
        self.assertEqual(b"", state.flush(1.01))
        self.assertEqual(b"\xe0\xf0\x6b", state.flush(1.05))
        self.assertEqual(b"", state.flush(2.0))

    def test_two_held_keys_and_focus_loss(self):
        state = keys.KeyState()
        self.assertEqual(b"\x14", state.press("Control_L", 0))
        self.assertEqual(b"\x1d", state.press("w", 0))
        self.assertEqual(b"", state.press("XF86AudioMute", 0))
        self.assertEqual(b"\xf0\x14\xf0\x1d", state.release_all())
        self.assertEqual(b"", state.release_all())


if __name__ == "__main__":
    unittest.main()
