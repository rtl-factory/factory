# nand2mario (`n2m`)

A Game Boy-compatible system for the DE10-Lite and DE2-115 boards, ported from the captain's nand2anything repository (issue #67). It reuses the `common` UART, VGA, memory, package, macro and board blocks; the default system clock is 25 MHz.

| Folder | What it holds |
|---|---|
| `rtl/<block>/` | The design: CPU, memory map, cartridge, peripherals, video, DMA, storage, UART loader, audio, and the `system` top |
| `dv/<block>/` | Python models and cocotb tests, per block, plus the full-system and board smoke tests |
| `fpga/<board>/` | The DE10-Lite and DE2-115 board tops, clocks, Quartus settings and constraints, and the shared `board_io` |
| `scripts/` | Host scripts: assembler and linker, image builders, regressions, board and Quartus drivers |
| `sw/` | Software that runs on the design's CPU: smoke programs, original games and menu, private ROM |
| `cfg/` | Simulation targets and per-board build config |

Its documents are in the wiki at `wiki/src/nand2mario/`: start with the HAS, `wiki/src/nand2mario/rtl/has.md`, which describes the blocks and how to run the simulations.
