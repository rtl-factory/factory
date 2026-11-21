# nand2doom HAS

## Purpose

nand2doom is a 25 MHz RV32IM machine-mode system without MMU or TLB whose
product milestone is freestanding C Doom with VGA video and keyboard input,
on the DE10-Lite and DE2-115 boards. The
[RISC-V ownership boundary](https://github.com/amichai-bd/nand2anything/blob/aeebe92/wiki/src/riscv/ownership.md) owns neutral
execution ports and observation; nand2doom owns the trap adapter, physical
address map, caches, precise debug stop and every Doom device. nand2linux
privilege, MMU, interrupt, firmware and address-map policy are not parameters
of this platform. The software requirements are in the
[runtime PRD](../sw/runtime/prd.md) and the [Doom port PRD](../sw/doom/prd.md).

| ID | Requirement |
| --- | --- |
| PLAT-R1 | One 25 MHz system/pixel clock; portable Verilator tops receive it directly with no vendor library. Reset asserts asynchronously and releases synchronously. |
| PLAT-R2 | Reset starts SDRAM initialization, clears valid and control state, blanks VGA, empties input queues, invalidates caches and holds the CPU at PC `0x0000_0000`. After SDRAM initialization the host may load; only an explicit Resume executes. No boot ROM, filesystem or monitor. |
| PLAT-R3 | Every accepted data request selects exactly one owner; unmapped requests return an access error with no side effect. |
| PLAT-R4 | Every accepted operation completes or errors; arbitration is starvation-free; no block drops backpressure. |
| PLAT-R5 | A framebuffer swap takes effect only at vertical blank; a swap and palette commit in one write flip at the same blank. |
| PLAT-R6 | Precise Halt succeeds only when I-cache invalidation and D-cache maintenance both succeed; a writeback error keeps debug memory closed, times out the Halt and retries while halt stays requested. Timeout never resumes. |
| PLAT-R7 | The product top has no preload, fault-injection, direct-array or fast-memory port. |

## Block diagram

```text
       UART pins     PS/2 pins    JTAG scan ports                 VGA pins
           |             |              |                             ^
+----------|-------------|--------------|-----------------------------|----+
| n2d_system (platform)  v              v                             |    |
|          |      +--------------------------------+   +----------------+ |
|          |      | io: PS/2 receiver, JTAG        |   | video: frame-  | |
|          v      | keyboard, unified Set-2 FIFO   |   | buffer, palette| |
|  +-----------+  +--------------------------------+   | VGA scanout    | |
|  | io: UART  |-- CMD_INPUT -->^   ^ MMIO             +----------------+ |
|  | debug     |                    |                     ^ uncached ports |
|  +-----------+   +-----------+  +-------------------------------------+ |
|    | debug       | core      |--| platform data decoder, system and   | |
|    | control --->| RV32IM    |  | video-control registers             | |
|    | host memory +-----------+  +-------------------------------------+ |
|    v               | instr  | data (SDRAM)                              |
|  debug memory     v        v                                            |
|  adapters      I-cache   D-cache          (memory)                      |
+----|-------------|--------|---------------------------------------------+
     v             v        v        three line masters (memory_request_*)
  n2d_memory_controller: arbiter + SDRAM controller (memory) --> SDRAM pins
```

The board top (`fpga/<board>/`) wraps `n2d_system` and `n2d_memory_controller`
and supplies the clock, reset and pins.

| Block | Responsibility | Primary interfaces | MAS |
| --- | --- | --- | --- |
| Platform (`n2d_system`) | Integration top: data decode, system and video-control registers, halt-time cache maintenance, debug routing, PMON platform events | core data port, device ports, three line masters, board pins | [platform](platform/mas.md) |
| RV32IM core | Six-stage in-order pipelined execution, traps, retirement, precise debug stop by drain | instruction/data word ports, retirement, debug control | [core](core/mas.md) |
| Memory | I-cache, D-cache, line arbitration, SDRAM, coherence at debug halt | core word ports, 128-bit SDRAM lines, physical SDRAM pins | [memory](memory/mas.md) |
| Video | Double-buffered 8bpp framebuffer, palette, 2x scanout, RGB/sync | uncached CPU/host port, VGA RGB444/HSYNC/VSYNC | [video](video/mas.md) |
| I/O | UART packet debug and unified PS/2/host Set-2 input | serial pins, host-memory master, Virtual JTAG keyboard, input MMIO | [io](io/mas.md) |
| Software ([runtime](../sw/runtime/spec.md), [Doom port](../sw/doom/spec.md)) | `ilp32` runtime, linker/image format, Doom platform shim | SDRAM image, MMIO, framebuffer, external game-data manifest | none (software, not RTL) |
| FPGA ([DE10-Lite](../fpga/de10_lite/board-spec.md), [DE2-115](../fpga/de2_115/board-spec.md)) | Board tops, clocks, pins, Quartus gate | board pins | none (board specs) |

## Seams

| Seam | Blocks | Protocol | Interface owner |
| --- | --- | --- | --- |
| Instruction and data word ports | core, memory caches | Pipelined 32-bit word ready/valid: one request outstanding per port, the next accepted on the edge that samples the response, hits one edge after acceptance | [memory MAS](memory/mas.md#cpu-port-handshake) (requester obligations in the [core MAS](core/mas.md#pipelined-memory-handshake)) |
| Uncached data decode | core, platform, video, io | The same word handshake; one registered owner per outstanding data response | [platform MAS](platform/mas.md#data-decode-and-response) |
| Line port | I-cache, D-cache, debug adapter, memory controller | 128-bit ready/valid line request, one response pulse to the captured owner, one transaction outstanding | [memory MAS](memory/mas.md#line-port) |
| Three-master line seam | platform, memory controller (board top) | The line port, packed as `memory_request_*` / `memory_response_*` with masters 0 I-cache, 1 D-cache, 2 debug | [platform MAS](platform/mas.md#ports) |
| Framebuffer and palette ports | platform, video | Always ready, one response one clock after every accepted request; swap and commit pulses | [video MAS](video/mas.md#framebuffer-and-palette) |
| Debug port groups | io, platform | Packed one-direction groups: debug control and status, input event, transaction request and status, debug word request and response, screen read | [io MAS](io/mas.md#port-groups) |
| Host-memory transaction | io, memory debug adapter | Grant, begin, one word at a time, commit or abort, done/error after publication or rollback | [io MAS](io/mas.md#host-memory-transaction-boundary) (adapter behaviour in the [memory MAS](memory/mas.md#arbitration-and-debug-adaptation)) |
| Debug control | platform, core | `halt_request_i`, `resume_i`, `reset_pc_request_i`, `halt_ack_o` | [core MAS](core/mas.md#reset-and-debug-behavior) |
| Cache maintenance | platform, caches | Valid/ready request and a completion pulse per cache | [memory MAS](memory/mas.md#maintenance) |
| Input MMIO and injection | platform, io | 12-bit-offset ready/valid MMIO; UART injection and JTAG scan-inject producers | [io MAS](io/mas.md#ps2-mmio-registers) |
| JTAG scan ports | board top, io | Neutral scan signals into the common `n2a_jtag_keyboard` | [io MAS](io/mas.md#jtag-keyboard-composition) |
| PMON platform events | platform, core | One bit per event ID, registered by the core before counting | [core MAS](core/mas.md#performance-monitor) |
| Board binding | board top, platform, memory controller | Clock, synchronized reset, SDRAM, UART, PS/2, VGA and LED pins | [DE10-Lite board spec](../fpga/de10_lite/board-spec.md#composition) |

### Common reuse boundaries

Shared blocks are reused only through project-neutral contracts; decode,
cacheability, boot and error policy stay thin nand2doom integration.

| Boundary | Ownership and compatibility obligation |
| --- | --- |
| UART byte PHY | Common RX/TX; Doom keeps the debug parser, transactions, bulk routing, timeouts and framing. |
| VGA timing | Common timing generator; Doom keeps the framebuffer, palette, MMIO and scanout. |
| Reset and CDC | Common reset and bit synchronizers; keep asynchronous PHY reset, synchronized release and 25 MHz. |
| Register file and staging RAM | Common RV32 register file and RAMs; keep reset, old-data latency, masks and capacity. |
| PS/2 | Doom combines filtering, receive, FIFO and injection; a common receiver needs its own contract and consumer tests. |
| SDRAM | Doom owns its controller. Linux also uses burst-eight CAS-two lines, but Doom's open-row policy, bank map, refresh reserve and capture differ; the Linux BL8 controller is not a drop-in backend. |
| Cache/framebuffer arrays | Shared wrappers need identical latency, enables, masks, collision and initialization plus synthesis evidence. |

## Memory and register map

### Address map

32-bit byte addresses. Each block's MAS owns its register offsets.

| Inclusive range | Owner | Behavior |
| --- | --- | --- |
| `0x0000_0000`-`0x03ff_ffff` | SDRAM | 64 MiB cached executable/read/write memory; reset vector at the first word. |
| `0x1000_0000`-`0x1000_ffff` | Framebuffer | Uncached 64 KiB back-bank window; first 64,000 bytes are 320x200 indices. |
| `0x1001_0000`-`0x1001_0fff` | Palette | Uncached inactive-bank entries and defined readback; 256 32-bit entries. |
| `0x1002_0000`-`0x1002_ffff` | Framebuffer front view | Uncached read-only view of the displayed bank in the same layout; a write is an access error. |
| `0x2000_0000`-`0x2000_0fff` | System | Identification, completion/failure marker, monotonic tick, reset/halt status ([registers](platform/mas.md#system-and-video-control-registers)). |
| `0x2000_1000`-`0x2000_1fff` | Video control | Palette commit, framebuffer swap, active banks, frame sequence, and video status ([registers](platform/mas.md#system-and-video-control-registers)). |
| `0x2000_2000`-`0x2000_2fff` | Input | Unified FIFO data/status/control/inject for PS/2, UART and JTAG events ([registers](io/mas.md#ps2-mmio-registers)). |
| `0x2000_3000`-`0x2000_3fff` | UART application/status | Optional software byte queues and transport diagnostics; not the host debug master. `n2d_system` does not decode this window yet, so an access returns an access error. |
| all other addresses | Platform decoder | Access error with no side effect. |

The UART memory endpoint has its own host-side map, including a scanned
palette view at `0x1003_0000` that the CPU map does not have
([endpoint windows](io/mas.md#uart-memory-endpoint)).

## Clocks and resets

- **One clock.** `clk_i` is the 25 MHz system, pixel and SDRAM controller
  clock (PLAT-R1). On the boards a PLL divides the 50 MHz oscillator by two;
  the portable Verilator clock backends divide by two with no vendor library
  ([board composition](../fpga/de10_lite/board-spec.md#composition)). The
  SDRAM (and on the DE2-115 the VGA DAC) receive the inverted system clock.
- **Reset.** The board key or loss of PLL lock asserts reset asynchronously
  through the common `n2a_reset_sync`, which releases it on a 25 MHz edge;
  `n2d_system` stays in reset until the SDRAM controller reports
  initialization done (≥200 µs) (PLAT-R2, [board requirements](../fpga/de10_lite/board-spec.md#requirements)).
  Inside `n2d_system` the UART and PS/2 PHYs use the reset level
  asynchronously, the datapaths synchronously ([platform MAS](platform/mas.md#clocks-and-resets)).
- **Crossings.** UART RX, the PS/2 pins and the reset key are asynchronous
  inputs into synchronizers: the PS/2 pins each pass an `n2a_cdc_bit_sync`
  ([io MAS](io/mas.md#ps2-keyboard-receiver)). The JTAG TCK to system crossing
  is inside the common `n2a_jtag_keyboard` ([io MAS](io/mas.md#jtag-keyboard-composition)).
  SDRAM read data is captured from the inverted device clock
  ([memory MAS](memory/mas.md#sdram-controller)). The constraints are in each
  board's SDC ([constraints](../fpga/de10_lite/board-spec.md#constraints)).

## Top-level behaviour

- **Boot.** Reset clears every block (PLAT-R2); the core is held at PC
  `0x0000_0000` and the UART engine starts with halt requested. After SDRAM
  initialization the host loads the image over UART (Load, bulk or the stream
  session), sets the PC with Reset-PC and starts it with Resume, which
  invalidates both caches first ([io MAS](io/mas.md#version-1-wire-protocol)).
  There is no boot ROM.
- **Instruction flow.** Instruction traffic always enters the I-cache; a miss
  becomes a line read through the arbiter and the SDRAM controller.
- **Data flow.** Each data address selects exactly one of SDRAM via the
  D-cache, the back-bank window, the front view, palette, system/video
  control or input; anything else is an access error (PLAT-R3,
  [decode](platform/mas.md#data-decode-and-response)).
- **Drawing.** Software draws directly into the back bank with uncached
  stores that never miss in or evict from the D-cache, writes the inactive
  palette, then arms a swap and palette commit in one video-control write;
  both flip at the next vertical blank (PLAT-R5,
  [video MAS](video/mas.md#storage)). Scanout uses embedded RAM, so SDRAM
  bandwidth is shared only by instruction fills, data fills/writebacks and
  halted-host traffic.
- **Input.** PS/2, UART `CMD_INPUT` and the JTAG byte stream meet only at the
  input FIFO; software polls it through the input window
  ([io MAS](io/mas.md#ps2-keyboard-receiver)).
- **Host debug.** Halt drains the core, writes back every dirty line and
  invalidates both caches before it succeeds (PLAT-R6); Peek, Poke and Load
  run only in that maintained Halt. The
  [maintained Halt bound](memory/mas.md#maintained-halt-bound) gives
  65,536 clocks; short debug watchdogs stay 4,096.
- **Errors and recovery.** Every accepted operation completes or errors
  (PLAT-R4). A failed D-cache writeback keeps the dirty line, keeps debug
  memory closed and times out the Halt; maintenance retries while halt stays
  requested, and a timeout never resumes (PLAT-R6). A bulk transfer failure
  quarantines the UART until hardware reset ([bulk](io/mas.md#bulk-wire-extension)).

## Decisions

- **Framebuffer on chip.** The framebuffer is embedded RAM, not SDRAM scanout
  DMA, which keeps display traffic and coherence out of the SDRAM controller;
  the DE10-Lite stores half-width banks to fit
  ([framebuffer placement](memory/mas.md#framebuffer-placement)).
- **Per-board geometry.** `FRAMEBUFFER_FULL_WIDTH` selects the
  [bank geometry](video/mas.md#framebuffer-and-palette): full width by default
  and on the DE2-115, half width on the DE10-Lite. `DCACHE_WAYS` is four on the
  DE2-115 and two on the DE10-Lite ([cache contracts](memory/mas.md#cache-and-line-contracts)).
- **Thin integration.** Shared blocks are reused only through
  project-neutral contracts ([common reuse boundaries](#common-reuse-boundaries)).
- **No boot ROM, filesystem or monitor**; the host loads every image (PLAT-R2).

### RTL style

All product files keep the `n2d_` prefix
([project layout](https://github.com/amichai-bd/nand2anything/blob/aeebe92/wiki/src/nand2doom/README.md#product-source-layout-and-compatibility));
in this repository they live under `src/nand2doom/rtl/{core,memory,video,io,platform}/`
(nand2anything's `rtl/{core,ls,other}/`).
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

### Padless ASIC core

The ASIC flow stayed in nand2anything (`n2d_asic_soc` is not in this
repository); its contract is kept here because `n2d_system` serves it.

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

### Milestones

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

## Verification strategy

The [platform DV plan](../dv/platform/dv-plan.md) owns the system-level
evidence tiers and checks the platform requirements:

| Requirement | Where it is checked |
| --- | --- |
| PLAT-R1 | Board tops in Verilator with their portable clock models ([FPGA DV plan](../dv/fpga/dv-plan.md#features)) |
| PLAT-R2, R3, R4, R6 | `n2d_integrated_system`, `n2d_c_runtime_system` ([platform DV plan](../dv/platform/dv-plan.md#features)); maintained Halt in `n2d_halt_sdram` and `n2d_halt_boundaries` ([I/O DV plan](../dv/io/dv-plan.md#features)) |
| PLAT-R3, R5 | `n2d_video_control` ([platform DV plan](../dv/platform/dv-plan.md#features)); vertical-blank flips in the [video DV plan](../dv/video/dv-plan.md) |
| PLAT-R7 | `n2d_system_product`, `n2d_system_product_synthesis` ([platform DV plan](../dv/platform/dv-plan.md#features)) |

Each block is checked against its MAS by its own plan:
[core](../dv/core/dv-plan.md), [memory](../dv/memory/dv-plan.md),
[video](../dv/video/dv-plan.md), [I/O](../dv/io/dv-plan.md); the board tops by
the [FPGA DV plan](../dv/fpga/dv-plan.md) and the software by the
[software DV plan](../dv/software/dv-plan.md).
