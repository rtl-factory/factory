# common

The shared project: blocks and scripts that every project reuses, with no design of its own. Code: `src/common/`.

| Area | What it holds |
|---|---|
| Blocks (`rtl`) | UART receiver and transmitter, the UART memory endpoint, VGA timing, on-chip RAMs, the RV32 register file, clock-domain and reset synchronizers, shared packages and macros, and the JTAG keyboard endpoint |
| [Board parts](fpga/README.md) (`fpga`) | The board clock of each board, used by every project's board top |
| [Scripts](scripts/README.md) | The `scripts` discipline of `common`: host-side scripts bound to its blocks |

!!! note "MASes not ported yet"
    `common` has no HAS: it is a shelf of blocks, not one design. Each block gets a MAS at `rtl/<block>/mas.md` ([standard documents](../../bkm/design/standard-docs.md)); those live in nand2anything and are not ported yet.
