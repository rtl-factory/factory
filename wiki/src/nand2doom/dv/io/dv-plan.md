# io DV plan

## Scope

The I/O block (`n2d_uart_debug` with its version-1 engine, bulk framer,
stream session and UART memory endpoint adapter; `n2d_ps2_keyboard`; the JTAG
keyboard composition), checked against the [io MAS](../../rtl/io/mas.md),
and the maintained Halt bound of the [memory MAS](../../rtl/memory/mas.md#maintained-halt-bound),
which needs the full system. The host scripts' hardware-free tests are in the
[host-script contract](../../scripts/host/spec.md#uart-tests); physical board
loads and keyboard play are separate, authorized acceptance.

## Testbench

Serial targets drive and sample the real UART pins through Verilator and
cocotb at 25 MHz; a typed shortcut never replaces a serial-pin test. The host
codec `n2d_uart_debug_host.py` is an independent wire oracle (framing,
little-endian fields, CRC-8/CRC-32, sequences, statuses, payloads). Input
tests observe only public MMIO and endpoint status. Generated logs, XML and
opt-in waves stay under `workdir/cocotb/`. The
[host-script contract](../../scripts/host/spec.md#uart-tests)
adds hardware-free transport and manifest tests; host-only changes rerun the
two UART targets for codec compatibility.

## Features

Spec section names the requirement in the [io MAS](../../rtl/io/mas.md#requirements)
(the maintained Halt rows also check MEM-R9). Level is the target's level in
`src/nand2doom/cfg/targets.json`.

| ID | Spec section | Stimulus | Checker | Coverage point | Test | Level |
|---|---|---|---|---|---|---|
| IO-F01 | IO-R2..R5 | Ping identity and reset-held status; Load, Peek readback, byte-enabled Poke; sentinels across writes and rejections; overlapping Poke rejected with same-sequence BUSY while the active Peek completes; resume invalidation order, running rejection, delayed halt/maintenance acknowledgement, halted Reset-PC; bad version, command, checksum, alignment, window, zero and oversized length, Load and Poke strobes; missing grant and backpressure to timeout and abort; three-word writes failing on a later word (memory error, response timeout, grant loss) abort exactly once with no commit; inter-byte timeout, framing error and recovery. | Host codec `n2d_uart_debug_host.py` (wire oracle); memory sentinels | none defined | `n2d_uart_debug` (33; 220,000 clocks, 60 s) via `n2d_uart_debug_pins` | L0 |
| IO-F02 | IO-R9 (UART input) | `CMD_INPUT` while running, stable event under delayed ready, no halt/cache/arbitration/memory activity, BUSY while full, malformed address/count/strobe/data, recovery. | Host codec `n2d_uart_debug_host.py` (wire oracle); memory sentinels | none defined | `n2d_uart_debug` | L0 |
| IO-F03 | IO-R3 rollback | Error on the second publication after the first completed: grant stays requested, no response before rollback, both words keep original values, memory error, then load/peek succeed. | Host codec `n2d_uart_debug_host.py` (wire oracle); original word values | none defined | `n2d_uart_debug_memory` (31; 220,000 clocks, 60 s) | L0 |
| IO-F04 | IO-R6, maintained Halt | Product system, both caches, real SDRAM controller and shared burst-eight CAS-two pin model. RV32 stores dirty every D-cache line (2,048 for four ways, read from `DCACHE_WAYS`); serial Halt succeeds only after all 16,384 halfword publications; all 32 KiB compared; the reply starts within 49,962 clocks (25,386 for two ways) of the [maintained Halt bound](../../rtl/memory/mas.md#maintained-halt-bound). Old-code negative control fails on Halt timeout with no late success. Evidence records request start, request end and first reply start. | Shared burst-eight CAS-two pin model; all 32 KiB compared; reply-start bound | none defined | `n2d_halt_sdram` (82; 500,000 cycles/20 ms, 180 s) | L1 |
| IO-F05 | IO-R6 boundaries | Clean and partly dirty caches, a data request held across Halt, stalled or repeatedly failing maintenance through a DV-only pre-acceptance write fault; failures time out finitely, keep Peek and Resume closed and retain dirty data; a stalled instruction refill completes the drain with no retirement; Resume continues without replaying stores. | Halt, Peek and Resume responses; retained dirty data | none defined | `n2d_halt_boundaries` (82; 1,500,000 cycles/60 ms, 180 s) | L1 |
| IO-F06 | IO-R7 | 4/32/36-byte boundaries, two 4096-byte blocks, sentinels, legacy Ping after each exchange; staging/commit deltas forbid duplicates; reads are side-effect free; one atomic transaction per eight words. Measured 4,404,492 cycles, 140 s idle (Python-bound memory model); the 600 s bound is a safety envelope. | Independent CRC32 model; staging/commit deltas; sentinels | none defined | `n2d_uart_bulk` (81; 5,000,000 cycles, 600 s) | L1 |
| IO-F07 | IO-R7 negatives | Embedded legacy Halt/Resume in corrupt, oversized and truncated bodies; invalid ranges; read failure with no payload; grant/backpressure timeout; bulk-during-legacy BUSY; quarantine with no stray dispatch; a 1 ms valid-body pause; the real 40 ms gap on truncation. | Responses, quarantine and dispatch checks | none defined | `n2d_uart_bulk_negative` (180 s) | L1 |
| IO-F08 | IO-R7 bounds | All 65,535 bytes of a maximal oversized body; response stability under stalls; no legacy dispatch. Fixture scales absolute/gap counters to 70,000/1,024 cycles: drip input hits the absolute bound without a gap timeout. | Response stability; no legacy dispatch | none defined | `n2d_uart_bulk_router` | L0 |
| IO-F09 | IO-R7 rollback | Late second-chunk failure under variable delays and stalls: first 32 bytes committed, current chunk restored, sentinels kept, grant held through rollback; a later Resume is ignored in quarantine. | Committed bytes, restored chunk and sentinels | none defined | `n2d_uart_bulk_memory` | L0 |
| IO-F10 | Bulk readout | Compiled C/input/cache/VGA oracles, then the full 64,000-byte framebuffer and a 1,024-byte SDRAM palette fixture in exactly 17 bulk reads under held Halt. Not the firmware capture handshake. | Compiled C/input/cache/VGA oracles | none defined | `n2d_bulk_frame` | L2 |
| IO-F11 | IO-R8 | See [streaming load](#streaming-load). | Python 16-byte line model; chained digests | none defined | `n2d_uart_stream` (125; 3,000,000 clocks, 900 s) | L1 |
| IO-F12 | IO-R11 | See [UART memory endpoint](#uart-memory-endpoint). | Bench codec `uart_mem.py` and the external-SDRAM pin model | none defined | `n2d_de2_115_uart_mem`, `n2d_de10_lite_uart_mem` (59) | L1 |
| IO-F13 | IO-R9 (PS/2) | Non-harmonic pin timing; make/break and extended sequences; FIFO order and every count; overflow drop-new; response backpressure; disable; clear priority; bad start/parity/stop; partial-frame timeout; clear coincident with stop; mid-frame clear with no ghost event or stale prefix; invalid MMIO; UART-only injection while disabled; physical > UART > MMIO priority; pop plus UART at full; CONTROL over UART; legal and malformed INJECT. Scan-inject idle. | Public MMIO and endpoint status | none defined | `n2d_ps2_keyboard` (34; 50,000 clocks, 60 s) | L0 |
| IO-F14 | IO-R9 (JTAG) | See [JTAG keyboard input](#jtag-keyboard-input). | White-box offer monitor; exact FIFO events | none defined | `n2d_jtag_input` (128; 1,500,000 clocks, 300 s) | L0 |

Host bulk tests use an independent stdlib CRC32 model, fragmented reads,
sequence wrap, checked-prefix versus uncertain loss, no retries, no partial
snapshots, all masked tails, confinement and whole-operation reservation,
charging 16 ms per response plus 1 Mbaud wire time.

### Streaming load

`n2d_uart_stream` composes `n2d_uart_debug` at `BAUD=N2D_UART_LOAD_BAUD`
(3 Mbaud) with the real `n2d_debug_memory_adapter` in
`src/nand2doom/dv/io/n2d_uart_stream_memory.sv`, which scales the bounds to a
4,000-clock frame gap and 40,000-clock idle session. A Python 16-byte line
model with seeded 4..13-clock latency backs SDRAM with sentinels. The host
drives absolute 333.33 ns bit edges and samples each device bit at its centre
and ±100 ns, which must agree. Malformed frames come from a separate encoder.

- Hello with `N2S1`; a Write before Halt answered not-halted, no traffic.
- Range, alignment, unknown-kind and illegal-length frames answered in order.
- Three segments (2,500 bytes; 3,075 with a three-byte tail; 1,024 ending at
  64 MiB) as one burst with a corrupt payload (checksum), a dropped byte
  (checksum, next frame silent), a corrupt header (silent) and a truncated
  last frame (silent after the gap); replies in increasing sequence.
- Pipelined resend rounds, including a line-publication error answered memory
  after rollback, until all commit; exact bytes, sentinels and no stray line.
- Chained Digests equal to the model and the image CRC-32; a byte flipped in
  stored memory is detected.
- End returns the committed count; a version-1 Ping then succeeds.
- A host killed after 200 bytes of a Write: a restarted End is answered and
  Ping works; with no further traffic Ping works after the gap and idle
  bounds; neither truncated frame writes memory.
- Hello, two Writes, Digest and End with the host bit period at +1667 and
  −1667 ppm in both directions.

The `n2d_de2_115` board target also streams two Writes over JP5 at exactly
3 Mbaud, checks the chained digest and End and Peeks the tail word.
`common_uart_fractional` checks the PHY edge grid; host resend policy is in
`src/nand2doom/scripts/host/tests/test_n2d_uart_stream.py`
([host contract](../../scripts/host/spec.md#streaming-load)).

```text
python3 tools/sim/sim.py test n2d_uart_stream --seed 125
```

### UART memory endpoint

`board_uart_mem_endpoint` (`src/nand2doom/dv/fpga/uart_mem_board.py`) runs
through the real board tops: the DE2-115 at 3 Mbaud and the DE10-Lite at
1 Mbaud. Both use the external-SDRAM pin model, and the host side uses the
bench codec (`src/common/scripts/uart_mem/uart_mem.py`).
The scenario:

1. Identify: project, the three windows, and held from reset.
2. Hold, write a 72-byte RV32 program to SDRAM, read it back, entry 0,
   release. Identify then reports running.
3. The program sets palette entry 0, writes two back-bank words and arms a
   swap with a palette commit. It then loops, counting in SDRAM and writing
   the back bank.
4. Live reads of the front view are polled until they show the two words
   after the vertical-blank swap. The scanned palette view returns the
   committed colour, and a live SDRAM read answers not held.
5. A second hold: the loop counter read from SDRAM proves the program ran
   through the live reads.

The common block's own scoreboard and stall-bound tests are in the
[common DV](https://github.com/amichai-bd/nand2anything/blob/aeebe92/wiki/src/common/uart_mem/DV.md).

### JTAG keyboard input

`n2d_jtag_input` drives `src/nand2doom/dv/io/n2d_jtag_input_fixture.sv`,
which wires `n2a_jtag_keyboard` to `n2d_ps2_keyboard` exactly as `n2d_system`
does, using the [common endpoint Bench](https://github.com/amichai-bd/nand2anything/blob/aeebe92/wiki/src/common/jtag/DV.md) on a
free-running 25 MHz clock:

- every Doom action and text through the shared translation as exact
  make/break events, plus one extended break in a single command over a
  seven-bit padded hub scan;
- prefixes taken while full, the completing byte refused without overflow,
  and the resubmitted suffix completing after a pop;
- sixteen alignments of each competitor (CONTROL, physical completion, UART
  valid, CPU INJECT) against an offer launched with TCK stopped: the
  competitor lands, the offer is refused and its resubmission follows;
- concurrent physical, UART, INJECT and JTAG streams with disjoint codes:
  each arrives complete, once, in order, with no overflow.

A white-box monitor checks every offer against the arbitration rule and
requires each refusal reason to occur. System targets tie the scan ports idle
and list `src/nand2doom/dv/io/n2d_jtag_system_lint.v`, whose waivers are
`SYNCASYNCNET` on the endpoint's `u_sys_reset` and `u_scan_reset` stages and
`DECLFILENAME` on the common `sld_virtual_jtag` stub. Both board targets
(`n2d_de10_lite`, `n2d_de2_115`) elaborate the board top's
`n2a_jtag_virtual` on that stub and play the hub through it
(`board_jtag.py`): with
the CPU halted and PS/2 idle, W make/break and extended Up make/break (two
4-byte commands) must be exactly the four FIFO writes `01D 11D 275 375`.
`test_quartus` lints `n2a_jtag_virtual` against the stub; the host script is
covered by `src/nand2doom/scripts/host/tests/test_n2d_jtag_keyboard.py`.

## Coverage

No functional coverage points or code coverage numbers were recorded when the
plan came over from nand2anything, so every coverage point above is "none
defined". The maintained Halt target keeps an old-code negative control, and
the JTAG monitor requires each refusal reason to occur.

## Status

Physical board loads and keyboard play are separate, authorized acceptance.
