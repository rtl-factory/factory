"""Host keys to PS/2 Set-2 make and break codes for the board console.

A window reports key presses and releases by keysym (the Tk name of the key).
Every board keyboard input decodes Set-2, so the console forwards the physical
key and leaves its meaning to the program: Doom reads Ctrl as Fire, nand2mario
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
# Why: with Shift held Tk reports the shifted keysym; the key itself is the unshifted one.
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
    held key. X11 auto-repeat reports a held key as release then press at
    once, so a release is only sent once `settle` seconds pass without a new
    press of that key; `flush(now)` sends those."""

    def __init__(self, settle: float = 0.03):
        self.settle = settle
        self.held: set[str] = set()
        self.pending: dict[str, float] = {}

    def press(self, keysym: str, now: float) -> bytes:
        key = key_name(keysym)
        if key is None:
            return b""
        if self.pending.pop(key, None) is not None or key in self.held:
            return b""
        self.held.add(key)
        return make(key)

    def release(self, keysym: str, now: float) -> bytes:
        key = key_name(keysym)
        if key in self.held:
            self.pending[key] = now
        return b""

    def flush(self, now: float) -> bytes:
        out = bytearray()
        for key, since in list(self.pending.items()):
            if now - since >= self.settle:
                del self.pending[key]
                self.held.discard(key)
                out += brk(key)
        return bytes(out)

    def release_all(self) -> bytes:
        """Break every held key, as when the window loses focus."""
        out = b"".join(brk(key) for key in sorted(self.held))
        self.held.clear()
        self.pending.clear()
        return out
