import ctypes
from pathlib import Path
import unittest
from src.nand2doom.dv.software.capture_build import compile_probe

ROOT = Path(__file__).resolve().parents[4]

class CaptureHelperTests(unittest.TestCase):
    def test_production_state_machine(self):
        artifact, expected = compile_probe(ROOT/'workdir/n2d-capture-helper/helper-host', host=True)
        library=ctypes.CDLL(str(artifact))
        library.c_entry()
        got=list((ctypes.c_uint32*len(expected)).in_dll(library,'capture_results'))
        self.assertEqual(expected,got)

class SceneAdaptationTests(unittest.TestCase):
    def test_exact_hooks_idempotence_and_rejection(self):
        from src.nand2doom.scripts.n2d_doom import _adapt_scene
        original=('#include "doomstat.h"\n'
                  '\tI_FinishUpdate ();              // page flip or blit buffer\n\treturn;\n'
                  '    } while (!done);\n}')
        adapted=_adapt_scene(original)
        self.assertEqual(adapted,_adapt_scene(adapted))
        self.assertEqual(adapted.count('N2D_RecordSceneReady(gamestate == GS_LEVEL);'),2)
        for invalid in (original.replace('I_FinishUpdate','OtherUpdate'), original+original,
                        adapted+adapted):
            with self.assertRaises(RuntimeError):_adapt_scene(invalid)
        previous=adapted.replace('int gameplay','void').replace('gamestate == GS_LEVEL','')
        self.assertEqual(adapted,_adapt_scene(previous))

if __name__ == '__main__':
    unittest.main()
