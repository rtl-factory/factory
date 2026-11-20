# nand2doom runtime: requirements

!!! note "Ported, not yet in the standard template"
    Ported from nand2anything with paths rewritten (#78). Rearranging it into the [standard template](../../../../bkm/design/standard-docs.md) is #91.

The owned freestanding RV32IM runtime (`src/nand2doom/sw/runtime/`): startup, linker layout, memory primitives, the bounded heap, the performance-monitor header and the validation image that proves the platform before Doom runs.

## Requirements

| ID | Requirement |
| --- | --- |
| SW-R1 | Flags `-march=rv32im -mabi=ilp32 -O2 -ffreestanding`; no host runtime or syscall ABI. |
| SW-R2 | Startup sets the stack, global pointer when needed, copies data, zeroes BSS, then calls `main`. Framebuffer and MMIO windows are never RAM. |
| SW-R3 | Every image has a manifest: architecture, ABI, entry, load segments, lengths, SHA-256, memory-map revision, completion marker. Harness preload and serial UART load of the same segments are separate evidence. |
| SW-R4 | The runtime supplies only what the Doom port needs: startup, memory primitives, SDRAM heap, monotonic ticks, keyboard polling, framebuffer/palette access, completion/failure markers, a read-only game-data view. No filesystem, processes, VM, dynamic linking, sockets, audio or OS. |
| SW-R6 | Upstream archives, WAD data, libc packages and generated firmware are never committed; missing optional game data skips the opt-in Doom run. |

