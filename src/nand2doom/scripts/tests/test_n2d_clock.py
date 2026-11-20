"""Host-compiled production arithmetic and public sleep callback boundaries."""
import ctypes
from pathlib import Path
import unittest

from src.nand2doom.dv.software.clock_build import compile_probe
from src.nand2doom.dv.software.clock_vectors import old_clock_fails

ROOT = Path(__file__).resolve().parents[4]


class ClockTests(unittest.TestCase):
    def test_fixed_origin_alias_is_reproduced(self):
        old_clock_fails()

    def test_compiled_production_clock(self):
        artifact, expected = compile_probe(ROOT / "workdir/unit-tests/n2d_clock", host=True)
        library = ctypes.CDLL(str(artifact))
        library.c_entry()
        observed = list((ctypes.c_uint32 * len(expected)).in_dll(library, "probe_results"))
        self.assertEqual(expected, observed)
