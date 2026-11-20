# nand2doom host-debug and input architecture

Three external transports: one 25 MHz UART endpoint for host control, image
loading, memory peek/poke and bounded debug; one receive-only PS/2 Set-2
keyboard; and a host keyboard over the USB-Blaster Virtual JTAG cable. PS/2,
UART `CMD_INPUT`, CPU INJECT and JTAG all produce the same ten-bit Set-2 event
in one eight-entry FIFO. No path changes the ISA; the
[platform](../platform/spec.md#address-map) owns address decode. The
[host CLI](../../scripts/n2d_uart/spec.md) owns serial transport, identity
gating, manifest validation and acknowledged loading; the [DV plan](../../dv/io/plan.md)
owns verification.

<!-- wiki-budget: three wire protocols (version 1, bulk, stream), the common endpoint adoption, their bounds and the input arbitration are register-level contracts for RTL and host tools -->

## Requirements

| ID | Requirement |
| --- | --- |
| IO-R1 | UART 8-N-1, LSB first, common PHY, at the [link rate](#link-rate). |
| IO-R2 | One decoded host operation outstanding; requests and responses carry the same sequence. |
| IO-R3 | Version-1 memory writes are atomic: malformed, unsupported, timed-out, misaligned, out-of-range, busy and memory-error requests return an explicit failure with no partial write. A publication failure restores every earlier published line. |
| IO-R4 | Peek/Poke/Load need an acknowledged, cache-maintained Halt and never snoop a running cache. Host memory windows: SDRAM, framebuffer, palette only. |
| IO-R5 | Resume invalidates caches before releasing halt and before its OK response. Reset-PC never resumes. Reset starts with halt requested. |
| IO-R6 | A Halt timeout leaves the stop request asserted and maintenance active; Ping reports only core halt acknowledgement. |
| IO-R7 | [Bulk](#bulk-wire-extension) packets commit as ascending atomic eight-word transactions; any failure quarantines until hardware reset. |
| IO-R8 | [Stream](#streaming-load-session) loads resynchronize, accept idempotent resends and digest stored memory. |
| IO-R9 | Every input source produces `{extended, break, scan_code}` in one FIFO with fixed priority; UART, CPU INJECT and JTAG input are never silently lost. |
| IO-R10 | Host tools refuse game assets without the manifest and provenance of the [software policy](../../sw/spec.md#doom-source-and-assets). |
| IO-R11 | The common [UART memory endpoint](#uart-memory-endpoint) loads SDRAM while held and reads the displayed frame and palette while the CPU runs, costing it at most one framebuffer or palette port cycle per 64 clocks. |

## UART bridge

`src/nand2doom/rtl/io/n2d_uart_debug.sv` owns the shared PHY and routes
bytes through four routers in fixed order: the common
[UART memory endpoint](#uart-memory-endpoint), the
[stream session](#streaming-load-session), the
[bulk framer](#bulk-wire-extension) and the version-1 engine
(`n2d_uart_debug_engine`). No software UART, GDB protocol or board policy.
Reset is asynchronous active high: it clears packet and transaction state and
asserts `halt_req`. Everything else advances on `clk_i`.

### Port groups

Packed one-direction groups from `n2d_pkg` (`src/nand2doom/rtl/platform/n2d_pkg.sv`),
plus `clk_i`, `reset_i`, `uart_rx_i`, `uart_tx_o` and the scalar
`input_event_ready_i`. Cocotb targets use the stateless flat-pin wrapper
`src/nand2doom/dv/io/n2d_uart_debug_pins.sv`.

| Port | Type | Fields |
| --- | --- | --- |
| `debug_control_o` | `n2d_debug_control_t` | `halt_req`, `cache_invalidate`, `reset_pc_valid`, `reset_pc[31:0]` |
| `debug_status_i` | `n2d_debug_status_t` | `halt_ack`, `cache_maint_done` |
| `input_event_o` | `n2d_input_event_t` | `valid`, `code[9:0]` |
| `txn_req_o` | `n2d_debug_txn_req_t` | `arb_req`, `txn_begin`, `txn_commit`, `txn_abort` |
| `txn_status_i` | `n2d_debug_txn_status_t` | `arb_grant`, `txn_done`, `txn_error` |
| `mem_req_o` | `n2d_debug_mem_req_t` | `valid`, `write`, `addr[31:0]`, `wdata[31:0]`, `wstrb[3:0]` |
| `mem_rsp_i` | `n2d_debug_mem_rsp_t` | `ready`, `rsp_valid`, `rdata[31:0]`, `rsp_error` |
| `screen_req_o` | `n2d_screen_req_t` | `valid`, `palette`, `offset[15:0]` |
| `screen_rsp_i` | `n2d_screen_rsp_t` | `ready`, `rsp_valid`, `rdata[31:0]` |

`n2d_pkg` also owns command and status codes, start bytes, the INJECT address
and the pure CRC-8, CRC-32, range and command helpers. Engine-only constants
are exempt from unused-parameter lint because the bulk-router fixture
elaborates the framer alone. Parser, discard parser and executor state are
packed structs in `n2d_types_pkg` with asynchronous reset; parameter-sized
fields (payload images sized by `MAX_WORDS`, indices, timeout counters) are
separate registers. The response-start bundle is last in the executor's
next-state block, so it wins over the serializer advance.

## Version-1 wire protocol

Little-endian. CRC-8/ATM: polynomial `0x07`, init 0, no reflection, no final
XOR. Codec: `src/nand2doom/scripts/n2d_uart_debug_host.py`.

| Request byte | Field | Response byte | Field |
| ---: | --- | ---: | --- |
| 0 | `0xa5` (not in CRC) | 0 | `0x5a` (not in CRC) |
| 1 | Version `0x01` | 1 | Version `0x01` |
| 2 | Command | 2 | Command OR `0x80` |
| 3 | Sequence | 3 | Sequence |
| 4-7 | Byte address | 4 | Status |
| 8 | Word count `0..8` | 5 | Returned words (0 on errors and non-data commands) |
| 9 | Byte enables `[3:0]` | 6... | Four bytes per word |
| 10... | Four bytes per word (Load, Poke) | last | CRC over bytes 1..end |
| last | CRC over bytes 1..end | | |

| Command | Code | Semantics |
| --- | --- | --- |
| Ping | `0x01` | Any state. Two words: identity `0x3144_324e` (`N2D1`) and bit 0 halt-acknowledged. |
| Halt | `0x02` | Precise stop; OK only after halt acknowledgement and cache maintenance. |
| Resume | `0x03` | Needs maintained halt; pulses `cache_invalidate_o`, deasserts halt, waits one clock, then responds OK. |
| Reset-PC | `0x04` | Needs maintained halt; address field, aligned SDRAM; pulses `reset_pc_valid_o`, stays halted. |
| Capabilities | `0x05` | Zero address/count/enables; any state; three words: `0x3242324e` (`N2B2`), max payload 4096, flags 3 (bit0 write, bit1 read). |
| Peek | `0x10` | 1..8 words; byte enables ignored. |
| Poke | `0x11` | 1..8 words; ≥1 enable, same enables per word. |
| Load | `0x12` | 1..8 words; enables must be `0xf`. |
| Input | `0x20` | Address exactly `0x2000_200c`, count 1, enables `0xf`, bits 31:10 zero, bits 9:0 the event. Misaligned → alignment; other address → range; other shape → length. Waits on FIFO ready: OK on acceptance, BUSY after `OP_TIMEOUT_CYCLES`. No halt, maintenance, arbitration or memory traffic. |

Statuses: OK `0x00`, version `0x01`, unsupported `0x02`, checksum `0x03`,
alignment `0x04`, range `0x05`, length/byte-enable `0x06`, not halted `0x07`,
timeout `0x08`, memory `0x09`, UART framing `0x0a`, grant `0x0b`, busy `0x0c`,
internal protocol failure `0x0d` (bulk only).

### Parser and bounded progress

- The parser waits for `0xa5`, collects the nine-byte header, at most 32
  payload bytes and the CRC; nothing happens before the CRC passes. A framing
  error discards the request; a 512-cycle gap inside a packet returns timeout
  with the command and sequence captured so far.
- A discard-only parser consumes one complete CRC-valid frame arriving while
  an operation or response is active into a one-entry rejection slot; a
  same-sequence BUSY follows the active response. Malformed overlap frames
  are discarded.
- `OP_TIMEOUT_CYCLES` is 1,024 standalone and 4,096 in the platform.
  `HALT_TIMEOUT_CYCLES` follows it standalone and is **65,536** in the
  platform ([maintained Halt bound](../memory/spec.md#maintained-halt-bound)).
  Halt expiry returns one timeout and sends nothing later.
- The platform asserts `cache_maint_done_i` only after both caches'
  maintenance succeeds, retries failures while halted (Peek/Poke/Load get
  `ST_NOT_HALTED` until then) and acknowledges no host transaction during the
  pre-resume invalidation.

### Host-memory transaction boundary

Legal bursts are 1..8 aligned words inside one window: SDRAM
`0x0000_0000..0x03ff_ffff`, framebuffer `0x1000_0000..0x1000_ffff`, palette
`0x1001_0000..0x1001_0fff`. System, video control, PS/2, UART and unmapped
addresses are rejected.

1. Assert `arb_req_o`; no memory request before `arb_grant_i`, which must
   stay high throughout.
2. `txn_begin` opens an exclusive transaction; one word at a time; write
   responses acknowledge staging, not commit.
3. After every word succeeds, `txn_commit` starts publication; the bridge
   holds `arb_req_o` and sends nothing until `txn_done`; `txn_error` then
   selects memory error.
4. Grant loss, memory error or a 1,024-cycle request/response/commit timeout
   emits `txn_abort` and waits for `txn_done` (discard or reverse rollback)
   before the error response.

Assertions (`N2A_ASSERT_CLOCKED_ARST_FATAL*`) reject memory traffic outside
halt and grant, simultaneous commit and abort, unknown PHY busy state and
resume invalidation while halted; the bulk framer's two history properties use
`N2A_ASSERT_AFTER_RESET`.

## Bulk wire extension

`n2d_uart_bulk` owns version-2 framing over the shared PHY and issues internal
eight-word legacy requests to the unchanged engine. Clients must discover it
with Capabilities; there is no automatic fallback or retry.

CRC32 is IEEE reflected `0xedb88320`, init and final XOR `0xffffffff`
(`zlib.crc32`) over every byte after the delimiter, sent little-endian.

| Request byte | Meaning | Response byte | Meaning |
| --- | --- | --- | --- |
| 0 | `0xa6` | 0 | `0x6a` |
| 1 | Version 2 | 1 | Version 2 |
| 2 | `0x31` write, `0x32` read | 2 | Command OR `0x80` |
| 3 | Sequence u8 | 3 | Sequence |
| 4..7 | Address u32 | 4 | Status |
| 8..9 | Length u16, 4..4096, multiple of four | 5..8 | Echoed address |
| 10.. | Write payload | 9..10 | Payload length (read success only) |
| final 4 | CRC32 | 11..12 | Completed memory bytes |
| | | 13.. | Read payload |
| | | final 4 | CRC32 |

- Address word-aligned; 33-bit end arithmetic, no wrap. Writes: 64 MiB SDRAM
  only. Reads: SDRAM or the 64 KiB framebuffer. Byte tails use masked Poke.
- Staging: one 4096x8 `n2a_sync_ram` (synchronous read, no array reset); RX,
  internal read-response writes and TX reads use disjoint phases with a
  three-cycle per-byte scheduler; UART stalls hold address and data.
- At a legacy frame boundary a bulk SOF claims routing even while the old
  executor is busy; the old response drains, then receives BUSY. A delimiter
  inside a legacy frame is legacy data. During bulk service no byte reaches
  either legacy parser.
- Framing, CRC and length errors before publication have zero memory effect.
  Writes execute as ascending atomic eight-word transactions: a failure keeps
  the committed prefix, rolls back the failing chunk and reports the
  committed byte count. Read failure returns no payload. The adapter waits for
  commit or rollback and never releases memory mid-rollback.
- Any failed response, receive framing or timeout error, or unsolicited RX
  latches **quarantine** until hardware reset: the error response finishes
  but no later byte reaches the engine. Reset is an operator action.
- Bounds: 1,000,000 cycles (40 ms) between bulk bytes (legacy gap stays 512);
  50,000,000 cycles (2 s) for the whole receive phase, stopped before memory
  execution. A maximal malformed write (65,535 payload, 65,549 wire bytes)
  takes 655.49 ms at 1 Mbaud and is discarded by its declared length. Host
  defaults: 1 s per command (may poison, never retry), 1800 s whole load.
  Per-direction wire time is at most 41 ms; the 1,024/4,096-clock watchdogs
  apply per internal operation.
- A valid packet CRC is not storage proof: the host reads back. Bulk readout
  is not a frame/palette capture handshake; the capture owner reads its SDRAM
  palette shadow.

## Link rate

`n2d_uart_debug` derives the bit period from `CLOCK_HZ` (default
`N2D_SYSTEM_CLOCK_HZ`, 25 MHz) and `BAUD` (default `N2D_UART_BAUD`, 1 Mbaud) as
whole clocks plus a remainder fed to the common PHYs' fractional accumulator
([fractional period](https://github.com/amichai-bd/nand2anything/blob/aeebe92/wiki/src/common/uart/HAS.md#fractional-period)), so the
average rate is exact. `n2d_system` exposes `UART_BAUD` (default 1 Mbaud: the
DE10-Lite and simulation fixtures). The DE2-115 selects `N2D_UART_LOAD_BAUD`,
**3 Mbaud** (8 1/3 clocks/bit) for the whole link.
`src/nand2doom/cfg/uart.json` records `board_baudrates`; every host `--baud`
accepts a board name.

3 Mbaud is the FT232R maximum and an exactly generated rate (3 MHz /
(n + k/8), n ≥ 2, plus 3 and 2 Mbaud). Integer-divisor rates 3.125 Mbaud and
2.083 Mbaud sit 4.2% from the nearest FT232R rate, too close to the last-bit
edge.

### Bit-timing error budget

Each frame resynchronizes on its start edge. Assumptions: FT232R internal
oscillator ±1667 ppm
([datasheet](https://ftdichip.com/wp-content/uploads/2020/08/DS_FT232R.pdf)
§5.4-5.5); device oscillator ±100 ppm (<0.4 ns effect); FT232R sampler
(undocumented) 0..20.8 ns detection, centre sample, ±20.8 ns vote. At
3 Mbaud the device samples bit k (start 0, stop 9) at `4, 12, 20, 29, 37, 45,
54, 62, 70, 79` clocks after capturing the start edge and transmits bit k at
`0, 8, 16, 25, 33, 41, 50, 58, 66, 75` (frame end 83); at 2 Mbaud
`6, 18, 31, 43, 56, 68, 81, 93, 106, 118` and `0, 12, 25, ..., 112` (end 125). Worst case over all
four tolerance corners:

| Rate | Path | Margin, ideal clocks | Margin with tolerances | Share of a bit |
| --- | --- | ---: | ---: | ---: |
| 3 Mbaud (333.3 ns) | Device receive | 133.3 ns | 127.5 ns | 38% |
| 3 Mbaud | Host receive | 98.3 ns | **93.9 ns** | **28%** |
| 2 Mbaud (500 ns) | Device receive | 220.0 ns | 212.0 ns | 42% |
| 2 Mbaud | Host receive | 188.3 ns | 180.8 ns | 36% |

The load rate stays 3 Mbaud (Doom manifest wire time 98.9 s, plus USB
scheduling and one 7.3-million-word digest scan). If bring-up shows errors
resends cannot absorb, the fallback is exactly 2 Mbaud by changing
`N2D_UART_LOAD_BAUD` alone (148 s wire time).

## Streaming load session

`src/nand2doom/rtl/io/n2d_uart_stream.sv` is the first router. A `0xa7`
arriving while the bulk framer (`idle_o`), the engine (`idle_o`,
`frame_idle_o`) and the transmitter are idle opens a session and is consumed
as a frame start; any other `0xa7` is legacy data. During a session no byte,
framing error or transmit grant reaches bulk or the engine, the stream owns
the transmitter and debug memory port, and the engine must not request memory
(asserted). A bulk quarantine keeps the stream from opening.

| Byte | Host frame field |
| ---: | --- |
| 0 | `0xa7` |
| 1 | Kind: `0x48` Hello, `0x57` Write, `0x56` Digest, `0x45` End |
| 2..3 | Sequence u16, echoed |
| 4..7 | SDRAM byte address |
| 8..11 | Length: Write 1..1024; Digest 1 byte..64 MiB; Hello/End 0 |
| 12 | CRC-8/ATM over bytes 1..11 |
| 13.. | Write payload; Digest 4-byte seed |
| last 4 | CRC-32 (zlib) over bytes 1..last payload byte |

Replies are ten bytes: `0x7a`, kind OR `0x80`, sequence (2), status (version-1
codes), u32 value, CRC-8 over bytes 1..8. Values: Hello `0x3153324e`
(`N2S1`); Write the frame address; Digest the raw CRC-32 register; End the
number of Writes committed OK. A Digest scans stored SDRAM from `address`
through the debug port, continuing the reflected CRC-32 from `seed` without
inversion: chaining from `0xffffffff` yields the complement of the whole-image
CRC-32.

- **Receive.** Hunt for `0xa7`; a bad header CRC is dropped silently. A good
  header with unknown kind or illegal length is answered command or length
  status. Otherwise the payload and CRC-32 are consumed exactly; failures are
  answered checksum, alignment or range (33-bit end, SDRAM only). A framing
  error or 1,000,000 clocks (40 ms) without a byte abandons only that frame.
  A dropped byte costs that frame (checksum reply) and the next (no reply).
- **Queue.** Two 1 KiB slots in one 512x32 `n2a_sdp_ram`; a slot is reserved
  on a good header; a frame finding both full is dropped without reply. Only
  CRC-valid frames do memory work, and only in maintained Halt (else
  not-halted). No credit gates transmission (a 1 KiB frame is ~87,000 clocks
  at 3 Mbaud); only a Digest makes the host wait for its reply.
- **Commit.** Ascending eight-word transactions through the unchanged
  `n2d_debug_memory_adapter`; a short final word uses low byte enables. Grant
  loss, word error or an `OP_TIMEOUT_CYCLES` timeout aborts, waits for
  rollback and answers grant, memory or timeout; earlier transactions stay
  committed and the host resends. Replies leave in frame order.
- **End.** End is answered with the committed count and the session closes
  after the reply leaves; frames behind End are discarded. 50,000,000 idle
  clocks (2 s, nothing received, queued or in flight) or reset also close it.
  A killed host's partial frame is dropped after 40 ms; a restarted host's
  Hello or End is then parsed normally.
- **Assertions.** Queue above two, a byte reaching the legacy path, memory
  access outside maintained Halt, simultaneous commit and abort, a write into
  an occupied slot, and an engine memory request during a session.

## UART memory endpoint

`n2d_uart_debug` instantiates the common
[`n2a_uart_mem`](https://github.com/amichai-bd/nand2anything/blob/aeebe92/wiki/src/common/uart_mem/HAS.md) as its outermost router, on
every board top and the ASIC SoC. Its `0xa8` claims the link only while the
stream session, bulk framer and engine are all between frames, and while
`n2d_uart_mem_port` is idle. A bulk quarantine therefore keeps it closed until
hardware reset, as it does the stream.

| Parameter | Value |
| --- | --- |
| Project | `0x2064326e` (`n2d `) |
| Entry | exactly `0x0000_0000`: the reset vector |
| Max payload | 4096 bytes |
| Request gap | 40 ms (`STREAM_GAP_TIMEOUT_CYCLES`) |
| Hold timeout | `HALT_TIMEOUT_CYCLES`, the [maintained Halt bound](../memory/spec.md#maintained-halt-bound) |
| Bus timeout | `OP_TIMEOUT_CYCLES` |
| Live gap | 64 clocks |

| Window | Base | Size | Attributes |
| --- | --- | ---: | --- |
| SDRAM | `0x0000_0000` | 64 MiB | write, held read |
| Front framebuffer view | `0x1002_0000` | 64,000 | held and live read |
| Scanned palette view | `0x1003_0000` | 1 KiB | held and live read |

The scanned palette view exists only for the endpoint; the CPU map has no
`0x1003_0000` window.

- **Hold.** Held means maintained Halt (`halt_ack` and `cache_maint_done`).
  Hold ORs into the engine's `halt_req`. Release also pulses the engine's
  `release_i`, which drops its own halt request. A bench release therefore
  runs the CPU, although the engine resets with halt requested.
- **Entry.** Entry pulses `reset_pc_valid`, like Reset-PC; the core restarts
  at its reset vector.
- **SDRAM.** `n2d_uart_mem_port` issues each SDRAM word as one transaction of
  the [host-memory path](#host-memory-transaction-boundary): begin, one word,
  then commit, or abort on error. Loads keep that path's staging and
  rollback. The memory port mux gives the endpoint port priority while it is
  busy, then the stream session, then the engine.
- **Screen reads.** A screen address becomes one `n2d_system` screen read.
  It goes to the framebuffer port with the front-bank select, or to palette
  port A with `palette_request_active_i` (the active bank; never a write). It
  is answered on the next clock. The halted debug bridge has priority over
  it, and it has priority over the CPU, which waits that one cycle.
- **CPU cost.** The endpoint paces live reads 64 clocks apart, so the CPU
  loses at most one framebuffer or palette port cycle per 64 clocks. SDRAM,
  the caches and VGA scanout are untouched. At 3 Mbaud a 64,000-byte frame
  plus the palette is about 0.22 s on the wire, about 4.5 fps. At 1 Mbaud it
  is about 0.65 s.
- **Assertions.** No byte reaches the n2d routers during an endpoint frame.
  No other path requests memory while the port is busy. A release never
  arrives while the engine owns a frame.

## PS/2 keyboard receiver

`src/nand2doom/rtl/io/n2d_ps2_keyboard.sv`, receive-only at 25 MHz. Board
tops wire `PS2_CLK`/`PS2_DAT` straight through `n2d_system`; no pin adapter.

- Each pin passes its own `n2a_cdc_bit_sync`; data is sampled on registered
  falling clock edges. Frame: low start, eight LSB-first bits, odd parity,
  high stop. Bad start, parity or stop discards the frame and sets its sticky
  diagnostic; no falling edge for 5,000 clocks (200 µs) discards and sets
  timeout. Every discard clears pending prefixes.
- `E0`/`F0` set pending extended/break; the next byte forms the event (bit 9
  extended, bit 8 break, bits 7:0 code) into an eight-entry circular FIFO.
- Full: the new event is dropped and overflow set; a same-edge pop makes room.
- Push priority: CONTROL (suppresses all pushes), physical PS/2 (suppresses
  the rest even when dropped), UART injection (held valid until ready), CPU
  INJECT (not ready until it owns a slot), scan-inject (JTAG). A DATA pop
  counts in the space calculation. At most one push per edge (asserted).
- Every accepted CONTROL write abandons the active frame and pending
  prefixes, including an event whose stop edge is on that clock. Disabling
  receive keeps queued events and diagnostics. Reset enables receive and
  clears everything.
- Excluded: transmit, layout translation, typematic, interrupts, hot plug.

### PS/2 MMIO registers

12-bit offset in window `0x2000_2000`; single-outstanding ready/valid channel;
responses stable under backpressure.

| Offset | Access | Definition |
| ---: | --- | --- |
| `0x000` DATA | read | Bit 31 valid; bit 9 extended; bit 8 break; bits 7:0 scan code. A valid read pops one event; empty reads zero. |
| `0x004` STATUS | read | Bit 0 valid, 1 empty, 2 full, 6:3 count, 8/9/10/11 sticky overflow/parity/frame/timeout, 12 receiver enabled. |
| `0x008` CONTROL | read/write | Read bit 0 enabled. A lane-0 write sets enable from bit 0, clears the FIFO with bit 1, clears diagnostics with bit 2; clears beat simultaneous receive. |
| `0x00c` INJECT | write | Full-word only; bits 31:10 zero, bits 9:0 one event. Backpressures until it owns a slot. Reads and malformed writes error. |

Writes to DATA or STATUS, unmapped offsets and CONTROL writes to upper lanes
error with no effect; a CONTROL write with no lane is accepted with no effect.

## JTAG keyboard

The common [Virtual JTAG keyboard endpoint](https://github.com/amichai-bd/nand2anything/blob/aeebe92/wiki/src/common/jtag/HAS.md) lets a
host offer up to four raw Set-2 bytes per command through a held mailbox. The
[host tool](../../scripts/n2d_jtag_keyboard/spec.md) sends Doom actions, raw
events or text through the shared [JTAG transport](../../../common/scripts/jtag/spec.md).

Scan-inject port (`scan_inject_valid_i`, `scan_inject_byte_i`,
`scan_inject_ready_o`): an offer lasts one cycle and is accepted only when
ready. `E0`/`F0` set the port's own pending qualifiers and are always ready.
Any other byte is ready only when no CONTROL, physical completion, UART valid
or accepted INJECT owns the edge and a slot is free (a same-edge pop counts);
acceptance clears the qualifiers, refusal keeps them, so the host resubmits
only the refused suffix (the endpoint reports the accepted prefix). Only reset
and the completing byte clear these qualifiers; CONTROL, disable and physical
errors do not, and a refusal never sets overflow.

### JTAG keyboard composition

`n2d_system` instantiates `n2a_jtag_keyboard` (`u_jtag_keyboard`) on
`clk_i`/`reset_i`, takes its neutral scan ports from the `scan_*` boundary and
joins `inject_*` to the scan-inject port. The endpoint owns the IR1/IR2
protocol, TCK/system crossings and the hub's late-TDI rule (the command is the
last 64 bits) ([common HAS](https://github.com/amichai-bd/nand2anything/blob/aeebe92/wiki/src/common/jtag/HAS.md)). Each board top
(`n2d_de10_lite`, `n2d_de2_115`) binds the common `n2a_jtag_virtual`
(`u_jtag_virtual`) to those ports; the ASIC top passes its own pins and DV
compositions tie them idle, so the scan domain stays in reset. The SDCs
constrain the hub TCK and mailbox buses ([FPGA timing](../../fpga/spec.md#constraints)).

## Files

| Path | Role |
| --- | --- |
| `src/nand2doom/rtl/io/n2d_uart_debug.sv` | PHY, routers, debug control |
| `src/nand2doom/rtl/io/n2d_uart_debug_engine.sv` | Version-1 parser and executor |
| `src/nand2doom/rtl/io/n2d_uart_bulk.sv` | Bulk framer |
| `src/nand2doom/rtl/io/n2d_uart_stream.sv` | Stream session |
| `src/nand2doom/rtl/io/n2d_uart_mem_port.sv` | Common endpoint bus adapter |
| `src/nand2doom/rtl/io/n2d_ps2_keyboard.sv` | PS/2 receiver, FIFO, MMIO |
| `src/nand2doom/rtl/platform/n2d_pkg.sv`, `n2d_types_pkg.sv` | Codes, helpers, state records |
| `src/nand2doom/scripts/n2d_uart_debug_host.py` | Host codec |
