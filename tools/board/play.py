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
# Why: any other pad button is an action key, placed in order on a grid right of the D-pad.
GRID_COLUMN, GRID_WIDTH, GRID_COLUMNS = 27, 14, 2


def layout(pad: dict, aliases: dict | None = None) -> dict:
    """Each pad button's (row, column, width, face): the handheld's named places,
    then every other button on the action grid, labelled with the laptop key to
    press for it (an alias when the project declares one)."""
    typed = {board: laptop for laptop, board in (aliases or {}).items()}
    places, slot = {}, 0
    for name, key in pad.items():
        if name in LAYOUT:
            places[name] = LAYOUT[name]
            continue
        row, column = divmod(slot, GRID_COLUMNS)
        places[name] = (1 + 3 * row, GRID_COLUMN + GRID_WIDTH * column, GRID_WIDTH - 1,
                        f"{name}:{face(typed.get(key, key))}"[:GRID_WIDTH - 3])
        slot += 1
    return places


def face(key: str) -> str:
    return KEY_FACES.get(key, key.upper() if len(key) == 1 else key)


KEY_FACES = {"Control_L": "Ctrl", "Control_R": "Ctrl", "Shift_L": "Shift", "Shift_R": "Shift",
             "Alt_L": "Alt", "Alt_R": "Alt", "space": "Spc", "Return": "Ent", "Escape": "Esc", "Tab": "Tab"}


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


def legend(pad: dict, aliases: dict) -> str:
    typed = {board: laptop for laptop, board in aliases.items()}
    return "  ".join(f"{name}={face(key)}" + (f" or {face(typed[key])}" if key in typed else "")
                     for name, key in pad.items())


def button(places: dict, name: str, lit: bool) -> str:
    row, col, width, face = places[name]
    top, left = PAD_TOP + row, PAD_LEFT + col
    colour = "\x1b[30;42m" if lit else "\x1b[0m"
    inner = face.center(width - 2)
    return (f"{at(top, left)}{colour}┌{'─' * (width - 2)}┐{at(top + 1, left)}│{inner}│"
            f"{at(top + 2, left)}└{'─' * (width - 2)}┘\x1b[0m")


def screen(title: str, kitty: bool, pad: dict, aliases: dict | None = None) -> str:
    places = layout(pad, aliases)
    out = ["\x1b[H\x1b[2J\x1b[?25l", at(2, 3), f"\x1b[1mPLAYING  {title}\x1b[0m", at(4, 3),
           "Every key you press goes to the board over JTAG.  Press Ctrl+] to return to the console.",
           at(5, 3), "This terminal reports key releases." if kitty else
           "This terminal reports no key releases: a key counts as held while it repeats."]
    if pad:
        out += [at(6, 3), f"Keys: {legend(pad, aliases or {})}   (click a button to press it)",
                at(PAD_TOP - 1, PAD_LEFT), "╭" + "─" * 56 + "╮"]
        out += [at(PAD_TOP + r, PAD_LEFT) + "│" + " " * 56 + "│" for r in range(0, 14)]
        out += [at(PAD_TOP + 14, PAD_LEFT), "╰" + "─" * 56 + "╯"]
        out += [button(places, name, False) for name in places]
    return "".join(out)


def hit(pad: dict, col: int, row: int) -> str | None:
    """The pad button under a click at 1-based (`col`, `row`)."""
    for name, (r, c, width, _) in layout(pad).items():
        top, left = PAD_TOP + r, PAD_LEFT + c
        if top <= row < top + 3 and left <= col < left + width:
            return name
    return None


def play(send, title: str, *, pad: dict | None = None, aliases: dict | None = None,
         fd_in: int = 0, fd_out: int = 1, clock=time.monotonic) -> int:
    """Run play mode on the terminal until Ctrl+] (or end of input); `send(codes,
    label)` delivers Set-2 bytes; `pad` maps pad buttons to key names and `aliases`
    laptop keys to the board keys they send. Returns the
    number of key events forwarded."""
    import termios
    import tty
    pad, aliases = dict(pad or {}), dict(aliases or {})
    places = layout(pad, aliases)
    saved = termios.tcgetattr(fd_in) if os.isatty(fd_in) else None
    kitty, state = False, KeyState()
    keys, forwarded, clicked, lit = TerminalKeys(), 0, None, set()

    def show(last: str):
        held = set(state.held) | ({key_name(pad[clicked])} if clicked else set())
        out = [at(PAD_TOP + 16, 3), f"\x1b[2KLast: {last}"]
        for name, key in pad.items():
            on = key_name(key) in held
            if on != (name in lit):
                lit.symmetric_difference_update({name})
                out.append(button(places, name, on))
        os.write(fd_out, "".join(out).encode())

    try:
        if saved is not None:
            tty.setraw(fd_in)
        kitty = probe_kitty(fd_in, fd_out, clock=clock)
        state = KeyState() if kitty else TimedKeys()
        if kitty:
            os.write(fd_out, f"\x1b[>{KITTY_FLAGS}u".encode())
        os.write(fd_out, MOUSE_ON + screen(title, kitty, pad, aliases).encode())
        while True:
            data = b""
            if select.select([fd_in], [], [], TICK)[0]:
                data = os.read(fd_in, 1024)
                if not data:
                    return forwarded
            now, last = clock(), None
            for code, col, row, kind in MOUSE.findall(data):
                if int(code) & ~0x1C:
                    continue   # Why: only a plain left button counts; the wheel (64, 65) never releases
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
                # Why: an alias stands for a board key the terminal may never report (Ctrl, Alt alone)
                key = aliases.get(key, key)
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
