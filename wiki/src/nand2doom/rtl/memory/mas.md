# memory MAS

## Purpose

The memory system joins the core's instruction and data ports to external
SDRAM, keeping framebuffer traffic outside the caches. It owns the I-cache,
D-cache, line arbiter, debug adapter, SDRAM controller, cache maintenance and
the portable fast memory model, all at 25 MHz. The
[platform address map](../has.md#address-map) owns region values;
this page owns their cacheability and transactions. Framebuffer storage is
owned by [video](../video/mas.md); M9K packing and fits by the
[FPGA page](../../fpga/de10_lite/board-spec.md#memory-implementation). The [DV plan](../../dv/memory/dv-plan.md) owns
verification.

<!-- wiki-budget: cache, arbiter and SDRAM command/latency tables plus the maintained Halt budget are cycle contracts the RTL and DV check -->

### Requirements

| ID | Requirement |
| --- | --- |
| MEM-R1 | Separate blocking, physically addressed caches: read-only I-cache, write-back write-allocate D-cache ([geometry](#cache-and-line-contracts)). |
| MEM-R2 | SDRAM is cached and executable; framebuffer, palette and peripheral windows are uncached and bypass the D-cache. |
| MEM-R3 | Cache hits respond exactly one edge after acceptance; back-to-back hits sustain one access per cycle ([handshake](#cache-and-line-contracts)). |
| MEM-R4 | Debug Halt drains the core, writes back every dirty line, invalidates both caches and only then succeeds; Resume starts with both caches invalid. Host access needs no snooping. |
| MEM-R5 | No dirty data is lost: refill, writeback and maintenance errors retain the dirty line for retry. |
| MEM-R6 | One outstanding line transaction; retained-owner round robin: under continuous requests each master is accepted at least once per three completions. Prefetch and background drain have lowest priority. |
| MEM-R7 | Debug writes are all-or-original-image: up to eight words commit together or every published line is restored. |
| MEM-R8 | SDRAM refresh gaps never exceed 195 clocks (7.8 µs) under any traffic ([controller](#sdram-controller)). |
| MEM-R9 | Maintained Halt completes in under 65,536 clocks for either D-cache geometry on a healthy controller. |

## Interface

### Ports

Every port is in the `clk_i` domain; the SDRAM pins are bound by the board top
([composition](../../fpga/de10_lite/board-spec.md#composition)). Parameters:
`n2d_dcache` `WAYS` (2 or 4, default 4) and `PREFETCH_LINES` (0, 1 or 2,
default 0); `n2d_cached_rv32im` `DCACHE_WAYS` (default 4);
the common SDRAM controller `n2a_sdram_ctrl` takes the nand2doom configuration
(`BURST_BEATS` 8, `OPEN_ROW` 1, `ROW_XOR_BANK` 1, `INIT_WAIT_MIN` 2, the rest at
the controller defaults; [common MAS](../../../common/rtl/sdram/mas.md)) from the
`N2D_SDRAM_*` constants in `n2d_pkg`, which every bench fixture reads too;
`n2d_debug_memory_adapter` `MAX_WORDS` (8).

| Module | Port group | Ports (direction, width) | Contract |
| --- | --- | --- | --- |
| `n2d_icache` | CPU | `cpu_request_valid_i`, `cpu_request_address_i` (32) in, `cpu_request_ready_o` out; `cpu_response_valid_o`, `cpu_response_data_o` (32), `cpu_response_error_o` out | [CPU port handshake](#cpu-port-handshake) |
| `n2d_dcache` | CPU | as the I-cache plus `cpu_request_write_i`, `cpu_request_write_data_i` (32), `cpu_request_byte_enable_i` (4) in | [CPU port handshake](#cpu-port-handshake) |
| both caches | Line | `line_request_valid_o`, `line_request_write_o`, `line_request_address_o` (26), `line_request_write_data_o` (128), `line_request_byte_enable_o` (16) out, `line_request_ready_i` in; `line_response_valid_i`, `line_response_data_i` (128), `line_response_error_i` in; D-cache also `line_request_prefetch_o`, `line_request_background_o` out | [Line port](#line-port) |
| both caches | Maintenance | `maintenance_valid_i` in, `maintenance_ready_o`, `maintenance_done_o` out; D-cache also `maintenance_error_o` | [Maintenance](#maintenance) |
| both caches | Events | `hit_event_o`, `miss_event_o`; D-cache also `prefetch_issue_event_o`, `prefetch_useful_event_o`, `prefetch_drop_event_o`, `prefetch_deferred_event_o`, `prefetch_cancel_event_o`, `write_buffer_pending_o`, `write_buffer_wait_o` out | [Pipeline](#pipeline), [prefetch](#next-line-prefetch), [victim buffer](#dirty-victim-buffer) |
| `n2d_memory_arbiter` | Masters | `master_request_valid_i`, `master_request_prefetch_i`, `master_request_write_i` (3 each), `master_request_address_i` (78), `master_request_write_data_i` (384), `master_request_byte_enable_i` (48) in, `master_request_ready_o` (3) out; `master_response_valid_o` (3), `master_response_data_o` (384), `master_response_error_o` (3) out | Three packed line ports, owner order instruction, data, debug ([arbitration](#arbitration-and-debug-adaptation)) |
| `n2d_memory_arbiter` | Memory | one line port (`memory_request_*` out, `memory_request_ready_i` in, `memory_response_*` in) | [Line port](#line-port) |
| `n2d_memory_controller` | Owners | `instruction_request_valid_i`, `instruction_request_address_i` (26) in, `instruction_request_ready_o` out, `instruction_response_valid_o`, `instruction_response_data_o` (128), `instruction_response_error_o` out; `data_request_*` with `data_request_prefetch_i` and `debug_request_*` as full line ports | The instruction owner is read-only: it has no write or byte-enable input |
| `n2d_memory_controller`, `n2a_sdram_ctrl` | SDRAM | `init_done_o` out; `sdram_address_o` (13), `sdram_bank_o` (2), `sdram_cke_o`, `sdram_cs_n_o`, `sdram_ras_n_o`, `sdram_cas_n_o`, `sdram_we_n_o`, `sdram_dqm_o` (2), `sdram_dq_o` (16), `sdram_dq_oe_o` out; `sdram_dq_i` (16) in | [SDRAM controller](#sdram-controller); `n2a_sdram_ctrl` takes one line port (`request_*`, `response_*`) |
| `n2d_debug_memory_adapter` | Transaction and word | `transaction_begin_i`, `transaction_commit_i`, `transaction_abort_i` in, `transaction_done_o`, `transaction_error_o` out; `word_request_valid_i`, `word_request_write_i`, `word_request_address_i` (32), `word_request_write_data_i` (32), `word_request_byte_enable_i` (4) in, `word_request_ready_o` out; `word_response_valid_o`, `word_response_data_o` (32), `word_response_error_o` out; one line port out | [Debug adapter](#arbitration-and-debug-adaptation), driven per the [host-memory transaction boundary](../io/mas.md#host-memory-transaction-boundary) |
| `n2d_cached_rv32im` | Composition | `enable_i` in; instruction and data line ports, both maintenance handshakes, I/D hit and miss events, the core observation record and `halt_ack_o` | [Arbitration and debug adaptation](#arbitration-and-debug-adaptation) |

### Cache and line contracts

The D-cache way count (`n2d_system` `DCACHE_WAYS`) is the only geometry
parameter; four-way 32 KiB won the trace study of
[#74](https://github.com/amichai-bd/nand2anything/issues/74). The DE10-Lite
keeps two ways because 32 KiB two-way needs 16 more M9Ks than remain.

| Board | D-cache | Replacement | D-cache M9Ks |
| --- | --- | --- | ---: |
| DE2-115 (default) | 32 KiB, four-way | Tree pseudo-LRU, three bits per set | 36 |
| DE10-Lite | 16 KiB, two-way | One victim bit per set | 18 |

Address bits `[3:0]` select within a line, `[12:4]` the set, `[25:13]` the
13-bit tag, independent of way count.

### CPU port handshake

The caches implement the pipelined extension of the
[shared boundary](https://github.com/amichai-bd/nand2anything/blob/aeebe92/wiki/src/riscv/ownership.md#request-and-response-boundary); the
[core](../core/mas.md#pipelined-memory-handshake) owns the requester side.
Requests are 32-bit word transactions with byte address and byte enables.

- A request is accepted on a rising edge with reset low and valid and ready
  high; the payload is captured on that edge.
- One request outstanding per port; the edge that samples a response may
  accept the next request.
- A hit, or a request completed locally with error, responds one edge after
  acceptance. A miss responds after refill and any buffer drain.
- Ready depends only on cache state, never on valid: high when idle and in a
  hit's response cycle; low during a miss and while maintenance is requested
  or active.
- An instruction request may be withdrawn or re-addressed before acceptance
  with no effect. A data request and payload stay stable until accepted.
- Response valid has no ready; each sampled-high edge completes exactly the
  oldest pending request. Back-to-back hits keep it high with a new
  completion every edge; it never holds or repeats a response.
- Addresses outside the 26-bit SDRAM space, and unaligned instruction
  requests, complete locally with error and no lookup or event. Data requests
  keep their low address bits for lane selection.

### Line port

A 128-bit ready/valid line port carries operation, aligned 26-bit byte
address, write data, sixteen byte enables, response data and error. The
requester holds the full payload until accepted; exactly one later response
pulse returns to the captured owner. Reads use zero enables; writebacks all
16; halted-host word writes only their four lanes (no read-modify-write).
Lines must be 16-byte aligned within `0x0000_0000..0x03ff_ffff`; violations
return an error without an SDRAM command. An error response has no write side
effect. Each cache owns one outstanding line transaction.

### Maintained Halt bound

The platform budgets **65,536 clocks (2.62144 ms)** for core drain plus
maintenance, separate from the 4,096-clock debug-memory watchdogs. It assumes
an initialized, progressing controller, no injected errors and completing MMIO.
A blocked peripheral or repeated errors produce a bounded Halt failure; a
deadline never discards dirty data.

| Contribution | Clocks | Reason |
| --- | ---: | --- |
| CPU drain | 256 | Owed fetch, owed data operation and one execute-stage access: five 24-clock line services plus 16 pipeline clocks (136); a divide (34) excludes the second data access; a presented prefetch adds one service (160). |
| Victim buffer drain | 24 | One full-line write; serial, since the walk starts only once the buffer is empty. |
| D-cache walk | 2,048 x 24 = 49,152 (two-way 1,024 x 24 = 24,576) | One slot per way and set. |
| I-cache walk | 514 | 512 steps plus handshake; added although concurrent. |
| Coordination | 16 | Edge detection, RAM startup, completion. A Halt during the 512-clock post-reset sweep sees a clean cache. |
| **Total** | **49,962 (two-way 25,386)** | D-cache slots x 24 + 810; below 65,536; no serial time charged. |

A slot is 24 clocks: the slowest [SDRAM line](#sdram-controller) (row-miss
read 11, write 10), plus 4 for a refresh before acceptance and 5 for
sequencing. 2,048 dirty writes alone need ≥16,384 controller clocks, so a
4,096-clock allowance cannot work. After core acknowledgement no CPU request
competes. Ping reports only core acknowledgement; an expired Halt leaves
maintenance active and sends no late success.

## Microarchitecture

### Pipeline

Both caches drive the RAM read address from request bits `[12:4]` whenever
they can accept, so the accept edge reads every way's tag and data. LOOKUP,
the next cycle, compares tags, selects way and word and drives the hit
response combinationally from RAM outputs with ready still high. This RAM →
tag compare → ready/response capture path is the longest cache path. A miss
leaves LOOKUP with ready low and responds through registered fields the cycle
after the line response. Hit and registered responses never coincide; hit and
miss event pulses (one per valid lookup) are registered one edge after
LOOKUP.

### Instruction cache

`n2d_icache`: each way is one single-port `n2a_sync_ram` `g_way[w].u_way_ram`
of packed `{tag[12:0], line[127:0]}`. A miss picks the first invalid way,
else the replacement way, requests one line, installs it and returns the word
in the same completion; selecting a way makes the other the next victim. A
failed refill changes nothing. The RAM is addressed by the captured set only
in the line-response state (ready low). Valid and replacement bits are reset
flops (`DFF_RST_EN`). Invalidation clears both ways of one set per cycle
through set 511 without touching data.

### Data cache

`n2d_dcache` (`WAYS` 2 or 4): per way an SDP `n2a_sdp_ram`
`g_way[w].u_data_ram` (512x128), and one SDP `u_set_ram` metadata word per set
`{tag[WAYS-1:0][12:0], valid[WAYS-1:0], dirty[WAYS-1:0], plru[WAYS-2:0]}`
(512x31 or 512x63), separate because maintenance and the next request read a
set other than the one written.

- **Store hit.** Enabled bytes merge into the read line, which is written on
  the edge ending LOOKUP and marked dirty: no bubble. A one-cycle forward
  record (line, set, way, sharing the victim-line register) substitutes the
  written line in the next lookup of that set and way.
- **Metadata.** Every change rewrites the whole word: hit updates the tree
  (and dirty on a store); install writes tag, valid, dirty and tree;
  maintenance drops one way's valid and dirty. One write per clock. A
  one-cycle metadata forward covers same-set reads on the write edge.
- **Reset sweep.** Data RAMs are not reset. After reset the sweep writes zero
  to sets 0..511, one per clock, with request and maintenance ready low for
  those 512 clocks; a second reset forgets resident, even dirty, lines.
- **Replacement.** Lowest invalid way, else tree pseudo-LRU: `WAYS − 1` node
  bits heap-ordered (node `n` at bit `n−1`, children `2n`, `2n+1`), each
  naming the subtree of the next victim. A hit or install points every node
  on its path away from it. Two ways reduce to the I-cache bit. Maintenance
  does not reset the tree. An assertion forbids one tag in two ways.
- **Miss.** LOOKUP captures the victim line, tag and dirty ownership. Refill
  comes first unless the dirty victim needs an occupied buffer slot or the
  requested line is the buffered victim. A pending store merges before
  install and marks dirty. A refill or required-drain error completes the CPU
  operation with error and never installs. An assertion checks that an
  unaccepted data request stays stable.

### Dirty-victim buffer

One slot (valid, line address, 128 data bits, flops) owns displaced dirty
bytes until a successful full-mask write response. Only a successful install
transfers the victim; a refill error leaves it valid and dirty in the array.

- A second dirty eviction waits for space; a demand read of the buffered
  line waits for its writeback (a stale prefetch copy cannot serve it).
- The drain (`line_request_background_o`) is lowest priority at the arbiter
  and loses to a clean demand refill until presented; once presented it holds
  until accepted and its response completes before another D-cache line
  request. Hits continue meanwhile.
- A failed background drain keeps the slot and stops idle retries; a blocked
  demand or maintenance retries it and completes with error if that fails.
- Maintenance drains the slot before any array invalidation. Reset forgets
  buffered data: reset is a destructive boundary, not a flush.
- PMON 94 counts occupied-slot clocks and 95 miss clocks waiting for space
  or a same-line drain; both overlap other events.

### Next-line prefetch

`PREFETCH_LINES` (0, 1 or 2; module default 0) sizes a flop buffer;
`n2d_system` `DCACHE_PREFETCH_LINES` selects it and both board tops pass 2.

- **Trigger.** Every install (from memory or buffer) queues the next 16-byte
  line unless maintenance is requested, a prefetch is owed, or that line is
  buffered or in flight. Arrays are not checked.
- **Priority.** A queued candidate is offered only after demand completes, on
  an idle or completing-hit edge with no new CPU request, maintenance request
  or buffered dirty victim. A same-line demand cancels it; a newer install
  replaces it; maintenance cancels an unoffered one. Once presented
  (`line_request_prefetch_o`) its payload is held and service completes, so a
  miss waits at most one prefetch line service.
- **Buffer hit.** In the refill-request cycle, a buffered copy of the miss
  line installs as a refill would (store merged, dirty) with no line request;
  the response follows one edge later (three edges after acceptance with a
  clean victim). A miss on the in-flight line waits for it. A buffer hit is
  still an array miss for events.
- **Fill.** A successful response fills the lowest free slot, else the
  round-robin slot; an error response is dropped.
- **Coherence.** A dirty-victim enqueue drops any buffered copy; a prefetch
  response matching an enqueued or buffered dirty victim is discarded; a new
  prefetch of a buffered dirty line waits for its drain. Lines in the arrays
  are never looked up in the buffer.
- **Maintenance.** Start clears the buffer and discards a presented or
  in-flight prefetch's response; none starts while maintenance is requested.
- **Events.** `prefetch_issue_event_o` when a candidate is offered,
  `prefetch_useful_event_o` per buffer refill, `prefetch_drop_event_o` per
  line dropped unused (several per clock drain from a backlog counter): PMON
  80..82. Canceled intent is event 97; deferred clocks event 96
  ([PMON](../core/mas.md#performance-monitor)).

### Maintenance

Each cache has a valid/ready request and a completion pulse, accepted only
after the active CPU transaction drains; no CPU request is accepted until it
completes. I-cache: invalidate all sets. D-cache: drain the buffer, then scan
sets 0..511 and ways ascending, prefetching the next set as the last way
retires (one clean entry per cycle); clean entries invalidate at once, dirty
ones after a successful full-line write. An error ends the walk with done and
error, preserving the entry; a retry rescans. The platform completes both
caches before reporting precise halt.

### Arbitration and debug adaptation

`n2d_memory_controller` connects the I-cache, D-cache and debug line ports to
`n2d_memory_arbiter` and the SDRAM controller. Debug traffic enters only while
halted.

- **Arbiter.** Three packed line ports in owner order instruction, data,
  debug/loader. A round-robin pointer starts after the last completed owner;
  the chosen request handshakes directly downstream, its owner is captured
  and all others wait until its one response is routed. A request marked
  prefetch or background drain (`master_request_prefetch_i`) is a candidate
  only when no master presents demand; the pointer still advances past it. A
  stalled chosen request stays chosen until accepted, so a later request
  cannot change a stalled payload. Bounded progress assumes the controller
  completes every legal request.
- **Debug adapter.** `n2d_debug_memory_adapter` reads one line per word read.
  Writes stage in an eight-entry register queue between begin and commit
  (staging acknowledges nothing). Commit reads each target line into an
  original-image queue, then publishes the partial-line write, in order. On a
  later error or abort it writes the saved lines back in reverse order,
  retrying a failed restore, and pulses done/error only after rollback. The
  UART bridge holds its grant through that pulse
  ([bulk](../io/mas.md#bulk-wire-extension) composes these transactions and
  is not globally atomic).
- **Composition.** `n2d_cached_rv32im` wires the core to both caches and
  exposes independent line ports, both maintenance handshakes and hit/miss
  events; bypass policy is outside it.

### SDRAM controller

The controller is the common `n2a_sdram_ctrl` in the nand2doom configuration
([common MAS](../../../common/rtl/sdram/mas.md), which owns its design and lists
where it differs from the earlier nand2doom controller): one transaction, open
row, 25 MHz, one burst of eight x16 beats per line. The board top binds commands, DQ output enable and the
inverted clock to pins ([FPGA](../../fpga/de10_lite/board-spec.md#composition)).

| SDRAM field | Byte-address bits |
| --- | --- |
| row | `[25:13]` |
| bank | `[12:11]` XOR `[14:13]` |
| x16 column | `[10:1]`; a line starts at column `[2:0]=0` |
| byte lane | `[0]`, represented by DQM |

The bijective map spreads neighbouring 2 KiB regions over all four banks, and
the XOR sends a victim and its refill (same set, different tag) to different
banks in three of four cases.

- **Initialization.** NOP for 5,000 cycles (200 µs), precharge-all, two
  auto-refreshes, mode `0x023` (burst eight, sequential, CAS two), two idle
  clocks between commands. The registered init-done output stays high until
  reset and releases the platform.
- **Row policy.** Per bank open flag and row. Row hit: column command next
  clock. Closed bank: ACTIVE, then column (tRCD). Row miss: PRECHARGE that
  bank (A10 low), ACTIVE (tRP), then column; other banks stay open. No
  auto-precharge.
- **Read capture.** READ issues at controller edge C0 and is registered by the
  inverted device clock at C0+20 ns. CL2 data launches after device edge n+1
  plus tAC (≤6 ns) ([datasheet p. 28](https://www.alldatasheet.com/html-pdf/2160567/ISSI/IS42S16320D-7TL/3521/28/IS42S16320D-7TL.html)),
  so beat k is captured at C0+(80+40k) ns on consecutive controller edges;
  the response pulses on the last capture. One edge later would drop beat
  zero. Cycle-level models return beat k before edge N+1+k.
- **Write.** WRITE carries beat zero, the next seven clocks beats one to
  seven, each with its own DQM pair; the response pulses the edge after the
  last beat. After a read burst at least one idle clock precedes driving DQ.

| Line | Closed bank | Row hit | Row miss |
| --- | ---: | ---: | ---: |
| Read | 10 | 9 | 11 |
| Write | 9 | 8 | 10 |

Latency is acceptance to registered response in clocks; a due refresh adds
up to four before acceptance; the arbiter adds one to the owner.

| Parameter (IS42S16320D-7) | Minimum | Clocks | Controller |
| --- | ---: | ---: | --- |
| tRCD | 15 ns | 1 | exactly 1 |
| tRP | 15 ns | 1 | exactly 1 |
| tRAS | 37 ns | 1 | ≥10 (a burst follows every ACTIVE) |
| tRC | 60 ns | 2 | ≥2 |
| tRFC | 60 ns | 2 | ≥2 |
| tWR (tDPL) | 14 ns | 1 | ≥2 |
| tMRD | 14 ns | 1 | 2 |

Refresh: the age counter reserves 32 clocks; at age 163 it blocks new
requests, precharges all banks if any row is open, waits one clock, refreshes
and resets age. The worst path (row-miss read accepted at 162) reaches
REFRESH 14 clocks later; a parameter check covers the reserve and an assertion
enforces 195 clocks. Refresh also bounds row-open time. Refresh runs as soon
as due.

### Fast model

`n2d_sdram_fast_model` replaces the controller in fast system tests with the
same accepted-request/response contract: architectural line data, byte masks
and deterministic latency, no pin timing or vendor library. Its
integration-test fault input makes a request accepted while asserted complete
with error and no write (tied low except in the compiled-C target). A
simulation-only reset-time preload writes up to 256 byte-enabled bytes per
clock; only the simulation platform wrapper connects it, and it is absent from
every product closure.

### Register style

Each controller keeps its state in one packed `regs_q` of the type-only
`n2d_ls_types_pkg`, computed in a blocking `always_comb` and registered
through one `DFF_RST_VAL`. Parameter-sized fields (D-cache victim,
forward and maintenance ways, miss/forward metadata, prefetch buffer;
adapter staged counts and indices) are separate zero-reset registers. The
adapter's staged words and saved lines use the reset-clearing indexed write.
Pulse outputs default low each clock.

### Files

| Path | Role |
| --- | --- |
| `src/nand2doom/rtl/memory/n2d_icache.sv`, `n2d_dcache.sv` | Caches |
| `src/nand2doom/rtl/memory/n2d_cached_rv32im.sv` | Core plus caches |
| `src/nand2doom/rtl/memory/n2d_memory_arbiter.sv`, `n2d_memory_controller.sv` | Arbitration and composition |
| `src/nand2doom/rtl/memory/n2d_debug_memory_adapter.sv` | Debug word adapter |
| `src/common/rtl/sdram/n2a_sdram_ctrl.sv` | SDRAM controller (common) |
| `src/nand2doom/rtl/memory/n2d_ls_types_pkg.sv` | State records |
| `src/nand2doom/dv/memory/n2d_sdram_fast_model.sv` | Fast model |

## Clocks and resets

One clock, `clk_i` (25 MHz). The SDRAM receives the inverted clock from the
board top, and read data is captured on controller edges from that inverted
device clock: the controller owns this half-cycle relationship
([read capture](#sdram-controller)). Reset is synchronous; after reset the
D-cache sweeps its 512 sets with ready low, data RAMs are not reset, and a
second reset forgets resident and buffered dirty lines
([data cache](#data-cache), [dirty-victim buffer](#dirty-victim-buffer)). The
SDRAM controller initializes the device after reset and its init-done output
releases the platform ([SDRAM controller](#sdram-controller)).

## Design choices

- **D-cache geometry.** Four-way 32 KiB won the trace study of
  [#74](https://github.com/amichai-bd/nand2anything/issues/74); the DE10-Lite
  keeps two ways for M9K capacity ([cache and line contracts](#cache-and-line-contracts)).
- **Separate D-cache metadata RAM**, because maintenance and the next request
  read a set other than the one written ([data cache](#data-cache)).
- **XOR bank map**, so a victim and its refill usually go to different banks
  ([SDRAM controller](#sdram-controller)).

### Framebuffer placement

The framebuffer is on chip, not SDRAM scanout DMA, which keeps display
traffic and coherence out of the controller. DE10-Lite logical budget
([board](https://www.terasic.com.tw/cgi-bin/page/archive.pl?CategoryNo=218&Language=English&No=1021&PartNo=2):
1,638 Kbit M9K, 64 MiB SDRAM): two half-width banks 524,288 bits, I- and
D-cache data 131,072 bits each, tags and state under 48,000, palette 6,144:
under 841,000 bits (51%). Read-port replication for scanout doubles the
framebuffer to 1,048,576 physical bits. A second full-width bank does not
fit, hence [half width](../video/mas.md#framebuffer-and-palette). If packing
ever fails, moving the framebuffer to SDRAM needs a new decision covering line
buffers, display priority, coherence and underrun.

## Verification

The [memory DV plan](../../dv/memory/dv-plan.md) owns verification. Specific
to this microarchitecture: the metadata forward on the write edge, the
512-clock reset sweep, tree pseudo-LRU victim order, the victim buffer and
prefetch coherence cases, and the SDRAM read-capture phase on the board
targets ([FPGA DV](../../dv/fpga/dv-plan.md)).
