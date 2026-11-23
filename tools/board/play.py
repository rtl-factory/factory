"""Play mode of the board console: the whole terminal forwards every key press
and release to the board's JTAG keyboard until Ctrl+] returns.

When the project declares a pad (its buttons and the keys they stand for),
play draws a generic handheld controller: a button lights while its key is
held, whether from the keyboard or a mouse click on it, and a click presses
and releases that key on the board. Releases come from the kitty keyboard
protocol when the terminal answers its query; a key reported without one is a
tap (`KeyState.tap`). Keys go to the console's worker, which owns the JTAG
keyboard session, so they stay in order with every other board action.
Spec: wiki/tools/board/spec.md#play.
"""
from __future__ import annotations

import os
import re
import select
import time

from tools.board.keys import CTRL_RIGHT_BRACKET, KeyState, TerminalKeys, TimedKeys, brk, key_name, make

TICK = 0.01
# Ref: kitty keyboard protocol progressive enhancement: disambiguate (1), report event types (2),
# report all keys as escape codes (8).
KITTY_FLAGS = 1 | 2 | 8
# Ref: xterm mouse tracking: button press and release (1000) in SGR coordinates (1006).
MOUSE_ON, MOUSE_OFF = b"\x1b[?1000h\x1b[?1006h", b"\x1b[?1006l\x1b[?1000l"
MOUSE = re.compile(rb"\x1b\[<(\d+);(\d+);(\d+)([Mm])")
PAD_TOP, PAD_LEFT = 9, 5
# Why: one generic handheld layout, button -> (row, column, width, face) from the pad's corner.
LAYOUT = {"Up": (1, 9, 7, "▲"), "Left": (4, 2, 7, "◀"), "Right": (4, 16, 7, "▶"), "Down": (7, 9, 7, "▼"),
          "B": (5, 36, 7, "B"), "A": (2, 45, 7, "A"), "Select": (10, 15, 10, "SELECT"),
          "Start": (10, 27, 9, "START")}


def probe_kitty(fd_in: int, fd_out: int, seconds: float = 0.5, clock=time.monotonic) -> bool:
    """Ask for the kitty keyboard flags, then primary attributes, which every
    terminal answers; a flags reply before it means the protocol works."""
    os.write(fd_out, b"\x1b[?u\x1b[c")
    reply, deadline = b"", clock() + seconds
    attributes = re.compile(rb"\x1b\[\?[0-9;]*c")
    while clock() < deadline and not attributes.search(reply):
        if select.select([fd_in], [], [], TICK)[0]:
            reply += os.read(fd_in, 256)
    found = attributes.search(reply)
    return re.search(rb"\x1b\[\?[0-9]*u", reply[:found.start()] if found else reply) is not None


def drain(fd_in: int, seconds: float = 0.15):
    """Discard what the terminal still sends (the release of Ctrl+], a late
    reply), so the console does not read play-mode escape codes."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if select.select([fd_in], [], [], TICK)[0] and not os.read(fd_in, 4096):
            return


def at(row: int, col: int) -> str:
    return f"\x1b[{row};{col}H"


def button(name: str, lit: bool) -> str:
    row, col, width, face = LAYOUT[name]
    top, left = PAD_TOP + row, PAD_LEFT + col
    colour = "\x1b[30;42m" if lit else "\x1b[0m"
    inner = face.center(width - 2)
    return (f"{at(top, left)}{colour}┌{'─' * (width - 2)}┐{at(top + 1, left)}│{inner}│"
            f"{at(top + 2, left)}└{'─' * (width - 2)}┘\x1b[0m")


def screen(title: str, kitty: bool, pad: dict) -> str:
    legend = "  ".join(f"{name}={key}" for name, key in pad.items())
    out = ["\x1b[H\x1b[2J\x1b[?25l", at(2, 3), f"\x1b[1mPLAYING  {title}\x1b[0m", at(4, 3),
           "Every key you press goes to the board over JTAG.  Press Ctrl+] to return to the console.",
           at(5, 3), "This terminal reports key releases." if kitty else
           "This terminal reports no key releases: a key counts as held while it repeats."]
    if pad:
        out += [at(6, 3), f"Pad keys: {legend}   (click a button to press it)",
                at(PAD_TOP - 1, PAD_LEFT), "╭" + "─" * 56 + "╮"]
        out += [at(PAD_TOP + r, PAD_LEFT) + "│" + " " * 56 + "│" for r in range(0, 14)]
        out += [at(PAD_TOP + 14, PAD_LEFT), "╰" + "─" * 56 + "╯"]
        out += [button(name, False) for name in LAYOUT if name in pad]
    return "".join(out)


def hit(pad: dict, col: int, row: int) -> str | None:
    """The pad button under a click at 1-based (`col`, `row`)."""
    for name, (r, c, width, _) in LAYOUT.items():
        top, left = PAD_TOP + r, PAD_LEFT + c
        if name in pad and top <= row < top + 3 and left <= col < left + width:
            return name
    return None


def play(send, title: str, *, pad: dict | None = None, fd_in: int = 0, fd_out: int = 1,
         clock=time.monotonic) -> int:
    """Run play mode on the terminal until Ctrl+] (or end of input); `send(codes,
    label)` delivers Set-2 bytes; `pad` maps pad buttons to key names. Returns the
    number of key events forwarded."""
    import termios
    import tty
    pad = {name: key for name, key in (pad or {}).items() if name in LAYOUT}
    saved = termios.tcgetattr(fd_in) if os.isatty(fd_in) else None
    if saved is not None:
        tty.setraw(fd_in)
    kitty = probe_kitty(fd_in, fd_out, clock=clock)
    state: KeyState = KeyState() if kitty else TimedKeys()
    keys, forwarded, clicked, lit = TerminalKeys(), 0, None, set()

    def show(last: str):
        held = set(state.held) | ({key_name(pad[clicked])} if clicked else set())
        out = [at(PAD_TOP + 16, 3), f"\x1b[2KLast: {last}"]
        for name, key in pad.items():
            on = key_name(key) in held
            if on != (name in lit):
                lit.symmetric_difference_update({name})
                out.append(button(name, on))
        os.write(fd_out, "".join(out).encode())

    try:
        if kitty:
            os.write(fd_out, f"\x1b[>{KITTY_FLAGS}u".encode())
        os.write(fd_out, MOUSE_ON + screen(title, kitty, pad).encode())
        while True:
            data = b""
            if select.select([fd_in], [], [], TICK)[0]:
                data = os.read(fd_in, 1024)
                if not data:
                    return forwarded
            now, last = clock(), None
            for code, col, row, kind in MOUSE.findall(data):
                if int(code) & 3:
                    continue
                if kind == b"M" and clicked is None:
                    clicked = hit(pad, int(col), int(row))
                    if clicked:
                        send(make(key_name(pad[clicked])), f"{clicked} click")
                        forwarded, last = forwarded + 1, f"{clicked} (click)"
                elif kind == b"m" and clicked:
                    send(brk(key_name(pad[clicked])), f"{clicked} release")
                    clicked, last = None, last or "click released"
            for kind, key in keys.feed(MOUSE.sub(b"", data), now):
                if kind == CTRL_RIGHT_BRACKET:
                    return forwarded
                codes = {"release": state.release, "tap": state.tap}.get(kind, state.press)(key, now)
                if codes:
                    forwarded, last = forwarded + 1, f"{key} {kind}"
                    send(codes, f"{key} {kind}")
            codes = state.flush(now)
            if codes:
                send(codes, "release (no repeat)")
                last = last or "released"
            if last:
                show(last)
    finally:
        codes = state.release_all() + (brk(key_name(pad[clicked])) if clicked else b"")
        if codes:
            send(codes, "release held keys")
        os.write(fd_out, MOUSE_OFF + (b"\x1b[<u" if kitty else b"") + b"\x1b[?25h")
        drain(fd_in)
        if saved is not None:
            termios.tcsetattr(fd_in, termios.TCSADRAIN, saved)
