# common

The shared project: blocks and scripts that every project reuses, with no design of its own. Code: `src/common/`.

| Area | What it holds |
|---|---|
| [Blocks](rtl/README.md) (`rtl`) | The SDRAM controller, UART receiver and transmitter, the UART memory endpoint, VGA timing, on-chip RAMs, the RV32 register file, clock-domain and reset synchronizers, shared packages and macros, and the JTAG keyboard endpoint |
| [Board parts](fpga/README.md) (`fpga`) | The board clock of each board, used by every project's board top |
| [Verification](dv/README.md) (`dv`) | Shared simulation models and benches: the SDRAM device model, the SDRAM controller benches and the UART wire helper |
| [Formal](fv/README.md) (`fv`) | Formal proofs of shared blocks: the SDRAM controller |
| [Scripts](scripts/README.md) | The `scripts` discipline of `common`: host-side scripts bound to its blocks |

!!! note "MASes not ported yet"
    `common` has no HAS: it is a shelf of blocks, not one design. Each block gets a MAS at `rtl/<block>/mas.md` ([standard documents](../../bkm/layout.md#standard-documents)); the SDRAM controller and the RAMs have one; the others live in nand2anything and are not ported yet, and their code cites them there at a pinned commit.
