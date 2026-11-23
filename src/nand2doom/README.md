# nand2doom (`n2d`)

Bare-metal Doom on our own RV32IM CPU, caches, SDRAM controller and framebuffer, for the DE10-Lite and DE2-115 at 25 MHz. Ported from the captain's nand2anything repository (issue #78). It reuses the `common` UART, UART memory endpoint, VGA timing, RAMs, RV32 register file, synchronizers and JTAG keyboard.

Its documents are in the wiki at the mirrored path, `wiki/src/nand2doom/`: the landing page lists the block specs and verification plans, the layout and how to run it.

| Folder | What it holds |
|---|---|
| `rtl/core/`, `rtl/memory/`, `rtl/video/`, `rtl/io/`, `rtl/platform/` | Product RTL |
| `fpga/de10_lite/`, `fpga/de2_115/` | Board tops, clock backends, settings fragments and SDC |
| `dv/<block>/` | cocotb tests, models and native harnesses |
| `cfg/` | Simulation targets, the Quartus board config and UART settings |
| `sw/` | C runtime, validation program, Doom platform adaptation, third-party notices |
| `scripts/` | Host scripts, one folder per script group (`sw_build/`, `board/`, `host/`, `perf/`), each with its tests |

```sh
python3 tools/sim/sim.py test --project nand2doom --level L0
python3 src/nand2doom/scripts/board/quartus.py --board de10_lite   # or de2_115
```
