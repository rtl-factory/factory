# rtl

The `rtl` discipline of `common`: blocks that every project's design reuses. Code: `src/common/rtl/`.

| Block | Document |
| --- | --- |
| SDRAM controller (`n2a_sdram_ctrl`) | [MAS](sdram/mas.md): one x16 SDRAM controller for every project, configured per project |
| UART board rate and bit period (`n2a_uart_pkg`) | [MAS](uart/mas.md): the factory board rate, 2 Mbaud, and the common PHY's bit period for a clock |
| RAMs (`n2a_sync_ram`, `n2a_sync_ram_wt`, `n2a_sdp_ram`, `n2a_tdp_ram`) | [MAS](memory/mas.md): the on-chip RAM contracts and their portable and Intel backends |

The other blocks' MASes live in nand2anything and are not ported yet.
