"""Prepare an original program image before its bounded registry simulation.

The default program is the CPU smoke feeding the ``n2m_cpu`` target; the
system program feeds ``n2m_system`` through ``system_regression``.

Spec: wiki/src/nand2mario/scripts/regression/spec.md.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[4]
# Why: tests repoint ROOT, and must still read the real registry beside this tool.
REGISTRY = Path(__file__).resolve().parents[2] / "cfg/targets.json"

PROGRAMS = {
    'cpu': dict(source='src/nand2mario/sw/cpu/smoke.asm', layout='src/nand2mario/sw/cpu/layout.json',
                entry='smoke::start', target='n2m_cpu', variable='N2M_CPU_IMAGE',
                action='mario-cpu-preparation'),
    'system': dict(source='src/nand2mario/sw/system/system_smoke.asm',
                   layout='src/nand2mario/sw/system/layout.json', entry='system_smoke::start',
                   target='n2m_system', variable='N2M_SYSTEM_IMAGE',
                   action='mario-system-preparation'),
    'system_background': dict(source='src/nand2mario/sw/system/background.asm',
                              layout='src/nand2mario/sw/system/background_layout.json',
                              entry='background::start', target='n2m_system_background',
                              variable='N2M_SYSTEM_IMAGE', action='mario-system-background-preparation'),
    'system_objects': dict(source='src/nand2mario/sw/system/objects.asm',
                           layout='src/nand2mario/sw/system/objects_layout.json',
                           entry='objects::start', target='n2m_system_objects',
                           variable='N2M_SYSTEM_IMAGE', action='mario-system-objects-preparation'),
    'system_mbc1': dict(source='src/nand2mario/sw/system/mbc1.asm',
                        layout='src/nand2mario/sw/system/mbc1_layout.json', entry='mbc1::start',
                        target='n2m_system_mbc1', variable='N2M_SYSTEM_IMAGE',
                        action='mario-system-mbc1-preparation',
                        expand=dict(banks=64, bytes=1048576, name='cartridge.gb')),
    'system_vga': dict(source='src/nand2mario/sw/system/background.asm',
                       layout='src/nand2mario/sw/system/background_layout.json',
                       entry='background::start', target='n2m_system_vga',
                       variable='N2M_SYSTEM_IMAGE', action='mario-system-vga-preparation'),
    'system_snapshot': dict(source='src/nand2mario/sw/system/snapshot.asm',
                            layout='src/nand2mario/sw/system/snapshot_layout.json',
                            entry='snapshot::start', target='n2m_system_snapshot',
                            variable='N2M_SYSTEM_IMAGE', action='mario-system-snapshot-preparation'),
    'de10_lite': dict(source='src/nand2mario/sw/system/background.asm',
                      layout='src/nand2mario/sw/system/background_layout.json',
                      entry='background::start', target='n2m_de10_lite',
                      variable='N2M_SYSTEM_IMAGE', action='mario-de10-lite-preparation'),
    'de2_115': dict(source='src/nand2mario/sw/system/background.asm',
                      layout='src/nand2mario/sw/system/background_layout.json',
                      entry='background::start', target='n2m_de2_115',
                      variable='N2M_SYSTEM_IMAGE', action='mario-de2-115-preparation'),
    'system_sdram': dict(source='src/nand2mario/sw/system/sdram_boot.asm',
                         layout='src/nand2mario/sw/system/sdram_boot_layout.json', entry='sdram_boot::start',
                         target='n2m_system_sdram', variable='N2M_SYSTEM_IMAGE',
                         action='mario-system-sdram-preparation',
                         expand=dict(banks=8, bytes=131072, name='cartridge.gb')),
    'system_preload': dict(source='src/nand2mario/sw/system/sdram_boot.asm',
                           layout='src/nand2mario/sw/system/sdram_boot_layout.json', entry='sdram_boot::start',
                           target='n2m_system_preload', variable='N2M_SYSTEM_IMAGE',
                           action='mario-system-preload-preparation',
                           expand=dict(banks=8, bytes=131072, name='cartridge.gb')),
    'system_sdram_preload': dict(source='src/nand2mario/sw/system/sdram_boot.asm',
                                 layout='src/nand2mario/sw/system/sdram_boot_layout.json',
                                 entry='sdram_boot::start', target='n2m_system_sdram_preload',
                                 variable='N2M_SYSTEM_IMAGE', action='mario-system-sdram-preload-preparation',
                                 expand=dict(banks=8, bytes=131072, name='cartridge.gb')),
}


def builder_timeout(target, headroom=960):
    """Outer wall bound for the runner: the registry target's timeout_seconds
    plus headroom above the runner's own compile allowance, so the runner's
    timeout fires first."""
    registry = json.loads(REGISTRY.read_text())
    return int(registry['targets'][target]['timeout_seconds']) + headroom


def fresh_path(value):
    path = Path(value).absolute()
    if ('..' in path.parts or path == ROOT/'workdir'
            or not path.is_relative_to(ROOT/'workdir')
            or any(p.is_symlink() for p in (path, *path.parents))
            or path.exists()):
        raise ValueError('output must be fresh, unlinked and below workdir')
    return path


def execute(output, workdir, program='cpu'):
    recipe = PROGRAMS[program]
    output, workdir = fresh_path(output), fresh_path(workdir)
    if output.is_relative_to(workdir) or workdir.is_relative_to(output):
        raise ValueError('software and simulator outputs must be separate')
    output.mkdir(parents=True)
    software = output/'software'
    argv = [sys.executable, '-B', 'src/nand2mario/scripts/sw_build/cpu_sw.py',
            '--source', recipe['source'], '--layout', recipe['layout'],
            '--entry', recipe['entry'], '--output', str(software)]
    code = subprocess.run(argv, cwd=ROOT, timeout=30).returncode
    if code:
        return code
    image = software/'program.gb'
    if not image.is_file() or image.is_symlink() or image.stat().st_size != 32768:
        raise ValueError('assembler did not produce the complete smoke image')
    if 'expand' in recipe:
        expand = recipe['expand']
        expanded = software/expand['name']
        argv = [sys.executable, '-B', 'src/nand2mario/scripts/sw_build/mbc1_image.py', '--input', str(image),
                '--output', str(expanded), '--banks', str(expand['banks'])]
        code = subprocess.run(argv, cwd=ROOT, timeout=30).returncode
        if code:
            return code
        if not expanded.is_file() or expanded.is_symlink() or expanded.stat().st_size != expand['bytes']:
            raise ValueError('expander did not produce the complete cartridge image')
        image = expanded
    # Why: run through sim.py so the runner keeps the registry oracle, seed, cycle bound and timeout.
    return subprocess.run([sys.executable, '-B', 'tools/sim/sim.py',
        '--workdir', str(workdir), 'test', '--direct', recipe['target']], cwd=ROOT,
        env=dict(os.environ, **{recipe['variable']: str(image)}),
        timeout=builder_timeout(recipe['target'])).returncode


def main(argv=None, program='cpu'):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--workdir', type=Path, required=True)
    parser.add_argument('--program', choices=sorted(PROGRAMS), default=program,
                        help='program recipe (default: the caller\'s program)')
    args = parser.parse_args(argv)
    try:
        return execute(args.output, args.workdir, args.program)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(error, file=sys.stderr)
        return 124 if isinstance(error, subprocess.TimeoutExpired) else 1


if __name__ == '__main__':
    raise SystemExit(main())
