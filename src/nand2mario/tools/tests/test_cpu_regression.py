"""Preparation contract only; no real software builder or simulator."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from src.nand2mario.tools import cpu_regression as adapter


class PreparationTests(unittest.TestCase):
    def test_fresh_image_precedes_unchanged_cpu_test(self):
        with tempfile.TemporaryDirectory() as name, patch.object(adapter, 'ROOT', Path(name)):
            output, build = Path(name)/'workdir/artifacts', Path(name)/'workdir/build'
            def run(argv, **kwargs):
                if 'src/nand2mario/tools/cpu_sw.py' in argv:
                    image = Path(argv[-1])/'program.gb'
                    image.parent.mkdir()
                    image.write_bytes(bytes(32768))
                return subprocess.CompletedProcess(argv, 0)
            with patch.object(adapter.subprocess, 'run', side_effect=run) as command:
                self.assertEqual(adapter.execute(output, build), 0)
                self.assertEqual(command.call_count, 2)
                self.assertEqual(command.call_args.args[0][-2:], ['--direct', 'n2m_cpu'])
                self.assertEqual(command.call_args.kwargs['env']['N2M_CPU_IMAGE'], str(output/'software/program.gb'))
                with self.assertRaises(ValueError):
                    adapter.execute(output, build)

    def test_system_program_feeds_system_target(self):
        with tempfile.TemporaryDirectory() as name, patch.object(adapter, 'ROOT', Path(name)):
            output, build = Path(name)/'workdir/artifacts', Path(name)/'workdir/build'
            def run(argv, **kwargs):
                if 'src/nand2mario/tools/cpu_sw.py' in argv:
                    self.assertIn('src/nand2mario/sw/system/system_smoke.asm', argv)
                    self.assertIn('system_smoke::start', argv)
                    image = Path(argv[-1])/'program.gb'
                    image.parent.mkdir()
                    image.write_bytes(bytes(32768))
                return subprocess.CompletedProcess(argv, 0)
            with patch.object(adapter.subprocess, 'run', side_effect=run) as command:
                self.assertEqual(adapter.execute(output, build, 'system'), 0)
                self.assertEqual(command.call_args.args[0][-2:], ['--direct', 'n2m_system'])
                self.assertEqual(command.call_args.kwargs['env']['N2M_SYSTEM_IMAGE'], str(output/'software/program.gb'))
                self.assertNotIn('N2M_CPU_IMAGE', command.call_args.kwargs['env'])
        from src.nand2mario.tools import system_regression
        with patch.object(adapter, 'main', return_value=5) as main:
            self.assertEqual(system_regression.main(['--output', 'a', '--workdir', 'b']), 5)
            self.assertEqual(main.call_args.kwargs, {'program': 'system'})

    def test_background_program_feeds_background_target(self):
        with tempfile.TemporaryDirectory() as name, patch.object(adapter, 'ROOT', Path(name)):
            output, build = Path(name)/'workdir/artifacts', Path(name)/'workdir/build'
            def run(argv, **kwargs):
                if 'src/nand2mario/tools/cpu_sw.py' in argv:
                    self.assertIn('src/nand2mario/sw/system/background.asm', argv)
                    self.assertIn('background::start', argv)
                    image = Path(argv[-1])/'program.gb'
                    image.parent.mkdir()
                    image.write_bytes(bytes(32768))
                return subprocess.CompletedProcess(argv, 0)
            with patch.object(adapter.subprocess, 'run', side_effect=run) as command:
                self.assertEqual(adapter.execute(output, build, 'system_background'), 0)
                self.assertEqual(command.call_args.args[0][-2:], ['--direct', 'n2m_system_background'])
                self.assertEqual(command.call_args.kwargs['env']['N2M_SYSTEM_IMAGE'], str(output/'software/program.gb'))
        # The system entry point selects the recipe through --program.
        from src.nand2mario.tools import system_regression
        with patch.object(adapter, 'execute', return_value=0) as execute:
            self.assertEqual(system_regression.main(['--output', 'workdir/a', '--workdir', 'workdir/b',
                                                     '--program', 'system_background']), 0)
            self.assertEqual(execute.call_args.args[2], 'system_background')

    def test_mbc1_program_is_expanded_before_its_target(self):
        with tempfile.TemporaryDirectory() as name, patch.object(adapter, 'ROOT', Path(name)):
            output, build = Path(name)/'workdir/artifacts', Path(name)/'workdir/build'
            def run(argv, **kwargs):
                if 'src/nand2mario/tools/cpu_sw.py' in argv:
                    self.assertIn('src/nand2mario/sw/system/mbc1.asm', argv)
                    self.assertIn('mbc1::start', argv)
                    image = Path(argv[-1])/'program.gb'
                    image.parent.mkdir()
                    image.write_bytes(bytes(32768))
                elif 'src/nand2mario/tools/mbc1_image.py' in argv:
                    self.assertEqual(argv[argv.index('--input') + 1], str(output/'software/program.gb'))
                    self.assertEqual(argv[argv.index('--banks') + 1], '64')
                    Path(argv[argv.index('--output') + 1]).write_bytes(bytes(1048576))
                return subprocess.CompletedProcess(argv, 0)
            with patch.object(adapter.subprocess, 'run', side_effect=run) as command:
                self.assertEqual(adapter.execute(output, build, 'system_mbc1'), 0)
                self.assertEqual(command.call_count, 3)
                self.assertEqual(command.call_args.args[0][-2:], ['--direct', 'n2m_system_mbc1'])
                self.assertEqual(command.call_args.kwargs['env']['N2M_SYSTEM_IMAGE'], str(output/'software/cartridge.gb'))
        # A short expansion stops before the simulator.
        with tempfile.TemporaryDirectory() as name, patch.object(adapter, 'ROOT', Path(name)):
            output, build = Path(name)/'workdir/artifacts', Path(name)/'workdir/build'
            def run_short(argv, **kwargs):
                if 'src/nand2mario/tools/cpu_sw.py' in argv:
                    image = Path(argv[-1])/'program.gb'
                    image.parent.mkdir()
                    image.write_bytes(bytes(32768))
                elif 'src/nand2mario/tools/mbc1_image.py' in argv:
                    Path(argv[argv.index('--output') + 1]).write_bytes(bytes(100))
                return subprocess.CompletedProcess(argv, 0)
            with patch.object(adapter.subprocess, 'run', side_effect=run_short) as command:
                with self.assertRaisesRegex(ValueError, 'cartridge image'):
                    adapter.execute(output, build, 'system_mbc1')
                self.assertEqual(command.call_count, 2)

    def test_vga_program_feeds_vga_target(self):
        with tempfile.TemporaryDirectory() as name, patch.object(adapter, 'ROOT', Path(name)):
            output, build = Path(name)/'workdir/artifacts', Path(name)/'workdir/build'
            def run(argv, **kwargs):
                if 'src/nand2mario/tools/cpu_sw.py' in argv:
                    self.assertIn('src/nand2mario/sw/system/background.asm', argv)
                    self.assertIn('background::start', argv)
                    image = Path(argv[-1])/'program.gb'
                    image.parent.mkdir()
                    image.write_bytes(bytes(32768))
                return subprocess.CompletedProcess(argv, 0)
            with patch.object(adapter.subprocess, 'run', side_effect=run) as command:
                self.assertEqual(adapter.execute(output, build, 'system_vga'), 0)
                self.assertEqual(command.call_args.args[0][-2:], ['--direct', 'n2m_system_vga'])
                self.assertEqual(command.call_args.kwargs['env']['N2M_SYSTEM_IMAGE'], str(output/'software/program.gb'))

    def test_prepare_failure_stops_simulator(self):
        with tempfile.TemporaryDirectory() as name, patch.object(adapter, 'ROOT', Path(name)), \
                patch.object(adapter.subprocess, 'run', return_value=subprocess.CompletedProcess([], 7)) as run:
            self.assertEqual(adapter.execute(Path(name)/'workdir/a', Path(name)/'workdir/b'), 7)
            self.assertEqual(run.call_count, 1)

    def test_missing_image_stops_simulator(self):
        with tempfile.TemporaryDirectory() as name, patch.object(adapter, 'ROOT', Path(name)), \
                patch.object(adapter.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as run:
            with self.assertRaisesRegex(ValueError, 'complete smoke image'):
                adapter.execute(Path(name)/'workdir/a', Path(name)/'workdir/b')
            self.assertEqual(run.call_count, 1)

    def test_confinement_and_overlap(self):
        for path in ('/tmp/unowned', adapter.ROOT/'workdir', adapter.ROOT/'workdir/../escape'):
            with self.assertRaises(ValueError):
                adapter.fresh_path(path)
        with tempfile.TemporaryDirectory() as name, patch.object(adapter, 'ROOT', Path(name)):
            base = Path(name)/'workdir'
            with self.assertRaisesRegex(ValueError, 'separate'):
                adapter.execute(base/'a', base/'a/b')
            base.symlink_to('/tmp', target_is_directory=True)
            with self.assertRaises(ValueError):
                adapter.fresh_path(base/'a')


    def test_builder_bound_follows_the_registry_timeout(self):
        import json
        registry = json.loads(adapter.REGISTRY.read_text())['targets']
        for program, recipe in adapter.PROGRAMS.items():
            with self.subTest(program=program):
                self.assertEqual(adapter.builder_timeout(recipe['target']),
                                 registry[recipe['target']]['timeout_seconds'] + 960)
        self.assertEqual(adapter.builder_timeout('n2m_system_sdram'), 1860)

if __name__ == '__main__':
    unittest.main()
