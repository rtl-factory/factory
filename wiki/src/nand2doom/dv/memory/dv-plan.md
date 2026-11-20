# memory DV plan

## Scope

The memory block (`n2d_icache`, `n2d_dcache` in both geometries with its
victim buffer and prefetch, `n2d_cached_rv32im`, `n2d_memory_arbiter` and the
fast line memory, `n2d_debug_memory_adapter`, `n2d_sdram_controller`), checked
against the [memory MAS](../../rtl/memory/mas.md). The maintained Halt bound
(MEM-R9) is checked in the [I/O DV plan](../io/dv-plan.md#features), the
board SDRAM phase on the board tops in the [FPGA DV plan](../fpga/dv-plan.md),
and the UART side of debug rollback in the [I/O DV plan](../io/dv-plan.md).

## Testbench

All targets are cocotb 2.1.0 on Verilator at 25 MHz with logs under
`workdir/`; models never inspect RTL arrays or Verilator state.

| Model | Owns |
| --- | --- |
| `cache_model.py` | Two or four ways over an independent byte array: set, tag, word, invalid-first then tree pseudo-LRU, writeback, refill, byte merge, maintenance. |
| Line responders | Deterministic or seeded random readiness and two- to six-edge latency; check held payloads; record every line transaction. |
| `cache_driver.py` | `run_stream`: one-outstanding pipelined requester presenting the next request on the response cycle, seeded gaps, records accept-to-response edges; every oracle hit must take one edge. |
| `sdram_pin_model.py` | Independent x16 pin model from the [address map and command rules](../../rtl/memory/mas.md#sdram-controller); reused by `n2d_halt_sdram` and both board targets. |

### Targets

| Target | Seed, bound | Command |
| --- | --- | --- |
| `n2d_icache` | 30, 30 s | `python3 tools/sim/sim.py test n2d_icache` |
| `n2d_dcache` (`WAYS` 4), `n2d_dcache_2way` | 30, 30 s | `... test n2d_dcache` |
| `n2d_dcache_write_buffer`, `_2way` | 30 | `... test n2d_dcache_write_buffer` |
| `n2d_dcache_prefetch` (`WAYS` 4, `PREFETCH_LINES` 2) | 30, 60 s | `... test n2d_dcache_prefetch` |
| `n2d_cached_rv32im` | 30, 4,096 cycles | `... test n2d_cached_rv32im` |
| `n2d_memory_fast` | 31, 4,096 clocks | `... test n2d_memory_fast` |
| `n2d_debug_memory` | | `... test n2d_debug_memory` |
| `n2d_sdram_controller` | 31, 12,000 clocks | `... test n2d_sdram_controller` |
| `n2d_sdram_board_timing` | 166, 12,000 clocks, 500 µs | `... test n2d_sdram_board_timing` |

## Features

Spec section names the requirement in the [memory MAS](../../rtl/memory/mas.md#requirements).
Level is the target's level in `src/nand2doom/cfg/targets.json`.

| ID | Spec section | Stimulus | Checker | Coverage point | Test | Level |
|---|---|---|---|---|---|---|
| MEM-F01 | MEM-R1, R3 (I) | Every word position, same-line hits, three tags in one set (conflict order vs oracle), invalidation forces refill, delayed ready/response, held payload, refill error, unaligned and out-of-range local errors one edge after acceptance, twelve back-to-back hits, hits on interleaved refill response edges, a withdrawn request during maintenance gives no response, 400 random accesses over four tags and sets. | `cache_model.py` oracle; line responders record and check every line transaction | none defined | `n2d_icache` | L0 |
| MEM-F02 | MEM-R1, R3, R5 (D) | Cold loads, hits, partial-store merge, write allocate, conflicts beyond the way count, dirty eviction, clean refill, flush/invalidate walk, backpressure, delay, refill errors, lane selection, range errors, failed maintenance writeback then lossless retry; per-line traffic and final bytes vs oracle. Store-then-load across both ways of one set on the forwarding edge; hits on clean and dirty refill response edges; 500 accesses with 40% stores over six tags and four sets, then maintenance equals the oracle. | `cache_model.py` oracle; line responders record and check every line transaction | none defined | `n2d_dcache`, `_2way` (`PREFETCH_LINES` 0) | L0 |
| MEM-F03 | Replacement | Hand-derived traces as well as the oracle: four-way fill, hit on way 0, next miss evicts way 2 (true LRU would pick 1), dirty victims drain in tree order, final four lines hit; two-way victim bit likewise; all ways dirty then maintenance writes back in ascending way order. | Hand-derived traces and `cache_model.py` | none defined | `n2d_dcache`, `_2way` | L0 |
| MEM-F04 | Metadata and sweep | Exactly 512 ready-low clocks after every reset; a 300-access gap-free stream on one set with `WAYS + 2` tags accepted on each metadata write edge (disabling the forward fails); clean walk exactly `WAYS x 512 + 3` clocks; dirty walk within 24 clocks per entry; a reset forgets a resident dirty line. | `cache_model.py` oracle; line responders record and check every line transaction; exact clock counts | none defined | `n2d_dcache`, `_2way` | L0 |
| MEM-F05 | Victim buffer | Read-first eviction with blocked write acceptance, full-slot backpressure, victim reread, refill error keeping the dirty array line, background drain failure, same-line retry failure, maintenance error/retry, maintenance with a stalled presented write; public pending/wait ports; per-line order preserved. | Line responders; public pending/wait ports; per-line order | none defined | `n2d_dcache_write_buffer`, `_2way` | L0 |
| MEM-F06 | Prefetch | See [prefetch](#prefetch). | Prefetch-free `cache_model.py` oracle and traffic checks | none defined | `n2d_dcache_prefetch` | L0 |
| MEM-F07 | Composition | Factorial-five with `MUL` plus seven stores 127 at `0x80`, loaded twice from one allocated line; I and D hits and misses required; maintenance writes the signature to memory; one-edge completions equal each cache's hit events. | Stored signature; one-edge completions against each cache's hit events | none defined | `n2d_cached_rv32im` | L0 |
| MEM-F08 | MEM-R6 | Three continuously active masters, six lines each with readback, partial-line write, ownership and bounded progress; a D read marked prefetch loses to every demand regardless of pointer and is granted alone. | Readback, ownership and bounded-progress checks | none defined | `n2d_memory_fast` | L0 |
| MEM-F09 | MEM-R7 | Two staged writes visible only after commit; a later abort keeps both originals; byte-mask placement; serial second-publication failure with reverse rollback and retained grant. | Readback of staged, committed and rolled-back words | none defined | `n2d_debug_memory`; `n2d_uart_debug_memory` ([I/O DV](../io/dv-plan.md)) | L0 |
| MEM-F10 | MEM-R8 | See [SDRAM](#sdram). | `sdram_pin_model.py` | none defined | `n2d_sdram_controller` | L0 |
| MEM-F11 | MEM-R9 | Owned by the [I/O DV](../io/dv-plan.md#features). | Owned by the I/O DV plan | none defined | `n2d_halt_sdram`, `n2d_halt_boundaries` | L1 |
| MEM-F12 | Board capture | See [SDRAM](#sdram). | `sdram_pin_model.py` and the board-target SDRAM BFM | none defined | `n2d_sdram_board_timing`, board targets ([FPGA DV](../fpga/dv-plan.md)) | L0 (`n2d_sdram_board_timing`), L1 (board targets) |

### Prefetch

`dcache_prefetch_contract` compares every CPU result with the prefetch-free
oracle; demand traffic must equal the oracle's minus buffer-served refills of
previously prefetched lines; no prefetch writes.

- A demand miss followed by exactly one next-line prefetch.
- A buffer hit installs without a line request, responds three edges after
  acceptance and prefetches the following line.
- A miss on the in-flight line waits and is served from the buffer.
- A store to a buffered line installs it merged and dirty.
- A dirty line re-prefetched then evicted: its writeback drops the stale copy
  and the next miss reads memory.
- Maintenance with a prefetch in flight (response discarded) or completed
  (buffer cleared) drops exactly one line.
- Same-line demand before the offer cancels intent and issues one demand
  read; unrelated demand during a delayed refill gives exact
  demand-then-prefetch traffic; a blocked prefetch keeps valid and address.
- Three phases of 400 mostly sequential accesses (35% stores) under random
  timing, each then maintenance; plus an idle sequential phase with over 100
  useful prefetches. Issue = prefetch requests, useful = buffer refills, and
  every issued line ends useful or dropped; canceled and deferred counts are
  separate.

Mutants that ignore new demand at selection, select during a refill or
withdraw a held prefetch fail the traffic-priority check, the delayed-demand
check and the held-request assertion. Both geometries run the directed
contract.

### SDRAM

`n2d_sdram_controller` (no fast model) first proves the oracle: its minimum
table equals the datasheet-derived one (`MIN_EDGES`: tRC and tRFC two, others
one); a legal minimum-spaced sequence passes; tRP, tRCD or tRFC one clock
short, a command inside a read burst and a write without turnaround fail. The
model requires precharge/refresh/refresh/MRS order and mode `0x023`, and
cycle-checks tRCD, tRP, tRC, tRAS, tRRD, tWR, tRFC, tMRD, per-bank row
legality, single- versus all-bank precharge, refresh with banks closed,
uninterrupted bursts, DQ drive only on write beats and one idle clock after
reads (read beats on the second edge after READ). Directed: full write and
read-back, beat order, per-beat DQM, same-bank row miss keeping another bank
open, closed-bank read, each with exact latency (read 10/9/11, write 9/8/10)
and ACTIVE count; misaligned and enable-carrying reads rejected. Then 160
random full and masked lines over three 64 KiB regions against a shadow, with
row hits, misses and ≥8 refreshes, every gap ≤195 clocks.

`n2d_sdram_board_timing` composes only the controller and resolved pins:
the device registers READ on the inverted clock and drives beat zero 46 ns
later, then seven beats 40 ns apart, with poison outside read windows.
Distinct seeded halfwords prove beat order; masked writes are checked in
device storage (all, first/last, alternating, zero enables) across row hits,
banks and misses; refresh spacing is checked. An extra read-wait state, the
negative control, drops the first halfword and captures poison.

## Coverage

No functional coverage points or code coverage numbers were recorded when the
plan came over from nand2anything, so every coverage point above is "none
defined". The adequacy evidence kept instead is the negative controls: the
prefetch mutants, the disabled metadata forward, the oracle's short-timing
sequences and the extra read-wait state of `n2d_sdram_board_timing`.

## Status

No open gaps are recorded for this plan.
