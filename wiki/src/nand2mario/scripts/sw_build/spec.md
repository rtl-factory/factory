# Software build: spec

How the software build meets its [requirements](prd.md). Code: `src/nand2mario/scripts/sw_build/`; tests in its `tests/`.

## Parts

| Part | Role |
|---|---|
| `asm/` | The toolchain package: `expressions` (parser and evaluator), `assembler`, `objects` (object format and validation), `linker` (layout and relocation), `package` (image and header), `assets` (DMG shades and 2bpp tiles), `interfaces` (hardware facts from `cfg/cpu-software.json`), `interfaces_inc.py` (generates `src/nand2mario/sw/cpu/interfaces.inc` from `cfg/sw-interfaces.json`; `--check`, `--write`). JSON schemas for objects, layouts, maps, listings and symbols sit beside them |
| `cpu_sw.py` | The command: `--source` (repeatable), `--layout`, `--entry unit::symbol`, `--output`, `--asset NAME=PATH`, `--title`, `--profile dmg-direct-v1 or dmg-loader-v1`, `--dry-run` |
| `sw_games.py` | `build` or `run` the original games of `cfg/sw-games.json` through `cpu_sw` |
| `mbc1_image.py` | `--input` (32 KiB direct image), `--output`, `--banks` (power of two, 2 to 128; default 64) |
| `game_library.py` | `build` or `run` the library image; `--backing onchip or sdram` |

## Images

A direct-entry image is 32 KiB, with the header at `$0100` to `$014F` and the title in `$0134` onward. The MBC1 expansion copies the image's second half (bank 1) into every switched bank, writes the bank number at bank offset `$3FFE` and a per-bank pattern at `$3F00` to `$3FFD`, sets header bytes `$0147` to `$0149` for MBC1+RAM+BATTERY with 32 KiB RAM, and recomputes both checksums. The library catalogue is a record per slot (valid, profile, length bits 15:0, CRC32, title, length bits 23:16 and seven reserved bytes) in the encoding of the original nand2mario at 510be34b (`cfg/sw-import.json`); the RTL side is `n2m_library_pkg`.

## Confinement

Every output path is resolved and must sit below the repository's `workdir/`. Existing links and special files are rejected before anything is created, at the target and at every ancestor. `--dry-run` validates and prints the plan. The tests point the module `ROOT` at a [scratch checkout](../../../../tools/bazel/spec.md#unit-tests), so they write nothing into the checkout's `workdir/`.

## Origin

Ported from the original nand2mario and nand2anything (issues #67 and #70; provenance in `cfg/cpu-import.json` and `cfg/sw-import.json`). It was regrouped here in issue #74, where the toolchain package moved from `scripts/sw/` to `scripts/sw_build/asm/` so it is not mistaken for the `sw` discipline.
