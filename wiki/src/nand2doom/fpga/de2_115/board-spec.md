# DE2-115 board spec

## Board

The Terasic DE2-115 (**EP4CE115F29C7**, Cyclone IV E) top, `src/nand2doom/fpga/de2_115/`. The board contracts both boards share (requirements, composition, memory implementation, timing, the Quartus report gate and the fitted results of both boards) are in the [DE10-Lite board spec](../de10_lite/board-spec.md), the base board of `cfg/n2d_quartus.json`; this page holds what is specific to the DE2-115. The [DV plan](../../dv/fpga/dv-plan.md) owns verification.

The DE2-115 is the primary physical target. The design uses its 50 MHz
oscillator, `KEY[0]` as reset, the low chip of its 32-bit SDRAM pair (the
high chip masked), the ADV7123 VGA DAC, the JP5 GPIO header for the UART at
3 Mbaud, the on-board PS/2 connector, `LEDR[3:0]` for status with the other
LEDs and digits off, and the USB-Blaster for programming and the host JTAG
keyboard. The board-by-board comparison, the requirements both boards meet and
why the shared contracts live on the DE10-Lite page are in the
[DE10-Lite board spec](../de10_lite/board-spec.md#board).

## Top level

`n2d_de2_115` (`src/nand2doom/fpga/de2_115/n2d_de2_115.sv`) wraps `n2d_system`
(full-width framebuffer, four-way D-cache, `UART_BAUD` =
`N2D_UART_LOAD_BAUD`) and `n2d_memory_controller`, with the clock backend
`n2a_de2_115_clock_intel.sv` (`altpll`) in Quartus and
`n2a_de2_115_clock_portable.sv` in Verilator. The composition, the vendor
primitives and their simulation stand-ins and the RAM mapping are shared with
the DE10-Lite ([composition](../de10_lite/board-spec.md#composition), [vendor primitives](../de10_lite/board-spec.md#vendor-primitives),
[memory implementation](../de10_lite/board-spec.md#memory-implementation)). The Quartus script
resolves the DE2-115 from the shared manifest by swapping the clock backend
and the board top.

## Pins and I/O

### Electrical interfaces

`boards.de2_115` in `n2d_quartus.json` owns every DE2-115 pin, standard and
file swap. Pins equal the [nand2linux DE2-115 assignments](https://github.com/amichai-bd/nand2anything/blob/aeebe92/wiki/src/nand2linux/fpga/DE2-115.md#pins);
a unit test compares every shared pin and standard.

| Signal | Pin | Standard |
| --- | --- | --- |
| `CLOCK_50` | Y2 | 3.3-V LVTTL |
| `KEY[0]` (reset) | M23 | 2.5 V |
| `UART_RX` (JP5 pin 1, `GPIO[0]`) | AB22 | 3.3-V LVTTL, weak pull-up |
| `UART_TX` (JP5 pin 2, `GPIO[1]`) | AC15 | 3.3-V LVTTL |
| `PS2_CLK`, `PS2_DAT` | G6, H5 | 3.3-V LVTTL, weak pull-ups |
| `DRAM_CLK` | AE5 | 3.3-V LVTTL |
| `VGA_CLK`, `VGA_BLANK_N`, `VGA_SYNC_N` | A12, F11, C10 | 3.3-V LVTTL |
| `VGA_HS`, `VGA_VS` | G13, C13 | 3.3-V LVTTL |
| `LEDR`, `LEDG`, `HEX0`-`HEX2`, `HEX3[1:0]` | manual tables | 2.5 V |
| `HEX3[6:2]`, `HEX4`-`HEX7` | manual tables | 3.3-V LVTTL |

The JP5 voltage jumper must select 3.3 V (GND on pin 12). The host link is a
3.3 V TTL-232R cable at 3 Mbaud 8-N-1; scripts take `--port` and
`--baud de2_115` ([UART scripts](../../scripts/host/spec.md#uart-host)). SDRAM/VGA
8 mA; UART TX, LEDs and digits 4 mA; slew rate 2 stated on 2.5 V outputs.

## Clocks and constraints

`CLOCK_50` feeds the Cyclone IV E `altpll`; its 25 MHz output is the system
clock, and `DRAM_CLK` and `VGA_CLK` receive it inverted. The constraints both
boards share are in the [DE10-Lite constraints](../de10_lite/board-spec.md#constraints); the
DE2-115 SDC (`src/nand2doom/fpga/de2_115/n2d_de2_115.sdc`) differs as follows:

- `n2d_de2_115.sdc`: the
  same set on `CLOCK_50`, with `sdram_clock` on `DRAM_CLK` and `vga_dac_clock`
  on `VGA_CLK` as inverted `clk[0]`; SDRAM delays apply to `DRAM_DQM[*]`; RGB
  against `vga_dac_clock` with ADV7123 0.2 ns setup / 1.5 ns hold plus 0.5 ns
  skew (max 0.7, min −2.0 ns).

## Budget

Last passing build in the factory (Quartus Prime Lite 25.1std, `python3 src/nand2doom/scripts/board/quartus.py --board de2_115`, issue #78): the full report gate passed.

| Resource or check | Result |
|---|---|
| Logic elements | 29,181 of 114,480 (25%) |
| M9K blocks | 320 of 432 |
| PLLs | 1 of 4 |
| Worst setup slack, any clock and corner | 5.413 ns |
| Minimum slack, any clock, check and corner | 0.123 ns (hold) |

System clock (25 MHz PLL output) slack per corner, in ns:

| Corner | Setup | Hold |
|---|---|---|
| Slow 1200mV 85C | 6.848 | 0.317 |
| Slow 1200mV 0C | 7.402 | 0.324 |
| Fast 1200mV 0C | 9.215 | 0.123 |

Built from commit `c3ed333` (the RTL, settings and constraints are unchanged at the head of #96); 90 reviewed warning occurrences, 86 of them constant outputs (unused LEDs and digits).

The earlier nand2anything fits of this board are in the
[DE10-Lite fitted results](../de10_lite/board-spec.md#fitted-results) and
[timing baseline](../de10_lite/board-spec.md#timing-baseline-and-50-mhz-analysis), beside the
DE10-Lite's.

## Build and program

```sh
python3 src/nand2doom/scripts/board/quartus.py --board de2_115
python3 src/nand2doom/scripts/board/quartus.py --board de2_115 --elaborate-only
```

The full build must pass the shared [report gate](../de10_lite/board-spec.md#report-gate), whose
DE2-115 column lists this board's differences; the elaboration-only check runs
on every RTL change ([tool compatibility](../de10_lite/board-spec.md#tool-compatibility-and-warnings)).

### Bring-up

The [play command](../../scripts/host/spec.md#play) automates:

1. `jtagconfig` lists one `USB-Blaster [1-1]` with device `020F70DD`; JTAG
   scripts [resolve its name by IDCODE](../../scripts/host/spec.md#device-resolution).
2. `quartus_pgm -c "USB-Blaster [1-1]" -m jtag -o "p;n2d_de2_115.sof@1"`
   with an image holding the stream loader and JTAG keyboard.
3. `n2d_uart.py --baud de2_115 --port PORT ping` (`N2D1`), then `halt`.
4. `n2d_uart.py --baud de2_115 --port PORT load MANIFEST --stream --resume`:
   stream, check each segment's SDRAM digest (the play command skips matching
   segments), resume at the entry.
5. Input is ten-bit Set-2 events (Right: make `0x274`, break `0x374`) from the
   [local controls](../../scripts/host/spec.md#local-controls) by UART `CMD_INPUT`
   or JTAG.

Programming resets the design and stops refresh, so it needs a full load. An
interrupted stream needs no reprogramming (partial frame dropped after 40 ms,
session closed after 2 s idle); a killed legacy bulk transfer can latch
quarantine until `KEY[0]` or reprogramming.
