# Software build

The host toolchain for the software that runs on nand2mario's own CPU: an LR35902 assembler, linker and packager, and the builders for the original games, the MBC1 test cartridges and the game library. It runs on the host, so it is a script; the programs it builds live in the `sw` discipline (`src/nand2mario/sw/`, ADR 0012). Code: `src/nand2mario/scripts/sw_build/` (the toolchain package is `asm/`).

- [Requirements](prd.md)
- [Spec](spec.md)
