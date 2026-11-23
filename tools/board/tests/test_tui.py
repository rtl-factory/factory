"""Terminal console: terminal key parsing (kitty protocol and plain bytes), the
timed keys of a terminal without releases, and the console driven through a
pseudo-terminal against a fake board: program, load, and play forwarding
make and break codes until Ctrl+] leaves."""
import os
from pathlib import Path
import pty
import select
import sys
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.board import board as console  # noqa: E402
from tools.board import keys, tui  # noqa: E402
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

    def test_plain_bytes_are_presses_and_a_lone_escape_waits(self):
        t = keys.TerminalKeys(escape_wait=0.05)
        self.assertEqual(t.feed(b"Xz\r\x1b[A\x1bOP{", 0.0),
                         [("press", "x"), ("press", "z"), ("press", "Return"), ("press", "Up"),
                          ("press", "F1"), ("press", "bracketleft")])
        self.assertEqual(t.feed(b"\x1b", 1.0), [])
        self.assertEqual(t.feed(b"", 1.1), [("press", "Escape")])
        self.assertEqual(t.feed(b"\x1b[", 2.0) + t.feed(b"B", 2.01), [("press", "Down")])

    def test_timed_keys_hold_while_repeating_then_break(self):
        state = keys.TimedKeys(quiet=0.15)
        self.assertEqual(state.press("x", 0.0), bytes([0x22]))
        self.assertEqual(state.press("x", 0.1) + state.flush(0.2), b"")
        self.assertEqual(state.flush(0.26), bytes([0xF0, 0x22]))
        self.assertEqual(state.press("Up", 1.0) + state.release_all(), bytes([0xE0, 0x75, 0xE0, 0xF0, 0x75]))


class FakeKeyboard:
    def __init__(self, sent):
        self.sent = sent

    def send(self, codes):
        self.sent.append(bytes(codes))

    def close(self):
        return "closed"


class FakeBoard:
    """The console's board actions, recorded."""
    resolve_path = staticmethod(console.resolve_path)
    board_for_sof = staticmethod(console.board_for_sof)

    def __init__(self):
        self.calls, self.sent = [], []

    def program(self, board, sof, quartus_bin=None, out=print):
        self.calls.append(("program", board.key, sof.name))
        out("programmed")

    def load(self, board, program, port, out=print):
        self.calls.append(("load", board.key, program.name, port))

    def check_chain(self, boards, board, quartus_bin=None):
        self.calls.append(("check", board.key))
        return True, f"USB-Blaster [1-1]: {board.idcode}, matches {board.key}", "press k"

    def jtag_device(self, board, quartus_bin=None):
        self.calls.append(("connect", board.key))
        return "USB-Blaster [1-1]", "@1: EP4CE115 (0x020F70DD)"

    def Keyboard(self, cable, device, quartus_bin=None):  # noqa: N802 (the console module's class)
        return FakeKeyboard(self.sent)


class ConsoleTests(unittest.TestCase):
    def drive(self, kitty, script):
        """Run the console on a pseudo-terminal; the test is the terminal."""
        boards = {b.key: b for b in console.parse_config(CONFIG, "demo")}
        board = next(iter(boards.values()))
        master, slave = pty.openpty()
        fake = FakeBoard()
        app = tui.Console(boards, (board, "selected demo/de2_115"), port="/dev/ttyFAKE",
                          fd_in=slave, fd_out=slave, actions=fake)
        thread = threading.Thread(target=app.run, daemon=True)
        thread.start()
        screen = bytearray()

        def read_until(token, seconds=3.0):
            deadline = time.monotonic() + seconds
            while token not in screen and time.monotonic() < deadline:
                if select.select([master], [], [], 0.02)[0]:
                    screen.extend(os.read(master, 65536))
            self.assertIn(token, screen)

        read_until(b"\x1b[c")
        os.write(master, (b"\x1b[?0u" if kitty else b"") + b"\x1b[?62;22c")
        for data, expect in script:
            screen.clear()
            os.write(master, data)
            if expect:
                read_until(expect)
            time.sleep(0.25)
        thread.join(5)
        os.close(master)
        os.close(slave)
        self.assertFalse(thread.is_alive())
        return fake, app

    def test_program_load_and_kitty_play_send_make_and_break(self):
        fake, app = self.drive(True, [
            (b"p", b"programmed"), (b"l", None),
            (b"i", b"PLAY"), (b"\x1b[120u", None), (b"\x1b[120;1:2u", None), (b"\x1b[120;1:3u", None),
            (b"\x1b[1;1:1A", None), (b"\x1b[93;5u", b"play: left"), (b"q", None)])
        self.assertEqual(fake.calls, [("check", "demo/de2_115"), ("program", "demo/de2_115", "demo.sof"),
                                      ("load", "demo/de2_115", "", "/dev/ttyFAKE"),
                                      ("check", "demo/de2_115"), ("connect", "demo/de2_115")])
        self.assertEqual(app.last_key, "release held keys -> board took 3/3 bytes (e0 f0 75)")
        self.assertEqual(b"".join(fake.sent), bytes([0x22, 0xF0, 0x22, 0xE0, 0x75, 0xE0, 0xF0, 0x75]))
        self.assertTrue(app.kitty)

    def test_plain_terminal_play_breaks_after_the_repeats_stop(self):
        fake, app = self.drive(False, [(b"i", b"presses only"), (b"x", None), (b"\x1d", b"play: left"),
                                       (b"q", None)])
        self.assertFalse(app.kitty)
        self.assertEqual(b"".join(fake.sent), bytes([0x22, 0xF0, 0x22]))

    def test_test_key_and_the_screen_show_the_link(self):
        fake, app = self.drive(True, [(b"t", b"test Space"), (b"q", None)])
        self.assertEqual(b"".join(fake.sent), bytes([0x29, 0xF0, 0x29]))
        screen = "\n".join(app.lines(120, 40))
        for text in ("[c] Cable", "matches demo/de2_115", "[k] Keyboard  connected", "(1 sent)", "Next: press i"):
            self.assertIn(text, screen)

    def test_keys_without_a_keyboard_are_reported(self):
        sent = []
        worker = tui.Worker(lambda kind, value: sent.append((kind, value)))
        worker.send(b"\x22", "x press")
        self.assertEqual(sent, [("sent", ("x press", "dropped: keyboard not connected"))])

    def test_hints_follow_the_cable_and_the_keyboard(self):
        boards = {b.key: b for b in console.parse_config(CONFIG, "demo")}
        app = tui.Console(boards, None, fd_in=0, fd_out=1, actions=FakeBoard())
        app.cable = (False, "no JTAG cable found", "plug in the board's USB-Blaster")
        self.assertEqual(app.hint(), "plug in the board's USB-Blaster")
        app.cable, app.keyboard = (True, "ok", "press k"), "failed"
        self.assertIn("press p (program) and l (load)", app.hint())


if __name__ == "__main__":
    unittest.main()
