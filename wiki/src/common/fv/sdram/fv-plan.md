# SDRAM controller FV plan

## Scope

`n2a_sdram_ctrl` ([MAS](../../rtl/sdram/mas.md)), in both project configurations: nand2doom (burst eight, open row) and nand2mario (burst one, close page). The promises are: every command leaves the idle clocks its parameters and the burst need; refresh never misses its deadline; each accepted request gets exactly one response within the latency table; and a misaligned request answers with an error and issues nothing. A broken rule corrupts SDRAM contents or loses a response. The breaking cases need a request to arrive on a particular clock relative to refresh, row state and back-pressure, which random simulation reaches only by luck.

- Tool kind: a property checker on the RTL (SymbiYosys, k-induction at depth 24 for the proof, BMC to depth 64 for the covers), because the question is "does the controller ever break this rule".
- Harness: `src/common/fv/sdram/n2a_sdram_ctrl_fv.sv`, with parameter `LINE` selecting the configuration. It is run by `n2a_sdram_ctrl_line.sby` and `n2a_sdram_ctrl_word.sby`.
- The power-up wait is four clocks instead of 5000. It is a plain counter, and DV checks it at full length. The RTL's own six assertions are proven with the harness.

## Properties

Spec lines are in the [MAS](../../rtl/sdram/mas.md): Parameters (WAIT_* and refresh), Ports (request hold, one response), Latency (table), and Reset and errors. Labels follow the harness: `p_` property, `a_` assumption, `h_` helper invariant, `c_` cover. Results hold for both configurations. Each target runs with `python3 tools/fv/fv.py run n2a_sdram_ctrl_line n2a_sdram_ctrl_word`. Sync commit for every row: `3d0d9bd` (this pull request's RTL).

| Property | Harm if broken | Spec line | Result |
|---|---|---|---|
| `command_spacing`: each command follows the previous one by at least its WAIT_* idle clocks, by the burst after a READ, and by the burst plus WAIT_WR after a WRITE | A command lands inside tRCD, tRP, tRFC, tMRD or tWR, or interrupts a burst, and the device corrupts or loses data | Parameters, WAIT_* | proven |
| `column_after_activate_or_column`: a READ or WRITE follows an ACTIVATE or another column command, never a PRECHARGE or REFRESH | A column command goes to a closed row | Microarchitecture, phases | proven |
| `refresh_interval`: once initialized, no gap between REFRESH commands exceeds REFRESH_DEADLINE + 1 clocks | Rows lose charge and data | Parameters, REFRESH_DEADLINE | proven |
| `no_accept_while_outstanding`, `response_only_when_outstanding`: requests are accepted one at a time, ready rises no earlier than the response clock, and no response comes without a request | Two accesses overlap, or a requester takes a stray pulse as its answer | Ports, one response per accepted request | proven |
| `response_within_latency`: every accepted request answers within the longest latency of its configuration (12 clocks for nand2doom, 10 for nand2mario) | A requester hangs | Latency | proven |
| `misaligned_answers_next_clock`, `misaligned_issues_nothing`, `error_only_for_misaligned`, `aligned_no_error` | A misaligned request touches memory, or a good access reports an error | Reset and errors | proven |
| `dq_only_writing`: DQ is driven only on a WRITE clock or its burst | Bus contention with the device | Microarchitecture, read capture | proven |
| `cke_cs_active`: CKE high and CS low out of reset | The device ignores every command | Ports, sdram pins | proven |

Helper invariants (proven; they only narrow the state the induction may start from):

- The harness counters agree with the DUT's: refresh age, the outstanding access, and the last command.
- The phase encoding is valid, and WAIT and power-up counts stay below four.
- init_done and refresh tracking match the init phases, and the init refresh count is in range.
- In close page, at most the addressed bank is open, and only inside an access.
- The refresh age is bounded per phase.

Covers: a read response, a write response, an error response and a second access after the first. All are reached within 53 clocks (nand2mario) and 27 clocks (nand2doom).

Scratch breaks, each made on a copy of the RTL. Each failed the proof, and each was then dropped:

- ACTIVATE to READ without the tRCD wait fails `command_spacing`.
- A misaligned request that also goes to ACTIVATE fails `misaligned_issues_nothing`.
- Refreshing only at the deadline instead of at REFRESH_DUE fails the proof's induction (`h_access_only_when_outstanding`).
- Never refreshing fails the same way.
- Two breaks still prove, as they should, because they change data or latency but break no rule here: dropping the close-page precharge wait (RESPOND and IDLE still give tRP), and ending an open-row read capture one beat early (the next command still follows the burst). DV catches both: the latency table and the shadow compare ([DV plan](../../dv/sdram/dv-plan.md)).

Runtime on the cloud container: nand2mario 51 s, nand2doom 6 s.

## Assumptions

- `request_held`: a request that is presented and not accepted stays valid with the same payload. Spec: Ports, "hold it and its payload until an edge samples request_ready_o high". Checked on the RTL side by the controller's own stalled-request assertion, in simulation.
- The proof starts in reset and checks nothing while reset is asserted.

Out of scope: data values, which are tied off. Read data, write data and byte lanes are checked bit for bit by the DV benches and both projects' contract tests. Also out of scope are the board's DRAM_CLK phase and the device model.

## Covered files

`src/common/rtl/sdram/n2a_sdram_ctrl.sv`, `src/common/rtl/sdram/n2a_sdram_pkg.sv`. Unbounded proof by k-induction.
