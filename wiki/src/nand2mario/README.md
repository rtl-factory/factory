# nand2mario

A Game Boy-compatible system (LR35902 CPU, memory map, cartridge mapper, timer, interrupts, PPU and VGA display, DMA, joypad, serial, SDRAM-backed cartridges, UART loader) for the DE10-Lite and DE2-115 boards, ported from the captain's nand2anything repository (issue #67). Code: `src/nand2mario/`.

| Discipline | Documents |
|---|---|
| [RTL](rtl/README.md) | The [HAS](rtl/has.md): blocks, boards, software and how to run the simulations |
| [Scripts](scripts/README.md) | The host scripts' PRDs and specs: board flow, regression, software build, showcase |

!!! note "Not ported yet"
    Its MASes, DV plans, board specs and the specs of its software live in nand2anything and are not ported yet; code headers already name their standard paths.
