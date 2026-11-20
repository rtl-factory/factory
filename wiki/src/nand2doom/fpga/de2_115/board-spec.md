# nand2doom DE2-115 board spec

!!! note "Ported, not yet in the standard template"
    Ported from nand2anything with paths rewritten (#78). Rearranging it into the [standard template](../../../../bkm/design/standard-docs.md) is #91.

The Terasic DE2-115 (**EP4CE115F29C7**, Cyclone IV E) top, `src/nand2doom/fpga/de2_115/`. The board contracts both boards share (requirements, composition, memory implementation, timing, the Quartus report gate and the fitted results of both boards) are in the [DE10-Lite board spec](../de10_lite/board-spec.md), the base board of `cfg/n2d_quartus.json`; this page holds what is specific to the DE2-115. The [DV plan](../../dv/fpga/dv-plan.md) owns verification.

## Electrical interfaces

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
3.3 V TTL-232R cable at 3 Mbaud 8-N-1; tools take `--port` and
`--baud de2_115` ([UART tools](../../scripts/n2d_uart/spec.md)). SDRAM/VGA
8 mA; UART TX, LEDs and digits 4 mA; slew rate 2 stated on 2.5 V outputs.

## Bring-up

The [play command](../../scripts/n2d_play/spec.md) automates:

1. `jtagconfig` lists one `USB-Blaster [1-1]` with device `020F70DD`; JTAG
   tools [resolve its name by IDCODE](../../scripts/n2d_jtag_keyboard/spec.md#device-resolution).
2. `quartus_pgm -c "USB-Blaster [1-1]" -m jtag -o "p;n2d_de2_115.sof@1"`
   with an image holding the stream loader and JTAG keyboard.
3. `n2d_uart.py --baud de2_115 --port PORT ping` (`N2D1`), then `halt`.
4. `n2d_uart.py --baud de2_115 --port PORT load MANIFEST --stream --resume`:
   stream, check each segment's SDRAM digest (the play command skips matching
   segments), resume at the entry.
5. Input is ten-bit Set-2 events (Right: make `0x274`, break `0x374`) from the
   [local controls](../../scripts/n2d_control/spec.md) by UART `CMD_INPUT`
   or JTAG.

Programming resets the design and stops refresh, so it needs a full load. An
interrupted stream needs no reprogramming (partial frame dropped after 40 ms,
session closed after 2 s idle); a killed legacy bulk transfer can latch
quarantine until `KEY[0]` or reprogramming.

## Budget

Last passing build in the factory (Quartus Prime Lite 25.1std, `python3 src/nand2doom/scripts/quartus.py --board de2_115`, issue #78): the full report gate passed.

| Resource or check | Result |
|---|---|
| Logic elements | 29,181 of 114,480 (25%) |
| M9K blocks | 320 of 432 |
| PLLs | 1 of 4 |
| Worst setup slack, any corner | 5.413 ns at 25 MHz |
| Minimum slack (hold), any check and corner | 0.123 ns |
