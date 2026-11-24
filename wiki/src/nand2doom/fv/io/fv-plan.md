# io FV plan

## Scope

The event FIFO of `n2d_ps2_keyboard`: one eight-entry queue fed by four producers (physical PS/2, UART injection, CPU INJECT and the JTAG scan-inject byte stream) and drained by DATA reads ([I/O MAS](../../rtl/io/mas.md#ps2-keyboard-receiver)). It stands in for the common block issue #104 offered (the JTAG keyboard mailbox): that block's spec lives outside this repository, while this FIFO, which the JTAG byte stream feeds, has its spec here. The promises: IO-R9 (no source but a full physical receiver ever loses an event), events read in order, occupancy reported truly, one push per edge in the spec's fixed priority, and the scan-inject qualifier rules. A lost or reordered key event is a stuck key in the game; the breaking cases need a full FIFO, a same-edge pop and two producers on one edge, which random stimulus rarely lines up. Tool kind: property checker on the RTL (SymbiYosys, PDR for the proof, BMC for covers) against a reference queue written from the spec in the harness `src/nand2doom/fv/io/n2d_ps2_keyboard_fv.sv`.

The PS/2 pins are free inputs, so every frame the receiver can decode is included. The frame timeout is shortened from 5,000 to 4 cycles in the harness: it bounds the gap between PS/2 clock edges, not the FIFO, and the shorter counter keeps the state small. The RTL's own three always-present checks (count within depth, one source per edge, response stable under back-pressure) are proven with it.

## Properties

Spec lines are in [I/O MAS](../../rtl/io/mas.md): IO-R9 (line 29), PS/2 MMIO registers (lines 316 to 330), JTAG keyboard (lines 338 to 346), PS/2 keyboard receiver (lines 399 to 409). The sync commit of the rows below is the merge of the pull request for #135. #104 rewrote the RTL's response-stability check without `$stable` (same check, see [the formal runner spec](../../../../tools/fv/spec.md#front-end-limits)). #135 moved the framing and both prefix states into the common [PS/2 blocks](../../../common/rtl/ps2/mas.md): the harness's white-box reads of the physical event (`physical_event_push`, `physical_event_data`) kept their names, and the scan-qualifier invariants now read `u_scan_decoder.pending_q`. No property changed; every row passed again, and a scratch break of the scan acceptance (ignoring the decoder's ready) still fails the proof.

In the harness each property is a labelled assertion `p_<property>` (`events_read_in_order` is split into `p_events_read_in_order`, `p_status_reports_occupancy`, `p_every_request_answered` and the no-error checks; `fixed_priority` into three), helper invariants are `h_` and covers `c_`, so a failing run names the property.

| Property | Harm if broken | Spec line | Assumptions | Result | Command | Sync commit |
|---|---|---|---|---|---|---|
| `one_push_per_edge`, `physical_has_priority`: at most one source is accepted per edge, and a physical event, even a dropped one, blocks the others | Two events written to one slot; one is lost | Receiver "Push priority", "At most one push per edge" | reset | proven (all depths) | `python3 tools/fv/fv.py run n2d_ps2_keyboard` | merge of #135 |
| `fixed_priority`: CPU INJECT is accepted only while no UART event is offered, a scan byte only while neither is, and a UART event is taken whenever a slot is free and no CONTROL or physical event owns the edge | A busy CPU or host starves UART injection | Receiver "Push priority: ... UART injection (held valid until ready), CPU INJECT ..., scan-inject"; JTAG keyboard "ready only when no CONTROL, physical completion, UART valid or accepted INJECT owns the edge" | reset | proven | `python3 tools/fv/fv.py run n2d_ps2_keyboard` | merge of #135 |
| `no_accept_without_slot`: UART, INJECT and scan bytes are accepted only into a free slot (a same-edge pop counts) | A held source believes its event landed while it was discarded (IO-R9) | IO-R9 "never silently lost"; INJECT "Backpressures until it owns a slot" | reset | proven | `python3 tools/fv/fv.py run n2d_ps2_keyboard` | merge of #135 |
| `control_suppresses_pushes`: nothing is pushed on an edge with an accepted CONTROL write | A push races a FIFO clear | Receiver "CONTROL (suppresses all pushes)" | reset | proven | `python3 tools/fv/fv.py run n2d_ps2_keyboard` | merge of #135 |
| `scan_prefix_always_ready`: an offered E0 or F0 is always ready | The host resubmits a prefix and doubles it | JTAG keyboard "`E0`/`F0` ... are always ready" | reset | proven | `python3 tools/fv/fv.py run n2d_ps2_keyboard` | merge of #135 |
| `events_read_in_order`: a DATA read returns the oldest event with bit 31 set, or zero when empty, and STATUS reports valid, empty, full and count of the reference queue | Keys arrive out of order, are repeated, or the count lies | DATA, STATUS rows; Receiver "eight-entry circular FIFO" | reset | proven | `python3 tools/fv/fv.py run n2d_ps2_keyboard` | merge of #135 |
| `drop_sets_overflow`, `only_drop_sets_overflow`: a physical event dropped when full sets overflow, and nothing else does (a refused scan byte never does) | Lost keys go unreported, or a refusal is reported as a loss | Receiver "Full: the new event is dropped and overflow set"; JTAG keyboard "a refusal never sets overflow" | reset | proven | `python3 tools/fv/fv.py run n2d_ps2_keyboard` | merge of #135 |

The reference queue's event for a scan byte is `{extended, break, byte}` from qualifiers the harness keeps by the spec (E0 or F0 sets one, only an accepted completing byte or reset clears both). Helper invariants (proven): the RTL's count, read pointer and scan qualifiers equal the reference queue's, and every occupied slot holds the reference event.

Covers, all reached within 40 cycles (`cover` task): the FIFO full; full with a push and a pop on one edge; a physical event dropped when full (24 cycles); a physical event accepted; a UART, an INJECT and a scan event (with both qualifiers) accepted; a scan byte refused; a DATA read returning an extended event; a CONTROL clear of a non-empty FIFO; a stalled response with the FIFO full.

Scratch breaks, each failing with a counterexample trace: CPU INJECT taking the edge from a held UART event (`fixed_priority`, found missing in review), UART ready ignoring a physical event (`physical_has_priority` and the RTL's one-source check), INJECT ready ignoring the count (`no_accept_without_slot`), scan ready ignoring CONTROL (`control_suppresses_pushes`), prefix not always ready (`scan_prefix_always_ready`), the read pointer skipping a slot (`events_read_in_order`), a drop not setting overflow (`drop_sets_overflow`), a UART refusal setting overflow (`only_drop_sets_overflow`), STATUS count off by one (`events_read_in_order`), the response data changing under back-pressure (the RTL's stability check). A refusal clearing the scan qualifiers was caught by the qualifier invariant.

Runtime on the cloud container: proof 11.8 s, covers 3.5 s (both tasks together 11.8 s wall clock).

## Assumptions

No interface assumptions: the producers, the PS/2 pins and the MMIO channel are free inputs, so the proof also holds when a producer withdraws a request early. The proof starts in reset and checks nothing while reset is asserted.

Out of scope: PS/2 frame decoding (parity, stop bit, timeout values) and the decoders' qualifier rules, both proven in the [common PS/2 FV plan](../../../common/fv/ps2/fv-plan.md), the diagnostics other than overflow, the MMIO error responses, and the read data of CONTROL and the diagnostic STATUS bits.

## Covered files

`src/nand2doom/rtl/io/n2d_ps2_keyboard.sv` (the FIFO, push arbitration and MMIO response) with the common `n2a_ps2_receiver` and `n2a_ps2_decoder` inside it (`src/common/rtl/ps2/`), unbounded (PDR proof), with `FRAME_TIMEOUT_CYCLES` 4.
