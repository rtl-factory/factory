# nand2mario scripts

The `scripts` discipline of nand2mario: host-side code bound to this design. Each script drives, builds for or checks nand2mario, and would be useless without it ([repository layout](../../../bkm/workflow/repo-layout.md)). Code: `src/nand2mario/scripts/<script>/`, with each script's tests in its `tests/` folder.

| Script | What it does |
|---|---|
| [board](board/README.md) | Builds the board image with Quartus and gates it, programs a board, loads cartridges over UART, reads back the displayed frame, presses joypad keys over JTAG |
| [regression](regression/README.md) | Prepares program images before the CPU and system simulations, and runs the Mooneye and licensed-game conformance suites |
| [sw_build](sw_build/README.md) | The host software toolchain: assembles, links and packages the design's own programs, games, MBC1 test cartridges and the game library |
| [showcase](showcase/README.md) | Renders the simulation showcase (PNG, GIF, provenance) from game-qualification frames |

Project-wide checks that belong to no one script (the target registry, the memory map against the RTL package, the cartridge model) stay in `src/nand2mario/scripts/tests/`.
