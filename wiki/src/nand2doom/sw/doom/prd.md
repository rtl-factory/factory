# nand2doom Doom port: requirements

## Problem

The product milestone is Doom on the nand2doom CPU: the pinned DoomGeneric
port with its platform adapter, clock, keyboard map, direct framebuffer
rendering, engine adaptations and capture mailbox. It builds on the
[runtime](../runtime/prd.md) and inherits its requirements SW-R1 to SW-R4.

## Requirements

| ID | Requirement |
| --- | --- |
| SW-R5 | Engine adaptations never change a pixel or game state at matching time; each patch checks its pinned seam and count, is idempotent and fails the build on an ambiguous or modified seam. |
| SW-R6 | Upstream archives, WAD data, libc packages and generated firmware are never committed; missing optional game data skips the opt-in Doom run. |
| SW-R7 | Production firmware uses the cycle tick; only verification variants change the time source. |

## Out of scope

- The runtime services themselves (startup, heap, primitives): the [runtime](../runtime/prd.md).
- Audio and the other services the runtime does not supply (SW-R4).

## Blocks

None: this is software. The hardware it runs on is in the [HAS](../../rtl/has.md).

## Acceptance

Checks are in the [software DV plan](../../dv/software/dv-plan.md#features).

| Requirement | Check |
| --- | --- |
| SW-R5 | Doom build unit tests: seam and idempotence; drawer, wall-loop, persistence and visplane equivalence against pinned upstream with whole-frame CRCs |
| SW-R6 | No automated check is recorded; the opt-in Doom run needs the pinned inputs, fetched into the local download cache |
| SW-R7 | `n2d_doom_clock` checks the production clock helper; the performance harness rejects a manifest of another variant |
