# DE10-Lite board spec

## Board

The Terasic DE10-Lite (**10M50DAF484C7G**, MAX 10), with the board top
`n2d_de10_lite` (`src/nand2doom/fpga/de10_lite/`). The design uses its 50 MHz
oscillator, KEY0 as reset, the one IS42S16320D 16-bit SDRAM, the 4-bit
resistor VGA DAC, Arduino D0/D1 for the UART, GPIO0/1 for an external PS/2
connector, `LEDR[3:0]` for status, and the USB-Blaster for the host JTAG
keyboard ([board contracts](#board-contracts)).

Two thin board tops bind the portable [platform](../../rtl/has.md)
`n2d_system` and `n2d_memory_controller` to the Terasic DE10-Lite
(**10M50DAF484C7G**, MAX 10) and the Terasic DE2-115 (**EP4CE115F29C7**,
Cyclone IV E, the primary physical target). Both run at 25 MHz and share the
source closure, address map, serial protocol and RAM roles; only board files
differ. This page is the DE10-Lite board spec and owns the contracts both boards share, the way `cfg/n2d_quartus.json` makes the DE10-Lite the base board and the DE2-115 a set of overrides; the [DE2-115 board spec](../de2_115/board-spec.md) holds what is specific to the DE2-115. The [DV plan](../../dv/fpga/dv-plan.md) owns verification.

### Why this page holds the shared contracts

Both board tops share one source closure, one Quartus script (`quartus.py`)
with one report gate, and one config, `cfg/n2d_quartus.json`, in which the
DE10-Lite is the base board and the DE2-115 a set of overrides under
`boards.de2_115`. The shared contracts (the requirements, the board contracts
table, the composition, the memory implementation, the constraints both SDCs
share, the tool rules and report gate, and the earlier fits of both boards)
therefore have one home, here on the base board's page, as the config has;
splitting them would give each fact two copies. The DE2-115 page holds what
only the DE2-115 has (its pins and electrical rules, its SDC differences, its
budget and its bring-up) and links here for the rest. The script tests also
read the FPGA timing tables from this page (`src/nand2doom/scripts/tests/test_n2d_timing.py`).

<!-- wiki-budget: the Quartus report gate (warning, RAM, clock and timing contract) and recorded fits are acceptance criteria the Quartus script and tests enforce -->

### Requirements

| ID | Requirement |
| --- | --- |
| FPGA-R1 | The hardware closure holds the RV32IM platform, both caches, the framebuffer and the SDRAM controller; no fast-memory, preload or fault-injection module. |
| FPGA-R2 | 50 MHz oscillator → PLL divide-by-two → 25 MHz system, VGA and SDRAM clock. SDRAM receives the inverted system clock. |
| FPGA-R3 | Reset (board key or loss of PLL lock) asserts asynchronously through `n2a_reset_sync` and releases on a 25 MHz edge; `n2d_system` stays reset until SDRAM initialization (≥200 µs) completes; loading starts with the CPU halted at PC 0. |
| FPGA-R4 | `LEDR[0]` PLL locked, `[1]` SDRAM initialized, `[2]` CPU halted, `[3]` platform reset. |
| FPGA-R5 | VGA is RGB444 on an 800x525 raster. Framebuffer: half width on the DE10-Lite (`FRAMEBUFFER_FULL_WIDTH=0`), full width on the DE2-115 (`=1`). |
| FPGA-R6 | Each board's [Quartus command](https://github.com/amichai-bd/nand2anything/blob/aeebe92/wiki/tools/build/SPEC.md#quartus-hardware-build) completes synthesis, fit, timing and assembly with the [report gate](#report-gate) passing; outputs stay under `workdir/`; at most two compile workers. |
| FPGA-R7 | Every RTL change needs a new fitted closure and SOF; an older image is not evidence. Verilator needs no vendor libraries; simulation and compilation never claim physical operation; programming needs separate authorization. |

### Board contracts

| Area | DE10-Lite (`n2d_de10_lite`) | DE2-115 (`n2d_de2_115`) |
| --- | --- | --- |
| Clock | `MAX10_CLK1_50` → MAX 10 `altpll` | `CLOCK_50` → Cyclone IV E `altpll` |
| Reset | KEY0 (`RESET_N`), Schmitt trigger | `KEY[0]`, 2.5 V (board debounced) |
| SDRAM | One IS42S16320D, 16-bit | Low chip of the 32-bit pair: `DQ[15:0]`, `DRAM_DQM = {2'b11, dqm}` (high chip masked, never drives); `DQ[31:16]` unassigned; same 64 MiB map |
| VGA | 4-bit resistor DAC | ADV7123: channels `{c, c}`; `VGA_CLK` = inverted system clock (RGB latched half a pixel after launch); `VGA_BLANK_N`=1, `VGA_SYNC_N`=0 |
| UART | Arduino D0/D1 (AB5/AB6), 1 Mbaud | JP5 `GPIO[0]` RX / `GPIO[1]` TX, 3 Mbaud ([link rate](../../rtl/io/mas.md#link-rate)) |
| PS/2 | GPIO0/1 (V10/W10), external connector | On-board connector |
| Status | `LEDR[3:0]` | `LEDR[3:0]`; `LEDR[17:4]`, `LEDG`=0, `HEX0`-`HEX7`=1 (off) |
| Framebuffer | Half width, 128 M9Ks | Full width, 256 M9Ks |
| D-cache | Two ways | Four ways |

Keyboard input on both boards is PS/2 and the host
[JTAG keyboard](../../rtl/io/mas.md#jtag-keyboard) through the unified Set-2 FIFO;
each board top binds the common `n2a_jtag_virtual` to the endpoint inside
`n2d_system`.

## Top level

`n2d_de10_lite` wraps `n2d_system` and `n2d_memory_controller` and binds them
to the board pins.

### Composition

- The common board clocks `n2a_de10_lite_clock` / `n2a_de2_115_clock` ([board clock](../../../common/fpga/de10_lite/board-spec.md)) each have two backend files,
  never compiled together: `*_intel.sv` (Quartus list) instantiates `altpll`
  `u_pll` (20,000 ps reference, multiply 1, divide 2, 50% duty, zero phase,
  dynamic phase/scan ports unused), and `*_portable.sv` (Verilator) divides by
  two and reports lock three reference edges after reset release. Tool tests
  reject a list with both or neither.
- `n2d_memory_controller` gets the synchronized reset and three owner ports
  (instruction, data, debug); the instruction write payload is unused.
  Observation outputs have no pins (local Verilator empty-port waiver).
- The board top binds SDRAM pins by continuous assignment: command, address,
  bank and DQM from the controller; `DRAM_CLK` the inverted system clock;
  `DRAM_DQ` the single resolved `tri` bus, driven only while output enable is
  high. No pin adapter module. Controller states and mapping follow the
  [memory contract](../../rtl/memory/mas.md#sdram-controller).
- The Quartus script resolves the DE2-115 from the shared manifest by swapping
  the clock backend and the board top.

### Vendor primitives

| Primitive | Where | Simulation stand-in |
| --- | --- | --- |
| `altpll` (`u_pll`) | `n2a_de10_lite_clock_intel.sv` | `n2a_de10_lite_clock_portable.sv`: divide by two, lock three reference edges after reset release |
| `altsyncram` | `n2a_sync_ram_intel.sv`, `n2a_sdp_ram_intel.sv`, `n2a_tdp_ram_intel.sv` (Quartus list) | the portable common RAMs (simulation list) |
| `sld_virtual_jtag` | inside the common `n2a_jtag_virtual` (`u_jtag_virtual`) | the common `sld_virtual_jtag` stub |

The Quartus and simulation source lists in `n2d_quartus.json` never name both
backends of a pair ([tool compatibility](#tool-compatibility-and-warnings)).

### Memory implementation

| Array | Geometry | DE10-Lite M9Ks | DE2-115 M9Ks | Mode |
| --- | --- | ---: | ---: | --- |
| I-cache ways (tag+line) | 2 × 512x141 | 16 | 16 | Single port |
| D-cache data ways | 2 (4) × 512x128 | 16 | 32 | Simple dual port |
| D-cache metadata | 512x31 (512x63) | 2 | 4 | Simple dual port |
| Framebuffer CPU copies | 4 (8) × 16,384x8 | 64 | 128 | Single port |
| Framebuffer scan copies | 4 (8) × 16,384x8 | 64 | 128 | Simple dual port |
| Bulk UART staging | 4096x8 | 4 | 4 | Single port |
| Stream payload | 512x32 | 2 | 2 | Simple dual port |
| Fetch BTB | 1024x36 | 4 | 4 | Simple dual port |
| Palette (`u_palette_low` 512x8, `u_palette_high` 512x4) | `n2a_tdp_ram` | 2 | 2 | True dual port |
| **Total of device** | | **174 / 182** | **320 / 432** | |

- The D-cache metadata RAM (tags, valid, dirty, PLRU) is separate because
  maintenance reads a different set than it writes; each metadata bank has
  one decoded write port per cycle. Store merging is outside the RAM port.
  Maintenance prefetches the next set while retiring way one.
- Valid/dirty/replacement bits, the register file, small debug queues and
  the reset-black palette registers are flops. No capacity or interface is
  reduced to fit.
- Framebuffer lanes `g_framebuffer[0..3]` (DE2-115 `[0..7]`, bank 0 first)
  each have a single-port CPU copy and an SDP scan copy with identical
  writes ([video](../../rtl/video/mas.md)).
- `ram_requirements` in `n2d_quartus.json` gates every SP/SDP array by
  instance path (`<consumer>|n2a_sync_ram:<instance>|altsyncram:u_ram` or
  `<consumer>|n2a_sdp_ram:<instance>|n2a_sdp_ram_enabled:u_enabled|altsyncram:u_ram`),
  mode, geometry and `single_port`; the DE2-115 entry overrides the counts.
  The palette is covered by whole-table accounting. The RAM report `Type`
  column is not gated.

### BTB storage

The [fetch BTB](../../rtl/core/mas.md#fetch-branch-target-buffer) is one
`n2a_sdp_ram` (`u_if|n2a_sdp_ram:g_btb.u_table`), 1024x36 in four SDP M9Ks
with old-data read-during-write; a reset sweep clears valid bits, so no
per-entry flops remain. `AUTO_RAM_RECOGNITION OFF` is the way to stop Quartus
copying a small register array into an M9K (Quartus 25.1 ignores
`ramstyle "logic"`). The [capacity study](https://github.com/amichai-bd/nand2anything/blob/aeebe92/wiki/src/nand2doom/performance-btb-capacity-364.md)
records the 1024-entry fits. The 256-entry introduction
([#334](https://github.com/amichai-bd/nand2anything/issues/334), base
`ff6a5f2`, seed 1) replaced a 16-entry register BTB: DE10-Lite 25,796 → 25,354
LEs, 11,712 → 11,089 registers, 1,979 → 1,894 LABs, 171 M9Ks, setup 4.728 →
4.432 ns, hold 0.150 → 0.044 ns; DE2-115 27,958 → 27,496 LEs, 12,379 → 11,756
registers, 2,114 → 2,013 LABs, 317 M9Ks, setup 6.341 → 6.759 ns, hold 0.125 →
0.130 ns. Worst setup paths are SDRAM `DRAM_DQ` captures, not BTB paths.

### Files

| Path | Role |
| --- | --- |
| `src/nand2doom/fpga/de10_lite/n2d_de10_lite.sv`, `src/nand2doom/fpga/de2_115/n2d_de2_115.sv` | Board tops |
| `src/common/fpga/de10_lite/n2a_de10_lite_clock_{intel,portable}.sv`, `src/common/fpga/de2_115/n2a_de2_115_clock_{intel,portable}.sv` | Clock backends (common, [DE10-Lite](../../../common/fpga/de10_lite/board-spec.md), [DE2-115](../../../common/fpga/de2_115/board-spec.md)) |
| `src/nand2doom/cfg/n2d_quartus.json` | Pins, sources, RAM requirements, reviewed diagnostics |
| `src/nand2doom/fpga/de10_lite/de10_lite.sdc`, `src/nand2doom/fpga/de2_115/n2d_de2_115.sdc` | Constraints |

## Pins and I/O

### DE10-Lite electrical interfaces

The top level of `n2d_quartus.json`
owns every DE10-Lite pin and the source manifest. UART and PS/2 are **3.3 V
logic**; a 5 V keyboard needs level conversion; the board has no native PS/2
connector or USB-UART. RX and PS/2 inputs have weak pull-ups. KEY0 uses
Schmitt trigger, other pins 3.3-V LVTTL. SDRAM and VGA outputs 8 mA; UART TX
and LEDs 4 mA. Unused pins are inputs. Pins follow the Terasic DE10-Lite User
Manual tables 3-2, 3-3, 3-5, 3-7, 3-8, 3-10 and 3-12
([manual](https://e2e.ti.com/cfs-file/__key/communityserver-discussions-components-files/73/DE10_2D00_Lite_5F00_User_5F00_Manual.pdf)).

The DE2-115 pins are in the [DE2-115 board spec](../de2_115/board-spec.md#electrical-interfaces).

## Clocks and constraints

`MAX10_CLK1_50` feeds the `altpll` (divide by two) whose 25 MHz output is the
system, VGA and SDRAM clock; the SDRAM receives it inverted (FPGA-R2).

### Constraints

- `de10_lite.sdc`: 50 MHz
  input, derived PLL clocks and uncertainty, inverted SDRAM clock at its pin.
  After deriving, `get_clock_info -targets` must resolve exactly one clock,
  one target and one DRAM clock port before fitting.
- SDRAM: [IS42S16320D](https://www.issi.com/WW/pdf/42-45R-S_86400D-16320D-32160D.pdf)
  CAS-two tAC 6 ns, tOH 2.5 ns, tIS 1.8 ns, tIH 0.8 ns, plus 1 ns board
  allowance (skew and forwarded-clock variation): DQ input delay max 7 / min
  1.5 ns; command/data output max 2.8 / min −1.8 ns. These are assumptions,
  not measured delays. CL2 beat zero launches after device edge n+1 plus tAC
  and is captured on the next controller rising edge.
- DE10-Lite `DRAM_CLK` is fixed at L14, bank 6 I/O/DPCLK3
  ([pin table](https://cdrdv2-public.intel.com/748245/10m50da.pdf)), not a
  PLL output, so the inverted clock reaches it through fabric.
- JTAG (both boards): remove the vendor hub's asynchronous TCK group, create
  `jtag_tck` (100 ns) on `altera_reserved_tck`, time the mailbox buses (80 ns
  and 100 ns max, 0 ns min), cut only the first synchronizer stages, bound
  `altera_reserved_tdi/tms/tdo`. A missing collection fails
  ([JTAG composition](../../rtl/io/mas.md#jtag-keyboard-composition)).
- UART RX, PS/2 and the reset key are asynchronous into synchronizers;
  synchronizer stage-to-stage paths are not cut. VGA, UART TX, LEDs and
  digits use a 10 ns output budget against the system clock, zero hold.
- The Quartus launcher deadline (1800 s DE10-Lite, 5400 s DE2-115, for
  compile and audit) is owned by the
  [build specification](https://github.com/amichai-bd/nand2anything/blob/aeebe92/wiki/tools/build/SPEC.md#quartus-hardware-build).

The DE2-115 SDC differences are in the [DE2-115 board spec](../de2_115/board-spec.md#clocks-and-constraints).

## Budget

Last passing build in the factory (Quartus Prime Lite 25.1std, `python3 src/nand2doom/scripts/board/quartus.py --board de10_lite`, issue #78): the full report gate passed with 7 reviewed warning occurrences.

| Resource or check | Result |
|---|---|
| Logic elements | 27,241 of 49,760 (55%) |
| M9K blocks | 174 of 182 (8 left) |
| PLLs | 1 of 4 |
| Minimum slack, any clock, check and corner | 0.141 ns |

System clock (25 MHz PLL output) slack per corner, in ns:

| Corner | Setup | Hold |
|---|---|---|
| Slow 1200mV 85C | 4.263 | 0.324 |
| Slow 1200mV 0C | 4.978 | 0.283 |
| Fast 1200mV 0C | 8.493 | 0.155 |

Built from commit `c3ed333` (the RTL, settings and constraints are unchanged at the head of #96).

The earlier fits of both boards, from nand2anything, follow.

### Fitted results

Built by `quartus.py --board de2_115|de10_lite`, Quartus Prime 25.1std.0
Build 1129 Lite, fitter seed 1, 25 MHz, full gate passed. Sources: DE2-115
`c506a56`, DE10-Lite `939c653` (byte-identical RTL, constraints, settings).
`n2d_timing.py --board BOARD --mhz 25 --sta-only --build-dir workdir/quartus/n2d_BOARD`
regenerates the timing tables. Current 1024-entry BTB fits are in the
[capacity study](https://github.com/amichai-bd/nand2anything/blob/aeebe92/wiki/src/nand2doom/performance-btb-capacity-364.md#resource-cost).

| Resource | DE2-115 (EP4CE115F29C7) | DE10-Lite (10M50DAF484C7G) |
| --- | ---: | ---: |
| Logic elements | 51,349 / 114,480 (45%) | 42,879 / 49,760 (86%) |
| Combinational functions | 47,594 (42%) | 39,568 (80%) |
| Dedicated logic registers | 24,026 (21%) | 20,351 (41%) |
| LABs | 4,022 / 7,155 (56%) | 2,979 / 3,110 (96%) |
| M9K blocks | 315 / 432 (73%) | 169 / 182 (93%) |
| Memory bits | 2,579,680 / 3,981,312 (65%) | 1,386,720 / 1,677,312 (83%) |
| Embedded 9-bit multipliers | 8 / 532 | 8 / 288 |
| PLLs | 1 / 4 | 1 / 4 |
| Pins | 159 / 529 | 63 / 360 |

M9K counts predate the palette arrays and the fetch BTB (one block was a
Quartus copy of the former 16x14 BTB tag array). The eight multiplier
elements are the RV32M 33x33 product in four 18x18 multipliers with operand
registers packed in. SOF sha256:
`a0e92a8365a192a5d119fcd24708bfcdc36c96092b58ae18bfe634e5fa424995` (DE2-115),
`ee22eb02a4d95a682567e944cf218af66281408b9ac31bd4a808f4b8888c4e09` (DE10-Lite).

| Board | Corner | System clock Fmax | Setup | Hold | Recovery | Removal |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| DE2-115 | Slow 1200 mV 85 C | 32.10 MHz | 6.265 ns | 0.359 ns | 15.293 ns | 1.187 ns |
| DE2-115 | Slow 1200 mV 0 C | 35.08 MHz | 7.179 ns | 0.336 ns | 15.623 ns | 1.090 ns |
| DE2-115 | Fast 1200 mV 0 C | 62.20 MHz | 9.305 ns | 0.152 ns | 17.553 ns | 0.563 ns |
| DE10-Lite | Slow 1200 mV 85 C | 31.89 MHz | 2.329 ns | 0.264 ns | 12.530 ns | 1.070 ns |
| DE10-Lite | Slow 1200 mV 0 C | 34.08 MHz | 3.250 ns | 0.227 ns | 12.961 ns | 0.992 ns |
| DE10-Lite | Fast 1200 mV 0 C | 64.72 MHz | 7.480 ns | 0.121 ns | 16.292 ns | 0.465 ns |

Worst over every clock including `jtag_tck`. Worst SDRAM margins over all
corners: DE2-115 read setup 7.065, read hold 22.700, write setup 6.265, write
hold 20.564 ns, VGA DAC setup ≥11.846 ns; DE10-Lite read setup 2.329 (its
overall worst path, through the L14 fabric route), read hold 23.873, write
setup 7.862, write hold 19.003 ns.

### Timing baseline and 50 MHz analysis

Source `4fc59c7` (nand2doom and common RTL identical to `1ce8014`), Quartus
25.1std.0 Build 1129 Lite, seed 1. 25 MHz builds pass the full gate:
DE10-Lite 25,615 LEs (51%), 1,919 LABs (62%), 171/182 M9Ks; DE2-115 27,886
LEs (24%), 2,113 LABs (30%), 317/432 M9Ks. The 50 MHz rows are a scratch
what-if (`n2d_timing.py --board BOARD --mhz 50`, 20 ns refit); shipped
constraints stay at 25 MHz ([timing analysis](https://github.com/amichai-bd/nand2anything/blob/aeebe92/wiki/tools/build/SPEC.md#timing-analysis)).
Fmax covers only same-clock paths, so SDRAM transfers to `sdram_clock` are
excluded; setup slack is the worst per latch clock including I/O.

| Board, fit | Corner | System Fmax | System setup | `sdram_clock` setup | `vga_dac_clock` setup | `jtag_tck` setup |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| DE10-Lite, 25 MHz | Slow 85 C | 34.88 MHz | 4.806 ns | 9.831 ns | | 27.449 ns |
| DE10-Lite, 25 MHz | Slow 0 C | 37.60 MHz | 5.447 ns | 10.396 ns | | 28.266 ns |
| DE10-Lite, 25 MHz | Fast 0 C | 63.91 MHz | 8.688 ns | 13.689 ns | | 33.538 ns |
| DE2-115, 25 MHz | Slow 85 C | 34.83 MHz | 5.601 ns | 5.911 ns | 9.587 ns | 26.242 ns |
| DE2-115, 25 MHz | Slow 0 C | 38.04 MHz | 6.267 ns | 6.947 ns | 10.279 ns | 27.330 ns |
| DE2-115, 25 MHz | Fast 0 C | 64.81 MHz | 8.467 ns | 11.041 ns | 14.511 ns | 32.375 ns |
| DE10-Lite, 50 MHz what-if | Slow 85 C | 47.79 MHz | -4.353 ns | 1.528 ns | | 28.850 ns |
| DE10-Lite, 50 MHz what-if | Slow 0 C | 52.02 MHz | -3.737 ns | 1.917 ns | | 29.550 ns |
| DE10-Lite, 50 MHz what-if | Fast 0 C | 68.98 MHz | -0.842 ns | 4.222 ns | | 34.231 ns |
| DE2-115, 50 MHz what-if | Slow 85 C | 47.13 MHz | -2.319 ns | -1.016 ns | 2.320 ns | 27.273 ns |
| DE2-115, 50 MHz what-if | Slow 0 C | 51.45 MHz | -1.855 ns | -0.272 ns | 2.846 ns | 28.258 ns |
| DE2-115, 50 MHz what-if | Fast 0 C | 69.21 MHz | -0.381 ns | 2.737 ns | 5.740 ns | 32.944 ns |

At Slow 85 C every worst system-clock path is one of two combinational cones
(18 to 25 levels, 27.7 to 28.7 ns, 67% to 72% routing on the 30 worst). Of
the 2,000 worst endpoints at 25 MHz all but one start in Q102H or the I-cache
request register.

| Cause | Chain | DE10-Lite, 25 MHz fit | DE2-115, 25 MHz fit | 50 MHz what-if (DE10-Lite / DE2-115) |
| --- | --- | ---: | ---: | ---: |
| A. Q102H operand to fetch request | `slot_Q102H.instruction`, rs1 forwarding, effective-address adder, system MMIO decode (framebuffer, video, keyboard), `data_request_ready`, `advance_Q102H`, late flush, RAS pop/push, IF `request_accept`, to `u_icache` `request_address`, IF `pc`/`outstanding_pc`, PMON `pending`, `minstret`, divider, BTB/RAS arrays, keyboard FIFO and debug engine | 11.334 ns, 25 levels (PMON `pending`); 11.683 ns, 22 levels (I-cache request) | 12.533 ns, 18 levels (I-cache request); 13.098 ns, 20 levels (PMON `pending`) | -0.927 / -1.216 ns |
| B. I-cache hit to fetch request | `u_icache` `request_address` (M9K read), way compare and `selected_hit_way`, bypassed `cpu_response_data`, IF return/branch predecode (`head_return`, `predicted_taken`), queue valid, `request_accept`, back to `request_address`, `pc` and `outstanding_pc` | 11.723 ns, 22 levels | 11.289 ns, 24 levels | -0.066 / 0.449 ns |
| C. SDRAM DQ read capture | `DRAM_DQ` input (7 ns external delay after inverted `DRAM_CLK`) to `u_sdram` `read_data`, a half-cycle relationship | 4.806 ns | 5.601 ns | -4.353 / -2.319 ns |
| D. SDRAM command and data outputs | `u_sdram` `state`/`beat` decode to `DRAM_ADDR`/`DRAM_WE_N`/`DRAM_CAS_N`/`DRAM_DQ`, half cycle to `sdram_clock` | 9.831 ns | 5.911 ns | 1.528 / -1.016 ns |

Near-zero 50 MHz paths under cause A: D-cache prefetch match to the arbiter
and SDRAM command decode (14.062 / 15.674 ns at 25 MHz; 0.385 / 0.819 ns at
50), Q102H to the framebuffer scan-RAM write enable (14.365 / 16.560; 0.626 /
0.788), palette to `VGA_G` (0.384 ns DE10-Lite), RV32M product to the cone
(0.059 / 0.042). The multiplier itself (3 levels, 9.2 to 9.4 ns) has 28 ns of
slack at 25 MHz. Causes C and D are fixed after the mid-cycle inverted launch,
so their period limit is `2 x (half-period - slack)`:

| Board | Same-clock Fmax (25 / 50 MHz fit) | DQ capture limit | SDRAM output limit | Implied maximum system clock |
| --- | ---: | ---: | ---: | --- |
| DE10-Lite | 34.9 / 47.8 MHz | 32.9 / 34.8 MHz | 49.2 / 59.0 MHz | 32.9 MHz as built; ~35 MHz refit; DQ capture limits |
| DE2-115 | 34.8 / 47.1 MHz | 34.7 / 40.6 MHz | 35.5 / 45.4 MHz | 34.7 MHz as built; ~40 MHz refit; DQ capture, then core and SDRAM outputs |

50 MHz needs, per [#77](https://github.com/amichai-bd/nand2anything/issues/77):
(1) phase-shifted SDRAM clocking with full-cycle or tuned DQ capture and I/O
registers (capture lands 12.3 to 14.4 ns after launch against a 10 ns half
period); (2) a registered data address and MMIO decode for cause A and (3) a
registered fetch address or queue-head-only prediction for cause B, the split
cache timing of [#124](https://github.com/amichai-bd/nand2anything/issues/124),
together 1 to 1.3 ns at Slow 85 C; (4) the near-zero paths and 10 ns output
budgets.

## Build and program

```sh
python3 src/nand2doom/scripts/board/quartus.py --board de10_lite
python3 src/nand2doom/scripts/board/quartus.py --board de10_lite --elaborate-only
```

The full build must pass the [report gate](#report-gate) (FPGA-R6); the
elaboration-only check runs on every RTL change ([tool compatibility](#tool-compatibility-and-warnings)).
Outputs stay under `workdir/`. No programming or bring-up procedure is
written for the DE10-Lite; the DE2-115 is the primary physical target
([DE2-115 bring-up](../de2_115/board-spec.md#bring-up)), and programming needs
separate authorization (FPGA-R7).

### Tool compatibility and warnings

Quartus Standard needs explicit generate blocks and separate genvars; a type
inside a generate block may not size itself from a generate-local parameter
(use plain sized signals); parameter checks use `ASSERT_INITIAL`, not an
elaboration `$error` in a generate block; enum membership uses equality/OR.
Verilator accepts violations, so every nand2doom RTL change runs
`quartus.py --board de10_lite|de2_115 --elaborate-only` (analysis and
elaboration, ~25 s and 0.8 GB each), registered as level1 entries
`check:doom:quartus-elaborate:de10-lite` and `check:doom:quartus-elaborate:de2-115`.
Its gate allows each reviewed unread object, the exact connectivity aggregate
(with its complete 21-row report) and the LogicLock advisory at most once,
bounds generated discovery as below, and rejects fitted-only diagnostics. It
is not fit or timing evidence.

- The QSF defines `SYNTHESIS=1` and `DISABLE_ASSERTIONS=1`; the latter
  removes only the two D-cache stable-request assertion history flops.
  Verilator never selects it.
- Four discarded-field reductions use lint-only `UNUSED_BITS`.
- The Quartus list selects `n2a_sync_ram_intel.sv`, `n2a_sdp_ram_intel.sv`,
  `n2a_tdp_ram_intel.sv` and the board `*_clock_intel.sv`, never both of a
  pair. Both lists carry `n2a_jtag_virtual.sv`; simulation adds the
  `sld_virtual_jtag` [stub](https://github.com/amichai-bd/nand2anything/blob/aeebe92/wiki/src/common/jtag/HAS.md#architecture).
- Synchronizers use `SYNCHRONIZER_IDENTIFICATION FORCED`; `ASYNC_REG` is
  omitted.

### Report gate

The build keeps unmodified compile/audit logs and every warning occurrence,
in order, in `evidence.json`; `warning_summary` records stage, totals, counts
per code and category, and each semantic identity's min/max/observed count.
Unknown, critical, duplicate, missing or changed diagnostics fail; source
lines and staging prefix may move, but ownership, object, pin/value, PLL
hierarchy and routing endpoint may not. `reviewed_unused_objects` is empty
(every 10036 fails). A full compile requires exactly one each of 13024,
12241, 292013, 15064 and 169177, plus one 13410 per configured constant pin
(two DE10-Lite, 85 DE2-115): totals 7 and 90 before optional discovery.

| Diagnostic | DE10-Lite interpretation | DE2-115 delta |
| --- | --- | --- |
| 12125 | Optional generated discovery: each `db/<entity>.tdf` at most once, one `Source Files Read` row `yes` / `Auto-Generated Megafunction`, a real generated file with matching primitive header and sole `SUBDESIGN`. Families: one `altpll`, ten `altsyncram`, two `decode`, one `mux`. | Same |
| 12241 | Only after validating every connectivity warning against the Virtual JTAG contract below. | Same |
| 13024/13410 | Only `DRAM_CKE=1`, `DRAM_CS_N=0`. | Also `DRAM_DQM[3:2]=1`, `VGA_BLANK_N=1`, `VGA_SYNC_N=0`, `LEDR[17:4]=0`, `LEDG=0`, `HEX0`-`HEX7=1`. |
| 292013 | Lite has no LogicLock; none is used. | Same |
| 169177/169178 | 21 pins: `DRAM_DQ[15:0]`, `MAX10_CLK1_50`, `RESET_N` (`3.3 V SCHMITT TRIGGER`), `UART_RXD`, `PS2_CLK`, `PS2_DAT` (3.3-V LVTTL). | 20 pins: `DRAM_DQ[15:0]`, `CLOCK_50`, `UART_RX`, `PS2_CLK`, `PS2_DAT`. |
| 15064 | Only PLL `clk[0]` to `DRAM_CLK` through L14 fabric. | Only PLL `clk[0]` to `VGA_CLK`; none for `DRAM_CLK`. |
| 15714 | Must not occur: every output has explicit drive. | Same |

AN447: exactly one summary followed by the complete 169178 detail set; each
child names a configured port, pad, standard (compared case- and
whitespace-insensitively) and board-top source. This validates report
identity, not voltage or physical safety.

`reviewed_connectivity`: the sole hierarchy
`n2a_jtag_virtual:u_jtag_virtual|sld_virtual_jtag:u_vjtag`
with exactly 21 unconnected outputs: `virtual_state_e1dr`, `virtual_state_pdr`,
`virtual_state_e2dr`, `virtual_state_cir`, `tms`, `jtag_state_tlr`,
`jtag_state_rti`, `jtag_state_sdrs`, `jtag_state_cdr`, `jtag_state_sdr`,
`jtag_state_e1dr`, `jtag_state_pdr`, `jtag_state_e2dr`, `jtag_state_udr`,
`jtag_state_sirs`, `jtag_state_cir`, `jtag_state_sir`, `jtag_state_e1ir`,
`jtag_state_pir`, `jtag_state_e2ir`, `jtag_state_uir`. Each row is type
`Output`, severity `Warning`, detail `Declared by entity but not connected by
instance. Logic that only feeds a dangling port will be removed.` The parser
requires the four-column borders and full semicolon framing; any deviation,
or the aggregate without its validated rows, fails.

Timing gate, at Slow 1200 mV 85 C, Slow 1200 mV 0 C and Fast 1200 mV 0 C:

- system clock setup/hold/recovery/removal and minimum pulse width,
  forwarded SDRAM (and DE2-115 VGA) clock setup/hold, reference minimum pulse
  width; missing, negative or truncated results fail; no ignored SDC;
- `check_timing` allows only the forwarded-clock output-with-clock entries,
  no virtual clocks, and the single vendor
  `u_clock|u_pll|auto_generated|pll_lock_sync` no-clock entry (a lock-event
  qualifier into the common reset synchronizer); unconstrained-path counts
  are zero;
- twelve nonempty SDRAM read/write setup/hold path reports with zero
  violations, consistent margins, controller/DRAM endpoints and expected
  clocks (a DE2-115 write may end on `DRAM_DQM[*]`);
- the Clocks table holds only the 20 ns reference, the 40 ns PLL clock, the
  generated SDRAM clock (PLL master, `DRAM_CLK` sole target, inverted,
  rise/fall 20/40 ns), the DE2-115 `vga_dac_clock` likewise on `VGA_CLK`,
  and the 100 ns `jtag_tck`.

RAM gate: the [memory table](#memory-implementation) per array with both
port geometries, mode and Single Clock. I-cache, framebuffer CPU and staging
need Single Port with Port A RDW `Old data`; D-cache, framebuffer scan, stream
payload and BTB need Simple Dual Port with Mixed Width RDW `Old data`. Unused
RDW fields may report `Don't care` or `New data with NBE Read`.
