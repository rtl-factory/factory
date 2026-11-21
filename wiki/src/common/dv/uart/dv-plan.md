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
| W1 | Send, both forms | 40 random bytes, back to back; time form a quarter clock off the edges | the receiver's bytes equal the sent bytes, with no framing error | `n2a_uart_wire` | L0 |
| W2 | Receive, both forms | 40 random bytes from the transmitter | the helper's bytes equal them; time form within a ±100 ns window | `n2a_uart_wire` | L0 |
| W3 | Framing-error hook | a frame with `stop=0` | the receiver reports a framing error | `n2a_uart_wire` | L0 |
| W4 | Bad-frame detection | all-low frame and an all-ones frame forced on tx | the helper raises on the low stop bit and reads 0xFF from the good frame | `n2a_uart_wire` | L0 |

## Status

The nand2doom and nand2mario benches that had their own bit drivers now use the helper. Their targets pass unchanged.
