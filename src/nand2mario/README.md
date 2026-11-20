# nand2mario (`n2m`)

Game Boy-compatible RTL, Python DV, original software/assets, and integration
specific to nand2mario belong here. Reuse common UART, VGA, memory, package,
macro, and board infrastructure. The default system clock is 25 MHz.

The LR35902 CPU lives in `rtl/core/n2m_cpu.sv` with focused control, execute,
bus, retirement and STOP-policy blocks. It reuses the accepted timebase and
ALU. Its [architecture and verification](../../wiki/src/nand2mario/core/HAS.md)
cover the compact bus/retirement interfaces and 25 MHz clock enables.

Original smoke software is in `sw/cpu/` (CPU smoke) and `sw/system/` (system
smoke with the timer, joypad and serial interrupts, the background frame
program with the VBlank interrupt, and the objects frame program with
window, sprites and OAM DMA); the self-contained local
[assembler/linker commands](../../wiki/tools/n2m-sw/SPEC.md) use no sibling
checkout, commercial ROM or external oracle.

`rtl/core/n2m_interfaces_pkg.sv` publishes the system-domain CPU bus and retirement
records. `rtl/memory/` holds the Game Boy memory map (`n2m_memory_pkg.sv`,
`n2m_memory_decode.sv`, `n2m_memory.sv`) and the project-owned byte-enabled
dual-port RAM it stores in. `rtl/cartridge/` holds the header-selected
ROM-only/MBC1 cartridge mapper (`n2m_cartridge_pkg.sv`, `n2m_cartridge.sv`)
on the memory map's cartridge port and the on-chip backing store behind its
backing port (`n2m_cartridge_backing.sv`). `rtl/timer/n2m_timer.sv` (with
`n2m_timer_pkg.sv`),
`rtl/interrupts/n2m_interrupts.sv`, `rtl/joypad/n2m_joypad.sv` and
`rtl/serial/n2m_serial.sv` are the DIV/TIMA/TMA/TAC, IF, P1 and SB/SC
registers on the memory map's peripheral port. `rtl/video/` holds the PPU
(`n2m_ppu_pkg.sv`, `n2m_ppu_registers.sv`, `n2m_ppu_timing.sv`,
`n2m_ppu_window.sv`, `n2m_ppu_objects.sv`, `n2m_ppu_background.sv`,
`n2m_ppu_mix.sv`, `n2m_ppu.sv`) and `rtl/dma/` the OAM DMA
(`n2m_dma_pkg.sv`, `n2m_dma.sv`), both independent rewrites from public
Game Boy documentation; `rtl/video/` also holds the display tier
(`n2m_display_pkg.sv`, `n2m_frame_bridge.sv`, `n2m_frame_ram.sv`,
`n2m_vga_scanout.sv`, `n2m_display.sv`) that stores complete frames and
scans them out 3x scaled on 640x480 VGA through the common raster timing
and RAM. `rtl/storage/` holds the SDRAM controller and package adapted under
their MIT notice (`n2m_sdram_pkg.sv`, `n2m_sdram_ctrl.sv`,
`LICENSE-sdram-controller.txt`) and the SDRAM cartridge backing
(`n2m_sdram_backing.sv`); `rtl/uart/` the UART cartridge loader
(`n2m_loader_pkg.sv`, `n2m_uart_loader.sv`) over the common byte PHY, with
its host tool `tools/cart_loader.py`. `rtl/system/n2m_system.sv` is
the single growing full-system simulation top (timebase, CPU, memory,
DMA, cartridge with the on-chip or SDRAM backing, the UART loader, timer,
interrupt flags, PPU, display, joypad and serial behind an I/O fan-out
today). The
[memory](../../wiki/src/nand2mario/memory/HAS.md),
[cartridge](../../wiki/src/nand2mario/cartridge/HAS.md),
[timer](../../wiki/src/nand2mario/timer/HAS.md),
[interrupts](../../wiki/src/nand2mario/interrupts/HAS.md),
[video](../../wiki/src/nand2mario/video/HAS.md),
[DMA](../../wiki/src/nand2mario/dma/HAS.md),
[storage](../../wiki/src/nand2mario/storage/HAS.md),
[UART loader](../../wiki/src/nand2mario/uart/HAS.md),
[display](../../wiki/src/nand2mario/display/HAS.md),
[joypad](../../wiki/src/nand2mario/joypad/HAS.md),
[serial](../../wiki/src/nand2mario/serial/HAS.md) and
[system](../../wiki/src/nand2mario/system/HAS.md) specifications own their
behavior; `dv/memory/`, `dv/cartridge/`, `dv/timer/`, `dv/interrupts/`, `dv/video/`, `dv/dma/`,
`dv/joypad/`, `dv/serial/`, `dv/storage/`, `dv/uart/` and `dv/system/` hold the Python models and tests.

`fpga/` holds the DE10-Lite board top `n2m_de10_lite.sv` (SDRAM backing and
UART loader on the board pins; the host keyboard through the common
`n2a_jtag_virtual` and `n2a_jtag_keyboard`), its source-list-selected clock
(`n2m_de10_lite_clock_{intel,portable}.sv`) backends, the Set-2-to-joypad
decoder, the board-input debouncer, the seven-segment decoder, the settings
fragment and the SDC; `cfg/n2m_quartus.json` owns the pins and the Quartus
source list, `tools/n2m_quartus.py` and `tools/n2m_board.py` build, program,
load images and send joypad keys, and `dv/fpga/` holds the board smoke
([FPGA HAS](../../wiki/src/nand2mario/fpga/HAS.md)).

## Audio (APU)

`rtl/audio/n2m_apu.sv` implements the full DMG sound register behaviour
(power control, channel status, length counters, wave RAM) but produces **no
audio output**: the system is silent by design, so simulation captures and
board runs have no sound.

## Original menu and library

The optional `CART_LIBRARY=1` system profile runs the pinned original menu,
snapshot window and CRC-checked game switch/return ABI. The default ordinary
cartridge configuration is unchanged. [Menu/library](../../wiki/src/nand2mario/sw/HAS.md)
owns catalogue/provenance, commands, independent pixel scenarios for on-chip
and SDRAM source backings, and the DE2-115 board dependency.

## Simulation quick start

All of these run in Verilator on the host; no board or Quartus is needed.

```sh
# every level0 Mario entry (unit, subsystem and diagram checks)
python3 tools/build/regress.py run --area mario --level level0
# one full-system program test
python3 tools/build/build.py test n2m_system
# re-render the showcase images from game-qualification results
python3 -B src/nand2mario/tools/sim_showcase_render.py --catalog wiki/showcase/nand2mario/catalog.json \
  --results <qualification workdir>/results --output wiki/showcase/nand2mario --source-commit <sha>
```

`src/nand2mario/tools/game_qualification.py run <game>` runs one pinned game
scenario ([#395](https://github.com/amichai-bd/nand2anything/issues/395)).
Compatibility: ROM-only and MBC1 cartridges; Super Mario Land (MBC1) runs in
simulation but needs the owner's [private ROM](sw/roms/private/README.md);
Mooneye acceptance results are in the
[conformance plan](../../wiki/src/nand2mario/conformance/DV.md)
([#441](https://github.com/amichai-bd/nand2anything/issues/441)) and a SameBoy
differential runner is a later step; the DMG boot
ROM is not modelled (cartridges start at 0x0100); the APU is silent.
The [simulation showcase](../../wiki/showcase/nand2mario/README.md) and
[board captures](../../wiki/showcase/nand2mario/board/README.md) show the results.
