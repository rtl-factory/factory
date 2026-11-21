"""Host-compiled production Doom keyboard map against an independent oracle.

The firmware table (src/nand2doom/sw/doom/keymap.h) is compiled with the host C
compiler and the pinned DoomGeneric doomkeys.h from the vendored engine archive,
then probed for every ten-bit Set-2 event. Expected Doom key values come from
that same header's definitions and, for printable keys, from the common JTAG
keyboard host's independent Set-2 table.
"""
import ctypes
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import unittest

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from src.nand2doom.scripts.host import n2d_control as control  # noqa: E402
from src.nand2doom.scripts.sw_build import n2d_doom as doom  # noqa: E402
from src.common.scripts.jtag import n2a_jtag_keyboard as jtag  # noqa: E402

OUTPUT = ROOT / "workdir/unit-tests/n2d_doom_keymap"
ARCHIVE = doom.VENDORED_DOWNLOADS / f"doomgeneric-{doom.ENGINE_REVISION}.tar.gz"
E0 = 0x200


def doomkeys():
    """doomkeys.h from the pinned archive, and its integer KEY_* definitions."""
    if not ARCHIVE.exists():
        doom._download(doom.ENGINE_URL, ARCHIVE, doom.ENGINE_SHA256)
    if hashlib.sha256(ARCHIVE.read_bytes()).hexdigest() != doom.ENGINE_SHA256:
        raise AssertionError("vendored engine archive does not match the pinned SHA-256")
    with tarfile.open(ARCHIVE) as archive:
        text = archive.extractfile(
            f"doomgeneric-{doom.ENGINE_REVISION}/doomgeneric/doomkeys.h").read().decode()
    values = {}
    for name, expression in re.findall(r"#define\s+(KEY_\w+)\s+(.+?)\s*$", text, re.M):
        expression = re.sub(r"KEY_\w+", lambda m: str(values[m[0]]), expression)
        if re.fullmatch(r"[0-9a-fx()+ ]+", expression):
            values[name] = eval(expression, {"__builtins__": {}})  # Why: the fullmatch allows only digits, hex and '+', so eval cannot run code
    return text, values


def expected_map(keys):
    """{ten-bit make event: Doom key} for every mapped event; all others are zero."""
    plain = {0x75: "UPARROW", 0x72: "DOWNARROW", 0x6B: "LEFTARROW", 0x74: "RIGHTARROW",
             0x14: "FIRE", 0x29: "USE", 0x12: "RSHIFT", 0x59: "RSHIFT", 0x11: "LALT",
             0x5A: "ENTER", 0x76: "ESCAPE", 0x0D: "TAB", 0x66: "BACKSPACE",
             0x4E: "MINUS", 0x55: "EQUALS"}
    extended = {0x75: "UPARROW", 0x72: "DOWNARROW", 0x6B: "LEFTARROW", 0x74: "RIGHTARROW",
                0x14: "FIRE", 0x12: "RSHIFT", 0x11: "LALT", 0x5A: "ENTER"}
    result = {code: keys["KEY_" + name] for code, name in plain.items()}
    result.update({E0 | code: keys["KEY_" + name] for code, name in extended.items()})
    for char in "abcdefghijklmnopqrstuvwxyz0123456789":
        result[jtag.SET2[char]] = ord(char)
    return result


def compile_probe():
    compiler = shutil.which(os.environ.get("CC", "cc"))
    if compiler is None:
        raise unittest.SkipTest("no host C compiler")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    text, _ = doomkeys()
    (OUTPUT / "doomkeys.h").write_text(text)
    probe = OUTPUT / "probe.c"
    probe.write_text('#include "keymap.h"\n'
                     "unsigned char probe(uint32_t event) { return n2d_doom_key(event); }\n")
    library = OUTPUT / "keymap_probe.so"
    subprocess.run([compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC",
                    "-I", str(OUTPUT), "-I", str(ROOT / "src/nand2doom/sw/doom"),
                    str(probe), "-o", str(library)], check=True, timeout=60)
    function = ctypes.CDLL(str(library)).probe
    function.argtypes, function.restype = [ctypes.c_uint32], ctypes.c_ubyte
    return function


class KeymapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.probe = staticmethod(compile_probe())
        _, cls.keys = doomkeys()
        cls.expected = expected_map(cls.keys)

    def test_every_ten_bit_event(self):
        for event in range(1024):
            make = event & ~0x100
            with self.subTest(event=hex(event)):
                self.assertEqual(self.probe(event), self.expected.get(make, 0))

    def test_index_ignores_bits_above_the_event(self):
        self.assertEqual(self.probe(0xFFFFFE75), self.probe(0x375))
        self.assertEqual(self.probe(0x435), ord("y"))

    def test_doom_bindings_reach_their_controls(self):
        """The engine's default bindings for the keys the issue names."""
        self.assertEqual(self.probe(jtag.SET2["y"]), ord("y"))       # Ref: key_menu_confirm
        self.assertEqual(self.probe(jtag.SET2["n"]), ord("n"))       # Ref: key_menu_abort
        self.assertEqual(self.probe(0x0D), self.keys["KEY_TAB"])      # Ref: key_map_toggle
        for weapon in "1234567":
            self.assertEqual(self.probe(jtag.SET2[weapon]), ord(weapon))

    def test_every_control_action_is_mapped(self):
        for action, event in control.ACTIONS.items():
            with self.subTest(action=action):
                self.assertNotEqual(self.probe(event), 0)
                self.assertEqual(self.probe(event), self.probe(event | 0x100))

    def test_multimedia_keys_are_not_letters(self):
        for code in (0x15, 0x23, 0x21, 0x32, 0x34, 0x3B, 0x2B, 0x4D):  # Ref: PS/2 set 2 E0-prefixed media keys
            self.assertEqual(self.probe(E0 | code), 0)
            self.assertNotEqual(self.probe(code), 0)


if __name__ == "__main__":
    unittest.main()
