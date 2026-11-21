# nand2doom runtime: requirements

## Problem

Software on the nand2doom CPU needs an owned freestanding RV32IM runtime:
startup, linker layout, memory primitives, the bounded heap, the
performance-monitor header and the validation image that proves the platform
before Doom runs. The [Doom port](../doom/prd.md) builds on it.

## Requirements

| ID | Requirement |
| --- | --- |
| SW-R1 | Flags `-march=rv32im -mabi=ilp32 -O2 -ffreestanding`; no host runtime or syscall ABI. |
| SW-R2 | Startup sets the stack, global pointer when needed, copies data, zeroes BSS, then calls `main`. Framebuffer and MMIO windows are never RAM. |
| SW-R3 | Every image has a manifest: architecture, ABI, entry, load segments, lengths, SHA-256, memory-map revision, completion marker. Harness preload and serial UART load of the same segments are separate evidence. |
| SW-R4 | The runtime supplies only what the Doom port needs: startup, memory primitives, SDRAM heap, monotonic ticks, keyboard polling, framebuffer/palette access, completion/failure markers, a read-only game-data view. No filesystem, processes, VM, dynamic linking, sockets, audio or OS. |
| SW-R6 | Upstream archives, WAD data, libc packages and generated firmware are never committed; missing optional game data skips the opt-in Doom run. |

## Out of scope

- A host runtime or syscall ABI (SW-R1).
- Anything the Doom port does not need: a filesystem, processes, virtual
  memory, dynamic linking, sockets, audio or an operating system (SW-R4).
- The Doom engine itself and its adaptations: the [Doom port](../doom/prd.md).

## Blocks

None: this is software. The hardware it runs on is in the [HAS](../../rtl/has.md).

## Acceptance

Checks are in the [software DV plan](../../dv/software/dv-plan.md#features).

| Requirement | Check |
| --- | --- |
| SW-R1 | Builder unit tests check the RV32IM/ILP32 flags, and the integration test runs the installed GNU cross-toolchain |
| SW-R2 | `n2d_c_runtime_system`: copied data, zeroed BSS, stack and odd-sized data/BSS guards in the signature words |
| SW-R3 | `n2d_c_runtime_system` loads every image word over UART; the fast targets preload the exact manifest bytes ([platform DV plan](../../dv/platform/dv-plan.md#testbench)) |
| SW-R4 | `n2d_c_runtime_system` exercises the memory primitives, heap, ticks, keyboard polling, framebuffer and palette and the completion marker; no check covers the exclusions |
| SW-R6 | No automated check is recorded; builder unit tests check that outputs stay inside the scratch directory |
