#!/usr/bin/env python3
"""Expand a locally built 32 KiB direct-entry image into a multi-bank MBC1
cartridge image for the n2m_system cartridge test.

The direct image's second half ($4000-$7FFF, bank 1) is the template of every
switched bank: each copy keeps the template code, carries its own bank number
at bank offset $3FFE and a per-bank data pattern at $3F00-$3FFD. The header
bytes $0147-$0149 are rewritten for MBC1+RAM+BATTERY with the requested ROM
size and 32 KiB of RAM, and both header checksums are recomputed. Output
stays below the repository workdir.

Spec: wiki/src/nand2mario/scripts/sw_build/spec.md.
"""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[4]
DIRECT_BYTES = 32768
BANK_BYTES = 16384
PATTERN_START, PATTERN_END, BANK_ID_OFFSET = 0x3F00, 0x3FFE, 0x3FFE
HEADER_TYPE, HEADER_ROM_SIZE, HEADER_RAM_SIZE = 0x147, 0x148, 0x149
TYPE_MBC1_RAM_BATTERY = 0x03
RAM_SIZE_32K = 0x03
ROM_SIZE_CODES = {2 << code: code for code in range(7)}   # banks -> header $0148


def bank_pattern(bank, offset):
    """Data byte at bank offset `offset` ($3F00-$3FFD) of switched bank `bank`."""
    return (bank * 0x25 + (offset - PATTERN_START)) & 0xFF


def expand(direct, banks):
    if len(direct) != DIRECT_BYTES:
        raise ValueError('the direct-entry image is exactly 32 KiB')
    if banks not in ROM_SIZE_CODES or banks < 2:
        raise ValueError('banks must be a power of two from 2 to 128')
    template = bytearray(direct[BANK_BYTES:])
    if any(byte != 0xFF for byte in template[PATTERN_START:PATTERN_END]) or template[BANK_ID_OFFSET] != 1:
        raise ValueError('bank template must leave $7F00-$7FFD unused and hold bank id 1 at $7FFE')
    image = bytearray(direct[:BANK_BYTES])
    for bank in range(1, banks):
        copy = bytearray(template)
        for offset in range(PATTERN_START, PATTERN_END):
            copy[offset] = bank_pattern(bank, offset)
        copy[BANK_ID_OFFSET] = bank
        image += copy
    image[HEADER_TYPE] = TYPE_MBC1_RAM_BATTERY
    image[HEADER_ROM_SIZE] = ROM_SIZE_CODES[banks]
    image[HEADER_RAM_SIZE] = RAM_SIZE_32K
    image[0x14D] = (-sum(image[0x134:0x14D]) - 25) & 0xFF
    image[0x14E:0x150] = b'\0\0'
    image[0x14E:0x150] = (sum(image) & 0xFFFF).to_bytes(2, 'big')
    return bytes(image)


def confined(path):
    path = Path(path).absolute()
    if path.is_symlink() or not path.resolve().is_relative_to(ROOT / 'workdir'):
        raise ValueError('paths must be unlinked and below repository workdir')
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, help='32 KiB direct-entry image below workdir')
    parser.add_argument('--output', required=True, help='expanded image path below workdir')
    parser.add_argument('--banks', type=int, default=64, help='16 KiB ROM banks (power of two, 2..128)')
    args = parser.parse_args(argv)
    try:
        source, target = confined(args.input), confined(args.output)
        image = expand(source.read_bytes(), args.banks)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(image)
        print(f'{target}: {len(image)} bytes, {args.banks} banks')
        return 0
    except (OSError, ValueError) as error:
        print(f'error: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
