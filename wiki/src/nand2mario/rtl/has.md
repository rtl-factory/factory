# nand2mario HAS

!!! note "Seed, not yet a full HAS"
    This page is the block overview that used to sit in `src/nand2mario/README.md`, rearranged into the HAS sections ([standard documents](../../../bkm/layout.md#standard-documents)). The full HAS and the block MASes live in nand2anything and are not ported yet. Sections with no content yet say so.

## Purpose

A Game Boy-compatible system (LR35902 CPU, memory map, cartridge mapper, timer, interrupts, PPU and VGA display, DMA, joypad, serial, SDRAM-backed cartridges, UART loader) for the DE10-Lite and DE2-115 boards, ported from the captain's nand2anything repository (issue #67). It reuses the `common` UART, VGA, memory, package, macro and board blocks.

## Block diagram

`rtl/system/n2m_system.sv` is the single full-system simulation top: timebase, CPU, memory, DMA, cartridge with the on-chip or SDRAM backing, the UART loader, timer, interrupt flags, PPU, display, joypad and serial behind an I/O fan-out. Paths below are under `src/nand2mario/`.

| Block | Code | What it does |
|---|---|---|
| core | `rtl/core/` | The LR35902 CPU (`n2m_cpu.sv`) with its control, execute, bus, retirement and STOP-policy blocks, the ALU, and the timebase that makes the 25 MHz clock enables; `n2m_interfaces_pkg.sv` publishes the system-domain CPU bus and retirement records |
| memory | `rtl/memory/` | The Game Boy memory map (`n2m_memory_pkg.sv`, `n2m_memory_decode.sv`, `n2m_memory.sv`) and the byte-enabled dual-port RAM it stores in |
| cartridge | `rtl/cartridge/` | The header-selected ROM-only/MBC1 mapper on the memory map's cartridge port, its on-chip backing store, and the optional game library |
| timer, interrupts, joypad, serial | `rtl/timer/`, `rtl/interrupts/`, `rtl/joypad/`, `rtl/serial/` | The DIV/TIMA/TMA/TAC, IF, P1 and SB/SC registers on the memory map's peripheral port |
| video | `rtl/video/` | The PPU (registers, timing, window, objects, background, mix) and the display tier that stores complete frames and scans them out 3x scaled on 640x480 VGA through the common raster timing and RAM; also the frame snapshot |
| dma | `rtl/dma/` | OAM DMA |
| storage | `rtl/storage/` | The SDRAM cartridge backing, which drives the common SDRAM controller `n2a_sdram_ctrl` in the nand2mario configuration, the `SDRAM_*` constants of `n2m_sdram_backing_pkg` ([common MAS](../../common/rtl/sdram/mas.md)). The backing, not the board top, makes DRAM_CLK as the inverted `clk_i`, and `n2m_system` carries it to the pin |
| uart | `rtl/uart/` | The board UART link `n2m_uart_link`: the common byte PHYs, the common UART memory endpoint `n2a_uart_mem` and the UART cartridge loader behind it. A common request (`0xA8`) at a loader frame boundary goes to the endpoint, every other byte to the loader. The endpoint (project `n2m `, entry 0x0100 only) holds the core in reset and reaches the cartridge backing's load port through `n2m_uart_mem_port`, one word as four byte requests: ROM window at 0, cartridge RAM window at 0x0020_0000, both written and read while held, sized by `CART_ROM_BYTES` and `CART_RAM_BYTES`. The link runs at the factory board rate of 2 Mbaud on both boards (12 1/2 clocks per bit at 25 MHz, through the PHY's fractional accumulator; `UART_CLKS_PER_BIT`, `UART_FRAC_NUM`, `UART_FRAC_DEN`). The host sides are the common `src/common/scripts/uart_mem/` (load) and the script `scripts/board/cart_loader.py` (loader frames, frame snapshot) |
| audio | `rtl/audio/` | The APU: the full DMG sound register behaviour (power control, channel status, length counters, wave RAM) but **no audio output**; the system is silent by design |

The PPU and the DMA are independent rewrites from public Game Boy documentation.

## Seams

Not written yet: each seam's MAS is not ported.

## Memory and register map

The Game Boy memory map, implemented in `rtl/memory/n2m_memory_decode.sv`, with the cartridge port and the peripheral port behind it. Not written out here yet.

## Clocks and resets

The default system clock is 25 MHz; the CPU and peripherals run on clock enables from the timebase. Crossings are not written out here yet.

## Top-level behaviour

- Cartridges start at 0x0100: the DMG boot ROM is not modelled.
- ROM-only and MBC1 cartridges are supported. Super Mario Land (MBC1) runs in simulation but needs the owner's private ROM (rules in `sw/roms/private/README.md`, private, never published). Board captures are in `sw/roms/private/media/` under the same rule.
- The optional `CART_LIBRARY=1` system profile runs the pinned original menu, the snapshot window and the CRC-checked game switch and return ABI; the default ordinary cartridge configuration is unchanged. Menu/library owns the catalogue and provenance, its commands, independent pixel scenarios for the on-chip and SDRAM source backings, and the DE2-115 board dependency.

## Decisions

Not ported yet.

## Verification strategy

Each block's Python models and cocotb tests are in `dv/<block>/`, and the full-system tests in `dv/system/`; the board smoke is in `dv/fpga/`. Mooneye acceptance results are in the conformance plan ([nand2anything #441](https://github.com/amichai-bd/nand2anything/issues/441)); a SameBoy differential runner is a later step. DV plans are not ported yet.

All of these run in Verilator on the host, with no board or Quartus:

```sh
# every L0 nand2mario target (unit, subsystem and board smoke checks)
python3 tools/sim/sim.py test --project nand2mario --level L0
# one full-system program test (its wrapper assembles the program first)
python3 tools/sim/sim.py test n2m_system
```

`python3 src/nand2mario/scripts/regression/game_qualification.py run <game>`, run from the repository root, runs one pinned game scenario ([nand2anything #395](https://github.com/amichai-bd/nand2anything/issues/395)).

## Boards

`fpga/de10_lite/` holds the DE10-Lite board top (SDRAM backing and UART loader on the board pins; the host keyboard through the common JTAG endpoint), the Quartus settings fragment and the SDC; `fpga/de2_115/` holds the DE2-115 top and its files; both tops instantiate the common board clock `n2a_<board>_clock` ([DE10-Lite](../../common/fpga/de10_lite/board-spec.md), [DE2-115](../../common/fpga/de2_115/board-spec.md)); `fpga/board_io/` holds the shared Set-2-to-joypad map (its prefixes are decoded by the common [Set-2 decoder](../../common/rtl/ps2/mas.md)), board-input debouncer and seven-segment decoder. `cfg/n2m_quartus.json` and `cfg/n2m_de2_115_quartus.json` name the pins each top uses (locations in `src/common/fpga/<board>/pins.json`) and the Quartus source lists, the [Quartus tool](../../../tools/quartus/README.md) builds the images, and `scripts/board/n2m_board.py` runs that build, programs, loads images and sends joypad keys. On both boards the joypad is the host JTAG keyboard, decoded by `n2m_scancode_buttons` (the arrow keys, X for A, Z for B, Space for Select, Enter for Start), ORed with the debounced board fallback: `SW[7:0]` hold the button levels in joypad order and `KEY1` is a momentary Start. Board specs are not written yet; until they are, the board tops and their SDCs cite this section.

## Software

Original smoke software is in `sw/cpu/` (CPU smoke) and `sw/system/` (system smoke with the timer, joypad and serial interrupts, the background frame program with the VBlank interrupt, and the objects frame program with window, sprites and OAM DMA). The original games and menu are in `sw/games/`. They are built by the project's own assembler and linker (`scripts/sw_build/asm/`), with no sibling checkout, commercial ROM or external oracle.
