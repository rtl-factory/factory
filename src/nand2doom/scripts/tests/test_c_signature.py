"""C sub-build diagnostics and provenance, without executing a compiler."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from src.nand2doom.dv.core import c_signature as helper


class SignatureBuildTests(unittest.TestCase):
    def exercise(self, *, failure=None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            compiler, objcopy = root/'gcc', root/'objcopy'
            compiler.write_bytes(b'compiler'); objcopy.write_bytes(b'copy')
            def run(argv, **kwargs):
                if '--version' in argv:
                    return subprocess.CompletedProcess(argv, 0, 'GCC test\n', '')
                name = 'objcopy' if argv[0] == str(objcopy) else 'compile'
                self.assertEqual(kwargs['timeout'], 20)
                if name == failure:
                    return subprocess.CompletedProcess(argv, 0, '', 'warning: rejected')
                if name == 'objcopy':
                    Path(argv[-1]).write_bytes(b'\x13\x00\x00\x00')
                return subprocess.CompletedProcess(argv, 0, '', '')
            with patch.object(helper.Path, 'cwd', return_value=root), \
                    patch.object(helper.shutil, 'which', side_effect=[str(compiler), str(objcopy)]), \
                    patch.object(helper.subprocess, 'run', side_effect=run) as calls:
                if failure:
                    with self.assertRaisesRegex(RuntimeError, 'diagnostics'):
                        helper.compile_signature_program()
                    self.assertEqual(calls.call_count, 1 if failure == 'compile' else 2)
                    self.assertIn('warning:', json.loads((root/'c_signature'/(failure+'.json')).read_text())['stderr'])
                else:
                    words, _ = helper.compile_signature_program()
                    self.assertEqual(words, (0x13,))
                    tools = json.loads((root/'c_signature/tools.json').read_text())
                    self.assertEqual(tools['objcopy']['sha256'], hashlib.sha256(b'copy').hexdigest())
                    self.assertEqual(tools['gcc']['path'], str(compiler))

    def test_success_records_tools_and_commands(self):
        self.exercise()

    def test_compiler_warning_stops_before_objcopy(self):
        self.exercise(failure='compile')

    def test_objcopy_warning_fails_before_acceptance(self):
        self.exercise(failure='objcopy')


if __name__ == '__main__':
    unittest.main()
