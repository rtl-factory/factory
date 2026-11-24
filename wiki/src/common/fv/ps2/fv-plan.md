# PS/2 keyboard FV plan

## Scope

`n2a_ps2_receiver` and `n2a_ps2_decoder` ([MAS](../../rtl/ps2/mas.md)).

- **Receiver.** It reports a byte only for a well-formed frame, carries that
  frame's data, and reports each discard reason exactly when the spec says.
  It is silent while disabled or aborted. A broken rule types a phantom key,
  loses a key, or hides a line fault. The breaking cases are a stop edge on
  an abort clock, a timeout one clock before an edge, and a bad start right
  after a frame. Directed pin timing rarely reaches them.
- **Decoder.** Every non-prefix byte is offered as one event carrying exactly
  the qualifiers that the spec's rules leave pending. Prefixes are always
  ready. A refused byte keeps its qualifiers. A broken rule gives a stuck key
  (a lost break) or a doubled prefix on resubmission.
- Tool kind: a property checker on the RTL (SymbiYosys; PDR for the proof,
  BMC for the covers). It is checked against reference models written from
  the MAS, in `src/common/fv/ps2/n2a_ps2_receiver_fv.sv` and
  `n2a_ps2_decoder_fv.sv`.
- The receiver harness shortens the frame timeout from 5,000 to 4 clocks. It
  is a plain counter, and DV checks it at full length.

The nand2doom event FIFO proof (`n2d_ps2_keyboard`,
[its FV plan](../../../nand2doom/fv/io/fv-plan.md)) covers the composition:
it reads the physical event and the scan decoder's qualifiers from these
blocks.

## Properties

Spec lines are in the [MAS](../../rtl/ps2/mas.md), Interface: Receiver and
Decoder. Labels follow the harnesses: `p_` property, `a_` assumption, `h_`
helper invariant, `c_` cover. The sync commit for every row is this pull
request's RTL.

| Property | Harm if broken | Spec line | Assumptions | Result | Command |
|---|---|---|---|---|---|
| `byte_only_for_good_frame`, `byte_is_frame_data`: a byte comes exactly on the stop edge of a frame with odd parity and a high stop bit, and holds the frame's eight data bits | Phantom or lost key | Receiver: frame, stop edge | reset | proven | `python3 tools/fv/fv.py run n2a_ps2_receiver` |
| `parity_error_on_bad_parity`, `frame_error_on_bad_stop_or_start`, `timeout_after_silence`: each error comes exactly on its condition | A line fault goes unreported, or a good frame is reported as a fault | Receiver: discards, bad start, timeout | reset | proven | same |
| `silent_unless_receiving`: no byte and no error while `enable_i` is low or `abort_i` is high | An abort races a stop edge and a stale key lands | Receiver: "Outputs are valid only while `enable_i && !abort_i`" | reset | proven | same |
| `event_for_every_code`: an event is offered exactly for a valid non-prefix byte | A prefix becomes a key, or a key is swallowed | Decoder: prefixes produce no event | reset | proven | `python3 tools/fv/fv.py run n2a_ps2_decoder` |
| `event_carries_qualifiers`: the event is `{extended, key_break, byte}` from the reference qualifiers (set by `E0`/`F0`, cleared by a taken event, `clear_i` or reset) | Stuck key or wrong key | Decoder: qualifiers | reset | proven | same |
| `prefix_always_ready`: ready is high for a prefix, else equals `event_ready_i` | A producer resubmits a prefix and doubles it | Decoder: "always ready" | reset | proven | same |

Helper invariants (proven): the receiver's synchronizers, previous clock,
state, index, timer, parity and stored data bits equal the reference's. The
decoder's pending bits equal the reference qualifiers. The RTL's own
assertions are proven with the harness.

Covers, `cover` task:

- Receiver, within 40 clocks: a good byte; a good `E0`; a parity error
  alone; a stop error; both together; a bad start; a timeout; an abort on a
  stop edge; a falling edge while disabled mid-frame.
- Decoder, within 10 clocks: an event with both qualifiers; an extended make;
  a refused event with both qualifiers; a clear with a qualifier pending; a
  repeated `E0`.

Scratch breaks and runtimes are recorded in the pull request that added these
targets (#135).

## Assumptions

None on the interfaces: the pins, enable, abort, the byte stream, clear and
ready are free inputs. The proof starts in reset and checks nothing while
reset is asserted.

Out of scope: metastability (the synchronizer is a structural part), the
default 5,000-clock timeout (DV), and the users' policies (sticky
diagnostics, FIFO, key maps).

## Covered files

`src/common/rtl/ps2/n2a_ps2_receiver.sv`, `n2a_ps2_decoder.sv`,
`n2a_ps2_pkg.sv` and `src/common/rtl/sync/n2a_cdc_bit_sync.sv`, unbounded
(PDR), with `FRAME_TIMEOUT_CYCLES` 4.
