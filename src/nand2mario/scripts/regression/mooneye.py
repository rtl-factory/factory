#!/usr/bin/env python3
"""Fetch the pinned Mooneye acceptance ROMs and run them on n2m RTL.

`fetch` downloads the pinned release archive into workdir, checks the archive,
licence and every selected ROM against src/nand2mario/cfg/mooneye.json, and
extracts the selected ROMs. `run` simulates each ROM on the n2m_system_mooneye
target (Verilator, one compiled model per lane) and writes per-ROM JSON, a
summary and a Markdown table. No binary is committed; all output is in workdir.

Spec: wiki/src/nand2mario/scripts/regression/spec.md.
"""
import argparse
import concurrent.futures
import fnmatch
import hashlib
import io
import json
import os
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
PINS = ROOT / 'src/nand2mario/cfg/mooneye.json'
VERDICTS = ('PASS', 'FAIL', 'UNKNOWN', 'TIMEOUT', 'ERROR')


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def load(path=PINS):
    pins = json.loads(Path(path).read_text())
    if pins.get('schema_version') != 1:
        raise ValueError('mooneye pins: schema_version 1 required')
    seen = set()
    for item in pins['selected']:
        rom = item['rom']
        if rom in seen or not rom.startswith('acceptance/') or not rom.endswith('.gb') or '..' in rom:
            raise ValueError(f'mooneye pins: bad or duplicate rom {rom!r}')
        if len(item['sha256']) != 64:
            raise ValueError(f'mooneye pins: {rom} needs a sha256')
        seen.add(rom)
    if seen & {item['rom'] for item in pins['excluded']}:
        raise ValueError('mooneye pins: a rom is both selected and excluded')
    known = [item['rom'] for item in pins.get('known_failures', [])]
    if len(set(known)) != len(known) or not set(known) <= seen \
            or any(type(item.get('issue')) is not int for item in pins.get('known_failures', [])):
        raise ValueError('mooneye pins: known_failures must name distinct selected roms with an issue')
    return pins


def regressions(results, pins):
    """Selected ROMs outside known_failures that did not PASS (the pinned passing set)."""
    known = {item['rom'] for item in pins.get('known_failures', [])}
    return sorted(rom for rom, r in results.items() if rom not in known and r['verdict'] != 'PASS')


def generated(path):
    path = Path(path).resolve()
    work = (ROOT / 'workdir').resolve()
    if path == work or not path.is_relative_to(work):
        raise ValueError('output must be below repository workdir')
    return path


def extract(archive, pins):
    """Verified selected ROM bytes from the release archive bytes."""
    release = pins['release']
    if sha256(archive) != release['sha256']:
        raise ValueError('mooneye archive sha256 mismatch')
    roms = {}
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        if sha256(zf.read(release['license_member'])) != release['license_sha256']:
            raise ValueError('mooneye licence sha256 mismatch')
        for item in pins['selected']:
            data = zf.read(f"{release['name']}/{item['rom']}")
            if sha256(data) != item['sha256']:
                raise ValueError(f"{item['rom']}: sha256 mismatch")
            roms[item['rom']] = data
    return roms


def fetch(output, pins):
    output = generated(output)
    with urllib.request.urlopen(pins['release']['url'], timeout=120) as response:
        archive = response.read()
    roms = extract(archive, pins)
    for rom, data in roms.items():
        target = output / rom
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    (output / 'LICENSE').write_bytes(zipfile.ZipFile(io.BytesIO(archive)).read(pins['release']['license_member']))
    print(f'fetched {len(roms)} verified ROMs into {output}')
    return 0


def run_one(item, roms, output, lane, resume=False):
    rom = item['rom']
    image = roms / rom
    if sha256(image.read_bytes()) != item['sha256']:
        return rom, {'verdict': 'ERROR', 'detail': 'image sha256 mismatch'}
    result = output / 'results' / (rom.replace('/', '__') + '.json')
    result.parent.mkdir(parents=True, exist_ok=True)
    if resume and result.is_file():
        return rom, json.loads(result.read_text())
    env = dict(os.environ, N2M_SYSTEM_IMAGE=str(image), N2M_MOONEYE_RESULT=str(result))
    log = output / 'logs' / (rom.replace('/', '__') + '.log')
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open('w') as handle:
        # N2M_SIM_LAUNCHER optionally prefixes a host slot/queue wrapper.
        launcher = os.environ.get('N2M_SIM_LAUNCHER', '').split()
        status = subprocess.run(launcher + [sys.executable, '-B', 'tools/sim/sim.py', '--workdir', str(lane),
                                 'test', '--direct', item.get('target', 'n2m_system_mooneye')], cwd=ROOT, env=env,
                                stdout=handle, stderr=subprocess.STDOUT).returncode
    if status or not result.is_file():
        return rom, {'verdict': 'ERROR', 'detail': f'simulation exit {status}', 'log': str(log)}
    return rom, json.loads(result.read_text())


def table(results):
    lines = ['| ROM | Verdict | Cycles (25 MHz) |', '| --- | --- | ---: |']
    for rom in sorted(results):
        r = results[rom]
        lines.append(f"| `{rom.removeprefix('acceptance/')}` | {r['verdict']} | {r.get('cycles', '')} |")
    counts = {v: sum(r['verdict'] == v for r in results.values()) for v in VERDICTS}
    lines.append('')
    lines.append(', '.join(f'{v} {n}' for v, n in counts.items() if n))
    return '\n'.join(lines) + '\n'


def run(roms, output, pins, jobs, only, resume=False):
    roms, output = generated(roms), generated(output)
    selected = [i for i in pins['selected'] if not only or any(fnmatch.fnmatch(i['rom'], p) for p in only)]
    lanes = [output / f'lane{n}' for n in range(jobs)]
    results = {}
    with concurrent.futures.ThreadPoolExecutor(jobs) as pool:
        # Lane n runs ROMs n, n+jobs, ... sequentially to reuse its model.
        def lane_work(n):
            return [run_one(item, roms, output, lanes[n], resume) for item in selected[n::jobs]]
        for done in pool.map(lane_work, range(jobs)):
            for rom, result in done:
                results[rom] = result
                print(f"{result['verdict']:8} {rom}", flush=True)
    summary = {'release': pins['release']['name'], 'revision': subprocess.check_output(
        ['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(), 'results': results}
    (output / 'summary.json').write_text(json.dumps(summary, indent=2, sort_keys=True) + '\n')
    (output / 'table.md').write_text(table(results))
    print(table(results))
    # A harness ERROR, or any non-PASS outside the pinned known_failures, fails the run.
    known = {item['rom'] for item in pins.get('known_failures', [])}
    fixed = sorted(rom for rom in known if results.get(rom, {}).get('verdict') == 'PASS')
    if fixed:
        print('known failures now passing (remove from known_failures): ' + ', '.join(fixed))
    bad = regressions(results, pins)
    if bad:
        print('regressions against the pinned passing set: ' + ', '.join(bad))
    return 1 if bad or any(r['verdict'] == 'ERROR' for r in results.values()) else 0


def protocol_rom(registers):
    """A 32 KiB image (no Mooneye code) that loads B,C,D,E,H,L and stops at
    the LD B,B breakpoint: the harness smoke stimulus."""
    image = bytearray(32768)
    image[0x100:0x104] = bytes([0x00, 0xC3, 0x50, 0x01])       # nop; jp $0150
    code = []
    for opcode, value in zip((0x06, 0x0E, 0x16, 0x1E, 0x26, 0x2E), registers):
        code += [opcode, value]                                 # ld r,n
    image[0x150:0x150 + len(code) + 3] = bytes(code + [0x40, 0x18, 0xFE])  # ld b,b; jr -2
    return bytes(image)


def smoke(output, lane=None):
    """Run the harness end to end on two generated protocol ROMs; both the
    PASS and the FAIL verdict must come back. Needs no download."""
    output = generated(output)
    roms = output / 'roms'
    results = {}
    for name, registers in (('pass', (3, 5, 8, 13, 21, 34)), ('fail', (0x42,) * 6)):
        rom = f'acceptance/smoke_{name}.gb'
        data = protocol_rom(registers)
        (roms / rom).parent.mkdir(parents=True, exist_ok=True)
        (roms / rom).write_bytes(data)
        _, results[name] = run_one({'rom': rom, 'sha256': sha256(data)}, roms, output, Path(lane) if lane else output / 'lane0')
        print(f"{name}: {results[name]['verdict']}", flush=True)
    (output / 'smoke.json').write_text(json.dumps(results, indent=2, sort_keys=True) + '\n')
    return 0 if (results['pass']['verdict'], results['fail']['verdict']) == ('PASS', 'FAIL') else 1


TRACE_SMOKE_MODES = [(2, 0), (3, 80), (0, 252)]  # (STAT mode, dot) on line 0 with SCX 0


def trace_smoke(output, lane=None):
    """Run the PPU trace aid (n2m_system_ppu_trace) on a generated protocol
    ROM for one pass of line 0 and check that it writes the STAT mode changes
    of the fitted CPU-visible timing. Needs no download."""
    output = generated(output)
    rom = output / 'roms' / 'trace_smoke.gb'
    rom.parent.mkdir(parents=True, exist_ok=True)
    rom.write_bytes(protocol_rom((3, 5, 8, 13, 21, 34)))
    trace = output / 'trace.jsonl'
    env = dict(os.environ, N2M_SYSTEM_IMAGE=str(rom), N2M_TRACE_OUT=str(trace),
               N2M_TRACE_LINES='0', N2M_TRACE_FRAMES='1')
    status = subprocess.run([sys.executable, '-B', 'tools/sim/sim.py', '--workdir',
                             str(Path(lane) if lane else output / 'lane0'), 'test', '--direct', 'n2m_system_ppu_trace'],
                            cwd=ROOT, env=env).returncode
    if status or not trace.is_file():
        print(f'error: trace simulation exit {status}', file=sys.stderr)
        return 1
    events = [json.loads(line) for line in trace.read_text().splitlines()]
    modes = [(e['mode'], e['dot']) for e in events if 'mode' in e and e['ly'] == 0]
    print(f'line 0 modes: {modes}', flush=True)
    return 0 if modes == TRACE_SMOKE_MODES else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('fetch', 'run', 'smoke', 'trace-smoke'))
    parser.add_argument('--roms', default='workdir/mooneye/roms', help='fetched ROM directory below workdir')
    parser.add_argument('--output', default='workdir/mooneye/run', help='run output below workdir')
    parser.add_argument('--jobs', type=int, default=2, choices=range(1, 5))
    parser.add_argument('--resume', action='store_true',
                        help='keep per-ROM results already in --output and run the rest')
    parser.add_argument('--workdir', help='simulator build directory for smoke (the regression runner passes it)')
    parser.add_argument('--only', action='append', help='glob over pinned ROM paths; repeatable')
    args = parser.parse_args(argv)
    try:
        pins = load()
        if args.command == 'fetch':
            return fetch(args.roms, pins)
        if args.command == 'smoke':
            return smoke(args.output, args.workdir)
        if args.command == 'trace-smoke':
            return trace_smoke(args.output, args.workdir)
        return run(args.roms, args.output, pins, args.jobs, args.only, args.resume)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        print(f'error: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
