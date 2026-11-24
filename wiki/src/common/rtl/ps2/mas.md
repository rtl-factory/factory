# PS/2 keyboard MAS

## Purpose

Two blocks that every project's keyboard path shares. `n2a_ps2_receiver` turns
the PS/2 device clock and data pins into bytes. `n2a_ps2_decoder` turns a
Set-2 byte stream into key events `{extended, key_break, code}`. A project
keeps its own event store and key map around them. nand2doom's
`n2d_ps2_keyboard` uses one receiver and two decoders: one for the pins and
one for the JTAG byte stream ([I/O MAS](../../../nand2doom/rtl/io/mas.md#ps2-keyboard-receiver)).
nand2mario's `n2m_scancode_buttons` uses one decoder on the JTAG byte stream
([HAS](../../../nand2mario/rtl/has.md)). Code: `src/common/rtl/ps2/`.

## Interface

### Package

`n2a_ps2_pkg` (`src/common/rtl/ps2/n2a_ps2_pkg.sv`):

| Name | Kind | Meaning |
| --- | --- | --- |
| `PS2_PREFIX_EXTENDED`, `PS2_PREFIX_BREAK` | `logic [7:0]` | `E0` and `F0` |
| `t_ps2_event` | packed struct | `extended`, `key_break`, `code[7:0]`: bit 9, bit 8, bits 7:0 |
| `t_ps2_rx_errors` | packed struct | `parity`, `frame`, `timeout` |
| `ps2_is_prefix()` | function | true for `E0` and `F0` |

The receiver's state record, `t_ps2_rx_state`, lives with the other common
state records in `n2a_types_pkg`.

### Receiver

Parameter `FRAME_TIMEOUT_CYCLES` (default 5,000: 200 µs at 25 MHz; at least
2, checked at elaboration).

| Port | Dir | Width | Meaning |
| --- | --- | --- | --- |
| `clk_i`, `reset_i` | in | 1 | System clock; synchronous active-high reset |
| `ps2_clk_i`, `ps2_data_i` | in | 1 | PS/2 pins, asynchronous, idle high |
| `enable_i` | in | 1 | Low: the line is ignored and a frame in progress is held |
| `abort_i` | in | 1 | Abandon the frame in progress; nothing is reported on this clock |
| `byte_valid_o` | out | 1 | One clock per good frame, on its stop edge |
| `byte_o` | out | 8 | The frame's data while `byte_valid_o` |
| `errors_o` | out | `t_ps2_rx_errors` | One clock per discard, never with `byte_valid_o` |

- A frame is a low start bit, eight data bits LSB first, odd parity and a high
  stop bit. Each bit is sampled on a falling clock edge.
- On the stop edge, a frame with good parity and a high stop bit gives
  `byte_valid_o`. A bad parity bit gives `errors_o.parity`, a low stop bit
  `errors_o.frame`, and both can mark one frame. The receiver is then idle.
- A falling edge with data high while idle is a bad start: `errors_o.frame`.
- Inside a frame, `FRAME_TIMEOUT_CYCLES` clocks without a falling edge give
  `errors_o.timeout` and discard the frame (200 µs by default).
- Outputs are valid only while `enable_i && !abort_i`. `abort_i` wins over a
  stop edge on the same clock.
- Latency: a pin edge is seen three clocks after it reaches the pin (two
  synchronizer stages and the edge register). Bytes arrive at most once per
  frame, about every 1 ms at a 10 kHz device clock.

### Decoder

| Port | Dir | Width | Meaning |
| --- | --- | --- | --- |
| `clk_i`, `reset_i` | in | 1 | System clock; synchronous active-high reset |
| `byte_valid_i`, `byte_i` | in | 1, 8 | One Set-2 byte offered |
| `byte_ready_o` | out | 1 | The offered byte is taken on this clock |
| `clear_i` | in | 1 | Drop the pending qualifiers at the end of this clock |
| `event_valid_o`, `event_o` | out | 1, `t_ps2_event` | The event a non-prefix byte completes |
| `event_ready_i` | in | 1 | The event is taken on this clock |

- `E0` and `F0` set the pending `extended` and `key_break` qualifiers. They
  produce no event and are always ready (`byte_ready_o` is high for them
  whether or not they are valid).
- Any other byte is offered on the same clock as `event_o =
  {extended, key_break, byte_i}`, with no added latency. `byte_ready_o`
  follows `event_ready_i`. When the event is taken, both qualifiers clear.
  When it is refused, they stay, so the producer can offer the byte again.
- `clear_i` clears both qualifiers after this clock and wins over a prefix on
  the same clock. The event offered on that clock still carries the old
  qualifiers.
- Reset clears both qualifiers.

## Microarchitecture

### Receiver

Each pin passes its own `n2a_cdc_bit_sync` (two stages, reset high). The
state record holds the previous synchronized clock, `active`, a bit index,
the data byte and the parity bit. A separate counter, sized by
`FRAME_TIMEOUT_CYCLES`, holds the frame timeout. While receiving, a falling
edge does one of these things:

- Idle with data low: start a frame at index 0.
- Idle with data high: a bad start.
- Index 0 to 7: store data bit `index`.
- Index 8: store the parity bit.
- Index 9: the stop bit, which completes the frame.

Every edge inside a frame restarts the timeout. One transaction: the stop
edge reaches the synchronized clock, the edge register sees it on the next
clock, and on that clock `byte_valid_o` and `byte_o` are driven from the
stored data while the frame returns to idle. Assertions: the index never
passes 9, the index is 0 outside a frame, and a byte and an error never come
together.

### Decoder

Two pending bits. `event_valid_o`, `event_o` and `byte_ready_o` are
combinational in the byte, the pending bits and `event_ready_i`. The next
state is set by an `E0` or `F0`, cleared by a taken event, and cleared by
`clear_i` last.

## Clocks and resets

One clock, `clk_i`. The receiver owns the crossing of the two PS/2 pins into
`clk_i`, through `n2a_cdc_bit_sync`: each pin is a slow level, sampled only
after its clock has fallen. Both blocks reset synchronously, like the pin
synchronizers and every user ([RTL coding](../../../../bkm/rtl.md#rules-reset-and-clocks)).

## Design choices

- **Two blocks with a byte seam** (chosen). The receiver gives bytes and the
  decoder takes bytes. Two of the three users have no pins: the JTAG byte
  streams of both projects. Each of them uses only the decoder.
- **One block from pins to events, with a byte-inject port** (rejected).
  nand2doom's JTAG stream keeps qualifiers of its own: CONTROL and receive
  errors do not clear them. So the block would need two prefix states and a
  mux, and nand2mario would carry an unused receiver.
- **A registered event output** (rejected). nand2doom decides the priority
  of a push on the edge where the event completes, and a scan byte's refusal
  is reported on its own offer clock. A pipeline stage would change both.
- **Pulses for errors, stickiness in the user**. nand2doom keeps sticky
  diagnostics with a software clear. A pulse lets each project choose its
  own policy.

## Verification

- [DV plan](../../dv/ps2/dv-plan.md): the receiver at non-harmonic pin timing
  with every discard, enable and abort; the decoder against a reference model
  with random streams, back-pressure and clears.
- [FV plan](../../fv/ps2/fv-plan.md): both blocks against reference models
  written from this MAS, unbounded.
- The users' own suites pin their behaviour: nand2doom's `n2d_ps2_keyboard`,
  `n2d_jtag_input` and FIFO proof, and nand2mario's `n2m_de10_lite` JTAG
  joypad test.
