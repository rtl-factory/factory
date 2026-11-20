# nand2doom

Bare-metal Doom on our own RV32IM CPU: a six-stage pipelined core with I and D caches, an SDRAM controller, a 320x200x8 double-buffered framebuffer with VGA scanout, UART debug and loading, and PS/2 and JTAG keyboard input, for the DE10-Lite and DE2-115 boards at 25 MHz. Ported from the captain's nand2anything repository (`src/nand2doom` at `aeebe92`, issue #78). Code: `src/nand2doom/`.

## Pages

Each page sits at the wiki path that mirrors its code ([repository layout](../../bkm/workflow/repo-layout.md)). The [HAS](rtl/has.md) owns the block decomposition, address map, clocks and resets, the composition of `n2d_system` and the reuse boundaries with `common`.

| Block | Spec | Verification plan | Code |
|---|---|---|---|
| Platform (`n2d_system`) | [HAS](rtl/has.md) | [DV plan](dv/platform/dv-plan.md) | `rtl/platform/`, `dv/platform/` |
| Core | [MAS](rtl/core/mas.md) | [DV plan](dv/core/dv-plan.md) | `rtl/core/`, `dv/core/` |
| Memory | [MAS](rtl/memory/mas.md) | [DV plan](dv/memory/dv-plan.md) | `rtl/memory/`, `dv/memory/` |
| Video | [MAS](rtl/video/mas.md) | [DV plan](dv/video/dv-plan.md) | `rtl/video/`, `dv/video/` |
| I/O | [MAS](rtl/io/mas.md) | [DV plan](dv/io/dv-plan.md) | `rtl/io/`, `dv/io/` |
| DE10-Lite board (and the contracts both boards share) | [board spec](fpga/de10_lite/board-spec.md) | [DV plan](dv/fpga/dv-plan.md) | `fpga/de10_lite/`, `dv/fpga/` |
| DE2-115 board | [board spec](fpga/de2_115/board-spec.md) | [DV plan](dv/fpga/dv-plan.md) | `fpga/de2_115/`, `dv/fpga/` |
| Runtime and validation image | [PRD](sw/runtime/prd.md), [spec](sw/runtime/spec.md) | [DV plan](dv/software/dv-plan.md) | `sw/runtime/`, `dv/software/` |
| Doom port | [PRD](sw/doom/prd.md), [spec](sw/doom/spec.md) | [DV plan](dv/software/dv-plan.md) | `sw/doom/`, `sw/third_party/`, `dv/software/` |
| Scripts | [scripts](scripts/README.md) | | `scripts/` |

The pages came over from nand2anything (its per-area HAS and DV pages and tool specs, now script specs), with paths rewritten to this layout; links to pages that were not ported (the shared RISC-V ownership page, the build specification, the performance studies, the RTL diagram, the `common` block specs) point at nand2anything.

## Layout

| Code | What it holds |
|---|---|
| `rtl/core/` | `n2d_rv32i_decode_alu`, `n2d_rv32i_core_step`, and the pipelined product core `n2d_rv32im_core` with its stages (`n2d_if`, `n2d_dec`, `n2d_exe`, `n2d_ma`, `n2d_wb`, `n2d_ctrl`, `n2d_csr`, `n2d_muldiv`), packages and `n2d_pmon`. Both cores use the common `n2a_rv32_regfile`. |
| `rtl/memory/` | `n2d_icache`, `n2d_dcache`, `n2d_cached_rv32im`, `n2d_memory_arbiter`, `n2d_memory_controller`, `n2d_sdram_controller`, `n2d_debug_memory_adapter` and `n2d_ls_types_pkg` (the "ls" category of nand2anything). |
| `rtl/video/` | `n2d_framebuffer_vga`: framebuffer, palette and VGA scanout over the common timing block. |
| `rtl/io/` | `n2d_uart_debug`, `n2d_uart_debug_engine`, `n2d_uart_bulk`, `n2d_uart_stream`, `n2d_uart_mem_port` and `n2d_ps2_keyboard`. |
| `rtl/platform/` | `n2d_system` (the product top shared by both boards), `n2d_pkg`, `n2d_types_pkg`, `n2d_debug_device_line_bridge`, `n2d_pmon_platform_events`. |
| `fpga/de10_lite/`, `fpga/de2_115/` | Each board's top, its Intel and portable clock backends, its Quartus settings fragment and SDC. Pins and the source list are in `cfg/n2d_quartus.json` (DE2-115 as overrides under `boards`). |
| `dv/<block>/` | cocotb tests, models and native harnesses; targets in `cfg/targets.json`. |
| `sw/runtime/`, `sw/doom/`, `sw/third_party/` | Software that runs on the design's CPU: the C runtime and validation program, the Doom platform adaptation, and the third-party notices and pinned-input manifest. |
| `scripts/` | Host scripts, one folder each: `sw_build/` (image and Doom builds), `board/` (Quartus build and timing), `host/` (UART and JTAG host, play, controls, capture), `perf/` (performance harness and oracles). |

The ASIC flow (`n2d_asic_soc`, its OpenROAD files and IHP models) stayed in nand2anything, as it did for nand2mario.

## Run it

```sh
# simulation (Verilator); add --level L1 or L2 for the longer targets
python3 tools/sim/sim.py test --project nand2doom --level L0
# script unit tests
python3 -m pytest src/nand2doom/scripts
# Quartus fit and timing, both boards
python3 src/nand2doom/scripts/board/quartus.py --board de10_lite
python3 src/nand2doom/scripts/board/quartus.py --board de2_115
```

The software build needs `riscv64-unknown-elf-gcc` and binutils. The Doom inputs (DoomGeneric, Freedoom, picolibc) are not committed: the build fetches each from its pinned origin, checks its SHA-256 and caches it in `workdir/downloads/nand2doom/`.
