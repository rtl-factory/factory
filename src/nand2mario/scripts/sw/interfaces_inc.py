#!/usr/bin/env python3
"""Render the n2m assembler prelude from src/nand2mario/cfg/sw-interfaces.json.

`--check` fails when the committed prelude is stale; `--write` regenerates it.
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
FACTS = ROOT / 'src/nand2mario/cfg/sw-interfaces.json'
PRELUDE = ROOT / 'src/nand2mario/sw/cpu/interfaces.inc'
NAME = re.compile(r'[A-Z][A-Z0-9_]*')


def load(path=FACTS):
    data = json.loads(path.read_text())
    if data.get('schema_version') != 1 or not isinstance(data.get('symbols'), list):
        raise ValueError('sw-interfaces: schema_version 1 with a symbols list required')
    seen = set()
    for item in data['symbols']:
        name, value = item.get('name'), item.get('value')
        if not isinstance(name, str) or not NAME.fullmatch(name) or name in seen:
            raise ValueError(f'sw-interfaces: invalid or duplicate name {name!r}')
        if type(value) is not int or not 0 <= value <= 0xFFFFFFFF:
            raise ValueError(f'sw-interfaces: {name} needs an unsigned 32-bit integer value')
        seen.add(name)
    return data


def render(path=FACTS):
    data = load(path)
    digest = hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest()
    lines = ['; Generated from src/nand2mario/cfg/sw-interfaces.json by '
             'src/nand2mario/scripts/sw/interfaces_inc.py; DO NOT EDIT.',
             f'; Source SHA-256: {digest}']
    lines += [f"{item['name']} EQU {item['value']}" for item in data['symbols']]
    return '\n'.join(lines) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check', action='store_true')
    mode.add_argument('--write', action='store_true')
    args = parser.parse_args(argv)
    text = render()
    if args.write:
        PRELUDE.write_text(text)
        return 0
    if PRELUDE.read_text() != text:
        print(f'error: {PRELUDE.relative_to(ROOT)} is stale; run with --write', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
