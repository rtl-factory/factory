# nand2doom Doom port: requirements

!!! note "Ported, not yet in the standard template"
    Ported from nand2anything with paths rewritten (#78). Rearranging it into the [standard template](../../../../bkm/design/standard-docs.md) is #91.

The pinned DoomGeneric port (`src/nand2doom/sw/doom/`): its platform adapter, clock, keyboard map, direct framebuffer rendering, engine adaptations and capture mailbox. It builds on the [runtime](../runtime/prd.md) and inherits its requirements SW-R1 to SW-R4.

## Requirements

| ID | Requirement |
| --- | --- |
| SW-R5 | Engine adaptations never change a pixel or game state at matching time; each patch checks its pinned seam and count, is idempotent and fails the build on an ambiguous or modified seam. |
| SW-R6 | Upstream archives, WAD data, libc packages and generated firmware are never committed; missing optional game data skips the opt-in Doom run. |
| SW-R7 | Production firmware uses the cycle tick; only verification variants change the time source. |

