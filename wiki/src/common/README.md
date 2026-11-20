# common

The shared project: blocks and tools that every project reuses, with no design of its own. Code: `src/common/`.

| Area | What it holds |
|---|---|
| Blocks (`rtl/`) | UART receiver and transmitter, VGA timing, on-chip RAMs, clock-domain and reset synchronizers, shared packages and macros, and the JTAG keyboard endpoint |
| [Tools](tools/README.md) | Project tools of `common`: host-side tools bound to its blocks |

!!! note "Block specs not ported yet"
    The specs of the `common` blocks live in nand2anything and are not ported yet.
