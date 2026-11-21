# memory FV plan

## Scope

`n2d_memory_arbiter`, the line arbiter between the I-cache, the D-cache, the debug adapter and the SDRAM controller ([memory MAS](../../rtl/memory/mas.md#arbitration-and-debug-adaptation)). The promises: one line transaction at a time, every handshake forwarded once and to the right place, a stalled request held, prefetch only without demand, and the MEM-R6 round-robin bound. A broken grant corrupts memory or hangs a master; the breaking cases need three masters, back-pressure, prefetch and completion order to line up on particular edges, which random simulation rarely does. Tool kind: property checker on the RTL (SymbiYosys, PDR for the proof, BMC for covers), because the question is "does this block ever break the rule".

The harness is `src/nand2doom/fv/memory/n2d_memory_arbiter_fv.sv`; the RTL's own four assertions (one-hot ready and response, no request while busy, stalled master still valid) are proven with it.

## Properties

Spec lines are in [memory MAS](../../rtl/memory/mas.md): MEM-R6 (line 26), Line port (lines 98 to 105), Arbitration (lines 256 to 263). The sync commit of the rows below is `7ce42c1`.

| Property | Harm if broken | Spec line | Assumptions | Result | Command | Sync commit |
|---|---|---|---|---|---|---|
| `one_line_transaction_outstanding`: no request is presented downstream while a line transaction is in flight | Two transactions in flight return responses to the wrong owner | MEM-R6 "One outstanding line transaction" | both | proven (all depths) | `python3 tools/fv/fv.py run n2d_memory_arbiter` | `7ce42c1` |
| `one_grant_per_edge`: at most one master sees ready | Two masters believe their request was taken; one is lost | Arbitration "the chosen request handshakes directly downstream" | both | proven | `python3 tools/fv/fv.py run n2d_memory_arbiter` | `7ce42c1` |
| `no_lost_or_duplicated_request`: a master handshake happens exactly when the downstream handshake does | A request is dropped or issued twice | Line port "exactly one later response" | both | proven | `python3 tools/fv/fv.py run n2d_memory_arbiter` | `7ce42c1` |
| `forwarded_payload_is_the_winners`: the accepted downstream operation, address, data and enables are the accepted master's | Memory is written or read at another master's address | Arbitration "the chosen request handshakes directly downstream" | both | proven | `python3 tools/fv/fv.py run n2d_memory_arbiter` | `7ce42c1` |
| `response_to_owner`: each response pulse, its data and error go to the captured owner only, and no master sees a response otherwise | A cache fills a line with another master's data | Line port "exactly one later response pulse returns to the captured owner" | both | proven | `python3 tools/fv/fv.py run n2d_memory_arbiter` | `7ce42c1` |
| `stalled_request_held`: a stalled downstream request stays presented with the same payload | The controller sees a payload change under back-pressure | Arbitration "A stalled chosen request stays chosen until accepted" | both | proven | `python3 tools/fv/fv.py run n2d_memory_arbiter` | `7ce42c1` |
| `prefetch_only_without_demand`: a prefetch is chosen only when no master presented demand at the time it was chosen | Prefetch steals bandwidth from a stalled demand miss | Arbitration "a candidate only when no master presents demand" | both | proven | `python3 tools/fv/fv.py run n2d_memory_arbiter` | `7ce42c1` |
| `round_robin_bound`: a waiting demand request is accepted before a third completion of another master | A master starves | MEM-R6 "each master is accepted at least once per three completions" | both | proven | `python3 tools/fv/fv.py run n2d_memory_arbiter` | `7ce42c1` |

Helper invariants (proven, they only narrow the reachable state): the DUT's busy flag, owner and stall flag match the harness's view of the interface; the rotating pointer never holds the unused value 3.

Covers, all reached within 16 cycles (`cover` task): each of the three masters accepted; a stalled request then accepted; a prefetch accepted; the master after the last completed owner wins right after a completion; and, for each master, an acceptance after exactly two other completions, which shows the MEM-R6 bound is tight.

Scratch breaks, each made on a copy of the RTL and each failing the property it guards with a counterexample trace: presenting while busy (`one_line_transaction_outstanding` and the RTL's own check), ready to every valid master (`one_grant_per_edge`), the address of the pointer's master instead of the winner's (`forwarded_payload_is_the_winners`), the response routed to the pointer (`response_to_owner`), dropping the stalled selection (`stalled_request_held`), ignoring the prefetch flag (`prefetch_only_without_demand`), fixed priority instead of round robin (`round_robin_bound`, after 8 cycles).

Runtime on the cloud container: proof 8.7 s, covers under 1 s (both tasks together 8.7 s wall clock).

## Assumptions

- `master_holds_request`: a master that presents a request and is not accepted keeps it valid with the same prefetch flag, operation, address, data and enables. Spec: Line port "The requester holds the full payload until accepted". Driving side: the D-cache checks it in simulation (`line_request_held` in `n2d_dcache.sv`); the I-cache and the debug adapter do not yet check it, so for them it rests on their DV tests.
- `one_response_per_request`: the controller sends a response only while a line transaction it accepted is in flight, and only on a later edge. Spec: Line port "exactly one later response pulse". Driving side: not asserted in `n2d_sdram_controller.sv`; covered by the memory DV tests.
- The proof starts in reset and checks nothing while reset is asserted.

Out of scope: progress in time (the bound is counted in completions; bounded progress also needs the controller to complete every request, per the Arbitration bullet), the response data path's width (checked bit for bit, but only for routing), and the caches and the controller themselves.

## Covered files

`src/nand2doom/rtl/memory/n2d_memory_arbiter.sv`, unbounded (PDR proof).
