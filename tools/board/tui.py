"""The board console in the terminal: setup (board, SOF, program file, UART
port), the JTAG keyboard with its cable check, every option on screen with
what to do next, and a log.

Command keys act on the selected board; play mode forwards every key press and
release to the board's JTAG keyboard until Ctrl+] leaves it. Releases come from
the kitty keyboard protocol when the terminal answers its query; otherwise a
key is held while it repeats. Board actions run on one worker thread so the
screen never blocks, and programming closes the JTAG keyboard first because
quartus_pgm needs the cable alone.
Spec: wiki/tools/board/spec.md#terminal-ui.
"""
from __future__ import annotations

from collections import deque
import os
from pathlib import Path
import queue
import re
import select
import threading
import time

from tools.board import board as console
from tools.board.keys import CTRL_RIGHT_BRACKET, KeyState, TerminalKeys, TimedKeys, brk, key_name, make

TICK = 0.01
LOG_LINES = 200
# Ref: kitty keyboard protocol progressive enhancement: disambiguate (1), report event types (2),
# report all keys as escape codes (8).
KITTY_FLAGS = 1 | 2 | 8


class Worker:
    """One thread runs board actions and key sends in order; results go to `post`."""

    def __init__(self, post):
        self.post, self.jobs = post, queue.Queue()
        self.keyboard = None
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while True:
            job = self.jobs.get()
            try:
                job()
            except Exception as exc:  # Why: any failure is reported in the log; the worker keeps serving
                self.post("error", f"{type(exc).__name__}: {exc}")

    def submit(self, job):
        self.jobs.put(job)

    def close_keyboard(self):
        if self.keyboard is not None:
            keyboard, self.keyboard = self.keyboard, None
            keyboard.close()
            self.post("keyboard", ("off", "not connected"))

    def send(self, codes: bytes, label: str = ""):
        if not codes:
            return
        if self.keyboard is None:
            self.post("sent", (label, "dropped: keyboard not connected"))
            return
        try:
            accepted = self.keyboard.send(codes)
        except Exception as exc:
            self.close_keyboard()
            self.post("keyboard", ("failed", f"key send failed: {exc}"))
            raise RuntimeError(f"keyboard stopped: {exc}") from exc
        accepted = len(codes) if accepted is None else accepted
        self.post("sent", (label, f"board took {accepted}/{len(codes)} bytes ({codes.hex(' ')})"))


class Console:
    """The screen and its keys over one terminal (`fd_in`, `fd_out`)."""

    def __init__(self, boards: dict, first, *, port="", quartus_bin=None, fd_in=0, fd_out=1,
                 actions=console, clock=time.monotonic):
        self.boards, self.keys = boards, sorted(boards)
        self.fd_in, self.fd_out, self.actions, self.clock = fd_in, fd_out, actions, clock
        self.quartus_bin, self.port = quartus_bin, port
        self.events = queue.Queue()
        self.worker = Worker(lambda kind, text: self.events.put((kind, text)))
        self.log = deque(maxlen=LOG_LINES)
        self.cable = (None, "not checked yet", "press c to check the cable")
        self.keyboard, self.keyboard_detail = "off", "not connected"
        self.last_key, self.keys_sent = "none yet", 0
        self.mode, self.edit, self.buffer = "command", None, ""
        self.kitty = False
        self.input = TerminalKeys()
        self.state: KeyState = KeyState()
        self.running, self.dirty = True, True
        self.select(first[0].key if first else self.keys[0])
        # Why: a start-up board the chain did not confirm follows the first cable check that can
        self.follow = first is None or "choose the board" in first[1]
        self.chain_idcode = None if self.follow else self.board.idcode
        if first:
            self.note(first[1])

    def select(self, key: str):
        self.board = self.boards[key]
        if self.worker.keyboard is None:
            self.keyboard, self.keyboard_detail = "off", "not connected"
        self.sof = str(self.actions.resolve_path(self.board.sof))
        default = self.board.program_default
        self.program = str(self.actions.resolve_path(default)) if default else ""

    def note(self, text: str, error=False):
        for line in str(text).splitlines() or [""]:
            line = ("error: " if error else "") + line
            last = self.log[-1] if self.log else ""
            repeat = re.fullmatch(re.escape(line) + r"(?: \(x(\d+)\))?", last)
            if repeat:
                self.log[-1] = f"{line} (x{int(repeat[1] or 1) + 1})"
            else:
                self.log.append(line)

    def write(self, text: str):
        os.write(self.fd_out, text.encode())

    def hint(self) -> str:
        """What to do next, from the cable check and the keyboard state."""
        ready, _, cable_hint = self.cable
        if self.edit is not None:
            return "type the new value; Enter keeps it, Esc cancels"
        if self.keyboard == "failed":
            return ("press c to check the cable" if ready is not True else cable_hint) \
                + "; if the board was just powered or programmed, press p (program) and l (load) first"
        if ready is False:
            return cable_hint
        if self.keyboard == "connecting":
            return "connecting the keyboard (a few seconds)"
        if self.keyboard == "on":
            return ("in play: keys go to the board, Ctrl+] leaves" if self.mode == "play"
                    else "press i to play; the game reads arrows, X=A, Z=B, Enter=Start, Space=Select")
        return cable_hint if ready is None else "press k to connect the keyboard, or i to connect and play"

    def do_check(self):
        board = self.board
        if self.worker.keyboard is not None:
            # Why: the open keyboard session holds the chain, so jtagconfig would only report it in use
            self.cable = (True, f"in use by this console's keyboard ({self.keyboard_detail})",
                          "the keyboard is connected; press i to play")
            return
        self.cable = (None, "checking...", "running jtagconfig")
        self.worker.submit(lambda: self.worker.post("cable", (board, self.actions.check_chain(
            self.boards, board, self.quartus_bin))))

    def do_program(self):
        board, sof = self.board, Path(self.sof)

        def job():
            self.worker.close_keyboard()
            self.actions.program(board, sof, quartus_bin=self.quartus_bin,
                                 out=lambda t: self.worker.post("log", t))
            self.worker.post("log", "programmed: load the program, then connect the keyboard")
        self.worker.submit(job)

    def do_load(self):
        board, program, port = self.board, Path(self.program), self.port
        if not port:
            self.note("loading needs the UART port: press u to set it", error=True)
            return
        self.worker.submit(lambda: self.actions.load(board, program, port,
                                                     out=lambda t: self.worker.post("log", t)))

    def do_connect(self):
        board = self.board
        self.keyboard, self.keyboard_detail = "connecting", f"finding {board.idcode} on the cable..."

        def job():
            self.worker.close_keyboard()
            self.worker.post("cable", (board, self.actions.check_chain(self.boards, board, self.quartus_bin)))
            try:
                cable, device = self.actions.jtag_device(board, quartus_bin=self.quartus_bin)
                self.worker.keyboard = self.actions.Keyboard(cable, device, self.quartus_bin)
            except Exception as exc:
                self.worker.post("keyboard", ("failed", str(exc)))
                raise
            self.worker.post("keyboard", ("on", f"connected: {board.key} on {cable}"))
        self.worker.submit(job)

    def do_test_key(self):
        """One Space press and release, so the link is proven without playing."""
        if self.worker.keyboard is None and self.keyboard != "connecting":
            self.do_connect()
        self.worker.submit(lambda: self.worker.send(make("space") + brk("space"), "test Space"))

    def enter_play(self):
        if self.worker.keyboard is None and self.keyboard != "connecting":
            self.do_connect()
        self.mode = "play"
        self.state = KeyState() if self.kitty else TimedKeys()
        if self.kitty:
            self.write(f"\x1b[>{KITTY_FLAGS}u")
        self.note("play: every key goes to the board; Ctrl+] leaves"
                  + ("" if self.kitty else " (no key releases from this terminal: a key is held while it repeats)"))

    def leave_play(self):
        if self.kitty:
            self.write("\x1b[<u")
        codes = self.state.release_all()
        self.worker.submit(lambda: self.worker.send(codes, "release held keys"))
        self.mode = "command"
        self.note("play: left")

    def on_bytes(self, data: bytes, now: float):
        if self.mode == "play":
            for kind, key in self.input.feed(data, now):
                if kind == CTRL_RIGHT_BRACKET:
                    self.leave_play()
                    self.input.buffer.clear()
                    return
                codes = self.state.release(key, now) if kind == "release" else self.state.press(key, now)
                if codes:
                    label = f"{key_name(key)} {kind}"
                    self.worker.submit(lambda c=codes, l=label: self.worker.send(c, l))
            return
        # Why: arrow and function keys send escape sequences whose letters are no commands
        data = re.sub(rb"\x1b(\[[0-?]*[ -/]*[@-~]|O.)", b"", data)
        for char in data.decode("utf-8", "replace"):
            self.on_char(char)

    def on_char(self, char: str):
        if self.edit is not None:
            if char in "\r\n":
                setattr(self, self.edit, self.buffer.strip())
                if self.edit == "sof":
                    match = self.actions.board_for_sof(self.boards, Path(self.sof))
                    if match is not None and match.key != self.board.key:
                        self.follow = False
                        sof = self.sof
                        self.select(match.key)
                        self.sof = sof
                self.edit = None
            elif char == "\x1b":
                self.edit = None
            elif char in "\x7f\x08":
                self.buffer = self.buffer[:-1]
            elif char.isprintable():
                self.buffer += char
            return
        action = {"c": self.do_check, "p": self.do_program, "l": self.do_load, "k": self.do_connect,
                  "t": self.do_test_key, "i": self.enter_play, "b": self.next_board, "q": self.quit,
                  "\x03": self.quit}.get(char)
        if action:
            action()
        elif char in ("s", "f", "u"):
            self.edit = {"s": "sof", "f": "program", "u": "port"}[char]
            self.buffer = getattr(self, self.edit)

    def next_board(self):
        """The next board on the cable's IDCODE once a check found it, else the next
        of all; a connected keyboard reconnects to it."""
        self.follow = False
        connected = self.worker.keyboard is not None or self.keyboard in ("on", "connecting")
        keys = [k for k in self.keys if self.boards[k].idcode == self.chain_idcode] or self.keys
        at = keys.index(self.board.key) if self.board.key in keys else -1
        self.select(keys[(at + 1) % len(keys)])
        if connected:
            self.do_connect()
        else:
            self.do_check()

    def quit(self):
        self.running = False

    def tick(self, now: float):
        codes = self.state.flush(now)
        if codes:
            self.worker.submit(lambda: self.worker.send(codes, "release (no repeat)"))
        if self.mode == "play" and self.input.buffer:
            self.on_bytes(b"", now)
        while not self.events.empty():
            self.dirty = True
            kind, value = self.events.get()
            if kind == "cable":
                board, value = value
                if value[0]:
                    self.chain_idcode = board.idcode
                if board is self.board:
                    self.cable = value
                self.note(("cable ok: " if value[0] else "cable: ") + value[1])
                if self.follow and value[0] is False:
                    self.worker.submit(lambda: self.worker.post("detected", self.actions.detect_board(
                        self.boards, self.quartus_bin)))
            elif kind == "detected":
                board, why = value
                if self.follow and "choose the board" not in why:
                    self.follow, self.chain_idcode = False, board.idcode
                    self.note(why)
                    if board.key != self.board.key:
                        self.select(board.key)
                        self.do_check()
            elif kind == "keyboard":
                self.keyboard, self.keyboard_detail = value
                self.note(f"keyboard: {value[1]}", error=value[0] == "failed")
            elif kind == "sent":
                label, result = value
                self.last_key = f"{label} -> {result}"
                self.keys_sent += result.startswith("board took")
            else:
                self.note(value, error=kind == "error")

    def lines(self, width: int, height: int) -> list[str]:
        sof_built = "built" if Path(self.sof).is_file() else "NOT BUILT"
        ready, finding, _ = self.cable
        mark = {True: "OK ", False: "!! ", None: ".. "}[ready]
        kb_mark = {"on": "OK ", "failed": "!! ", "connecting": ".. ", "off": "-- "}[self.keyboard]
        mode = "PLAY: every key press and release goes to the board; Ctrl+] leaves" if self.mode == "play" \
            else "COMMAND: press a key below"
        same = [k for k in self.keys if self.boards[k].idcode == self.chain_idcode]
        rows = [
            f" Board console    {mode}",
            "",
            " Setup",
            f"   [b] Board     {self.board.key}  (IDCODE {self.board.idcode})"
            + (f"   b cycles the {len(same)} boards on this cable" if len(same) > 1 else ""),
            f"   [s] SOF       {self.sof}  ({sof_built})         [p] program the FPGA",
            f"   [f] {self.board.program_label[:9]:<9} {self.program or '(none: press f)'}",
            f"   [u] UART      {self.port or '(none: press u)'} at {self.board.baud} baud   [l] load it",
            "",
            " JTAG keyboard",
            f"   {mark}[c] Cable     {finding}",
            f"   {kb_mark}[k] {'Reconnect' if self.keyboard == 'on' else 'Connect  '} {self.keyboard_detail}",
            f"       [t] Test key  sends one Space; last: {self.last_key}  ({self.keys_sent} sent)",
            f"   Terminal      {'key releases (kitty protocol)' if self.kitty else 'presses only: held keys repeat'}",
            "",
            f" Next: {self.hint()}",
        ]
        if self.edit is not None:
            rows.append(f" Edit {self.edit}: {self.buffer}_")
        rows += [" [i] play   [q] quit", "-" * min(width, 100)]
        room = max(0, height - len(rows))
        rows += list(self.log)[-room:] if room else []
        return [row[:width] for row in rows]

    def render(self):
        try:
            width, height = os.get_terminal_size(self.fd_out)
        except OSError:
            width, height = 0, 0
        width, height = width or 100, height or 40
        self.write("\x1b[H\x1b[2J" + "\r\n".join(self.lines(width, height)))

    def probe_kitty(self, seconds=0.5) -> tuple[bool, bytes]:
        """Ask for the kitty keyboard flags, then primary attributes, which every
        terminal answers; a flags reply before it means the protocol works.
        Returns that and the bytes typed after the replies."""
        self.write("\x1b[?u\x1b[c")
        reply, deadline = b"", self.clock() + seconds
        attributes = re.compile(rb"\x1b\[\?[0-9;]*c")
        while self.clock() < deadline and not attributes.search(reply):
            ready, _, _ = select.select([self.fd_in], [], [], TICK)
            if ready:
                reply += os.read(self.fd_in, 256)
        found = attributes.search(reply)
        head, rest = (reply[:found.start()], reply[found.end():]) if found else (reply, b"")
        return re.search(rb"\x1b\[\?[0-9]*u", head) is not None, rest

    def run(self) -> int:
        import termios
        import tty
        saved = termios.tcgetattr(self.fd_in) if os.isatty(self.fd_in) else None
        try:
            if saved is not None:
                tty.setraw(self.fd_in)
            self.write("\x1b[?1049h\x1b[?25l")
            self.kitty, typed = self.probe_kitty()
            self.note("terminal: " + ("kitty keyboard protocol (key releases)" if self.kitty
                                      else "no kitty keyboard protocol (presses only)"))
            self.do_check()
            self.on_bytes(typed, self.clock())
            while self.running:
                ready, _, _ = select.select([self.fd_in], [], [], TICK)
                now = self.clock()
                if ready:
                    data = os.read(self.fd_in, 1024)
                    if not data:
                        break
                    self.on_bytes(data, now)
                    self.dirty = True
                self.tick(now)
                if self.dirty:
                    self.render()
                    self.dirty = False
        finally:
            if self.mode == "play":
                self.leave_play()
            self.worker.submit(self.worker.close_keyboard)
            done = threading.Event()
            self.worker.submit(done.set)
            done.wait(5)
            self.write("\x1b[?25h\x1b[?1049l")
            if saved is not None:
                termios.tcsetattr(self.fd_in, termios.TCSADRAIN, saved)
        return 0


def main(boards: dict, *, port: str = "", quartus_bin=None, first=None) -> int:
    if not boards:
        raise RuntimeError("no src/*/cfg/board.json found")
    return Console(boards, first, port=port, quartus_bin=quartus_bin).run()
