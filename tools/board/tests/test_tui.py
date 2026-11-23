"""Board console: terminal key parsing (kitty protocol and plain bytes), key
state with taps, play mode through a pseudo-terminal (keys, mouse clicks on
the pad, Ctrl+]), and the console app driven by Textual's pilot against a fake
board: the four steps, their failures with a fix, and the next-step button."""
import asyncio
import os
from pathlib import Path
import pty
import select
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.board import board as console  # noqa: E402
from tools.board import keys, play  # noqa: E402
from tools.board.tests.test_board import CONFIG  # noqa: E402


class TerminalKeysTests(unittest.TestCase):
    def test_kitty_press_repeat_release_with_modifiers_and_functional_keys(self):
        events = keys.TerminalKeys().feed(
            b"\x1b[13u\x1b[13;1:3u\x1b[1;1:1C\x1b[1;1:3C\x1b[120;2:2u\x1b[57441u\x1b[57441;2:3u"
            b"\x1b[5~\x1b[24;1:3~\x1b[?11u", 0.0)
        self.assertEqual(events, [("press", "Return"), ("release", "Return"), ("press", "Right"),
                                  ("release", "Right"), ("repeat", "x"), ("press", "Shift_L"),
                                  ("release", "Shift_L"), ("press", "Prior"), ("release", "F12")])

    def test_ctrl_right_bracket_leaves_in_both_forms(self):
        t = keys.TerminalKeys()
        self.assertEqual(t.feed(b"\x1d\x1b[93;5u\x1b[93;5:3u", 0.0),
                         [(keys.CTRL_RIGHT_BRACKET, ""), (keys.CTRL_RIGHT_BRACKET, "")])

    def test_plain_bytes_are_taps_and_a_lone_escape_waits(self):
        t = keys.TerminalKeys(escape_wait=0.05)
        self.assertEqual(t.feed(b"Xz\r\x1b[A\x1bOP{", 0.0),
                         [("tap", "x"), ("tap", "z"), ("tap", "Return"), ("press", "Up"),
                          ("tap", "F1"), ("tap", "bracketleft")])
        self.assertEqual(t.feed(b"\x1b", 1.0), [])
        self.assertEqual(t.feed(b"", 1.1), [("tap", "Escape")])
        self.assertEqual(t.feed(b"\x1b[", 2.0) + t.feed(b"B", 2.01), [("press", "Down")])

    def test_a_tap_breaks_after_its_quiet_time_and_never_sticks(self):
        state = keys.KeyState(quiet=0.15)
        self.assertEqual(state.tap("x", 0.0), bytes([0x22]))
        self.assertEqual(state.tap("x", 0.1) + state.flush(0.2), b"")
        self.assertEqual(state.flush(0.26), bytes([0xF0, 0x22]))
        self.assertEqual(state.tap("x", 1.0), bytes([0x22]), "a second tap makes the key again")
        self.assertEqual(state.press("Up", 1.0) + state.release("Up", 1.01) + state.flush(1.01),
                         bytes([0xE0, 0x75, 0xE0, 0xF0, 0x75]))

    def test_timed_keys_treat_every_press_as_a_tap(self):
        state = keys.TimedKeys(quiet=0.15)
        self.assertEqual(state.press("x", 0.0) + state.flush(0.2), bytes([0x22, 0xF0, 0x22]))


class Terminal:
    """A pseudo-terminal that plays the user and answers the protocol query."""

    def __init__(self, kitty: bool):
        self.master, self.slave = pty.openpty()
        self.kitty, self.screen = kitty, bytearray()

    def pump(self, seconds: float, until: bytes | None = None):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and not (until and until in self.screen):
            if select.select([self.master], [], [], 0.02)[0]:
                self.screen += os.read(self.master, 65536)
                if b"\x1b[c" in self.screen[-64:]:
                    os.write(self.master, (b"\x1b[?0u" if self.kitty else b"") + b"\x1b[?62;22c")
                    self.screen += b"<answered>"

    def type(self, data: bytes, wait: float = 0.05):
        os.write(self.master, data)
        self.pump(wait)

    def close(self):
        os.close(self.master)
        os.close(self.slave)


class PlayTests(unittest.TestCase):
    PAD = {"Up": "Up", "A": "x", "B": "z", "Start": "Return", "Select": "space"}

    def run_play(self, kitty, script):
        term, sent, result = Terminal(kitty), [], {}
        thread = threading.Thread(target=lambda: result.update(count=play.play(
            lambda codes, label: sent.append(bytes(codes)), "demo", pad=self.PAD,
            fd_in=term.slave, fd_out=term.slave)), daemon=True)
        thread.start()
        term.pump(2, b"PLAYING")
        for data, wait in script:
            term.type(data, wait)
        thread.join(3)
        term.close()
        self.assertFalse(thread.is_alive())
        return b"".join(sent), result["count"], bytes(term.screen)

    def test_kitty_keys_and_pad_clicks_make_and_break_and_light_the_pad(self):
        row, col = play.PAD_TOP + 2 + 1, play.PAD_LEFT + 45 + 2
        sent, count, screen = self.run_play(True, [
            (b"\x1b[120u", 0.05), (b"\x1b[120;1:3u", 0.05), (b"\x1b[1;1:1A", 0.05),
            (f"\x1b[<64;{col};{row}M".encode(), 0.05),
            (f"\x1b[<0;{col};{row}M".encode(), 0.05), (f"\x1b[<0;{col};{row}m".encode(), 0.05),
            (b"\x1b[93;5u", 0.3)])
        self.assertEqual(sent, bytes([0x22, 0xF0, 0x22, 0xE0, 0x75, 0x22, 0xF0, 0x22, 0xE0, 0xF0, 0x75]))
        self.assertEqual(count, 3)
        self.assertIn(b"\x1b[30;42m", screen, "a held button is drawn lit")
        self.assertIn(b"A (click)", screen)

    def test_action_buttons_sit_on_a_grid_and_click_like_the_pad(self):
        self.PAD = {"Up": "Up", "Fire": "Control_L", "Use": "space"}
        places = play.layout(self.PAD)
        row, col, width, face = places["Use"]
        self.assertEqual((face, places["Up"]), ("Use:Spc", play.LAYOUT["Up"]))
        click = (play.PAD_TOP + row + 1, play.PAD_LEFT + col + 1)
        sent, _, screen = self.run_play(True, [
            (f"\x1b[<0;{click[1]};{click[0]}M".encode(), 0.05), (f"\x1b[<0;{click[1]};{click[0]}m".encode(), 0.05),
            (b"\x1b[57442u", 0.05), (b"\x1b[57442;5:3u", 0.05), (b"\x1b[93;5u", 0.3)])
        self.assertEqual(sent, bytes([0x29, 0xF0, 0x29, 0x14, 0xF0, 0x14]))
        self.assertIn("Fire:Ctrl".encode(), screen)

    def test_kitty_terminal_sending_a_key_plainly_still_breaks_it(self):
        sent, _, _ = self.run_play(True, [(b"x", 0.3), (b"x", 0.3), (b"\x1b[93;5u", 0.3)])
        self.assertEqual(sent, bytes([0x22, 0xF0, 0x22]) * 2)

    def test_plain_terminal_releases_after_the_repeats_stop(self):
        sent, _, screen = self.run_play(False, [(b"z", 0.3), (b"\x1d", 0.3)])
        self.assertEqual(sent, bytes([0x1A, 0xF0, 0x1A]))
        self.assertIn(b"no key releases", screen)


class FakeKeyboard:
    def __init__(self, sent):
        self.sent = sent

    def send(self, codes):
        self.sent.append(bytes(codes))
        return len(codes)

    def close(self):
        return "closed"


class FakeBoard:
    """The console's board actions, recorded; `fail` names steps that raise."""
    resolve_path = staticmethod(console.resolve_path)

    def __init__(self, fail=()):
        self.calls, self.sent, self.fail = [], [], set(fail)

    def serial_ports(self):
        return ["/dev/ttyUSB7"]

    def check_chain(self, boards, board, quartus_bin=None):
        self.calls.append(("check", board.key))
        if "cable" in self.fail:
            return False, "no JTAG cable found", "plug in the board's USB-Blaster"
        return True, f"USB-Blaster [1-1]: {board.idcode}, matches {board.key}", "press k"

    def program(self, board, sof, quartus_bin=None, out=print):
        self.calls.append(("program", board.key, sof.name))
        if "program" in self.fail:
            raise console.BoardError("quartus_pgm exited 1")
        out("programmed")
        return "USB-Blaster [1-1]"

    def load(self, board, program, port, out=print):
        self.calls.append(("load", board.key, program.name, port))
        if "load" in self.fail:
            raise console.BoardError("loader exited 1")

    def jtag_device(self, board, quartus_bin=None):
        self.calls.append(("connect", board.key))
        return "USB-Blaster [1-1]", "@1: EP4CE115 (0x020F70DD)"

    def Keyboard(self, cable, device, quartus_bin=None):  # noqa: N802 (the console module's class)
        return FakeKeyboard(self.sent)


class ConsoleAppTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        tmp = Path(self.tmp.name)
        (tmp / "demo.sof").write_bytes(b"sof")
        (tmp / "game.bin").write_bytes(b"game")
        config = {**CONFIG, "program": {**CONFIG["program"], "default": str(tmp / "game.bin")},
                  "boards": {"de2_115": {**CONFIG["boards"]["de2_115"], "sof": str(tmp / "demo.sof")}}}
        self.board = console.parse_config(config, "demo")[0]

    def tearDown(self):
        self.tmp.cleanup()

    def drive(self, fake, presses, port="/dev/ttyUSB7", plays=None):
        from tools.board import tui
        plays = [] if plays is None else plays
        app = tui.BoardConsole({self.board.key: self.board}, (self.board, "selected"), port=port,
                               actions=fake, play_fn=lambda send, title, pad=None: plays.append(title) or 0)

        async def run():
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause(0.3)
                for key in presses:
                    await (pilot.click(key) if key.startswith("#") else pilot.press(key))
                    await pilot.pause(0.3)
        asyncio.run(run())
        return app

    def test_next_step_walks_cable_program_load_keyboard(self):
        fake = FakeBoard()
        app = self.drive(fake, ["n", "n", "n"])
        self.assertEqual([c[0] for c in fake.calls], ["check", "program", "load", "connect"])
        self.assertEqual({n: s.state for n, s in app.steps.items()},
                         {"cable": "ok", "program": "ok", "load": "ok", "keyboard": "ok"})
        self.assertIsNone(app.next_name())

    def test_a_failed_step_is_red_with_a_fix_and_stays_next(self):
        app = self.drive(FakeBoard(fail={"program"}), ["n"])
        step = app.steps["program"]
        self.assertEqual((step.state, app.next_name()), ("fail", "program"))
        self.assertIn("quartus_pgm exited 1", step.detail)
        self.assertIn("Cable", step.fix)
        app = self.drive(FakeBoard(fail={"cable"}), [])
        self.assertEqual(app.steps["cable"].fix, "plug in the board's USB-Blaster")

    def test_an_unconfirmed_start_board_moves_to_the_board_on_the_cable(self):
        from tools.board import tui
        other = console.parse_config({**CONFIG, "project": "other"}, "other")[0]
        fake = FakeBoard(fail={"cable"})
        fake.detect_board = lambda boards, quartus_bin=None: (other, "USB-Blaster [1-1]: IDCODE 020F70DD is de2_115")
        app = tui.BoardConsole({self.board.key: self.board, other.key: other},
                               (self.board, "opened on demo/de2_115; choose the board"), actions=fake)

        async def run():
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause(0.6)
        asyncio.run(run())
        self.assertEqual((app.board.key, app.follow), ("other/de2_115", False))

    def test_load_without_a_port_names_the_found_ports(self):
        app = self.drive(FakeBoard(), ["n", "n"], port="")
        self.assertEqual(app.steps["load"].state, "fail")
        self.assertIn("/dev/ttyUSB7", app.steps["load"].fix)

    def test_play_connects_first_and_the_test_key_reports_the_board_ack(self):
        fake, plays = FakeBoard(), []
        app = self.drive(fake, ["g", "#do_test"], plays=plays)
        self.assertEqual(plays, ["demo on the DE2_115"])
        self.assertIn(("connect", "demo/de2_115"), fake.calls)
        self.assertEqual(fake.sent, [b"\x29\xf0\x29"])
        self.assertEqual(app.last_key, "Test key (Space): the board took 3 of 3 bytes (29 f0 29)")


if __name__ == "__main__":
    unittest.main()
