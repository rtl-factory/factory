"""Terminal keys to PS/2 Set-2 make and break codes for the board console.

The terminal reports keys as bytes; `TerminalKeys` turns them into press,
repeat and release events by key name (the X11 keysym of the key). Every
board keyboard input decodes Set-2, so the console forwards the physical key
and leaves its meaning to the program: Doom reads Ctrl as Fire, nand2mario
reads X as A. Auto-repeat is collapsed so a held key is one make and one break.
Spec: wiki/tools/board/spec.md#keyboard.
"""
from __future__ import annotations

EXTENDED, RELEASE = 0xE0, 0xF0

# Ref: PS/2 scan code set 2 (IBM PC AT keyboard), make codes without the E0 prefix.
_PLAIN = {
    **dict(zip("abcdefghijklmnopqrstuvwxyz",
               (0x1C, 0x32, 0x21, 0x23, 0x24, 0x2B, 0x34, 0x33, 0x43, 0x3B, 0x42, 0x4B, 0x3A,
                0x31, 0x44, 0x4D, 0x15, 0x2D, 0x1B, 0x2C, 0x3C, 0x2A, 0x1D, 0x22, 0x35, 0x1A))),
    **dict(zip("1234567890", (0x16, 0x1E, 0x26, 0x25, 0x2E, 0x36, 0x3D, 0x3E, 0x46, 0x45))),
    "minus": 0x4E, "equal": 0x55, "bracketleft": 0x54, "bracketright": 0x5B, "backslash": 0x5D,
    "semicolon": 0x4C, "apostrophe": 0x52, "grave": 0x0E, "comma": 0x41, "period": 0x49,
    "slash": 0x4A, "space": 0x29, "Return": 0x5A, "Tab": 0x0D, "Escape": 0x76, "BackSpace": 0x66,
    "Shift_L": 0x12, "Shift_R": 0x59, "Control_L": 0x14, "Alt_L": 0x11, "Caps_Lock": 0x58,
    "F1": 0x05, "F2": 0x06, "F3": 0x04, "F4": 0x0C, "F5": 0x03, "F6": 0x0B,
    "F7": 0x83, "F8": 0x0A, "F9": 0x01, "F10": 0x09, "F11": 0x78, "F12": 0x07,
}
# Ref: PS/2 scan code set 2, keys sent with the E0 prefix.
_EXTENDED = {
    "Up": 0x75, "Down": 0x72, "Left": 0x6B, "Right": 0x74, "Home": 0x6C, "End": 0x69,
    "Prior": 0x7D, "Next": 0x7A, "Insert": 0x70, "Delete": 0x71,
    "Control_R": 0x14, "Alt_R": 0x11, "KP_Enter": 0x5A,
}
# Why: a shifted character names the unshifted key it was typed on.
_SHIFTED = {"exclam": "1", "at": "2", "numbersign": "3", "dollar": "4", "percent": "5",
            "asciicircum": "6", "ampersand": "7", "asterisk": "8", "parenleft": "9", "parenright": "0",
            "underscore": "minus", "plus": "equal", "braceleft": "bracketleft",
            "braceright": "bracketright", "bar": "backslash", "colon": "semicolon",
            "quotedbl": "apostrophe", "asciitilde": "grave", "less": "comma", "greater": "period",
            "question": "slash", "ISO_Left_Tab": "Tab"}


def key_name(keysym: str) -> str | None:
    """The physical key of a keysym, or None when the key has no Set-2 code."""
    key = _SHIFTED.get(keysym, keysym)
    if len(key) == 1:
        key = key.lower()
    return key if key in _PLAIN or key in _EXTENDED else None


def make(key: str) -> bytes:
    return bytes([EXTENDED, _EXTENDED[key]]) if key in _EXTENDED else bytes([_PLAIN[key]])


def brk(key: str) -> bytes:
    return bytes([EXTENDED, RELEASE, _EXTENDED[key]]) if key in _EXTENDED else bytes([RELEASE, _PLAIN[key]])


class KeyState:
    """Press and release events in, Set-2 bytes out, one make and one break per
    held key. A release is only sent once `settle` seconds pass without a new
    press of that key, so a release then press at once (auto-repeat) is no
    break; `flush(now)` sends those. A tap (a key the terminal reports with no
    release) holds the key until `quiet` seconds pass without another tap."""

    def __init__(self, settle: float = 0.0, quiet: float = 0.15):
        self.settle, self.quiet = settle, quiet
        self.held: set[str] = set()
        self.break_due: dict[str, float] = {}

    def press(self, keysym: str, now: float) -> bytes:
        key = key_name(keysym)
        if key is None:
            return b""
        if self.break_due.pop(key, None) is not None or key in self.held:
            return b""
        self.held.add(key)
        return make(key)

    def release(self, keysym: str, now: float) -> bytes:
        key = key_name(keysym)
        if key in self.held:
            self.break_due[key] = now + self.settle
        return b""

    def tap(self, keysym: str, now: float) -> bytes:
        key = key_name(keysym)
        out = KeyState.press(self, keysym, now)
        if key in self.held:
            self.break_due[key] = now + self.quiet
        return out

    def flush(self, now: float) -> bytes:
        out = bytearray()
        for key, due in list(self.break_due.items()):
            if now >= due:
                del self.break_due[key]
                self.held.discard(key)
                out += brk(key)
        return bytes(out)

    def release_all(self) -> bytes:
        """Break every held key, as on leaving play."""
        out = b"".join(brk(key) for key in sorted(self.held))
        self.held.clear()
        self.break_due.clear()
        return out


class TimedKeys(KeyState):
    """For a terminal without key releases: every press is a tap, so a key is
    held while it repeats and breaks once `quiet` seconds pass with no repeat."""

    def press(self, keysym: str, now: float) -> bytes:
        return self.tap(keysym, now)


CTRL_RIGHT_BRACKET = "leave"

# Ref: kitty keyboard protocol, "Functional key definitions" (CSI <code> u numbers).
_KITTY_CODES = {9: "Tab", 13: "Return", 27: "Escape", 127: "BackSpace", 57358: "Caps_Lock",
                57414: "KP_Enter", 57441: "Shift_L", 57442: "Control_L", 57443: "Alt_L",
                57447: "Shift_R", 57448: "Control_R", 57449: "Alt_R"}
_FINALS = {b"A": "Up", b"B": "Down", b"C": "Right", b"D": "Left", b"H": "Home", b"F": "End",
           b"P": "F1", b"Q": "F2", b"R": "F3", b"S": "F4"}
_TILDES = {2: "Insert", 3: "Delete", 5: "Prior", 6: "Next", 13: "F3", 15: "F5", 17: "F6", 18: "F7",
           19: "F8", 20: "F9", 21: "F10", 23: "F11", 24: "F12"}
_CHARS = {" ": "space", "-": "minus", "=": "equal", "[": "bracketleft", "]": "bracketright",
          "\\": "backslash", ";": "semicolon", "'": "apostrophe", "`": "grave", ",": "comma",
          ".": "period", "/": "slash", "!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6",
          "&": "7", "*": "8", "(": "9", ")": "0", "_": "minus", "+": "equal", "{": "bracketleft",
          "}": "bracketright", "|": "backslash", ":": "semicolon", '"': "apostrophe", "~": "grave",
          "<": "comma", ">": "period", "?": "slash"}
_LEGACY = {b"\r": "Return", b"\n": "Return", b"\t": "Tab", b"\x7f": "BackSpace", b"\x08": "BackSpace"}


def _char_key(char: str) -> str | None:
    if char.isascii() and char.isalnum():
        return char.lower()
    return _CHARS.get(char)


class TerminalKeys:
    """Terminal input bytes to ("press" | "repeat" | "release" | "tap", key name)
    events, from the kitty keyboard protocol (flags 1|2|8: every key as an escape
    code with its event type) or from plain bytes and SS3 sequences, which carry
    no release and so are taps, even from a kitty terminal that sends some keys
    plainly. Ctrl+]
    gives (CTRL_RIGHT_BRACKET, ""). A lone Escape is only told from the start of
    a sequence once `escape_wait` seconds pass with nothing after it."""

    def __init__(self, escape_wait: float = 0.05):
        self.buffer, self.escape_wait, self.since = bytearray(), escape_wait, 0.0

    def feed(self, data: bytes, now: float) -> list[tuple[str, str]]:
        if data:
            self.buffer += data
            self.since = now
        events = []
        while self.buffer:
            used, event = self._one(now)
            if used == 0:
                break
            del self.buffer[:used]
            if event is not None:
                events.append(event)
        return events

    def _one(self, now: float):
        buf = bytes(self.buffer)
        if buf[0] == 0x1D:
            return 1, (CTRL_RIGHT_BRACKET, "")
        if buf[0] != 0x1B:
            key = _LEGACY.get(buf[:1]) or (_char_key(chr(buf[0])) if buf[0] < 0x80 else None)
            return 1, (("tap", key) if key else None)
        if len(buf) == 1:
            return (1, ("tap", "Escape")) if now - self.since >= self.escape_wait else (0, None)
        if buf[1:2] == b"O":
            if len(buf) < 3:
                return 0, None
            key = _FINALS.get(buf[2:3])
            return 3, (("tap", key) if key else None)
        if buf[1:2] != b"[":
            return 1, ("tap", "Escape")
        end = next((i for i in range(2, len(buf)) if 0x40 <= buf[i] <= 0x7E), None)
        if end is None:
            return 0, None
        return end + 1, self._csi(buf[2:end].decode("ascii", "replace"), buf[end:end + 1])

    @staticmethod
    def _csi(params: str, final: bytes):
        if params.startswith("?") or params.startswith(">"):
            return None
        fields = [f.split(":") for f in params.split(";")] if params else [[""]]
        code = int(fields[0][0]) if fields[0][0].isdigit() else 1
        mods = int(fields[1][0]) - 1 if len(fields) > 1 and fields[1][0].isdigit() else 0
        kind = {"2": "repeat", "3": "release"}.get(fields[1][1] if len(fields) > 1 and len(fields[1]) > 1 else "", "press")
        if final == b"u":
            if code == 93 and mods & 4:
                return (CTRL_RIGHT_BRACKET, "") if kind == "press" else None
            key = _KITTY_CODES.get(code) or (_char_key(chr(code)) if code < 0x80 else None)
        elif final == b"~":
            key = _TILDES.get(code)
        else:
            key = _FINALS.get(final)
        return (kind, key) if key else None
