"""Independent encoding/link, closure and negative tests for the local tools."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from src.nand2mario.scripts.sw_build.cpu_sw import ROOT, build
from src.nand2mario.scripts.sw_build.asm.assembler import assemble
from src.nand2mario.scripts.sw_build.asm.expressions import AssemblyError
from src.nand2mario.scripts.sw_build.asm.linker import link
from src.nand2mario.scripts.sw_build.asm.objects import validate
from src.nand2mario.dv.core.cpu_model import CPU


class SoftwareTests(unittest.TestCase):
    def setUp(self):
        parent = ROOT / 'workdir/issue42/units'
        parent.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(self.temporary.cleanup)
        self.tree = Path(self.temporary.name)
        self.prelude = self.tree / 'interfaces.inc'
        self.prelude.write_text('')

    def assemble(self, text, name='main'):
        source = self.tree / (name + '.asm')
        source.write_text(text)
        return assemble(source, self.tree, self.prelude)

    def test_independent_opcode_matrix(self):
        # Construct forms/bytes from architectural fields, never FORMS/opcodes.json.
        regs = ['B', 'C', 'D', 'E', 'H', 'L', '[HL]', 'A']
        cases = [('NOP', [0]), ('STOP', [0x10, 0]), ('HALT', [0x76]),
                 ('DI', [0xF3]), ('EI', [0xFB]), ('RETI', [0xD9]),
                 ('LD SP, $d000', [0x31, 0, 0xD0]), ('LD [$c000], SP', [8, 0, 0xC0]),
                 ('LD HL, SP-1', [0xF8, 0xFF]), ('ADD SP, -128', [0xE8, 0x80])]
        for y, left in enumerate(regs):
            for z, right in enumerate(regs):
                if y == z == 6: continue
                cases.append((f'LD {left}, {right}', [0x40+8*y+z]))
            cases.extend([(f'INC {left}', [4+8*y]), (f'DEC {left}', [5+8*y]),
                          (f'LD {left}, $a5', [6+8*y, 0xA5])])
        for y, name in enumerate(['ADD', 'ADC', 'SUB', 'SBC', 'AND', 'XOR', 'OR', 'CP']):
            for z, register in enumerate(regs):
                cases.append((f'{name} A, {register}', [0x80+8*y+z]))
            cases.append((f'{name} A, $81', [0xC6+8*y, 0x81]))
        for y, name in enumerate(['RLC', 'RRC', 'RL', 'RR', 'SLA', 'SRA', 'SWAP', 'SRL']):
            for z, register in enumerate(regs):
                cases.append((f'{name} {register}', [0xCB, 8*y+z]))
        for x, name in enumerate(['BIT', 'RES', 'SET'], 1):
            for y in range(8):
                for z, register in enumerate(regs):
                    cases.append((f'{name} {y}, {register}', [0xCB, 64*x+8*y+z]))
        obj = self.assemble('SECTION "code", ROM\n' + '\n'.join(s for s, _ in cases))
        self.assertEqual(obj['sections'][0]['data'], [b for _, expected in cases for b in expected])

    def test_cross_unit_relocation_and_range(self):
        main = self.assemble('SECTION "code", ROM\nIMPORT helper\nstart: CALL helper\nJR start\n')
        other = self.assemble('SECTION "code", ROM\nEXPORT helper\nhelper: RET\n', 'other')
        layout = {'schema_version': 1, 'sections': [
            {'unit': 'main', 'section': 'code', 'region': 'ROM0', 'address': 0x150},
            {'unit': 'other', 'section': 'code', 'region': 'ROM0', 'address': 0x200}]}
        result = link([('main', main), ('other', other)], layout, {'unit': 'main', 'symbol': 'start'})
        self.assertEqual(result['image'][0x150:0x155], bytes([0xCD, 0, 2, 0x18, 0xFB]))
        layout['sections'][1]['address'] = 0x150
        with self.assertRaises(AssemblyError):
            link([('main', main), ('other', other)], layout, {'unit': 'main', 'symbol': 'start'})
        bad = self.assemble('SECTION "code", ROM\nstart: JR $3000\n')
        with self.assertRaises(AssemblyError):
            link([('main', bad)], {'schema_version': 1, 'sections': layout['sections'][:1]}, {'unit': 'main', 'symbol': 'start'})

    def test_diagnostics(self):
        for text in ('BOGUS A', 'LD A, 256', 'BIT 8, A', 'DB 256', 'LD A, missing('):
            with self.subTest(text=text), self.assertRaises(AssemblyError):
                self.assemble('SECTION "code", ROM\n' + text)
        with self.assertRaises(AssemblyError): validate({'schema_version': 999})
        with self.assertRaises(AssemblyError):
            self.assemble('INCLUDE "../outside.asm"')

    def test_cli_closed_environment_and_dry_run(self):
        source = ROOT / 'src/nand2mario/sw/cpu/smoke.asm'
        layout = ROOT / 'src/nand2mario/sw/cpu/layout.json'
        output = self.tree / 'built'
        plan = build([source], layout, 'smoke::start', output, True)
        self.assertTrue(plan['dry_run'])
        self.assertFalse(output.exists())
        # -I excludes user site and PYTHONPATH; only this CLI's own directory is
        # inserted explicitly. An import guard rejects any sibling donor file.
        script = '''import runpy,sys
from pathlib import Path
root=Path(sys.argv.pop(1)).resolve()
sys.path.insert(0,str(root/'src/nand2mario/scripts/sw_build'))
def audit(event,args):
    if event=='open' and isinstance(args[0],str):
        assert '/github/nand2mario/' not in str(Path(args[0]).resolve()), args[0]
sys.addaudithook(audit)
runpy.run_path(str(root/'src/nand2mario/scripts/sw_build/cpu_sw.py'),run_name='__main__')
'''
        env = {k: v for k, v in os.environ.items() if k not in ('PYTHONPATH', 'PYTHONHOME')}
        command = [sys.executable, '-I', '-c', script, str(ROOT), '--source', str(source),
                   '--layout', str(layout), '--entry', 'smoke::start', '--output', str(output)]
        result = subprocess.run(command, env=env, cwd=self.tree, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        image = (output / 'program.gb').read_bytes()
        self.assertEqual(len(image), 32768)
        self.assertEqual(image[0x104:0x134], bytes(48))
        model = CPU(); model.mem[:32768] = image
        for _ in range(100):
            model.step()
            if model.halted: break
        self.assertTrue(model.halted)
        self.assertEqual(model.mem[0xC000:0xC002], bytes([110, 0xA5]))
        with self.assertRaises(ValueError):
            build([source], layout, 'smoke::start', ROOT / 'src/output', True)

    def test_output_destinations_and_ancestors(self):
        source = ROOT / 'src/nand2mario/sw/cpu/smoke.asm'
        layout = ROOT / 'src/nand2mario/sw/cpu/layout.json'
        output = self.tree / 'outputs'
        output.mkdir()
        victim = ROOT / 'README.md'
        original = victim.read_bytes()
        destinations = ['program.gb', 'smoke.object.json', 'map.json',
                        'symbols.json', 'listing.json']
        # Interception ensures a regression cannot mutate any artifact/victim.
        for name in destinations:
            link = output / name
            link.symlink_to(victim)
            for dry_run in (True, False):
                with self.subTest(name=name, dry_run=dry_run), \
                     patch.object(Path, 'write_bytes', side_effect=AssertionError('write before validation')), \
                     patch.object(Path, 'write_text', side_effect=AssertionError('write before validation')), \
                     self.assertRaises(ValueError):
                    build([source], layout, 'smoke::start', output, dry_run)
            link.unlink()
        self.assertEqual(victim.read_bytes(), original)
        alias = self.tree / 'alias'
        alias.symlink_to(output, target_is_directory=True)
        non_directory = self.tree / 'regular-file'
        non_directory.write_text('preserve')
        for bad in [alias / 'nested', non_directory / 'nested']:
            for dry_run in (True, False):
                with self.subTest(path=bad, dry_run=dry_run), self.assertRaises(ValueError):
                    build([source], layout, 'smoke::start', bad, dry_run)
        (output / 'program.gb').mkdir()
        for dry_run in (True, False):
            with self.assertRaises(ValueError):
                build([source], layout, 'smoke::start', output, dry_run)


if __name__ == '__main__':
    unittest.main()
