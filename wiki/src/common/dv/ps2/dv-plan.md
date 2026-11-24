# PS/2 keyboard DV plan

## Scope

The common PS/2 receiver and Set-2 decoder ([MAS](../../rtl/ps2/mas.md)),
each tested alone through its ports. The users test their own composition:
nand2doom's `n2d_ps2_keyboard` and `n2d_jtag_input`
([I/O DV plan](../../../nand2doom/dv/io/dv-plan.md)) and nand2mario's
`n2m_de10_lite` JTAG joypad test.

## Testbench

- `src/common/dv/ps2/test_ps2_receiver.py`, top `n2a_ps2_receiver` with the
  default 5,000-clock timeout. A pin driver sends frames bit by bit in time
  (not in clocks), so the pin edges drift against the 25 MHz clock. A monitor
  samples every clock's outputs once they settle after the edge. It fails on
  a byte and an error in one clock, and it collects the bytes and error
  words.
- `src/common/dv/ps2/test_ps2_decoder.py`, top `n2a_ps2_decoder`. It drives
  one input set per clock. Its reference model holds the two qualifiers from
  the MAS rules, and the outputs are checked every clock.

## Features

| ID | Spec section | Stimulus | Checker | Coverage point | Test | Level |
|---|---|---|---|---|---|---|
| PS2-F1 | Receiver: frame | 28 frames (24 random, `00`, `FF`, `E0`, `F0`), random half period 1 to 2 µs | the bytes equal the frames, in order, with no error | every frame | `n2a_ps2_receiver` | L0 |
| PS2-F2 | Receiver: parity, stop | bad parity; low stop; both | one `parity`, one `frame`, then one word with both; no byte | each error word | `n2a_ps2_receiver` | L0 |
| PS2-F3 | Receiver: bad start | a falling edge with data high while idle | one `frame` error | the error | `n2a_ps2_receiver` | L0 |
| PS2-F4 | Receiver: timeout | start and one data bit, then silence | nothing 4,800 clocks after the last edge; one `timeout` by 200 clocks later; the next frame is received | the timeout | `n2a_ps2_receiver` | L0 |
| PS2-F5 | Receiver: enable | a good frame, a bad stop and a bad start while disabled; then a frame enabled | nothing while disabled (including no timeout); then the byte | the disabled frames | `n2a_ps2_receiver` | L0 |
| PS2-F6 | Receiver: abort | abort on the stop edge's clock; abort between two edges | no byte, no error, no timeout later; the next frame is received | both aborts | `n2a_ps2_receiver` | L0 |
| PS2-F7 | Decoder: events | W make and break, Up make and break, with idle clocks between bytes | events `01D`, `11D`, `275`, `375` | all four qualifier pairs | `n2a_ps2_decoder` | L0 |
| PS2-F8 | Decoder: refusal | `E0 F0`, then `6B` refused twice, then taken, then again | events `36B`, `06B`; ready follows `event_ready_i` | refusal with both qualifiers | `n2a_ps2_decoder` | L0 |
| PS2-F9 | Decoder: clear | clear alone after `E0`; clear on the event's clock after `F0` | `075`; `11C`, then `01C` | both | `n2a_ps2_decoder` | L0 |
| PS2-F10 | Decoder: all rules | 4,000 random clocks: valid 70%, prefixes and a few codes weighted, ready 70%, clear 5% | every clock against the reference: event valid, event, byte ready | more than 100 events, all four qualifier pairs | `n2a_ps2_decoder` | L0 |

Seeds: `n2a_ps2_receiver` 11, `n2a_ps2_decoder` 23 (each test prints its
seed; `N2A_SEED` overrides it).

Each checker was seen to fail on a scratch break of the RTL, which was then
dropped:

- Even parity accepted: PS2-F1 and PS2-F2 fail.
- Stop bit ignored: PS2-F2 fails.
- Timeout one clock early: PS2-F4 still passes, because its window is
  200 clocks wide; the receiver proof catches it.
- `abort_i` not gating the stop edge: PS2-F6 fails.
- The decoder clearing the qualifiers on a refused byte: PS2-F8 and PS2-F10
  fail.
- `clear_i` ignored: PS2-F9 and PS2-F10 fail.

## Coverage

Functional coverage is the coverage-point column, asserted by the tests
(every qualifier pair, every error word, both aborts). Code coverage is not
collected yet for any common block. Holes: a frame held while disabled
mid-frame is reached only by the proof (`c_disabled_mid_frame`), because no
user disables the receiver without aborting.

## Status

No open gaps.
