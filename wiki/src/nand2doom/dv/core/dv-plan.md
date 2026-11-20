# nand2doom RV32IM core verification

!!! note "Ported, not yet in the standard template"
    Ported from nand2anything with paths rewritten (#78). Rearranging it into the [standard template](../../../../bkm/design/standard-docs.md) is #91.

<!-- wiki-budget: exact per-program PMON prediction counts are the regression oracle and must stay with their programs -->

## Strategy

Every target is cocotb on Verilator with assertions and `--Wall`, driving only
public ports; models never read RTL hierarchy or Verilator internals. Waves
are opt-in. Seeds are recorded per target and printed. The supported flow is
two-state, so known-value assertions are not X/Z evidence.

| Model | Owns |
| --- | --- |
| Common register-file oracle | 32-entry list masked to 32 bits; `x0` reads zero; writes only enabled nonzero indices. |
| Decode/ALU model | Independent field extraction, named operations, sign extension, 32-bit masking and signed conversion. |
| `rv32i_smoke_model.py` | Encoders, registers, PC, flat memory and retirement records for the step and smoke targets. |
| `Rv32imModel` (`dv/core/rv32im_model.py`) | Registers, PC, bounded CSRs, `minstret`, trap entry/MRET, Harvard image (fetch past it returns EBREAK), ≥4 KiB data memory, access-error sets. Adopts the observed value of `mcycle` reads. |
| `dv/core/core_runner.py` | In-order pipelined responders, one outstanding request per port, next request accepted on the response-sampling edge; fixed or seeded ready/latency. Fails on a request while a response is owed, a changed or withdrawn data request, or a misaligned fetch. Compares every observation field-by-field in lockstep. |

Halt checks are boundary-based: at `halt_ack_o` no response is owed and no
request is accepted; no observation appears while halted; the first fetch
after resume is the model's next PC (zero after Reset-PC). Every core run
holds `enable_i` low for the `BTB_ENTRIES`-clock reset sweep, so all
architecture cases run with the fetch BTB active.

## Targets

Run `python3 tools/sim/sim.py test <target> [--seed N]` (`python3 tools/sim/sim.py list` lists the targets and their levels).

| Target | Seed and bounds |
| --- | --- |
| `n2d_rv32_regfile` | 192 edges, 30 s wall, 25 µs sim |
| `n2d_rv32i_decode_alu` | Seed 25, 4,608 transactions, 30 s, 20 µs sim |
| `n2d_rv32i_core_step` | Seed 26, 96 cycles, 30 s |
| `n2d_rv32i_program_smoke` | 80 cycles |
| `n2d_rv32im_core` (1024-entry BTB); `_btb0`, `_btb256`, `_btb512`, `_btb2048` | Seed 29, 4,096-cycle budget, 180 s, 20 ms sim |
| `n2d_muldiv` | Seed 70, 60,000 cycles, 120 s |
| `n2d_pmon_platform_events` | Seed 71 |

## Test map

| Requirement | Test | Cases |
| --- | --- | --- |
| Register file (common) | `n2d_rv32_regfile` | 32×32 read cross after reset; five `x0` writes; distinct `x1..x31` values and a second cross; same-edge old/new on both ports; reset beats an enabled write, third cross; 128 seeded operations checked before and after each edge. |
| Decode/ALU | `n2d_rv32i_decode_alu` | 4,174 vectors: ten `OP` functions × 64 operand pairs; six `OP-IMM` × 8 operands × 7 immediates (−2048..2047); 32 shift amounts × six shifts × 8 patterns; 256 seeded legal; all 126 unsupported opcodes; all 1,024 `OP` `funct3`/`funct7` pairs; all 256 shift-immediate pairs. One ns settle, no clock. |
| Step core | `n2d_rv32i_core_step` | Eleven-word program (ADDI, ADD, SUB, SW, LW, taken BEQ over poison, not-taken BNE): ten retirements, word 0 = 12, word 1 = 36; then an all-zero illegal at PC 44 gives one cause-2 trap and halt. |
| Smoke composition | `n2d_rv32i_program_smoke` | Same program through the preload port; signature `0x0000_0024` read through the debug port. |
| CORE-R1, CORE-R7 | `n2d_rv32im_core` directed | Every RV32I class, all loads/stores, FENCE, six CSR encodings, ECALL, EBREAK, MRET; shift 0/31, compare limits, `x0`, byte lanes, sign extension, misaligned jump/load/store, illegal, instruction/load/store access faults; field-shape checks on every observation. |
| CORE-R2, R3, R8 | `n2d_rv32im_core` CSR | Old/new values for all six ops, zero-source read-only access, read-only/unsupported traps, counter progress, encoded `x0` vs zero-valued `x1` on MISA, MINSTRET half precedence for CSRRW/S/C, ECALL handler: `mepc`, `mcause`, `mtval`, MIE/MPIE, MPP WARL. |
| RV32M | `n2d_rv32im_core` | Eight M ops × twelve pairs (zero divisor, overflow, extrema, all ones, mixed; four from seed 29). Corner sweep: every ordered pair of twelve corners (0, 1, −1, 2, −2, `INT_MIN`, `INT_MAX`, `INT_MIN+1`, `0xFFFF`, `0x10000`, mixed, −7) plus 48 seeded pairs (a third with 4- to 16-bit divisors), DIV→REM and DIVU→REMU pairs, MUL→MUL and MULH→ADD at distance one. |
| Compiled C | `n2d_rv32im_core` | Generated factorial signature (five MULs, 127, EBREAK); `programs/rv32im_signature.c` built with `riscv64-unknown-elf-gcc -march=rv32im -mabi=ilp32 -O1 -ffreestanding -fno-builtin -fno-pic -mno-relax -nostdlib -nostartfiles`, linked at 0, must store 127 at `0x80`. `N2D_RISCV_GCC`/`N2D_RISCV_OBJCOPY` select tools; missing tools fail. |
| Doom arithmetic | `n2d_rv32im_core` | `programs/doom_math.c` (`-Os`, libgcc, stack `0x2000`): `FixedMul`, `FixedDiv` via `__divdi3`, `SlopeDiv` over 24 distances/angles; signature `0xA17419B9`. Reports IPC, retirements and divide holds. |
| CORE-R9, CORE-R10, pipeline | `pipeline_cases.py` | See [pipeline cases](#pipeline-cases). |
| Reset and debug | `n2d_rv32im_core` | Halt on the first data request; halt and reset during DIV/DIVU/REM/REMU (~30 iterations) with backpressured fetch; halt at every cycle offset; double halts; Reset-PC at several offsets keeps registers and CSRs. |
| Multiply/divide unit | `n2d_muldiv` | See [multiply/divide unit](#multiplydivide-unit). |
| PMON | `n2d_rv32im_core` | See [PMON](#pmon). |
| Platform PMON events | `n2d_pmon_platform_events` | See [PMON](#pmon). |

### Pipeline cases

Model-checked directed cases in `dv/core/pipeline_cases.py`:

- every producer (ALU, LUI, word/byte load, MUL, MULH, DIV, CSR read, JAL
  link) into every consumer (ALU rs1/rs2, store data/address, branch, multiply
  and CSR operands) at distances 1..4; a divide among slow loads/stores;
- taken BEQ, BNE, JAL, JALR over three younger stores that must not land,
  and a not-taken branch whose store must;
- static prediction: a backward loop under 64 seeds of slow fetch (two
  flushes, one stale response dropped); a never-taken backward branch;
  misaligned backward branch/JAL targets (cause 0, no misaligned fetch); a
  fetch-fault target trapping (cause 1) only when taken; a 32-iteration
  two-instruction loop near three clocks per iteration;
- return stack: empty, three-deep with spilled `ra`, twelve-deep recursion,
  a replaced return address, a predicted return discarded by an older load
  trap and re-run with a cleared stack, misaligned `ra`, back-to-back calls;
  16 random-timing seeds and a halt at every second offset;
- BTB aliases one and two strides (max of 1 MiB and the alias span) above a
  trained forward branch and indirect jump: no poison store retires; removing
  the Q102H target check fails this and the return case;
- serialized CSR access behind in-flight work; a `minstret` write overriding
  its own count; an access error followed by ECALL, store, CSR write,
  illegal, misaligned load or branch traps first;
- quotient/remainder pairs that may or must not share a division; an older
  faulting load killing a DIVU in each phase; multiply lifetime with 1, 2, 3,
  8, 17 or 31-cycle responses, success and fault (24 programs);
- twelve seeded 160-instruction random streams with bounded loops, injected
  fetch/data errors and a resuming handler.

IPC is logged under a one-cycle responder for the signatures, a
200-instruction random stream, a 64-pixel column draw, a 32-element dot
product and the Doom kernel.

### Fetch BTB capacity

At `BTB_ENTRIES` N, events (92, 93, 78, 79). A site is an always-taken forward
branch, a skipped poison word and a stack-predicted return, called by
`JALR ra`; a program trains one site twice, runs a second, then the first.

- **Index and tag:** a second site 16 bytes away, at 2N, or at 1024N + 2N
  behaves identically; one at 4N, 512N or 1024N + 4N evicts the first
  (−2, 0, −1, +1); one at 1024N shares index and tag (0, +2, −1, +1). With
  N = 0 nothing is predicted at fetch.
- **Reset sweep:** trained sites at index N−1 and 0 give cold counts after
  reset and the sweep; a loop at N−3..N−1 is predicted after the sweep and
  never when run from reset with no settle clocks.

Testcase `btb_capacity_contract` runs on `_btb0`, `_btb512`, `_btb2048`; the
default and `_btb256` targets run it inside the full contract. Five `n2d_if`
mutants are killed at 512 entries: a 256-entry sweep, a narrower index, a tag
shifted up one bit, a tag ignoring its top bit, a lookup hitting during the
sweep.

### PMON

CSR reads are compared with values derived from the
[HAS rules](../../rtl/core/mas.md#performance-monitor):

- selector legalization on all four selectors; `mcountinhibit` mask `0x7d`;
  legal IDs 94..97 with ten input pulses each, ID 98 rejected;
- INSTRET from the cycle after the selector writer, low/high reads, a
  low-half write dropping its own event, low-to-high carry, high-half write
  keeping the low half;
- HPM inhibit (stopping writer counts, resuming does not); CY/IR inhibit;
  CYCLES equals `mcycle`;
- at three-cycle fetch and four-cycle data latency: DATA_HOLD 3, MULDIV_HOLD
  30 for `0x80001000 / 9`, BRANCH_REDIRECT 2, IFETCH_WAIT 1..3 per
  straight-line instruction; LOAD_USE_HOLD and MUL_USE_HOLD 1 at distance one
  and 0 at two; nonzero SYSTEM_SERIALIZE_HOLD;
- a platform event from ten pulses; an unsupported ID never counts;
  `pmon_events_o` shows the previous clock's legal events every cycle;
- `programs/pmon_configure.c` with the production header: a counter reads
  zero after configure while ICACHE_DEMAND_ACCESS fires inside it; a stopped
  counter stays stopped.

Prediction counts: each program runs three times behind four selector writes.
78, 79 and 9 match at every latency; 83, 84, 85, 92 and 93 are exact per
latency; the causes sum to IFETCH_WAIT; 86 is zero at one-cycle and nonzero at
three-cycle latency. Counts are (78, 79, 9) and (83, 84, 85, 92, 93):

| Program | (78, 79, 9) | Causes |
| --- | --- | --- |
| Three-iteration two-instruction loop, forward taken branch, JAL, never-taken backward branch | (5, 3, 4) | Latency 1: (3, 6, 0, 1, 0) (read-first training edge); latency 3: (2, 6, 0, 2, 0) |
| Fetch BTB: back-to-back JALs and a loop branch, three iterations | (9, 1, 8) | (3, 2, 0, 6, 0) |
| Counter BTB: forward branch taken on two of five iterations | (8, 4, 6) | (1, 8, 0, 7, 0) |
| Stale BTB (`_btb256`, one-cycle): branch trained by three `JAL x9` calls, aliases at 256/512/768 KiB (non-transfer, never-taken branch, JAL with another target), hop alias; a fourth call still predicted | (15, 5, 17) | (11, 10, 0, 6, 4) |
| Trap refill: ECALL handler MRETs to a JAL | (1, 0, 1) | (1, 0, 4, 0, 0) |
| Return: four calls to a leaf | (13, 1, 12) | (7, 2, 0, 6, 0) |
| BTB: always-taken forward branch and indirect call, four iterations | (15, 3, 16) | (6, 6, 0, 9, 0) |

Six `n2d_if` mutants are killed: never accepting a fetch prediction,
accepting a non-transfer (assertion `pipe_prediction_is_control_transfer`),
skipping the direct-target check, looking up the current PC, no not-taken
decrement, a rejected entry keeping its hit. `n2d_ctrl` assertions check the
IFETCH_WAIT causes every clock.

`n2d_pmon_platform_events` composes the derivation with the real
`n2d_memory_arbiter` (`dv/platform/n2d_pmon_events_fixture.sv`). Every cycle
MEM_BACKPRESSURE equals downstream valid-and-not-ready and MEM_MASTER_WAIT any
master valid-and-not-ready. Directed phases separate contention, waiting
behind an outstanding transaction (77 only) and downstream refusal (33).
3,000 random cycles of three-master traffic check events 14, 18, 19, 29, 30,
32, 34 and MEM_OWNER_* 87..91 (each over 50 clocks, never with 33) against a
transaction model.

### Multiply/divide unit

`n2d_muldiv` is driven as `n2d_ctrl` drives it, checked against `m_result` in
`rv32im_model.py` with an independent latency and reuse model:

- 400 pairs (144 corner pairs plus 256 seeded, a third short divisors)
  through the four multiplies with random gaps: each product two edges after
  issue;
- the same pairs through DIV, REM, DIVU, REMU, REM, DIV with exact latency,
  results held 0..3 cycles and operand ports scribbled after issue;
- kill on every edge from issue to after completion for five operand classes
  and both signednesses; only a completed divide may be reused; kill on the
  issue edge wins;
- reset during iterations cancels; reuse latency before reset, fresh latency
  after; multiplies issue back to back while a divide iterates.

## Production comparison

Performance-affecting core changes run the
[native performance harness](../platform/dv-plan.md#native-performance-harness)
sequentially at base and head with identical image, ELF and capture identity:

```sh
python3 src/nand2doom/scripts/n2d_perf.py --backend sdram --variant production --frames 2
python3 src/nand2doom/scripts/n2d_perf.py --backend <sdram|fast> --variant render-verification --frames 2
```

Compare gameplay IPC, cycles per frame (moving in 420,000-cycle rasters;
`swap_wait_cycles_per_frame` is the slack), FPS and the affected PMON
classes. Production must match the pinned scene CRCs; render verification's
oracle must pass at both.

## Fixed-point fixture provenance

`programs/doom_math.c` adapts `FixedMul`, `FixedDiv` (`doomgeneric/m_fixed.c`)
and `SlopeDiv` (`doomgeneric/tables.c`) from the pinned DoomGeneric archive in
the third-party manifest (`src/nand2doom/sw/third_party/manifest.json`),
keeping the Id Software and Simon Howard notices under GPL-2.0-or-later
(license (`src/nand2doom/sw/third_party/DOOMGENERIC-LICENSE`)). The
loop and signature store are project-owned.

## Gaps

- Cache hit/miss and uncached platform events have no focused test; the
  performance baseline cross-checks them.
- Embedded-multiplier mapping and 25 MHz board qualification:
  [#126](https://github.com/amichai-bd/nand2anything/issues/126).
