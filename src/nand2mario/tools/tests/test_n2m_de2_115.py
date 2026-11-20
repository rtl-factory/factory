"""DE2-115 electrical/source contract and target/image refusal checks."""
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import re
import tempfile
import unittest

from src.nand2mario.tools import n2m_board as board, n2m_quartus as quartus
from src.nand2mario.tools.tests.test_n2m_board import ROOT, top_ports, Args, CLOCKS, SYSTEM_CLOCK, sdram_report
from src.nand2mario.tools.tests.test_n2m_board import setUpModule  # noqa: F401 (creates workdir/)


from src.nand2mario.tools.tests.sof_identity_fixture import companion_bytes, passing_evidence


class De2Tests(unittest.TestCase):
    def setUp(self):
        self.config = quartus.load_config(ROOT, quartus.BOARD_CONFIGS['de2_115'])

    def test_every_top_port_has_a_pin(self):
        self.assertEqual(top_ports(ROOT / 'src/nand2mario/fpga/de2_115/n2m_de2_115.sv'), set(self.config['pins']))

    @unittest.skipUnless((ROOT / 'src/nand2linux/fpga/n2l_de2_115.qsf').exists()
                         and (ROOT / 'src/nand2doom/cfg/n2d_quartus.json').exists(),
                         'nand2linux and nand2doom are not ported')
    def test_pins_and_standards_match_reviewed_de2(self):
        qsf = (ROOT / 'src/nand2linux/fpga/n2l_de2_115.qsf').read_text()
        pins = {port: pin for pin, port in re.findall(r'set_location_assignment PIN_(\w+) -to (\S+)', qsf)}
        doom = json.loads((ROOT / 'src/nand2doom/cfg/n2d_quartus.json').read_text())['boards']['de2_115']
        for port, pin in self.config['pins'].items():
            peer = {'UART_RXD': 'UART_RX', 'UART_TXD': 'UART_TX'}.get(port, port)
            if not port.startswith('SW['):
                self.assertEqual(pin, pins[peer], port)
                standards = doom['io_standards']
                self.assertEqual(quartus.io_standard(self.config, port),
                                 standards.get(peer, standards.get(peer.split('[')[0], '3.3-V LVTTL')), port)
        # Terasic 2013 manual table 4-1, JP7 supply = 2.5 V for this profile.
        for index, pin in enumerate(('AB28', 'AC28', 'AC27', 'AD27', 'AB27', 'AC26', 'AD26', 'AB26')):
            self.assertEqual(self.config['pins'][f'SW[{index}]'], pin)
            self.assertEqual(quartus.io_standard(self.config, f'SW[{index}]'), '2.5 V')
        self.assertNotIn('DRAM_DQ[16]', self.config['pins'])
        settings = (ROOT / self.config['settings']).read_text()
        self.assertIn('RESERVE_ALL_UNUSED_PINS "AS INPUT TRI-STATED"', settings)
        for bank in range(3):
            self.assertIn(f'n2a_sdp_ram:g_banks[{bank}].u_ram', settings)
        self.assertEqual(3, settings.count('INTEL_DEVICE_FAMILY "Cyclone IV E"'))

    def test_simulation_and_synthesis_closures(self):
        targets = json.loads((ROOT / 'src/nand2mario/cfg/targets.json').read_text())['targets']
        simulation = [s for s in targets['n2m_de2_115']['sources'] if '/dv/' not in s]
        # Both lists bind the common n2a_jtag_virtual; only simulation adds
        # the dv/ stand-in of its vendor primitive.
        synthesis = [s.replace('_intel.sv', '_portable.sv') for s in self.config['sources']]
        self.assertEqual(simulation, synthesis)
        self.assertEqual('EP4CE115F29C7', self.config['device'])
        self.assertEqual('Cyclone IV E', self.config['family'])

    def test_plans_and_target_mismatch(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(0, board.main(['compile', '--board', 'de2_115', '--plan']))
            self.assertEqual(0, board.main(['program', '--board', 'de2_115']))
            self.assertNotEqual(0, quartus.main(['--board', 'de2_115', '--config', quartus.DEFAULT_CONFIG, '--plan']))
            self.assertNotEqual(0, board.main(['program', '--board', 'de2_115', '--sof', board.DEFAULT_SOF]))
            self.assertNotEqual(0, board.main(['joypad', '--board', 'de2_115', '--device', '@1: MAX10 (0x031050DD)', 'start']))
            self.assertEqual(0, board.main(['joypad', '--board', 'de2_115', '--cable', 'USB-Blaster [1-1]', '--device', '@1: 10CL120(Y|Z)/EP3C120/.. (0x020F70DD)', 'start']))

    def test_forwarded_dac_clock_and_path_gates(self):
        clocks = CLOCKS.replace('MAX10_CLK1_50', 'CLOCK_50')
        row = next(line for line in clocks.splitlines() if '; sdram_clock ;' in line)
        clocks += row.replace('sdram_clock', 'vga_dac_clock').replace('DRAM_CLK', 'VGA_CLK') + '\n'
        self.assertIn('vga_dac_clock', quartus.fitted_clocks(clocks, SYSTEM_CLOCK, self.config))
        with self.assertRaises(RuntimeError):
            quartus.fitted_clocks(clocks.replace('vga_dac_clock ; Generated ; 40.000 ; 25.0 MHz ; 20.000',
                                                'vga_dac_clock ; Generated ; 40.000 ; 25.0 MHz ; 0.000'),
                                   SYSTEM_CLOCK, self.config)
        with tempfile.TemporaryDirectory(dir=ROOT / 'workdir') as directory:
            reports = Path(directory)
            for corner in self.config['timing_corners']:
                label = corner.split()[0].lower() + '_' + corner.split()[-1].removesuffix('C')
                for kind in ('setup', 'hold'):
                    report = sdram_report(self.config, corner, 'write', kind,
                                          **{'To Node': 'VGA_R[3]', 'Latch Clock': 'vga_dac_clock'})
                    (reports / f'dac_{label}_{kind}.rpt').write_text(report)
            self.assertEqual(6, len(quartus.dac_timing(reports, self.config, SYSTEM_CLOCK)))
            path = reports / 'dac_slow_85_hold.rpt'
            original = path.read_text()
            for broken in (original.replace('5.000', '-0.001'),
                           original.replace('vga_dac_clock', 'sdram_clock'),
                           original.replace('VGA_R[3]', 'DRAM_DQ[3]'), ''):
                path.write_text(broken)
                with self.assertRaises(RuntimeError):
                    quartus.dac_timing(reports, self.config, SYSTEM_CLOCK)
            settings = (ROOT / self.config['constraints']).read_text()
            self.assertIn('VGA_R[*] VGA_G[*] VGA_B[*] VGA_HS VGA_VS VGA_BLANK_N VGA_SYNC_N', settings)
            self.assertNotIn('set_false_path -to', settings.split('# ADV7123:')[1])

    def test_identity_records_actual_image_and_input_hashes(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'workdir') as directory:
            output = Path(directory)
            quartus.stage(ROOT, output, self.config)
            (output / 'output_files').mkdir()
            sof = output / 'output_files/n2m_de2_115.sof'
            sof.write_bytes(b'example native image')
            (output / 'evidence.json').write_text(json.dumps(passing_evidence(self.config)))
            identity = quartus.image_identity(output, self.config, source_sha='a' * 40,
                                               quartus_version='Quartus test version')
            self.assertEqual(hashlib.sha256(sof.read_bytes()).hexdigest(), identity['sha256'])
            self.assertEqual(hashlib.sha256((output / 'inputs.json').read_bytes()).hexdigest(), identity['inputs_sha256'])
            self.assertEqual(identity, json.loads(sof.with_suffix('.json').read_text()))
            self.assertIn('vga_dac_clock', (output / 'audit.tcl').read_text())
            # A post-compile settings change cannot be accepted against old reports.
            (output / self.config['settings']).write_text('changed')
            with self.assertRaisesRegex(RuntimeError, 'staged input changed'):
                quartus.verify_reports(output, self.config)

    def test_receipt_hash_and_device_refusals(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'workdir') as directory:
            sof = Path(directory) / 'image.sof'
            sof.write_bytes(b'image')
            inputs, evidence = companion_bytes(self.config)
            sof.with_suffix('.inputs.json').write_bytes(inputs)
            sof.with_suffix('.evidence.json').write_bytes(evidence)
            identity = {'schema': 'n2m-sof/1', 'source_sha': 'a' * 40,
                        'quartus_version': 'Quartus test version',
                        'device': 'EP4CE115F29C7', 'project': 'n2m_de2_115',
                        'sha256': hashlib.sha256(b'image').hexdigest(),
                        'inputs_sha256': hashlib.sha256(inputs).hexdigest(),
                        'evidence_sha256': hashlib.sha256(evidence).hexdigest()}
            receipt = sof.with_suffix('.json')
            args = Args(board='de2_115', sof=str(sof), yes=True, cable='mock')
            calls = []

            def runner(command, **kwargs):
                calls.append(command)
                return type('Completed', (), {'returncode': 0})()

            def refuse():
                before = len(calls)
                with self.assertRaises(ValueError):
                    board.run_program(args, runner=runner, which=lambda name: name)
                self.assertEqual(before, len(calls), 'refusal must precede programmer invocation')

            refuse()  # Missing receipt.
            for field in identity:
                incomplete = dict(identity)
                incomplete.pop(field)
                receipt.write_text(json.dumps(incomplete))
                refuse()
                wrong_type = dict(identity, **{field: 1})
                receipt.write_text(json.dumps(wrong_type))
                refuse()
            for field, value in (('schema', 'n2m-sof/2'), ('source_sha', 'a' * 39),
                                 ('source_sha', 'x' * 40), ('quartus_version', '  '),
                                 ('sha256', '1' * 63), ('inputs_sha256', 'z' * 64),
                                 ('evidence_sha256', 'f' * 64), ('device', '10M50DAF484C7G')):
                receipt.write_text(json.dumps(dict(identity, **{field: value})))
                refuse()
            receipt.write_text(json.dumps(identity))
            for path in (sof, sof.with_suffix('.inputs.json'), sof.with_suffix('.evidence.json')):
                original = path.read_bytes()
                path.unlink()
                refuse()
                path.write_bytes(original + b'corrupted')
                refuse()
                path.write_bytes(original)
            original_inputs, original_evidence = inputs, evidence
            cases = [('non-JSON inputs', b'not JSON', original_evidence), ('empty inputs', b'{}', original_evidence),
                     ('non-JSON evidence', original_inputs, b'not JSON'), ('empty evidence', original_inputs, b'{}'),
                     ('array inputs', b'[]', original_evidence), ('array evidence', original_inputs, b'[]')]
            wrong_board, _ = companion_bytes(quartus.load_config(ROOT))
            cases.append(('different board', wrong_board, original_evidence))
            missing_hash = json.loads(original_inputs)
            missing_hash['sha256'].pop(next(iter(missing_hash['sha256'])))
            cases.append(('incomplete input hashes', json.dumps(missing_hash).encode(), original_evidence))
            mutations = {
                'missing timing': lambda e: e.pop('timing'),
                'wrong evidence board': lambda e: e.update(device='10M50DAF484C7G'),
                'negative minimum': lambda e: e.update(minimum_slack_ns=-5),
                'nonfinite timing': lambda e: e['timing'][0].update(slack_ns=float('nan')),
                'negative timing row': lambda e: e['timing'][0].update(slack_ns=-1),
                'nonzero TNS': lambda e: e['timing'][0].update(tns_ns=-1),
                'missing corner': lambda e: e['timing'].pop(),
                'duplicate corner': lambda e: e['timing'].append(copy.deepcopy(e['timing'][0])),
                'inconsistent minimum': lambda e: e.update(minimum_slack_ns=2),
                'failed Fmax': lambda e: next(iter(e['fmax'].values())).update(restricted_fmax_mhz=24),
                'resource overflow': lambda e: e['resources']['Total PLLs'].update(used=5, remaining=-1),
                'missing RAM': lambda e: e['resources']['ram_summary'].pop(),
                'wrong clock phase': lambda e: e['clock_relationships']['vga_dac_clock'].update(Rise='0'),
                'unconstrained path': lambda e: e['unconstrained'].update({'Unconstrained Output Port Paths': 1}),
                'ignored constraint': lambda e: e.update(ignored_constraints=1),
                'timing finding': lambda e: e['check_timing'].update(no_clock='2'),
                'missing SDRAM path': lambda e: e['sdram_timing']['paths'].pop(),
                'negative SDRAM path': lambda e: e['sdram_timing']['paths'][0].update(slack_ns=-1, Slack='-1'),
                'inconsistent SDRAM worst': lambda e: e['sdram_timing']['worst_margin_ns'].update(read_hold=0),
                'missing DAC path': lambda e: e['dac_timing'].pop(),
                'wrong DAC clock': lambda e: e['dac_timing'][0].update({'Latch Clock': 'sdram_clock'}),
                'negative DAC path': lambda e: e['dac_timing'][0].update(slack_ns=-1, Slack='-1'),
                'unknown warning': lambda e: e['reviewed_warnings'].append('Warning (99999): unreviewed'),
                'connectivity mismatch': lambda e: e['reviewed_warnings'].append(quartus.CONNECTIVITY_AGGREGATE),
            }
            for label, mutate in mutations.items():
                broken = json.loads(original_evidence)
                mutate(broken)
                cases.append((label, original_inputs, json.dumps(broken).encode()))
            for label, broken_inputs, broken_evidence in cases:
                with self.subTest(companion=label):
                    sof.with_suffix('.inputs.json').write_bytes(broken_inputs)
                    sof.with_suffix('.evidence.json').write_bytes(broken_evidence)
                    receipt.write_text(json.dumps(dict(identity,
                        inputs_sha256=hashlib.sha256(broken_inputs).hexdigest(),
                        evidence_sha256=hashlib.sha256(broken_evidence).hexdigest())))
                    refuse()
            sof.with_suffix('.inputs.json').write_bytes(original_inputs)
            sof.with_suffix('.evidence.json').write_bytes(original_evidence)
            receipt.write_text(json.dumps(identity))
            args.board = 'de10_lite'
            refuse()
            args.board = 'de2_115'
            self.assertEqual(0, board.run_program(args, runner=runner, which=lambda name: name)[0])
            self.assertEqual(1, len(calls))
            self.assertIn(f'p;{sof}@1', calls[0])


if __name__ == '__main__':
    unittest.main()
