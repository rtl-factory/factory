# nand2doom runtime: spec

How the runtime meets its [requirements](prd.md). Code: `src/nand2doom/sw/runtime/`. Freestanding little-endian RV32IM `ilp32` software: the owned runtime and
validation image, and the pinned DoomGeneric port with its platform adapter,
clock, keyboard map, direct framebuffer rendering, engine adaptations and
capture mailbox. The [build specification](https://github.com/amichai-bd/nand2anything/blob/aeebe92/wiki/tools/build/SPEC.md) owns
toolchain names and build recipes; the [DV plan](../../dv/software/dv-plan.md) owns verification.

## Runtime and validation image

| Path | Responsibility |
| --- | --- |
| `crt0.S` | Reset stack, initialized-data copy, BSS clear, and `main` entry. |
| `n2d_runtime.ld` | SDRAM load/run addresses, heap, stack, and overlap assertions. |
| `runtime.c` | Byte-exact memory primitives and a 16-byte-aligned bounded bump allocator (null on exhaustion; no free). |
| `n2d_pmon.h` | Header-only access to the machine-mode [performance monitor](../../rtl/core/mas.md#performance-monitor): event IDs, counter configuration, 64-bit reads and a stopped-group snapshot. |
| `validation.c` | Owned ABI, RV32M, memory, and indexed-framebuffer acceptance workload. |
| `../doom/` ([spec](../doom/spec.md)) | Actual-Doom startup/link layout, fixed-memory WAD, platform callbacks, and fail-closed host calls. |

`n2d_image.py` builds the
validation image with `riscv64-unknown-elf-gcc` and
`-march=rv32im -mabi=ilp32 -O2 -ffreestanding -fno-builtin`, no PIC,
relaxation, hosted startup or libraries, deterministic object names:

```text
python3 src/nand2doom/scripts/n2d_image.py --output workdir/software/n2d_validation
```

Outputs `n2d_validation.elf`, `.bin`, `.map` and `manifest.json` under
`workdir/`. Layout: text at `0x0000_0000`, data run address `0x0000_8000`
with a compact load image, heap from the end of BSS through `0x000e_ffff`,
stack at `0x0010_0000`, completion marker at `0x0000_4000`. Startup copies and
clears byte by byte; the link ends data and BSS at odd addresses with one-byte
guards that the signature checks.

The program makes non-inlined calls, reads copied data, observes zeroed BSS,
moves heap data, and uses volatile operands so GCC emits multiply, divide and
remainder. It writes all 64,000 framebuffer bytes as words in the pattern
`(3*x + 5*y) & 0xff`, programs 256 RGB444 palette entries with a frame commit,
and consumes a PS/2 make/release pair then the same pair by UART through one
translator: their digest is signature word nine, the first make keeps its
pixel-zero effect, and word ten proves identity and tick reads. It writes nine
result words plus completion `0xd00d0035` at `0x0000_4000` (last), then the
same marker to system MMIO `0x2000_0004`, and spins in one resident cache line
with no device request.

