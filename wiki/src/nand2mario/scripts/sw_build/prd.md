# Software build: requirements

## Problem

The simulations and the boards need program images: CPU smoke tests, the system program, the original games, multi-bank MBC1 test cartridges and a library image holding several games behind a menu. They must be built from committed source with local tools only, byte for byte the same every time, because a test that passes on an image nobody can rebuild proves nothing. And a builder must never overwrite anything outside the scratch area.

## Requirements

1. Assemble LR35902 source into relocatable objects, link several units against a section layout, and package a 32 KiB direct-entry image with a valid header, for the direct and the loader memory profiles. Use only Python in the repository: no external assembler, no network.
2. Reject, with a diagnostic naming the file and line: unknown opcodes, out-of-range operands and relocations, undefined or duplicate symbols, overlapping sections, and a loader image that places code in ROM bank 1.
3. Take the design's hardware addresses (memory regions, registers) from committed config (`src/nand2mario/cfg/cpu-software.json`, `sw-interfaces.json`), never from constants in the code; generate the assembler prelude from `sw-interfaces.json` and fail when the committed prelude is stale.
4. Build each original game declared in `src/nand2mario/cfg/sw-games.json` with its shade assets and header title, and match its recorded SHA-256.
5. Expand a direct image into an MBC1 cartridge of 2 to 128 banks, with each bank's number and data pattern, the MBC1+RAM+BATTERY header and both checksums recomputed.
6. Build the game-library image (a menu plus several games, with a catalogue in the original encoding) for the `CART_LIBRARY=1` system, for on-chip or SDRAM backing.
7. Write only below `workdir/`; refuse an output path that leaves it, is a link or special file, or has an unsafe ancestor; offer a dry run that writes nothing.
8. Record where each imported file came from (`src/nand2mario/cfg/cpu-import.json`, `sw-import.json`), and check imported files against their source hashes.

## Out of scope

- The programs themselves: they are the `sw` discipline (`src/nand2mario/sw/`).
- Loading an image onto a board: the [board flow](../board/README.md) does that.
- A general Game Boy toolchain: only what nand2mario's programs use.

## Acceptance

| Requirement | Check |
|---|---|
| 1 | An independent opcode matrix; cross-unit relocation and range; a closed-environment CLI run (`-I`, no `PYTHONPATH`) builds a 32 KiB image |
| 2 | Diagnostics tests; malformed images and overlapping slots fail; loader profile rejects ROM bank 1 placement |
| 3 | The committed prelude is fresh; regions match the RTL memory package; register spot checks |
| 4 | Each game matches its recorded SHA-256; undeclared or malformed assets fail |
| 5 | Banks, header and checksums; sizes; bad template rejected |
| 6 | Catalogue is the original encoding; named games run once with no duplicate menu |
| 7 | Output destinations and ancestors; CLI confinement; dry run |
| 8 | Every ported file has a manifest entry; imported assets match their source hashes |
