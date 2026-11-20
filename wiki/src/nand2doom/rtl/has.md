# nand2doom high-level architecture (HAS)

!!! note "Ported, not yet in the standard template"
    Ported from nand2anything with paths rewritten (#78). Rearranging it into the [standard template](../../../bkm/design/standard-docs.md) is #91.

nand2doom is a 25 MHz RV32IM machine-mode system without MMU or TLB whose
product milestone is freestanding C Doom with VGA video and keyboard input.
The [RISC-V ownership boundary](https://github.com/amichai-bd/nand2anything/blob/aeebe92/wiki/src/riscv/ownership.md) owns neutral
execution ports and observation; nand2doom owns the trap adapter, physical
address map, caches, precise debug stop and every Doom device. nand2linux
privilege, MMU, interrupt, firmware and address-map policy are not parameters
of this platform. The [DV plan](../dv/platform/dv-plan.md) owns verification.

| Owner | Responsibility | Primary interfaces |
| --- | --- | --- |
| [RV32IM core](core/mas.md) | Six-stage in-order pipelined execution, traps, retirement, precise debug stop by drain | instruction/data word ports, retirement, debug control |
| [Memory](memory/mas.md) | I-cache, D-cache, line arbitration, SDRAM, coherence at debug halt | core word ports, 128-bit SDRAM lines, physical SDRAM pins |
| [Video](video/mas.md) | Double-buffered 8bpp framebuffer, palette, 2x scanout, RGB/sync | uncached CPU/host port, VGA RGB444/HSYNC/VSYNC |
| [I/O](io/mas.md) | UART packet debug and unified PS/2/host Set-2 input | serial pins, host-memory master, Virtual JTAG keyboard, input MMIO |
| Software ([runtime](../sw/runtime/spec.md), [Doom port](../sw/doom/spec.md)) | `ilp32` runtime, linker/image format, Doom platform shim | SDRAM image, MMIO, framebuffer, external game-data manifest |
| [FPGA](../fpga/de10_lite/board-spec.md) | Board tops, clocks, pins, Quartus gate | board pins |

Shared blocks are reused only through project-neutral contracts; decode,
cacheability, boot and error policy stay thin nand2doom integration.

## Common reuse boundaries

| Boundary | Ownership and compatibility obligation |
| --- | --- |
| UART byte PHY | Common RX/TX; Doom keeps the debug parser, transactions, bulk routing, timeouts and framing. |
| VGA timing | Common timing generator; Doom keeps the framebuffer, palette, MMIO and scanout. |
| Reset and CDC | Common reset and bit synchronizers; keep asynchronous PHY reset, synchronized release and 25 MHz. |
| Register file and staging RAM | Common RV32 register file and RAMs; keep reset, old-data latency, masks and capacity. |
| PS/2 | Doom combines filtering, receive, FIFO and injection; a common receiver needs its own contract and consumer tests. |
| SDRAM | Doom owns its controller. Linux also uses burst-eight CAS-two lines, but Doom's open-row policy, bank map, refresh reserve and capture differ; the Linux BL8 controller is not a drop-in backend. |
| Cache/framebuffer arrays | Shared wrappers need identical latency, enables, masks, collision and initialization plus synthesis evidence. |

## Requirements

| ID | Requirement |
| --- | --- |
| PLAT-R1 | One 25 MHz system/pixel clock; portable Verilator tops receive it directly with no vendor library. Reset asserts asynchronously and releases synchronously. |
| PLAT-R2 | Reset starts SDRAM initialization, clears valid and control state, blanks VGA, empties input queues, invalidates caches and holds the CPU at PC `0x0000_0000`. After SDRAM initialization the host may load; only an explicit Resume executes. No boot ROM, filesystem or monitor. |
| PLAT-R3 | Every accepted data request selects exactly one owner; unmapped requests return an access error with no side effect. |
| PLAT-R4 | Every accepted operation completes or errors; arbitration is starvation-free; no block drops backpressure. |
| PLAT-R5 | A framebuffer swap takes effect only at vertical blank; a swap and palette commit in one write flip at the same blank. |
| PLAT-R6 | Precise Halt succeeds only when I-cache invalidation and D-cache maintenance both succeed; a writeback error keeps debug memory closed, times out the Halt and retries while halt stays requested. Timeout never resumes. |
| PLAT-R7 | The product top has no preload, fault-injection, direct-array or fast-memory port. |

## Address map

32-bit byte addresses. Subsystem pages own register offsets.

| Inclusive range | Owner | Behavior |
| --- | --- | --- |
| `0x0000_0000`-`0x03ff_ffff` | SDRAM | 64 MiB cached executable/read/write memory; reset vector at the first word. |
| `0x1000_0000`-`0x1000_ffff` | Framebuffer | Uncached 64 KiB back-bank window; first 64,000 bytes are 320x200 indices. |
| `0x1001_0000`-`0x1001_0fff` | Palette | Uncached inactive-bank entries and defined readback; 256 32-bit entries. |
| `0x1002_0000`-`0x1002_ffff` | Framebuffer front view | Uncached read-only view of the displayed bank in the same layout; a write is an access error. |
| `0x2000_0000`-`0x2000_0fff` | System | Identification, completion/failure marker, monotonic tick, reset/halt status. |
| `0x2000_1000`-`0x2000_1fff` | Video control | Palette commit, framebuffer swap, active banks, frame sequence, and video status. |
| `0x2000_2000`-`0x2000_2fff` | Input | Unified FIFO data/status/control/inject for PS/2, UART and JTAG events ([registers](io/mas.md#ps2-mmio-registers)). |
| `0x2000_3000`-`0x2000_3fff` | UART application/status | Optional software byte queues and transport diagnostics; not the host debug master. |
| all other addresses | Platform decoder | Access error with no side effect. |

### System and video-control registers

| Window | Offset | Access | Definition |
| --- | ---: | --- | --- |
| System | `0x000` | read | Identity |
| System | `0x004` | read/write, byte strobes | Software completion/failure marker |
| System | `0x008` | read | Wrapping 25 MHz tick |
| System | `0x00c` | read | Halt and maintenance status |
| Video | `0x000` | write lane 0 | Bit 0 requests a palette commit |
| Video | `0x004` | read | Active palette bank, pending commit, frame sequence |
| Video | `0x008` | read/write lane 0 | Write: bit 0 swap, bit 1 palette commit, armed the same clock. Read `{30'b0, swap_pending, front_bank}`. No lane 0 is an access error. |

## Composition

`src/nand2doom/rtl/platform/n2d_system.sv` is the
synthesizable single-clock integration top: core, both caches, UART debug,
framebuffer/palette/VGA and the unified Set-2 FIFO behind an external
three-master line-memory boundary (I-cache, D-cache, halted UART SDRAM
adapter). `FRAMEBUFFER_FULL_WIDTH` selects the
[bank geometry](video/mas.md#framebuffer-and-palette): full width by default
and on the DE2-115, half width on the DE10-Lite.

- Instruction traffic always enters the I-cache. Data addresses select
  exactly one of SDRAM via the D-cache, the back-bank window, the front view,
  palette, system/video control or input; a front-view write decodes as
  unmapped.
- Both framebuffer windows share one always-ready port with a one-clock
  response landing in the core's memory-completion slot, so framebuffer
  accesses complete without a hold (UNCACHED_WAIT zero), stores are
  effectively posted, and program order holds against swap requests.
- One registered owner routes the single outstanding data response; a
  request accepted on the response-sampling edge takes ownership on that
  edge, so the core can issue back to back across devices.
- Framebuffer and palette debug transactions pass through a second atomic
  staging adapter and a line-to-word bridge
  (`n2d_debug_device_line_bridge`) into their published ports, so an aborted
  multiword device command has no side effect.
- PS/2, UART `CMD_INPUT` and the JTAG byte stream meet only at the FIFO;
  input never enters a debug-memory adapter or maintenance control.
- `framebuffer_front_bank_o` and `framebuffer_swap_pending_o` let monitors
  attribute writes to the back bank and take frames at swaps; debug
  framebuffer requests always use the back bank.
- Assertions: one-hot decode, framebuffer bounds, debug exclusion after
  maintenance failure, reset-only use of simulation preload.

Scanout uses embedded RAM, so SDRAM bandwidth is shared only by instruction
fills, data fills/writebacks and halted-host traffic. Software draws directly
into the back bank with uncached stores that never miss in or evict from the
D-cache. The [maintained Halt bound](memory/mas.md#maintained-halt-bound)
gives 65,536 clocks; short debug watchdogs stay 4,096.

### Padless ASIC core

`n2d_asic_soc` reconnects the full DE2-115 logical profile with
`n2d_memory_controller`: RV32IM, both caches and predictor, full-width
framebuffer and palette, serial debug/bulk/stream transports, PS/2, the JTAG
keyboard endpoint, arbitration and the physical x16 SDRAM controller.
[`asic_soc.json`](https://github.com/amichai-bd/nand2anything/blob/aeebe92/src/nand2doom/cfg/asic_soc.json) owns the
source closure and fixed feature parameters;
[`n2d_asic_core.py`](https://github.com/amichai-bd/nand2anything/blob/aeebe92/src/nand2doom/scripts/n2d_asic_core.py)
emits absolute source paths or a hashed manifest (`sources|manifest`,
`--backend portable|ihp`). Every generated manifest stays below `workdir/`.
The external 64 MiB SDRAM remains off die; the core exposes DQ input,
output and output-enable separately rather than synthesizing a resolved bus.

The clock source/PLL and pads are outside this digital-core boundary.
`clk_i` is 25 MHz. `reset_ni` and `clock_ready_i` retain asynchronous
assertion/synchronous release through `n2a_reset_sync`; the system remains
reset until SDRAM initialization completes. The forwarded SDRAM and VGA
DAC clocks invert the core clock, preserving the board phase contract.
RGB remains four bits per channel before an external DAC/pad adapter.

`n2d_system` always takes the neutral JTAG scan interface from its `scan_*`
ports, so the ASIC top passes them to its pins and keeps the live keyboard
function; FPGA board tops bind the common `n2a_jtag_virtual` there
([JTAG keyboard composition](io/mas.md#jtag-keyboard-composition)).

All memories preserve depth, ports and cycle/collision semantics. An IHP
backend is eligible only after its common contract and full-core tests pass;
portable elaboration alone does not qualify a macro implementation.
The common comparison qualification is tracked in
[#593](https://github.com/amichai-bd/nand2anything/issues/593), separately
from the independent physical layout in
[#589](https://github.com/amichai-bd/nand2anything/issues/589). A padless
floorplan is neither an IO/package implementation nor foundry signoff.

### Simulation wrappers

| Wrapper | Memory | Loading |
| --- | --- | --- |
| `n2d_c_runtime_system` | Fast model | Serial UART Load of the manifest |
| `n2d_integrated_system` | Fast model | Simulation-only 256-byte byte-enabled preload of the exact manifest while product reset is asserted; excluded under `SYNTHESIS`; assertion rejects use after release |
| `n2d_sdram_system` | Product `n2d_memory_controller` and `n2d_sdram_device_model` | Preload; system held in reset until SDRAM initialization |

Both fast wrappers run the same generated RV32IM/ILP32 image.
`n2d_system_product` compiles only the synthesizable closure and structurally
excludes every wrapper.

## RTL style

All product files keep the `n2d_` prefix under `src/nand2doom/rtl/{core,ls,other}/`
([project layout](https://github.com/amichai-bd/nand2anything/blob/aeebe92/wiki/src/nand2doom/README.md#product-source-layout-and-compatibility)).
Product RTL writes no clocked process of its own: each register group is a
packed struct (or a few scalars) computed in a blocking `always_comb` from
`_q` values and registered through one canonical
[common register macro](https://github.com/amichai-bd/nand2anything/blob/aeebe92/wiki/src/common/clock_reset/HAS.md); asynchronous-reset
blocks use the asynchronous forms. Flop arrays use the common array and
indexed-write macros; RAMs use `n2a_sync_ram`/`n2a_sdp_ram`/`n2a_tdp_ram`.
Nets are `logic` with separate `assign`s; `DRAM_DQ` is the only `tri`.
Assertions use the common clocked, property, initial and parameter forms; no
product file has an `` `ifdef SYNTHESIS ``. Vendor-versus-portable selection
is only by source lists naming one backend per pair. Pure helpers live in
`n2d_pkg`; every state record and enum is a package type
([RTL BKM](https://github.com/amichai-bd/nand2anything/blob/aeebe92/wiki/bkm/bkm_rtl.md#types-and-shared-constants-live-in-packages)):
core-stage records in `n2d_core_pkg`, others in the type-only
`n2d_core_types_pkg`, `n2d_ls_types_pkg` and `n2d_types_pkg`. A
parameter-sized field is its own zero-reset register beside the record. Each
source list names a package before its first user.

## Milestones

In order: (1) build/DV, reset and RAM foundations; (2) register file,
decode/ALU, load/store, retirement; (3) RV32I program, RV32M, compiled C;
(4) caches, controller, fast memory, pin-level SDRAM; (5) UART host debug;
(6) PS/2 and framebuffer/VGA; (7) owned C renderer stress test; (8) licensed
Doom and game-data policy; (9) Doom boot, frame and input evidence;
(10) Quartus and authorized physical bring-up.

Cache geometry or M-unit latency changes need preserved behavior and a
measured need. Reference designs are interface and test evidence only; code
is adapted only with the provenance record AGENTS.md requires. Proprietary
game assets and commercial build output are excluded.
