#!/usr/bin/env python3
"""Build the original nand2mario games (src/nand2mario/cfg/sw-games.json).

Each game is assembled by cpu_sw.build with its declared shade assets and
header title; output goes below repository workdir.

Spec: wiki/src/nand2mario/scripts/sw_build/spec.md.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from src.nand2mario.scripts.sw_build import cpu_sw  # noqa: E402
from src.nand2mario.scripts.sw_build.asm.expressions import AssemblyError  # noqa: E402

ROOT = Path(__file__).resolve().parents[4]
MANIFEST = ROOT / 'src/nand2mario/cfg/sw-games.json'


def load(path=MANIFEST):
    data = json.loads(Path(path).read_text())
    if data.get('schema_version') != 1 or not data.get('games'):
        raise ValueError('sw-games: schema_version 1 with games required')
    return data['games']


def build(name, output, dry_run=False, games=None):
    games = games or load()
    if name not in games:
        raise ValueError(f'unknown game {name}; choose from {", ".join(sorted(games))}')
    game = games[name]
    directory = ROOT / game['directory']
    assets = [f"{asset}={directory / spec['source']}" for asset, spec in sorted(game['assets'].items())]
    return cpu_sw.build([directory / s for s in game['sources']], directory / game['layout'],
                        game['entry'], output, dry_run, assets, game['title'], game.get('profile', 'dmg-direct-v1'))


def simulate(name, output, workdir=None):
    """Build one game and run its scenario on n2m_system_sw_games."""
    if name == 'menu':
        from src.nand2mario.scripts.sw_build import game_library
        return game_library.simulate(output, workdir)
    output = Path(output).resolve()
    if not output.is_relative_to((ROOT / 'workdir').resolve()):
        raise ValueError('output must be below repository workdir')
    build(name, output / 'software')
    work = Path(workdir).resolve() / name if workdir else output / 'build'
    env = dict(os.environ, N2M_GAME=name, N2M_SW_GAMES=str(MANIFEST),
               N2M_SYSTEM_IMAGE=str(output / 'software/program.gb'))
    # N2M_SIM_LAUNCHER optionally prefixes a host slot/queue wrapper.
    launcher = os.environ.get('N2M_SIM_LAUNCHER', '').split()
    status = subprocess.run(launcher + [sys.executable, '-B', 'tools/sim/sim.py', '--workdir', str(work),
                                        'test', '--direct', 'n2m_system_sw_games'], cwd=ROOT, env=env).returncode
    test = work / 'cocotb/n2m_system_sw_games/test'
    for item in sorted(test.glob('*')):
        if item.suffix in ('.png', '.shades', '.json', '.xml'):
            shutil.copy2(item, output / item.name)
    return status


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('build', 'run'))
    parser.add_argument('game', nargs='+', help='one or more game names, or all')
    parser.add_argument('--output', required=True, help='directory below workdir (one subdirectory per game)')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--workdir', help='simulator build root (run only; default OUTPUT/GAME/build)')
    args = parser.parse_args(argv)
    try:
        games = load()
        if 'all' in args.game and args.game != ['all']:
            raise ValueError('all cannot be combined with named games')
        names = sorted(games) if args.game == ['all'] else args.game
        if args.command == 'run':
            return max(simulate(n, Path(args.output) / n, args.workdir) for n in names)
        plans = {n: build(n, Path(args.output) / n, args.dry_run, games) for n in names}
        print(json.dumps(plans, indent=2))
        return 0
    except AssemblyError as error:
        print(json.dumps(error.diagnostic), file=sys.stderr)
    except (OSError, ValueError, KeyError) as error:
        print(f'error: {error}', file=sys.stderr)
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
