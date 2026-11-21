#!/usr/bin/env python3
"""Build the original-game library for n2m_system CART_LIBRARY=1.

The catalogue encoding is adapted from the owner's nand2mario at 510be34b;
see cfg/sw-import.json. This tool writes only repository workdir files. The
existing cart_loader.py uploads the resulting image while the core is held.

Spec: wiki/src/nand2mario/scripts/sw_build/spec.md.
"""
import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path
import struct
import subprocess
import sys
import zlib

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from src.nand2mario.scripts.sw_build import cpu_sw
from src.nand2mario.scripts.sw_build import sw_games  # noqa: E402

ROOT = Path(__file__).resolve().parents[4]
SLOT_BYTES, MENU_INDEX, CATALOGUE_ADDRESS = 32768, 16, 0x88000
CATALOGUE_BYTES, LIBRARY_BYTES = 1024, 1024 * 1024
PROFILE_BYTES = {1: 32768, 2: 32768, 3: 65536}
ENTRY = struct.Struct('<BBHI16sB7x')


def pack(images):
    """{slot: (profile ID, image bytes)} -> deterministic library and rows.

    Empty slots and reserved bytes are zero. Titles are the 16 header bytes,
    unchanged. A 64 KiB image occupies two game slots, never menu slot 16.
    Menu slot 16 is required and must use the loader profile. There is no
    commercial-image lookup or implicit external input.
    """
    if MENU_INDEX not in images or images[MENU_INDEX][0] != 2:
        raise ValueError('slot 16 must contain the loader-profile menu')
    result = bytearray(LIBRARY_BYTES)
    occupied = set()
    rows = [dict(valid=0, profile=0, length=0, crc32=0, title=bytes(16)) for _ in range(17)]
    for slot, (profile, image) in sorted(images.items()):
        if type(slot) is not int or not 0 <= slot <= MENU_INDEX:
            raise ValueError('library index must be 0..16')
        if type(profile) is not int or profile not in PROFILE_BYTES:
            raise ValueError('profile must be direct=1, loader=2 or MBC1=3')
        image = bytes(image)
        if len(image) != PROFILE_BYTES[profile]:
            raise ValueError('image length does not match its profile')
        slots = set(range(slot, slot + len(image) // SLOT_BYTES))
        if max(slots) > (MENU_INDEX if slot == MENU_INDEX else MENU_INDEX - 1) or occupied & slots:
            raise ValueError('image overlaps another slot or the reserved menu')
        occupied |= slots
        row = dict(valid=1, profile=profile, length=len(image), crc32=zlib.crc32(image), title=image[0x134:0x144])
        rows[slot] = row
        start = slot * SLOT_BYTES
        result[start:start + len(image)] = image
        start = CATALOGUE_ADDRESS + slot * ENTRY.size
        result[start:start + ENTRY.size] = ENTRY.pack(
            row['valid'], profile, len(image) & 65535, row['crc32'], row['title'], len(image) >> 16)
    return bytes(result), rows


def build(output):
    """Build only the approved original sources: Springtrail, Stackdrop, menu."""
    output = cpu_sw.output_path(output, directory=True)
    for name in ('library.bin', 'library.json'):
        cpu_sw.output_path(output / name)
    images = {}
    sources = {}
    for slot, name in ((0, 'springtrail'), (1, 'stackdrop'), (16, 'menu')):
        sw_games.build(name, output / name)
        image = (output / name / 'program.gb').read_bytes()
        digest = hashlib.sha256(image).hexdigest()
        if digest != sw_games.load()[name]['image_sha256']:
            raise ValueError(f'{name}: image differs from the pinned donor build')
        images[slot] = (2 if name == 'menu' else 1, image)
        sources[str(slot)] = dict(name=name, sha256=digest, bytes=len(image),
                                 source_commit='510be34b49f5bc666ad828260ed0e193546736dd')
    image, rows = pack(images)
    (output / 'library.bin').write_bytes(image)
    report = dict(schema_version=1, library_sha256=hashlib.sha256(image).hexdigest(),
                  bytes=len(image), sources=sources,
                  catalogue=[dict(row, title_hex=row['title'].hex()) for row in rows])
    for row in report['catalogue']:
        del row['title']
    (output / 'library.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def simulate(output, workdir=None, backing="onchip"):
    """Run the menu's independent pixel, navigation and switch/return oracle."""
    if backing not in ('onchip', 'sdram'):
        raise ValueError('backing must be onchip or sdram')
    output = cpu_sw.output_path(output, directory=True)
    # Why: the fixture must be reproducible and redistributable, so it uses the unchanged
    # menu and the owner's deterministic, noncommercial test images; the shipped library
    # is built separately.
    sw_games.build('menu', output / 'menu')
    from src.nand2mario.dv.menu import fixture
    menu = (output / 'menu/program.gb').read_bytes()
    cpu_sw.build([ROOT / 'src/nand2mario/sw/system/menu_exit.asm'],
                 ROOT / 'src/nand2mario/sw/system/menu_exit_layout.json',
                 'menu_exit::Start', output / 'exit', title='EXIT DEMO')
    exit_image = (output / 'exit/program.gb').read_bytes()
    fixture_path = cpu_sw.output_path(output / 'fixture.bin')
    fixture_path.write_bytes(fixture.library_bytes(menu, exit_image).ljust(LIBRARY_BYTES, b'\0'))
    environment = dict(os.environ, N2M_MENU_IMAGE=str(output / 'menu/program.gb'),
                       N2M_LIBRARY_IMAGE=str(fixture_path), N2M_EXIT_IMAGE=str(output / 'exit/program.gb'),
                       N2M_MENU_BACKING=backing)
    work = cpu_sw.output_path(workdir or output / 'sim', directory=True)
    target = 'n2m_system_menu' + ('_sdram' if backing == 'sdram' else '')
    status = subprocess.run([sys.executable, '-B', 'tools/sim/sim.py', '--workdir', str(work),
                             'test', '--direct', target], cwd=ROOT, env=environment).returncode
    report = work / f'cocotb/{target}/test/menu-result.json'
    if status == 0:
        shutil.copy2(report, cpu_sw.output_path(output / 'menu-result.json'))
    return status


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('build', 'run'))
    parser.add_argument('--output', required=True, help='directory below repository workdir')
    parser.add_argument('--workdir', help='simulator build root (run only; default OUTPUT/sim)')
    parser.add_argument('--backing', choices=('onchip', 'sdram'), default='onchip', help='run scenario source backing')
    args = parser.parse_args(argv)
    try:
        if args.command == 'run':
            return simulate(args.output, args.workdir, args.backing)
        print(json.dumps(build(args.output), indent=2))
        return 0
    except (OSError, ValueError) as error:
        print(f'error: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
