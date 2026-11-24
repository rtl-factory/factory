# UART: board rate and bit period

The common byte PHYs `n2a_uart_rx` and `n2a_uart_tx` (`src/common/rtl/uart/`) carry every project's board UART; their full MAS lives in nand2anything and is not ported yet. This page holds the two facts every project shares, from `n2a_uart_pkg`.

## Board rate

`N2A_UART_BOARD_BAUD` is **2,000,000**: the one UART rate of every project's board image on every board, decided on #129. 2 Mbaud is a rate the common USB-to-UART adapters generate exactly (FT232R, FT2232H, CP2102N, CH340; not the original CP2102); the analysis is nand2doom's [link rate](../../../nand2doom/rtl/io/mas.md#link-rate).

Every board top derives its UART bit period from this constant, and every host default and board declaration equals it: `uart_mem.BOARD_BAUD` in the [common UART memory host](../../scripts/uart_mem/spec.md), each `boards.<board>.baud` of the [board console](../../../../tools/board/spec.md#board-declarations), nand2doom's `cfg/uart.json` and nand2mario's cartridge loader. The L0 unit test `src/common/scripts/uart_mem/tests/test_board_rate.py` fails when any of them differs.

## Bit period

The PHYs take a bit period of `CLKS_PER_BIT + FRAC_NUM / FRAC_DEN` clocks; a nonzero fraction runs an accumulator so the average rate is exact. `n2a_uart_clks_per_bit`, `n2a_uart_frac_num` and `n2a_uart_frac_den` give it for a clock and a rate, with the fraction reduced so the accumulator stays narrow. Both projects run their UART in a 25 MHz domain, so the board rate is 12 1/2 clocks a bit: an integer 12 or 13 would be 4% off, too close to the last-bit sampling edge once the adapter's own error is added.
