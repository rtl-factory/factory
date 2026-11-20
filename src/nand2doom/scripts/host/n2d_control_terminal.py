"""Terminal controls for running Doom (n2d_control --terminal).

Terminals that speak the kitty keyboard protocol (kitty, foot, Ghostty, WezTerm,
recent Alacritty) report every key press and release, so held keys reach Doom
as make and break exactly like the Tk window. Elsewhere only key bytes arrive:
each key is then a tap whose break follows after a short hold that terminal
auto-repeat extends. Spec: wiki/src/nand2doom/scripts/host/spec.md#terminal-controls.
"""
from __future__ import annotations

import os
import re
import select
import sys
import time

from src.nand2doom.scripts.host.n2d_control import BINDINGS, ControlError

PUSH_FLAGS = b"\x1b[>11u"     # disambiguate (1) + press/repeat/release (2) + every key as a code (8)
POP_FLAGS = b"\x1b[<u"
QUERY = b"\x1b[?u\x1b[c"      # kitty flags query, then primary DA so a silent terminal still answers
FOCUS_ON, FOCUS_OFF = b"\x1b[?1004h", b"\x1b[?1004l"
CSI = re.compile(rb"\x1b\[([0-9:;?<>=]*)([A-Za-z~])")

# Kitty functional key codes and the Tk keysyms BINDINGS already uses.
KITTY_KEYS = {9: "Tab", 13: "Return", 27: "Escape", 32: "space", 57414: "KP_Enter",
              57441: "Shift_L", 57442: "Control_L", 57443: "Alt_L",
              57447: "Shift_R", 57448: "Control_R", 57449: "Alt_R"}
ARROWS = {b"A": "Up", b"B": "Down", b"C": "Right", b"D": "Left"}
F10 = b"21"  # CSI 21 ~ : quit key in both modes
CTRL_C = 99  # 'c' with the Ctrl modifier quits in kitty mode


def key_symbol(code):
    if code in KITTY_KEYS:
        return KITTY_KEYS[code]
    if 32 < code < 127:
        return chr(code).lower()
    return None


class KittyParser:
    """Bytes -> (kind, symbol), kind one of press, repeat, release, quit, focus-in, focus-out."""

    def __init__(self):
        self.buffer = b""

    def feed(self, data):
        self.buffer += data
        events = []
        while True:
            start = self.buffer.find(b"\x1b")
            if start < 0:
                self.buffer = b""
                return events
            self.buffer = self.buffer[start:]
            match = CSI.match(self.buffer)
            if match is None:
                if len(self.buffer) > 64 or (len(self.buffer) > 1 and self.buffer[1:2] != b"["):
                    self.buffer = self.buffer[1:]  # not a CSI sequence: resynchronize
                    continue
                return events  # incomplete sequence; wait for more bytes
            self.buffer = self.buffer[match.end():]
            event = self.decode(match.group(1), match.group(2))
            if event:
                events.append(event)

    @staticmethod
    def decode(params, final):
        if final == b"I" and not params:
            return ("focus-in", None)
        if final == b"O" and not params:
            return ("focus-out", None)
        if params[:1] in (b"?", b">", b"<", b"="):
            return None  # protocol replies
        fields = params.split(b";")
        modifier = fields[1].split(b":") if len(fields) > 1 else [b"1"]
        try:
            mods = int(modifier[0] or b"1") - 1
            kind = {1: "press", 2: "repeat", 3: "release"}[int(modifier[1]) if len(modifier) > 1 else 1]
            code = int(fields[0].split(b":")[0] or b"1")
        except (ValueError, KeyError):
            return None
        if final == b"~":
            return ("quit", None) if fields[0] == F10 and kind == "press" else None
        if final in ARROWS:
            return (kind, ARROWS[final])
        if final != b"u":
            return None
        if code == CTRL_C and mods & 4:
            return ("quit", None) if kind == "press" else None
        symbol = key_symbol(code)
        return (kind, symbol) if symbol else None


class TapParser:
    """Legacy key bytes -> ('tap', symbol), ('quit', None) or focus events; no release exists."""

    LEGACY = {b"\x1b[A": "Up", b"\x1b[B": "Down", b"\x1b[C": "Right", b"\x1b[D": "Left",
              b"\x1bOA": "Up", b"\x1bOB": "Down", b"\x1bOC": "Right", b"\x1bOD": "Left",
              b"\x1b[I": "focus-in", b"\x1b[O": "focus-out", b"\x1b[21~": "quit"}

    def __init__(self):
        self.buffer = b""

    def feed(self, data, *, flush=False):
        """`flush` turns a lone pending ESC into the Escape key (no sequence followed it)."""
        self.buffer += data
        events = []
        while self.buffer:
            if self.buffer[:1] == b"\x1b":
                sequence = next((s for s in self.LEGACY if self.buffer.startswith(s)), None)
                if sequence:
                    name = self.LEGACY[sequence]
                    special = name in ("focus-in", "focus-out", "quit")
                    events.append((name, None) if special else ("tap", name))
                    self.buffer = self.buffer[len(sequence):]
                    continue
                match = CSI.match(self.buffer)
                if match:
                    self.buffer = self.buffer[match.end():]  # unsupported sequence
                    continue
                if not flush and (len(self.buffer) == 1 or self.buffer[1:2] in (b"[", b"O")):
                    return events  # maybe the start of a sequence
                events.append(("tap", "Escape"))
                self.buffer = self.buffer[1:]
                continue
            byte, self.buffer = self.buffer[0], self.buffer[1:]
            if byte == 3:
                events.append(("quit", None))
            elif byte == 9:
                events.append(("tap", "Tab"))
            elif byte in (10, 13):
                events.append(("tap", "Return"))
            elif byte == 32:
                events.append(("tap", "space"))
            elif 32 < byte < 127:
                events.append(("tap", chr(byte).lower()))
        return events


class Controls:
    """Maps parsed key events onto Session press/release; the source is the key symbol."""

    def __init__(self, session, *, clock=time.monotonic, first_hold=0.5, repeat_hold=0.15):
        self.session, self.clock = session, clock
        self.first_hold, self.repeat_hold = first_hold, repeat_hold
        self.deadlines = {}  # tap mode: symbol -> release time
        self.quit = False

    def held(self):
        return sorted(set(self.session.sources.values()))

    def handle(self, kind, symbol):
        if kind == "quit":
            self.quit = True
        elif kind == "focus-out":
            self.release_all()
        elif kind in ("press", "tap"):
            action = BINDINGS.get(symbol) if symbol else None
            if action is None:
                return
            source = ("key", symbol)
            if kind == "tap":
                hold = self.repeat_hold if source in self.session.sources else self.first_hold
                self.deadlines[symbol] = self.clock() + hold
            self.session.press(source, action)
        elif kind == "release" and symbol:
            self.deadlines.pop(symbol, None)
            self.session.release(("key", symbol))

    def tick(self):
        now = self.clock()
        for symbol, deadline in list(self.deadlines.items()):
            if now >= deadline:
                del self.deadlines[symbol]
                self.session.release(("key", symbol))

    def release_all(self):
        self.deadlines.clear()
        self.session.release_all()


def detect_kitty(read, write, *, clock=time.monotonic, seconds=0.5):
    """Query the terminal. True only for a kitty flags reply before the DA reply."""
    write(QUERY)
    data, deadline = b"", clock() + seconds
    while clock() < deadline:
        data += read(0.05)
        if re.search(rb"\x1b\[\?[0-9]*u", data):
            return True
        if re.search(rb"\x1b\[\?[0-9;]*c", data):
            return False
    return False


def run(session, read, write, *, kitty, clock=time.monotonic, flush_after=0.05):
    """Event loop over injected read(timeout) -> bytes and write(bytes); returns 0 or 1.

    Every exit releases the acknowledged holds and closes the session once; a
    failed command latches the session (no further commands, see n2d_control)."""
    controls = Controls(session, clock=clock)
    parser = KittyParser() if kitty else TapParser()
    shown, pending_since = None, None
    try:
        while not controls.quit:
            data = read(0.02)
            if kitty:
                events = parser.feed(data)
            else:
                stale = pending_since is not None and clock() - pending_since >= flush_after
                events = parser.feed(data, flush=stale)
                pending_since = (pending_since or clock()) if parser.buffer else None
            for kind, symbol in events:
                controls.handle(kind, symbol)
                if controls.quit:
                    break
            controls.tick()
            held = controls.held()
            if held != shown:
                write(("\r\x1b[K held: " + (", ".join(held) or "-")).encode())
                shown = held
        controls.release_all()
        session.close()
        write(b"\r\nDisconnected; held inputs released.\r\n")
        return 0
    except ControlError as error:
        write(f"\r\nFAULT: {error}; input state may be uncertain; operator recovery required.\r\n".encode())
        return 1
    except BaseException:
        try:
            session.close()  # Ctrl-C as a signal or another error: still release once.
        except ControlError as error:
            write(f"\r\nFAULT during release: {error}\r\n".encode())
        raise


def main_loop(session, *, stdin=None, stdout=None):
    """Connect `session`, put the TTY in raw mode and run until F10 or Ctrl-C."""
    import termios
    import tty
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    if not stdin.isatty():
        print("terminal controls need an interactive terminal", file=sys.stderr)
        return 3
    fd, out = stdin.fileno(), stdout.fileno()

    def write(data):
        os.write(out, data)

    def read(timeout):
        ready, _, _ = select.select([fd], [], [], timeout)
        return os.read(fd, 1024) if ready else b""

    print(f"Connecting controls to {session.port} ...", flush=True)
    try:
        session.connect()
    except ControlError as error:
        print(f"controls connect failed: {error}", file=sys.stderr)
        return 1
    saved = termios.tcgetattr(fd)
    kitty = False
    try:
        tty.setraw(fd)
        kitty = detect_kitty(read, write)
        if kitty:
            write(PUSH_FLAGS)
        write(FOCUS_ON)
        mode = ("real key press and release (kitty keyboard protocol)" if kitty else
                "TAP mode: this terminal reports no key release; keys are taps that auto-repeat extends")
        write((f"Doom controls on {session.port}: {mode}\r\n"
               "Arrows/WASD move and turn, Ctrl/F fire, Space/E use, Shift/R run, Alt/Q strafe,\r\n"
               "Enter, Esc, Y/N, 1-7 weapons, Tab map. Quit: F10 or Ctrl-C (releases every held key).\r\n").encode())
        return run(session, read, write, kitty=kitty)
    finally:
        if kitty:
            write(POP_FLAGS)
        write(FOCUS_OFF)
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
        print()
