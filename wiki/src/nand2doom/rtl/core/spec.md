# nand2doom RV32IM core architecture

The nand2doom CPU is the first implementation of the
[shared-candidate RV32IM contract](https://github.com/amichai-bd/nand2anything/blob/aeebe92/wiki/src/riscv/ownership.md): a single-hart,
machine-mode-only, in-order processor for freestanding C at the 25 MHz system
clock. This page owns the composition around that neutral contract: machine
trap state, debug stop, fetch prediction, the PMON, and the RTL decomposition.
The [platform](../platform/spec.md) is the system consumer; the [DV plan](../../dv/core/plan.md)
maps every requirement to tests.

<!-- wiki-budget: the PMON event-ID table and pipeline timing are the canonical register and cycle contract for RTL, firmware and n2d_perf -->

## Requirements

| ID | Requirement |
| --- | --- |
| CORE-R1 | RV32IM execution, integer-register reset, precise synchronous-exception events, request/response accounting and the observation record conform to the [cross-project owner](https://github.com/amichai-bd/nand2anything/blob/aeebe92/wiki/src/riscv/ownership.md). Boot PC is `0x0000_0000`; addresses are physical bytes; software uses the `ilp32` ABI. |
| CORE-R2 | Machine state: `misa` (read-only), `mstatus`, `mtvec`, `mepc`, `mcause`, `mtval`, `mscratch`, `mcycle[h]`, `minstret[h]` and the [PMON](#performance-monitor) CSRs. All six CSR encodings and `MRET` operate on it. Unsupported CSRs and writes to `misa` are illegal. |
| CORE-R3 | `mstatus.MPP` is WARL with the sole value M: reset, trap entry and MRET keep MPP=M. |
| CORE-R4 | `FENCE` is an ordering point. `FENCE.I`, self-modifying code and asynchronous interrupts are not required; I-cache invalidation after a host load is platform-owned and keyboard input is polled. |
| CORE-R5 | Excluded, and never relaxed by a nand2linux consumer: supervisor/user privilege, virtual memory, MMU/TLB, atomics, floating point, compressed instructions, multiple harts and speculative architectural side effects. |
| CORE-R6 | Prediction state (fetch BTB, return-address stack, static rules) is a hint and never changes architectural state. |
| CORE-R7 | Synchronous exceptions are precise: the faulting operation writes no register or memory, emits one trap observation, saves `mepc`/`mcause`/`mtval`, copies MIE to MPIE, clears MIE and redirects to direct-mode `mtvec`. Causes 0..7 and 11 apply. `MRET` sets MIE from MPIE, sets MPIE and resumes at aligned `mepc`. |
| CORE-R8 | CSRRS/CSRRC write intent tests the encoded source index (not its value); immediate forms use the five-bit field; CSRRW/CSRRWI always write. Reads return the old value. A counter-half write replaces only that half and wins over the same edge's increment. |
| CORE-R9 | No data request, including an uncached device access, is presented while an older instruction can still trap or behind a taken transfer or trap. |
| CORE-R10 | One observation per retirement or trap, in program order; none for discarded work. Retirement order, `order` and `minstret` are independent of timing. |
| CORE-R11 | PMON only observes; it never changes backpressure, timing or results. |

## Interface

The [shared boundary](https://github.com/amichai-bd/nand2anything/blob/aeebe92/wiki/src/riscv/ownership.md) owns the request/response
handshake and observation record. nand2doom adds debug control
(`halt_request_i`, `resume_i`, `reset_pc_request_i`, `halt_ack_o`), the PMON
ports (`pmon_platform_events_i`, `pmon_events_o`) and `enable_i`, which gates
new requests without creating a clock domain. Debug adds no observation
field; machine CSR observations, when a test needs them, are a sideband keyed
by `order`. Payloads stay stable under backpressure. Reset is synchronous,
active high; the platform synchronizes its release.

### Pipelined memory handshake

The [memory architecture](../memory/spec.md#cpu-port-handshake) owns the
handshake (one request outstanding per port, next request accepted on the
edge that samples the response, hits sampled one edge after acceptance).
Core obligations: an instruction request may be withdrawn or re-addressed
before acceptance and is consumed or discarded once accepted; a data request
and payload stay stable until accepted, so the core never presents one it
might cancel. `n2d_rv32im_core` overlaps one instruction and one data
transaction with back-to-back hits; `n2d_rv32i_core_step` never overlaps a
request with a response. Loads and stores publish the effective byte address,
aligned-word data and byte-lane masks.

### Cores and slices

| Module | Contract |
| --- | --- |
| `n2a_rv32_regfile` | Common register file, instantiated directly as `u_register_file` by both cores with no wrapper; the [common HAS](https://github.com/amichai-bd/nand2anything/blob/aeebe92/wiki/src/common/riscv/HAS.md) owns ports, `x0`, reset and old-before/new-after-edge behavior. |
| `n2d_rv32i_decode_alu` | Combinational. Legal: all ten `OP` and six `OP-IMM` functions plus SLLI/SRLI (`funct7=0000000`) and SRAI (`0100000`). Outputs raw `rs1/rs2/rd_index_o`, `rs1_read_o`, `rs2_read_o`, `rd_write_o` (zero for `rd`=0), sign-extended I `immediate_o`, `alu_result_o`, `illegal_o`. Anything else (including RV32M `funct7=0000001`) is illegal with zero controls, immediate and result. |
| `n2d_rv32i_core_step` | Non-pipelined RV32I teaching/smoke engine: OP, OP-IMM, six branches, aligned `LW`/`SW` (four lanes). One transaction at a time; retires arithmetic and branches from execute, loads/stores after the data response. Illegal, access error, misaligned target or word address produce one trap observation and halt. Reset zeroes PC and `order`. |
| `n2d_rv32i_program_smoke` | DV composition: two 64-word `n2a_sync_ram`s with one-cycle responses; misaligned or ≥256-byte addresses error. Host preload under reset and a registered debug read while `run_i` is low; never concurrent with `run_i`. |
| `n2d_rv32im_core` | Product core: complete RV32I (LUI/AUIPC, JAL/JALR, six branches, all ALU ops, signed/unsigned B/H/W loads, B/H/W stores, `FENCE`), all eight RV32M operations, machine-mode SYSTEM. Parameters `RAS_DEPTH` (default 8) and `BTB_ENTRIES` (default 1024, power of two 2..2048); 0 removes either. |

`n2d_rv32i_decode_alu` inputs:

| Port | Direction | Width | Meaning |
| --- | --- | ---: | --- |
| `instruction_i` | input | 32 | Candidate RV32 instruction bits |
| `rs1_data_i`, `rs2_data_i` | input | 32 each | Current register-file operand values |

## Pipeline

Six in-order stages (nand2linux Q100H..Q105H without MMU, privilege or replay), one instruction per clock on hits.

| Stage | Module | Responsibility |
| --- | --- | --- |
| Q100H | `n2d_if` | Fetch PC and fetch-BTB lookup; request when the queue has room; next PC is the predicted target or PC+4. |
| Q101H | `n2d_if` | Two-entry queue and response bypass; stale mark; fetch-prediction check; static and RAS prediction. |
| Q102H | `n2d_dec`, `n2d_exe`, `n2d_csr`, `n2d_muldiv` | Decode, register read and forwarding, ALU, branch/jump/MRET resolution, effective address, data request, multiply issue, divide, serialized SYSTEM. |
| Q103H | `n2d_ma`, `n2d_muldiv` | Data response, load lane select/extension, data-access trap; multiply operand registers. |
| Q104H | `n2d_muldiv` | Registered result; multiply product register and word select. |
| Q105H | `n2d_wb` | Register write and registered observation. |

`n2d_ctrl` owns every valid, stall, flush, redirect and halt decision;
datapath modules own values. `n2d_core_pkg` holds the stage records (fetch
slot, Q102H decode, Q103H record, Q104H/Q105H retirement record whose fields
are the observation fields) and the `n2d_ctrl`/`n2d_if` state records.
CSR, multiply/divide, PMON and step-core records are in the type-only
`n2d_core_types_pkg`, so standalone `n2d_muldiv` and step-core targets
compile without `n2d_core_pkg`.

### Timing and hazards

| Event | Cost |
| --- | --- |
| Transfer predicted at fetch and correct | 0 clocks |
| Transfer predicted in Q101H (static, return, or rejected fetch prediction) | 1 refill clock |
| Mispredict: unpredicted taken transfer, predicted branch not taken, wrong target, MRET | 2 refill clocks |
| Load-use or MUL-use at distance one | 1 bubble (forwards from Q104H) |
| Divide | 2..35 clocks in Q102H ([multiply and divide](#multiply-and-divide)) |
| D admission (request valid, not ready) | Q102H holds; bubble into Q103H |
| Memory hold (Q103H owes a response) | Q100H..Q103H hold; Q104H/Q105H drain |
| Serialization: CSR, ECALL, EBREAK, MRET, illegal, fetch-faulted, misaligned access or target | Waits in Q102H until Q103H..Q105H are empty, then commits |

Q102H forwards Q103H, then Q104H, then Q105H values (the common register file
has no write-through). A multiply's product is substituted from the
`n2d_muldiv` product register before Q104H forwarding. Serialization makes
traps precise and CSR/counter reads exact; a committed trap or MRET updates
trap state and redirects on its commit edge, and a `minstret` write marks
its record so it does not count itself.

### Fetch and instruction queue

`n2d_if` requests the fetch PC and on acceptance advances it to the BTB
target or PC+4. A response bypasses into Q101H when the queue is empty, so a
hit accepted on one edge is in Q102H on the next. A new request is issued
only when no response is owed (or it arrives this edge) and at most one queue
entry will stay occupied, so the queue cannot overflow. Every flush (Q101H
predicted redirect, Q102H redirect, Q103H trap, halt entry, resume) empties
the queue and marks any owed response stale; stale responses are dropped.
The mark is sticky, not a one-bit epoch, because a predicted redirect and the
Q102H mispredict of the same branch can flush twice before one slow response.
No request is presented in a flush cycle.

### Static prediction

A Q101H slot whose fetch prediction did not stand is predicted taken when it
is a `JAL`, or a `BRANCH` with negative immediate, without fetch error and
with immediate bit 1 clear (word-aligned target). The target is `pc` plus the
J/B immediate (`imm_j`/`imm_b`, shared with `n2d_dec`). `n2d_if` raises
`predict_redirect_Q101H`; on the take edge `n2d_ctrl` flushes and loads the
target, costing one refill clock. The take depends only on the late flush
sources (resume, Q103H trap, Q102H redirect, halt entry), never on the
prediction. Nothing is predicted while fetch is stopped. A misaligned static
target is fetched sequentially and traps in Q102H.

### Return-address stack

A circular stack of `RAS_DEPTH` 32-bit entries (default 8 from
`RAS_DEPTH_DEFAULT`), top index and count, updated when a Q101H slot is taken
into Q102H:

- `JAL`/`JALR` with `rd`=`ra` and no fetch error pushes `pc+4`; when full it
  overwrites the oldest entry.
- A return, exactly `JALR x0, 0(ra)` (`0x00008067`), with a nonzero count
  pops and is predicted to the top entry; with an empty stack it is not
  predicted.

Only `ra` links (`x5` calls are not stack-predicted). `n2d_ctrl` clears the
count on a Q102H or Q103H trap (`ras_clear`); a debug halt keeps the stack.
A stale or overwritten entry costs one mispredict.

### Fetch branch target buffer

Direct-mapped, `BTB_ENTRIES` entries in one `n2a_sdp_ram` (four M9Ks at
1024), indexed by `pc[INDEX+1:2]`. Each 36-bit `t_btb_entry` is valid, a
two-bit counter, a return flag, an 8-bit partial tag (the next eight PC bits,
`pc[19:12]` at 1024) and word target `target[25:2]` (all code is in the
64 MiB SDRAM at 0). Index plus tag cover 1 KiB of code per entry; addresses a
multiple of that alias. The [capacity study](https://github.com/amichai-bd/nand2anything/blob/aeebe92/wiki/src/nand2doom/performance-btb-capacity-364.md)
selected 1024.

- **Lookup.** The read address is `if_d.pc`, so the entry for `if_q.pc` is
  ready when that PC is requested. A hit predicts taken when the counter is
  ≥2, or for a return entry while the stack is nonempty (target = stack top).
  The prediction, hit, counter and target travel with the word
  (`fetch_taken`, `btb_hit`, `btb_counter`, `predicted_target`).
- **Q101H check.** The prediction stands for a JAL/branch whose `pc`+J/B
  immediate equals the target, a return whose target equals the stack top
  (any target when empty), or another JALR. Otherwise the slot falls back to
  static prediction with a redirect to the static target or `pc+4`, and
  `btb_hit` is cleared so it trains as a miss. A fetch error is never
  predicted.
- **Training.** `btb_update` for every branch/JAL/JALR committing in Q102H
  without exception. Taken: valid, resolved target, return flag, counter+1
  saturating at 3 (2 for a new entry). Not taken after a hit: counter−1
  saturating at 0, keeping `predicted_target`. A not-taken miss writes
  nothing. The RAM is read-first, so a lookup on the training edge sees the
  old entry.
- **Reset sweep.** For the first `BTB_ENTRIES` clocks after reset the write
  port clears one entry per clock, nothing hits and training is ignored. The
  product and core DV hold the core stopped longer than that.
- **Return entries.** A return fetched in the clock its call is taken sees
  the old stack and is predicted in Q101H instead.

### Control transfer

Branches, JAL, JALR and MRET resolve in Q102H. Fetch is redirected when the
resolved direction differs from `predicted_taken`, or a predicted-taken
transfer goes to a target other than `predicted_target` (bits 31..1
compared), or for MRET. The corrected path reaches Q102H two cycles later.
`next_pc`, the misaligned-target check and traps use the resolved direction.
A stale aligned prediction may be followed until a misaligned resolved target
traps in Q102H; nothing from the predicted path retires or makes a data
request.

### Data requests and traps

Q102H presents a data request only when its operands are ready, the access is
aligned, and Q103H owes no response or receives an error-free one this edge;
acceptance can coincide with the previous completion. `n2d_ma` selects and
extends load lanes (little-endian) in Q103H; stores shift into byte lanes in
Q102H. A Q103H data-access error completes as a cause 5 or 7 trap, updates
trap state on the response edge, flushes Q102H and the queue and redirects to
`mtvec`. `n2d_csr` masks `mtvec` and `mepc` to four-byte alignment on writes.
`order` counts observations entering Q105H. `mcycle` advances every clock;
`minstret` per ordinary retirement entering Q105H, and a CSR instruction or
MRET on its Q102H commit edge, unless `mcountinhibit` CY/IR stops it.

### Multiply and divide

`n2d_muldiv` has one issue interface. `n2d_ctrl` issues a multiply on the
Q102H-to-Q103H edge and a divide while Q102H holds it; a Q103H data trap kills
an in-flight divide.

| Operation | Q102H occupancy | Result |
| --- | --- | --- |
| MUL, MULH, MULHSU, MULHU | 1 cycle, back to back | Product register two edges after issue; forwarded from Q104H. |
| Divide with operands and signedness equal to the last completed divide | 2 cycles | Reused quotient/remainder. |
| Divide by zero | 2 cycles | Quotient all ones, remainder = dividend. |
| `msb(\|n\|) < msb(\|d\|)` or `n = 0` | 3 cycles | Quotient 0, remainder = dividend. |
| Other divide | `3 + s`, `s = msb(\|n\|) − msb(\|d\|) + 1`, ≤35 | Restoring, sign-corrected. |

Signed `0x80000000 / -1` returns `0x80000000`, remainder 0.

- **Multiplier.** The issue edge registers 33-bit signed operands (rs1
  sign-extended for MULH/MULHSU, rs2 for MULH, else zero-extended) into one
  signed `*`; the next edge registers the low 64 product bits and word
  select. Operand registers have only the issue enable, the product register
  is free running, neither is reset: the Quartus embedded-multiplier
  inference form. The [fitted images](../../fpga/spec.md#fitted-results) use four
  18x18 multipliers on both boards with the path outside the 400 worst.
- **Divider.** Issue registers raw operands and signedness; a setup cycle
  forms magnitudes and leading-one indices and preloads `{remainder,
  quotient}` with the dividend shifted left by `32 − s`, leaving `s` radix-2
  restoring iterations. `busy` (MULDIV_HOLD) covers setup and iterations;
  `result_valid` holds the result until Q102H advances. About 175 flops, no
  multiplier or memory block.
- **Reuse.** The unit keeps the last completed divide's operands, signedness,
  quotient and remainder. An identical divide finishes at issue, so REM after
  DIV (REMU after DIVU) costs one bubble; libgcc's `__udiv_qrnnd_c` in
  `FixedDiv` issues exactly these pairs. A computing divide clears the mark;
  a kill keeps it only for an already completed divide; reset clears it.

### Timing intent

Every stage register is a flop. The long paths are the Q102H operand path
and the response → Q102H advance → queue take → request-valid path;
prediction adds the bypassed response → flush path and ends the next fetch PC
at the BTB RAM address register (loop B of the
[FPGA timing baseline](../../fpga/spec.md#timing-baseline-and-50-mhz-analysis)).
`BTB_ENTRIES = 0` and `RAS_DEPTH = 0` remove the lookups without an RTL
change; fitted slack is in [BTB storage](../../fpga/spec.md#btb-storage).

## Reset and debug behavior

Reset clears valid state and writable CSRs, forces MPP=M, selects boot PC
`0x0000_0000`, cancels both transaction epochs and emits no request or
observation. The platform holds the core stopped until SDRAM initialization
and a host resume.

A halt request stops issue, discards the queue and any unaccepted fetch and
enters a drain. Q102H..Q105H complete, including a divide, a memory operation
and any redirect or trap; an owed fetch response is received and dropped.
When Q102H..Q105H are empty and no response is owed, `halt_ack_o` rises. The
platform combines it with cache-maintenance completion
([memory](../memory/spec.md)). The resume PC is the `next_pc` of the last
Q105H record; `reset_pc_request_i` while halted sets it to zero without
changing registers or CSRs (a simultaneous resume fetches from zero). A halt
withdrawn before acknowledgement continues without acknowledging. The worst
drain (data miss with dirty victim, instruction refill, divide) is inside the
256-clock allowance of the [maintained Halt bound](../memory/spec.md#maintained-halt-bound).

## Performance monitor

Four programmable 64-bit counters with the
[nand2linux PMON](https://github.com/amichai-bd/nand2anything/blob/aeebe92/wiki/src/nand2linux/pmon/HAS.md) CSR layout and event IDs,
reduced to machine mode (`src/nand2doom/rtl/core/n2d_pmon.sv`;
accessor `n2d_pmon.h`).

| Registers | Addresses | Contract |
| --- | --- | --- |
| `mhpmcounter3..6` | B03..B06 / B83..B86 | Writable low/high halves, 64 bits |
| `mhpmevent3..6` | 323..326 | Event ID; any unsupported value reads 0 |
| `mcountinhibit` | 320 | Writable mask `0x7d`: CY, IR, HPM3..6 |

No user/supervisor aliases, `mcounteren`, `mhpmevent` high halves, counters
7..31, sampling interrupts or filters: those addresses are illegal. All PMON
state resets to zero. Each enabled counter adds one modulo 2^64 per clock its
event is asserted.

- An event is selected and inhibited by the controls current in its cycle.
  CSR writes commit when the writer retires (on the serialized Q102H edge).
- A counter-half write replaces that half, keeps the up-to-date other half
  and drops that counter's event from the writer's cycle.
- Inhibit and selector writes take effect next cycle: a stopping writer is
  counted, a resuming one is not.
- Each counter registers its event and adds it one clock later; serialization
  makes a read observe every older event.
- Coherent snapshot: set the group's inhibit bits, read, restore the mask.

`pmon_events_o` is a registered 128-bit vector of the previous clock's legal
events, one bit per ID, for simulation harnesses. The platform supplies its
IDs through `pmon_platform_events_i` (same indexing) from
`src/nand2doom/rtl/platform/n2d_pmon_platform_events.sv`
inside `n2d_system`, derived from cache, line-memory and uncached handshakes
plus flops for the in-flight store flag, outstanding I/D read, outstanding
transaction and one-hot owner; `n2d_cached_rv32im` ties the input to zero.
MEM_BACKPRESSURE is reconstructed from the one-outstanding
[line-memory contract](../memory/spec.md#line-port): no
transaction outstanding, a master valid, none accepted. A controller with
more than one outstanding transaction must supply it directly.

IDs 1..35 keep the nand2linux meaning; nand2doom IDs start at 64. Unsupported
(read back 0): 0, 7, 11, 20..28, 31, 35, 70..75 (reserved: instruction
prefetch and SDRAM row hit/miss/refresh) and above 97. Events overlap; a CPI
stack is a priority classifier over `pmon_events_o`, not a counter sum.

| ID | Name | Unit | Src | Definition |
| -- | -- | -- | -- | -- |
| 1 | CYCLES | clocks | core | Every clock |
| 2 | INSTRET | instr | core | Every nontrap retirement, including counter writers |
| 3 | IFETCH_WAIT | clocks | core | Execute can take an instruction and none arrives (including refill clocks) |
| 4 | DATA_HOLD | clocks | core | Accepted data request without its response |
| 5 | LOAD_USE_HOLD | clocks | core | Execute stalled on a load one stage ahead, outside a data hold |
| 6 | MULDIV_HOLD | clocks | core | Divider busy (setup and iterations) |
| 8 | DATA_ADMISSION_HOLD | clocks | core | Data request held without `ready` |
| 9 | BRANCH_REDIRECT | occ | core | Retired taken branch, JAL or JALR, predicted or not |
| 10 | TRAP_ENTRY | occ | core | Trap observation |
| 12 | ICACHE_DEMAND_ACCESS | occ | platform | I-cache lookup |
| 13 | ICACHE_DEMAND_MISS | occ | platform | I-cache miss |
| 14 | ICACHE_REFILL | lines | platform | Error-free I-cache line response |
| 15 | DCACHE_READ_ACCESS | occ | platform | D-cache load lookup |
| 16 | DCACHE_READ_MISS | occ | platform | D-cache load miss |
| 17 | DCACHE_WRITE_MISS | occ | platform | D-cache store miss |
| 18 | DCACHE_REFILL | lines | platform | Error-free D-cache line read response, including prefetch |
| 19 | DCACHE_WRITEBACK | lines | platform | Accepted dirty-line write |
| 29 | MEM_FETCH_READ | lines | platform | Accepted I-cache line read |
| 30 | MEM_DATA_READ | lines | platform | Accepted D-cache line read, including prefetch |
| 32 | MEM_WRITE | lines | platform | Accepted line write (D-cache or debug) |
| 33 | MEM_BACKPRESSURE | clocks | platform | Downstream request valid without `ready` |
| 34 | MEM_READ_SERVICE | clocks | platform | Accepted I or D line read awaiting response |
| 64 | DCACHE_WRITE_ACCESS | occ | platform | D-cache store lookup |
| 65 | UNCACHED_FB_STORE | occ | platform | Accepted framebuffer store |
| 66 | UNCACHED_MMIO_ACCESS | occ | platform | Other accepted non-SDRAM data request |
| 67 | UNCACHED_WAIT | clocks | platform | Accepted non-SDRAM request without response |
| 68 | SYSTEM_SERIALIZE_HOLD | clocks | core | Serialized instruction waiting for older work, outside a data hold |
| 69 | MUL_USE_HOLD | clocks | core | Execute stalled on a multiply one stage ahead |
| 76 | DEBUG_HALTED | clocks | core | Halt acknowledged |
| 77 | MEM_MASTER_WAIT | clocks | platform | Any line master valid without `ready` |
| 78 | BRANCH_PREDICT_TAKEN | occ | core | Slot enters execute predicted taken (92, or a Q101H static/return redirect) |
| 79 | BRANCH_MISPREDICT | occ | core | Retired transfer that left the fetched path; two refill clocks each |
| 80 | DCACHE_PREFETCH_ISSUE | occ | platform | Next-line prefetch started ([memory](../memory/spec.md#next-line-prefetch)) |
| 81 | DCACHE_PREFETCH_USEFUL | occ | platform | Miss refilled from the prefetch buffer |
| 82 | DCACHE_PREFETCH_DROP | lines | platform | Prefetched line dropped unused (failed, replaced, written back, cleared) |
| 83 | IFETCH_PREDICT_BUBBLE | clocks | core | IFETCH_WAIT the clock after a Q101H redirect not followed by a mispredict |
| 84 | IFETCH_MISPREDICT_REFILL | clocks | core | IFETCH_WAIT in a mispredict's redirect clock and the next |
| 85 | IFETCH_REDIRECT_REFILL | clocks | core | IFETCH_WAIT in another late redirect's clock (trap, MRET, resume) and the next |
| 86 | IFETCH_SUPPLY_WAIT | clocks | core | Other IFETCH_WAIT while a response is owed or a request not accepted |
| 87 | MEM_OWNER_FETCH | clocks | platform | Line transaction is an I-cache read, acceptance through response |
| 88 | MEM_OWNER_DATA_READ | clocks | platform | Same span, D-cache demand read |
| 89 | MEM_OWNER_DATA_PREFETCH | clocks | platform | Same span, D-cache prefetch read |
| 90 | MEM_OWNER_DATA_WRITE | clocks | platform | Same span, D-cache line write |
| 91 | MEM_OWNER_DEBUG | clocks | platform | Same span, debug-master access |
| 92 | FETCH_PREDICT_TAKEN | occ | core | Slot enters execute with its fetch-BTB prediction accepted |
| 93 | FETCH_PREDICT_STALE | occ | core | Slot enters execute with its fetch prediction rejected (one refill clock) |
| 94 | DCACHE_WRITE_BUFFER_PENDING | clocks | platform | Displaced dirty line occupies the buffer |
| 95 | DCACHE_WRITE_BUFFER_WAIT | clocks | platform | D-miss waits for buffer space or same-line drain |
| 96 | DCACHE_PREFETCH_DEFERRED | clocks | platform | Queued candidate not selected this clock |
| 97 | DCACHE_PREFETCH_CANCEL | cands | platform | Queued intent canceled by same-line demand, replacement or maintenance |

IDs 83..86 are mutually exclusive and each implies IFETCH_WAIT; the rest of
IFETCH_WAIT is unattributed. A two-bit `refill_q` (`t_fetch_refill`) records
each flush cause (mispredict over late redirect over predicted redirect) for
one clock. IDs 87..91 are exclusive and never coincide with
MEM_BACKPRESSURE. Assertions check both groups.

## Files

| Path | Role |
| --- | --- |
| `src/nand2doom/rtl/core/n2d_rv32im_core.sv` | Product core top: stage registers, `u_register_file` |
| `src/nand2doom/rtl/core/n2d_{if,dec,exe,ma,wb,ctrl,csr,muldiv}.sv` | Pipeline stages and control |
| `src/nand2doom/rtl/core/n2d_core_pkg.sv`, `n2d_core_types_pkg.sv` | Stage and state records |
| `src/nand2doom/rtl/core/n2d_pmon.sv` | PMON counters, inside `n2d_csr` |
| `src/nand2doom/rtl/core/n2d_rv32i_decode_alu.sv`, `n2d_rv32i_core_step.sv` | RV32I slice and step core |
| `src/nand2doom/rtl/platform/n2d_pmon_platform_events.sv` | Platform PMON sources |
| `src/nand2doom/dv/core/n2d_rv32i_program_smoke.sv` | Flat-memory smoke composition |
