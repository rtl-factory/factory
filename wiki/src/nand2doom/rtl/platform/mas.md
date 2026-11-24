# platform MAS

## Purpose

`n2d_system` (`src/nand2doom/rtl/platform/n2d_system.sv`) is the
synthesizable single-clock integration top shared by both boards: core, both
caches, UART debug, framebuffer/palette/VGA and the unified Set-2 FIFO behind
an external three-master line-memory boundary (I-cache, D-cache, halted UART
SDRAM adapter). It owns the data address decode, the system and video-control
registers, halt-time cache maintenance, the routing of host debug traffic and
the platform PMON event sources. It serves the [HAS](../has.md) seams, address
map and top-level behaviour; the board tops wrap it with
`n2d_memory_controller` ([board composition](../../fpga/de10_lite/board-spec.md#composition)).

## Interface

### Parameters

| Parameter | Default | Meaning |
| --- | --- | --- |
| `UART_BAUD` | `N2D_UART_BAUD` (1 Mbaud) | Debug/load UART rate; both board tops select `N2D_UART_BOARD_BAUD` (2 Mbaud) ([link rate](../io/mas.md#link-rate)). |
| `FRAMEBUFFER_FULL_WIDTH` | 1 | Framebuffer bank geometry: 320x200 banks, or 160x200 half-width banks on the DE10-Lite ([video](../video/mas.md#framebuffer-and-palette)). |
| `DCACHE_WAYS` | 4 | D-cache ways over 512 sets of 16-byte lines: 4 (32 KiB) on the DE2-115, 2 (16 KiB) on the DE10-Lite ([memory](../memory/mas.md#cache-and-line-contracts)). |
| `DCACHE_PREFETCH_LINES` | 2 | D-cache next-line prefetch buffer lines; 0 disables the prefetch ([memory](../memory/mas.md#next-line-prefetch)). Both board tops pass 2. |

### Ports

All ports are in the `clk_i` domain except the scan ports, which are in the
`scan_tck_i` domain and cross inside `n2a_jtag_keyboard`.

| Group | Ports (direction, width) | Meaning |
| --- | --- | --- |
| Clock and reset | `clk_i` in, `reset_i` in | 25 MHz system clock; active-high reset ([clocks and resets](#clocks-and-resets)). |
| UART | `uart_rx_i` in, `uart_tx_o` out | Host debug and load link ([io](../io/mas.md#uart-bridge)). |
| PS/2 | `ps2_clk_i` in, `ps2_data_i` in | Keyboard pins, straight to `n2d_ps2_keyboard` ([io](../io/mas.md#ps2-keyboard-receiver)). |
| JTAG scan | `scan_tck_i`, `scan_tdi_i`, `scan_capture_i`, `scan_shift_i`, `scan_update_i`, `scan_update_ir_i` in (1 each), `scan_ir_i` in (2), `scan_tdo_o` out | Neutral scan interface of the JTAG keyboard endpoint: board tops bind the common `n2a_jtag_virtual`, DV compositions tie it idle ([io](../io/mas.md#jtag-keyboard-composition)). |
| Line memory | `memory_request_valid_o` (3), `memory_request_prefetch_o` (3), `memory_request_write_o` (3), `memory_request_address_o` (78 = 3 x 26), `memory_request_write_data_o` (384 = 3 x 128), `memory_request_byte_enable_o` (48 = 3 x 16) out; `memory_request_ready_i` (3), `memory_response_valid_i` (3), `memory_response_data_i` (384), `memory_response_error_i` (3) in | Three line masters, index 0 I-cache, 1 D-cache, 2 debug, each packed in that order, on the [line port](../memory/mas.md#line-port) contract. `memory_request_prefetch_o` marks only the D-cache's prefetch and background drain as low priority. |
| Retirement observation | `observation_*_o` out (valid, 64-bit order, PC, instruction, next PC, rd address/write/data, memory valid/write/address/read data/write data/read mask/write mask, trap, cause, trap value); `halt_ack_o` out | The core's observation record and halt acknowledgement ([core](../core/mas.md#interface)). |
| Framebuffer observation | `framebuffer_write_valid_o`, `framebuffer_write_address_o` (16), `framebuffer_write_data_o` (32), `framebuffer_write_strobe_o` (4), `framebuffer_front_bank_o`, `framebuffer_swap_pending_o` out | Every accepted framebuffer write and the bank state, so monitors attribute writes to the back bank and take frames at swaps. |
| Cache events | `instruction_hit_event_o`, `instruction_miss_event_o`, `data_hit_event_o`, `data_miss_event_o` out | One pulse per valid cache lookup ([memory](../memory/mas.md#pipeline)). |
| Debug and input observation | `debug_memory_request_valid_o`, `ps2_event_consumed_o` out | A debug SDRAM line request is valid; a valid event was popped from the input FIFO. |
| System observation | `system_completion_o` (32), `system_tick_o` (32) out | The completion marker and the tick counter of the system registers. |
| VGA | `pixel_x_o`, `pixel_y_o` (10 each), `visible_o`, `vga_red_o`, `vga_green_o`, `vga_blue_o` (4 each), `vga_hsync_o`, `vga_vsync_o`, `frame_start_o`, `frame_end_o`, `frame_sequence_o` (32) out | Scanout and frame observation from the video block ([video](../video/mas.md#framebuffer-and-palette)). |

### Data decode and response

The core's data port is decoded by address into exactly one owner
([address map](../has.md#address-map)):

| Owner | Range decoded | Ready |
| --- | --- | --- |
| SDRAM (D-cache) | below `0x0400_0000` | D-cache ready |
| Framebuffer back bank | `0x1000_0000`..`0x1000_ffff` | framebuffer port ready, unless the debug bridge or an endpoint screen read uses it |
| Framebuffer front view | `0x1002_0000`..`0x1002_ffff`, reads only; a write there is unmapped | as the back bank |
| Palette | `0x1001_0000`..`0x1001_0fff` | palette port ready, unless the debug bridge or a screen read uses it |
| System, video control | `0x2000_0000`..`0x2000_1fff` | no system response pending |
| Input | `0x2000_2000`..`0x2000_2fff` | `n2d_ps2_keyboard` MMIO ready |
| Error | anything else | no error response pending; one-cycle error response |

- One registered owner routes the single outstanding data response; a
  request accepted on the response-sampling edge takes ownership on that
  edge, so the core can issue back to back across devices.
- Both framebuffer windows share one always-ready port with a one-clock
  response landing in the core's memory-completion slot, so framebuffer
  accesses complete without a hold (UNCACHED_WAIT zero), stores are
  effectively posted, and program order holds against swap requests.
- Framebuffer and palette port priority: the halted debug bridge, then an
  endpoint screen read, then the CPU. A screen read takes one port cycle; the
  endpoint paces them, so the CPU loses at most that cycle per pacing gap
  ([UART memory endpoint](../io/mas.md#uart-memory-endpoint)). Debug
  framebuffer requests always use the back bank; a screen read uses the front
  bank or the active palette bank.

### System and video-control registers

Uncached, answered one clock after acceptance; offsets within each 4 KiB
window.

| Window | Offset | Access | Definition |
| --- | ---: | --- | --- |
| System | `0x000` | read | Identity `0x3144_324e` (`N2D1`) |
| System | `0x004` | read/write, byte strobes | Software completion/failure marker |
| System | `0x008` | read | Wrapping 25 MHz tick |
| System | `0x00c` | read | Halt and maintenance status: bit 0 halt acknowledged, bit 1 cache maintenance done |
| Video | `0x000` | write lane 0 | Bit 0 requests a palette commit |
| Video | `0x004` | read | Active palette bank (bit 0), pending commit (bit 1), frame sequence (bits 31:2, its low 30 bits) |
| Video | `0x008` | read/write lane 0 | Write: bit 0 swap, bit 1 palette commit, armed the same clock. Read `{30'b0, swap_pending, front_bank}`. No lane 0 is an access error. |

A write to a read-only register, a read of video `0x000`, a video `0x000`
write without lane 0 and any other offset return an access error. The
completion marker is an observable software handshake; the durable result
signature stays in SDRAM and is checked after a precise UART halt.

## Microarchitecture

- **Composition.** `u_core` (`n2d_rv32im_core`, `enable_i` tied high,
  `resume_i` = not halt requested), `u_icache`, `u_dcache`,
  `u_framebuffer` (`n2d_framebuffer_vga`), `u_keyboard` (`n2d_ps2_keyboard`),
  `u_jtag_keyboard` (`n2a_jtag_keyboard`), `u_debug` (`n2d_uart_debug` with
  `OP_TIMEOUT_CYCLES` 4,096 and `HALT_TIMEOUT_CYCLES` 65,536), two
  `n2d_debug_memory_adapter`s (`u_debug_adapter` for SDRAM,
  `u_debug_device_adapter` for framebuffer and palette),
  `u_debug_device_bridge` (`n2d_debug_device_line_bridge`) and
  `u_pmon_events` (`n2d_pmon_platform_events`).
- **Data owner.** `data_owner_q` (none, SDRAM, framebuffer, palette, system,
  PS/2, error) is set on acceptance and cleared by the response, with a
  same-edge acceptance winning over the clear.
- **System registers.** `sys_q` holds the response, the completion marker and
  the tick, which increments every clock.
- **Halt maintenance.** `maint_q` watches the halt request: a rising halt
  request arms one I-cache invalidate and one D-cache writeback, requested
  only once the core acknowledges halt. Each cache's completion is recorded;
  a D-cache completion with error is not, so maintenance is requested again.
  Cache maintenance done (`cache_maint_done` to the UART engine and system
  `0x00c` bit 1) is halt requested with nothing left to complete. Dropping the
  halt request clears the tracking.
- **Debug routing.** The UART engine's transaction grant is tied high. A
  transaction begun below `0x0400_0000` goes to the SDRAM adapter, one at or
  above `0x1000_0000` to the device adapter; `debug_owner_q` keeps that choice
  until the transaction is done. Device addresses are rebased so the palette
  (`0x1001_0000` and up) lands at device offset `0x1_0000`.
- **Device line bridge.** Framebuffer and palette debug transactions pass
  through the second atomic staging adapter and
  `n2d_debug_device_line_bridge` into their published ports, so an aborted
  multiword device command has no side effect. The bridge unpacks one
  16-byte-aligned line into four word requests (idle, issue, response),
  selects the palette by line address bit 16, skips write words with no
  enabled byte and never reports an error.
- **Input.** PS/2, UART `CMD_INPUT` and the JTAG byte stream meet only at the
  FIFO; input never enters a debug-memory adapter or maintenance control.
- **PMON events.** `n2d_pmon_platform_events` derives the platform event IDs
  from the cache, line-memory and uncached handshakes plus flops for the
  in-flight store flag, outstanding I/D read, outstanding transaction and
  one-hot owner ([PMON](../core/mas.md#performance-monitor)).
- **Assertions.** One-hot decode, framebuffer write bounds (offset below
  64,000), no framebuffer request together with a D-cache request, and debug
  exclusion after maintenance failure. The simulation wrappers add the
  reset-only use of the preload ([platform DV](../../dv/platform/dv-plan.md#testbench)).

### Files

| Path | Role |
| --- | --- |
| `src/nand2doom/rtl/platform/n2d_system.sv` | Integration top |
| `src/nand2doom/rtl/platform/n2d_debug_device_line_bridge.sv` | Debug line to framebuffer/palette word bridge |
| `src/nand2doom/rtl/platform/n2d_pmon_platform_events.sv` | Platform PMON sources |
| `src/nand2doom/rtl/platform/n2d_pkg.sv`, `n2d_types_pkg.sv` | Codes, helpers, state records |

## Clocks and resets

One clock, `clk_i` (25 MHz). Every block inside resets synchronously on
`reset_i`; the board top asserts it asynchronously and synchronizes its release
outside this boundary (through `n2a_reset_sync`, held until SDRAM
initialization completes;
[board requirements](../../fpga/de10_lite/board-spec.md#requirements)). The
only crossing inside is the JTAG scan domain, owned by `n2a_jtag_keyboard`
([io](../io/mas.md#jtag-keyboard-composition)).

## Design choices

- The framebuffer windows share one always-ready port, so stores are posted
  and need no hold, and program order holds against swap requests (above).
- Device debug writes use their own staging adapter and line bridge rather
  than direct word writes, so an aborted multiword device command leaves no
  side effect, as SDRAM writes do.
- The line-memory boundary is outside `n2d_system`, so the board tops bind the
  product `n2d_memory_controller` and the simulation wrappers either that
  controller or the [fast model](../memory/mas.md#fast-model).
- No alternative designs were recorded when this block came over from
  nand2anything.

## Verification

The [platform DV plan](../../dv/platform/dv-plan.md) checks `n2d_system`
through its wrappers and the product closure; the PMON platform events have
their own target in the [core DV plan](../../dv/core/dv-plan.md#pmon).
