# UART wire helper DV plan

## Scope

`src/common/dv/uart/uart_wire.py` is the one cocotb UART driver and receiver for every bench that talks to a serial pin (#131). Every project bench that drives or samples UART bits uses it; protocol framing above the byte (uart_mem, the nand2doom debug and stream protocols, the nand2mario cartridge loader) stays in each bench.

`UartWire` sends and receives 8N1 frames in one of two timing forms:

| Form | Arguments | Bit k starts | Used by |
| --- | --- | --- | --- |
| Clock | `clock`, `clocks_per_bit` (may be fractional, for example 12.5) | at edge floor(k × clocks_per_bit) of the clock; `drive_edge` picks the rising edge the caller is at or the next falling edge | fixtures with a known clock: nand2doom debug and bulk, nand2mario preload and loader |
| Time | `bit_ps` | round(k × bit_ps) ps after the frame starts, not tied to any clock | board tops and host-rate tests: nand2doom board, stream and halt targets, nand2mario board and snapshot |

Received bits are sampled at each bit's centre. A start bit that is not low or a stop bit that is not high there raises an AssertionError. `sample_window_ps` also samples that far either side of the centre and requires the three samples to agree. Hooks for protocol tests: `send(data, stop=0)` sends a low stop bit, `send_levels` drives any raw bit sequence, `idle` holds the line, and `receive_byte(on_start=...)` calls back at the start edge.

## Testbench

`n2a_uart_wire_fixture` loops the common PHY (`n2a_uart_rx`, `n2a_uart_tx`) at 12 1/2 clocks per bit (2 Mbaud at 25 MHz). Its `force_i` input can put any level on `tx_o`. `test_uart_wire.py` checks the helper against the PHY. The PHY has its own tests in each project, so the helper and the PHY are never checked only against each other.

## Features

| ID | Feature | Stimulus | Checker | Test | Level |
|---|---|---|---|---|---|
| W1 | Send, all three forms | 40 random bytes, back to back; time form a quarter clock off the edges | the receiver's bytes equal the sent bytes, with no framing error | `n2a_uart_wire` | L0 |
| W2 | Receive, all three forms | 40 random bytes from the transmitter | the helper's bytes equal them | `n2a_uart_wire` | L0 |
| W3 | Send bit timing | one 0x55 frame per form (every bit boundary a transition) | clock form, rising and falling drive: the line sampled between every pair of drive edges holds bit k from edge floor(k × 12.5); time form: the line holds bit k 1 ns after round(k × bit_ps) and 1 ns before round((k+1) × bit_ps) | `n2a_uart_wire` | L0 |
| W4 | Receive sample point | frames forced on tx whose bits are true only in the clock the helper samples (clock form) or within 120 ns of the centre (time form, ±100 ns window), wrong elsewhere | the helper reads the true byte | `n2a_uart_wire` | L0 |
| W5 | Framing-error hook | a frame with `stop=0` | the receiver reports a framing error | `n2a_uart_wire` | L0 |
| W6 | Bad-frame detection | forced on tx: an all-low frame, an all-ones frame, a start pulse shorter than half a bit, and (time form) a 40 ns glitch over a data bit's late window sample | the helper raises "stop bit", reads 0xFF, raises "start bit", and raises "unstable" | `n2a_uart_wire` | L0 |

Each checker was seen to fail: with the start-bit assert removed, the window assert removed, the window samples dropped, 12 instead of 12.5 clocks per bit on send or on receive, or the time-form sample moved to 0.3 of a bit, `n2a_uart_wire` fails.

## Coverage

`test_uart_wire.py` samples functional coverage after each check passes and writes it at the end ([sim spec, Coverage](../../../../tools/sim/spec.md#coverage)):

| Point | Bins | Feature |
|---|---|---|
| `uart_wire.transfer.form` × `direction` | rising, falling and time forms × send and receive: 6 | W1, W2 |
| `uart_wire.byte.ones` | data bits high in a checked byte, 0 to 8: 9 | W1, W2 |
| `uart_wire.bad_frame.form` × `outcome` | clock and time forms × stop bit, all ones, start bit, unstable: 7 | W6 |

The clock-form "unstable" bin is ignored: the sample window, and so an unstable frame, exists in the time form only (Scope). At seed 5 all 33 bins are hit.

Code coverage (`--//tools/bazel:coverage`) at seed 5 is line 94.1% (32/34), branch 96.4% (54/56), expression 80.0% (24/30) and toggle 96.4% (318/330), over the fixture and the common PHY it loops; every hole is in the PHY:

- Lines (2): the `default:` arms of the receiver's and transmitter's state `case`. Every value of the state enum has its own arm, so they are unreachable by design.
- Toggle (12), branches (2) and two expression terms: the fractional bit-rate accumulator at this fixture's 1/2 fraction. `frac_acc[1]` and `frac_sum[2]` never leave 0, and in the receiver's start state and the transmitter's start bit the accumulator's carry goes one way only. Other fractions reach them; the PHY's own tests in each project own that coverage, since this bench checks the helper, not the PHY.
- Expression (4 more): the `$isunknown` assertion terms on the receiver's synchronised input and the transmitter's `byte_valid_i` and `byte_i`, which a bench that never drives X cannot make true, and the receiver's check that a byte is never both valid and a framing error, which holds.

## Status

The nand2doom and nand2mario benches that had their own bit drivers now use the helper. Their targets pass unchanged.
