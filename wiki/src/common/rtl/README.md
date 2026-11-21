# rtl

The `rtl` discipline of `common`: blocks that every project's design reuses. Code: `src/common/rtl/`.

| Block | Document |
| --- | --- |
| SDRAM controller (`n2a_sdram_ctrl`) | [MAS](sdram/mas.md): one x16 SDRAM controller for every project, configured per project |

The other blocks' MASes live in nand2anything and are not ported yet.
