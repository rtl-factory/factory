# nand2mario

A Game Boy-compatible system (LR35902 CPU, memory map, cartridge mapper, timer, interrupts, PPU and VGA display, DMA, joypad, serial, SDRAM-backed cartridges, UART loader) for the DE10-Lite and DE2-115 boards, ported from the captain's nand2anything repository (issue #67). Code: `src/nand2mario/`.

!!! note "Specs not ported yet"
    Its block specs and verification plans live in nand2anything and are not ported yet; until they are, the code's own README (`src/nand2mario/README.md`) describes the blocks.
