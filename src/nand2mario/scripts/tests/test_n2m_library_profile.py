"""Maintained DE2 library selection; every refusal precedes a mocked programmer."""
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.nand2mario.scripts import n2m_board as board, n2m_quartus as quartus
from src.nand2mario.scripts.tests.test_n2m_board import ROOT, Args, setUpModule  # noqa: F401
from src.nand2mario.scripts.tests.sof_identity_fixture import companion_bytes, passing_evidence


def removed_ram_warnings():
    """Independent spelling of the observed Quartus save-RAM diagnostics."""
    headers = ['Warning (14284): Synthesized away the following node(s):',
               'Warning (14285): Synthesized away the following RAM node(s):']
    return headers + [
        'Warning (14320): Synthesized away node "n2m_system:u_system|n2m_library:g_library.u_library|'
        'n2m_cartridge_backing:u_active|n2m_dual_port_ram:u_ram|altsyncram:memory_rtl_0|'
        f'altsyncram_4g71:auto_generated|ram_block1a{i}" File: /mock/db/altsyncram_4g71.tdf Line: {36 + 21*i}'
        for i in range(8)]


def invalid_ram_warning_sets():
    good = removed_ram_warnings()
    yield 'missing node', good[:-1]
    yield 'extra node', good + [good[-1].replace('ram_block1a7', 'ram_block1a8')]
    yield 'duplicate node', good + [good[-1]]
    yield 'duplicate hides missing node', good[:-1] + [good[-2]]
    yield 'missing header', good[1:]
    yield 'duplicate header', good + [good[0]]
    yield 'orphan headers', good[:2]
    yield 'orphan detail', good[2:]
    yield 'missing one summary', good[:1] + good[2:]
    for label, old, new in [
            ('active ROM', ':u_ram|', ':u_rom|'), ('other RAM', ':u_active|', ':u_cartridge|'),
            ('wrong file', '/db/altsyncram_4g71.tdf', '/db/altsyncram_other.tdf')]:
        yield label, [w.replace(old, new) for w in good]


class LibraryProfileTests(unittest.TestCase):
    def setUp(self):
        self.ordinary = quartus.selected_config(ROOT, 'de2_115')
        self.library = quartus.selected_config(ROOT, 'de2_115', library=True)

    def test_default_and_library_profiles_share_board_facts(self):
        self.assertEqual(self.ordinary, quartus.load_config(ROOT, quartus.BOARD_CONFIGS['de2_115']))
        self.assertEqual(self.library['project'], 'n2m_de2_115_library')
        self.assertEqual(self.library['parameters'], {'CART_LIBRARY': 1})
        self.assertNotIn('CART_LIBRARY', self.ordinary['parameters'])
        changed = {'project', 'parameters', 'ram_requirements', 'reviewed_warnings'}
        for key in self.ordinary.keys() - changed:
            self.assertEqual(self.ordinary[key], self.library[key], key)
        self.assertEqual(self.library['ram_requirements'][:-1], self.ordinary['ram_requirements'])
        active = self.library['ram_requirements'][-1]
        self.assertEqual((active['count'], active['depth'], active['width'], active['m9ks_each'], active['mode']),
                         (1, 65536, 8, 64, 'Simple Dual Port'))
        source = (ROOT / 'src/nand2mario/fpga/de2_115/n2m_de2_115.sv').read_text()
        self.assertIn('parameter int unsigned CART_LIBRARY = 0', source)
        self.assertIn('.CART_LIBRARY      (CART_LIBRARY)', source)
        self.assertIn('.CART_SDRAM        (1)', source)
        targets = json.loads((ROOT / 'src/nand2mario/cfg/targets.json').read_text())['targets']
        self.assertEqual(targets['n2m_system_menu_sdram']['parameters']['CART_LIBRARY'], 1)
        simulation = [s for s in targets['n2m_de2_115']['sources'] if '/dv/' not in s]
        # Both lists bind the common n2a_jtag_virtual; only simulation adds
        # the dv/ stand-in of its vendor primitive.
        synthesis = [s.replace('_intel.sv', '_portable.sv') for s in self.library['sources']]
        self.assertEqual(simulation, synthesis)
        with tempfile.TemporaryDirectory(dir=ROOT / 'workdir') as directory:
            output = Path(directory)
            quartus.stage(ROOT, output, self.library)
            qsf = (output / 'n2m_de2_115_library.qsf').read_text()
            audit = (output / 'audit.tcl').read_text()
            self.assertIn('set_parameter -name CART_LIBRARY 1', qsf)
            self.assertIn('TOP_LEVEL_ENTITY n2m_de2_115', qsf)
            self.assertIn('vga_dac_clock', audit)  # Different project name must not skip board gates.

    def test_plans_are_inert_and_reject_unsupported_or_arbitrary_profiles(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), \
                patch.object(quartus, 'run', side_effect=AssertionError('plan invoked Quartus')), \
                patch.object(quartus, 'stage', side_effect=AssertionError('plan staged files')), \
                patch.object(Path, 'mkdir', side_effect=AssertionError('plan created directory')), \
                patch.object(Path, 'write_text', side_effect=AssertionError('plan wrote text')), \
                patch.object(Path, 'write_bytes', side_effect=AssertionError('plan wrote bytes')):
            self.assertEqual(quartus.main(['--board', 'de2_115', '--library', '--plan']), 0)
            self.assertEqual(board.main(['compile', '--board', 'de2_115', '--library', '--plan']), 0)
            self.assertEqual(board.main(['program', '--board', 'de2_115', '--library']), 0)
            self.assertNotEqual(quartus.main(['--board', 'de10_lite', '--library', '--plan']), 0)
            self.assertNotEqual(board.main(['program', '--board', 'de10_lite', '--library']), 0)
        with tempfile.TemporaryDirectory(dir=ROOT / 'workdir') as directory:
            config = Path(directory) / 'custom.json'
            changed = copy.deepcopy(self.library)
            changed['parameters']['CART_LIBRARY'] = 0
            config.write_text(json.dumps(changed))
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertNotEqual(quartus.main(['--board', 'de2_115', '--library', '--config', str(config), '--plan']), 0)
                config.write_text(json.dumps(self.library))
                self.assertNotEqual(quartus.main(['--board', 'de2_115', '--config', str(config), '--plan']), 0)

    def test_profile_cannot_override_unrelated_board_facts(self):
        source = json.loads((ROOT / quartus.LIBRARY_PROFILE).read_text())
        for mutate in (lambda p: p.update(pins={}), lambda p: p.update(project='n2m_de2_115'),
                       lambda p: p.update(parameters={'CART_LIBRARY': 0}),
                       lambda p: p.update(parameters={'CART_LIBRARY': True}),
                       lambda p: p.update(ram_requirements=[])):
            with tempfile.TemporaryDirectory(dir=ROOT / 'workdir') as directory:
                profile = Path(directory) / 'profile.json'
                data = copy.deepcopy(source)
                mutate(data)
                profile.write_text(json.dumps(data))
                with patch.object(quartus, 'LIBRARY_PROFILE', str(profile)), self.assertRaises(ValueError):
                    quartus.selected_config(ROOT, 'de2_115', library=True)

    def test_maintained_board_requires_its_canonical_top(self):
        for library in (False, True):
            config = copy.deepcopy(self.ordinary)
            config['top'] = 'n2m_de10_lite'
            with patch.object(quartus, 'load_config', return_value=config), self.assertRaises(ValueError):
                quartus.selected_config(ROOT, 'de2_115', library=library)

    def test_library_removed_ram_warnings_require_exact_complete_set(self):
        good = removed_ram_warnings()
        quartus.validate_library_ram_warnings(good, self.library)
        quartus.validate_library_ram_warnings([], self.library)
        evidence = passing_evidence(self.library)
        evidence['reviewed_warnings'] = sorted(good)
        quartus.validate_fit_evidence(evidence, self.library)
        with self.assertRaises(ValueError):
            quartus.validate_library_ram_warnings(good, self.ordinary)
        for label, warnings in invalid_ram_warning_sets():
            with self.subTest(label=label), self.assertRaises(ValueError):
                quartus.validate_library_ram_warnings(warnings, self.library)

    def test_receipt_modes_tampering_and_ram_dac_gates_precede_programmer(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'workdir') as directory:
            sof = Path(directory) / 'image.sof'
            sof.write_bytes(b'mocked image, never FPGA input')
            args = Args(board='de2_115', library=True, sof=str(sof), yes=True, cable='mock')
            calls = []

            def runner(command, **kwargs):
                calls.append(command)
                return type('Completed', (), {'returncode': 0})()

            def package(config, mutate_inputs=None, mutate_evidence=None):
                inputs, evidence = map(json.loads, companion_bytes(config))
                if mutate_inputs:
                    mutate_inputs(inputs)
                if mutate_evidence:
                    mutate_evidence(evidence)
                identity = dict(schema='n2m-sof/1', project=config['project'], device=config['device'],
                                source_sha='a' * 40, quartus_version='mock',
                                sha256=hashlib.sha256(sof.read_bytes()).hexdigest())
                for name, data in [('inputs', inputs), ('evidence', evidence)]:
                    raw = json.dumps(data).encode()
                    sof.with_suffix(f'.{name}.json').write_bytes(raw)
                    identity[name + '_sha256'] = hashlib.sha256(raw).hexdigest()
                sof.with_suffix('.json').write_text(json.dumps(identity))

            def refuse():
                before = len(calls)
                with self.assertRaises(ValueError):
                    board.run_program(args, runner=runner, which=lambda name: name)
                self.assertEqual(len(calls), before)

            package(self.library)
            args.library = False
            refuse()  # Missing explicit library selection.
            args.library = True
            package(self.ordinary)
            refuse()  # Ordinary SOF under library selection.
            for mutate in (lambda i: i['config']['parameters'].update(CART_LIBRARY=0),
                           lambda i: i['config'].update(project='n2m_de2_115'),
                           lambda i: i['config']['pins'].update(CLOCK_50='A1')):
                package(self.library, mutate_inputs=mutate)
                refuse()  # Rehashing a tampered companion cannot authorize it.
            for mutate in (lambda e: e['resources']['ram_summary'][-1].update({'Port A Depth': '32768'}),
                           lambda e: e['resources']['ram_summary'].pop(),
                           lambda e: e.update(dac_timing=[]),
                           lambda e: e['clock_relationships'].pop('vga_dac_clock')):
                package(self.library, mutate_evidence=mutate)
                refuse()
            for label, warnings in invalid_ram_warning_sets():
                with self.subTest(label=label):
                    package(self.library, mutate_evidence=lambda e: e.update(reviewed_warnings=sorted(warnings)))
                    refuse()
            package(self.library, mutate_evidence=lambda e: e.update(reviewed_warnings=sorted(removed_ram_warnings())))
            self.assertEqual(board.run_program(args, runner=runner, which=lambda name: name)[0], 0)
            self.assertEqual(len(calls), 1)
            self.assertIn(f'p;{sof}@1', calls[0])


if __name__ == '__main__':
    unittest.main()
