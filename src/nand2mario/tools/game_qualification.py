"""Acquire pinned licensed games and run their finite n2m compatibility scenarios.

No binary or screenshot is committed. All generated output stays in workdir.
The reference command uses the optional PyBoy 2.7.0 software oracle, not an
RTL simulator. Run it in a separate environment with pyboy==2.7.0.
"""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[3]
MANIFEST = ROOT / 'src/nand2mario/cfg/games.json'


def manifest():
    return json.loads(MANIFEST.read_text())


def generated(path):
    path = Path(path).absolute()
    if ('..' in path.parts or path == ROOT / 'workdir' or not path.is_relative_to(ROOT / 'workdir')
            or any(p.is_symlink() for p in (path, *path.parents))):
        raise ValueError('output must be an unlinked path below repository workdir')
    return path


def verify(data, pin):
    if len(data) != pin['size'] or hashlib.sha256(data).hexdigest() != pin['sha256']:
        raise ValueError('artifact size or SHA256 differs from the reviewed pin')


def check_image(data, pin):
    verify(data, pin)
    if len(data) != 32768 or data[0x147:0x14a] != bytes(3):
        raise ValueError('qualification requires 32 KiB ROM ONLY with no cartridge RAM')
    if data[0x143] not in (0, 0x80):
        raise ValueError('image is not DMG compatible')
    if (-sum(data[0x134:0x14d]) - 25) & 255 != data[0x14d]:
        raise ValueError('invalid Game Boy header checksum')
    if (sum(data) - data[0x14e] - data[0x14f]) & 65535 != int.from_bytes(data[0x14e:0x150], 'big'):
        raise ValueError('invalid Game Boy global checksum')


def acquire(game, output):
    pin = manifest()['games'][game]
    output = generated(output)
    output.mkdir(parents=True, exist_ok=True)
    # Notices travel beside the binary, even when a file is already cached.
    for name, entry in [*pin['notices'].items(), ('game.gb', pin)]:
        path = output / name
        if path.is_symlink():
            raise ValueError('artifact path must not be a symlink')
        if path.exists():
            data = path.read_bytes()
        else:
            with urllib.request.urlopen(entry['url'], timeout=30) as response:
                data = response.read(entry['size'] + 1)
        verify(data, entry)
        if name == 'game.gb':
            check_image(data, pin)
        if not path.exists():
            path.write_bytes(data)
    generated(output / 'provenance.json').write_text(json.dumps(pin, indent=2)+'\n')
    return output / 'game.gb'


def run_one(game, image, workdir):
    pin = manifest()['games'][game]
    image = generated(image)
    check_image(image.read_bytes(), pin)
    for name, notice in pin['notices'].items():
        verify((image.parent / name).read_bytes(), notice)
    workdir = generated(workdir)
    env = dict(os.environ, N2M_SYSTEM_IMAGE=str(image), N2M_GAME=game)
    return subprocess.run([sys.executable, '-B', 'tools/sim/sim.py',
        '--workdir', str(workdir), 'test', '--direct', 'n2m_system_games'], cwd=ROOT,
        env=env, timeout=7230).returncode


def run(game, image, output, workdir=None):
    """One owned build cache, separate retained evidence for every program."""
    output = generated(output)
    workdir = generated(workdir or output / 'build')
    if output.exists() or workdir.exists():
        raise ValueError('run requires fresh output and build directories')
    if output.is_relative_to(workdir):
        raise ValueError('evidence output cannot be inside the build directory')
    output.mkdir(parents=True)
    games = list(manifest()['games']) if game == 'all' else [game]
    summary = dict(source_sha=subprocess.check_output(
        ['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(), games={})
    for name in games:
        cartridge = acquire(name, output / 'assets' / name) if image is None else image
        status = run_one(name, cartridge, workdir)
        test = workdir / 'cocotb/n2m_system_games/test'
        if test.exists():
            shutil.copytree(test, output / 'results' / name)
            # The next run must not inherit any prior game's test evidence.
            shutil.rmtree(test)
        log = workdir / 'cocotb/n2m_system_games/test.log'
        if log.exists():
            shutil.copyfile(log, output / f'{name}.log')
            log.unlink()
        summary['games'][name] = dict(exit_status=status)
        (output / 'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    return int(any(item['exit_status'] for item in summary['games'].values()))


def reference(game, image, output):
    if importlib.metadata.version('pyboy') != '2.7.0':
        raise ValueError('reference requires PyBoy 2.7.0')
    from pyboy import PyBoy
    pin = manifest()['games'][game]
    image, output = generated(image), generated(output)
    check_image(image.read_bytes(), pin)
    output.mkdir(parents=True, exist_ok=False)
    emulator = PyBoy(str(image), window='null', sound_emulated=False, cgb=False,
                     color_palette=(0xffffff, 0xaaaaaa, 0x555555, 0))
    emulator.set_emulation_speed(0)
    first, last = pin.get('reference_frames', (351, 440))
    if pin.get('reference_dmg_entry'):
        # PyBoy's internal boot ROM enters $0100 with A=$11 (CGB) even when
        # cgb=False; a CGB-aware cartridge then takes its CGB path. A real DMG
        # boot ROM leaves A=$01, so present that at the entry point.
        def dmg_entry(_):
            emulator.register_file.A = 0x01
        emulator.hook_register(0, 0x0100, dmg_entry, None)
    hashes = set()
    try:
        emulator.tick(first - 1, True)
        for _ in range(last - first + 1):
            emulator.tick(1, True)
            shades = bytes((3-emulator.screen.ndarray[:, :, 0].astype('uint16')//85).astype('uint8').flat)
            digest = hashlib.sha256(shades).hexdigest()
            hashes.add(digest)
            (output / f'{digest}.shades').write_bytes(shades)
    finally:
        emulator.stop(save=False)
    (output / 'reference.json').write_text(json.dumps(dict(pyboy='2.7.0', image_sha256=pin['sha256'],
        frames=f'{first}..{last}', dmg_entry=bool(pin.get('reference_dmg_entry')),
        hashes=sorted(hashes)), indent=2)+'\n')
    if sorted(hashes) != sorted(pin['title_sha256']):
        raise ValueError('independent reference differs from the reviewed title hash set')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('fetch', 'run', 'reference'))
    parser.add_argument('game', choices=(*manifest()['games'], 'all'))
    parser.add_argument('--image', type=Path)
    parser.add_argument('--workdir', type=Path,
                        help='optional fresh simulator build directory (run only)')
    parser.add_argument('--output', type=Path, required=True,
                        help='asset, simulation, or reference output below workdir')
    args = parser.parse_args(argv)
    try:
        if args.action == 'run':
            if args.game == 'all' and args.image is not None:
                raise ValueError('all games requires their own pinned images, not --image')
            return run(args.game, args.image, args.output, args.workdir)
        if args.game == 'all':
            raise ValueError('all is supported only by run')
        if args.action == 'fetch':
            print(acquire(args.game, args.output))
            return 0
        if args.image is None:
            raise ValueError('--image is required for run/reference')
        reference(args.game, args.image, args.output)
        return 0
    except (OSError, ValueError, subprocess.SubprocessError, importlib.metadata.PackageNotFoundError) as error:
        print(error, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
