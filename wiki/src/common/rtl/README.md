# rtl

The `rtl` discipline of `common`: blocks that every project's design reuses. Code: `src/common/rtl/`.

| Block | Document |
| --- | --- |
| SDRAM controller (`n2a_sdram_ctrl`) | [MAS](sdram/mas.md): one x16 SDRAM controller for every project, configured per project |
| PS/2 keyboard (`n2a_ps2_receiver`, `n2a_ps2_decoder`) | [MAS](ps2/mas.md): PS/2 pins to bytes, and Set-2 bytes to key events, for every keyboard path |

The other blocks' MASes live in nand2anything and are not ported yet.
