# UART memory host in simulation: DV plan

## Scope

`src/common/dv/uart_mem/` holds the endpoint's own contract bench (`test_uart_mem.py` on `n2a_uart_mem_fixture`, targets `n2a_uart_mem_contract` and `n2a_uart_mem_load_readback`) and `sim_host.py`, which runs the [host library](../../scripts/uart_mem/spec.md) itself inside a cocotb bench (#190). A project bench that loads a board top or system through the endpoint uses `sim_host.py`, so the load it checks is the one the board console sends, not a bench copy of it.

## Simulated host

| Part | Role |
|---|---|
| `SimTransport(wire, poll_ps)` | `uart_mem.Transport` on a [UART wire](../uart/dv-plan.md): `write` sends the bytes on the DUT's receive pin; `read` returns what the wire's background receiver has collected from the DUT's transmit pin, polling every `poll_ps` until something arrives or the deadline passes |
| `client(wire, poll_ps=..., timeout_s=...)` | A `uart_mem.Client` on that transport whose clock is simulated time in seconds, so command deadlines count simulated time |
| `run(func, *args)` | Runs one blocking library call (`uart_mem.load`, `Client.identify`, ...) in a cocotb bridge thread while the simulation advances |

## Features

| ID | Feature | Stimulus | Checker | Test | Level |
|---|---|---|---|---|---|
| H1 | nand2mario load and boot at the board rate | `uart_mem.load` of a cartridge header and a program at 0x0100 over garbage ROM, host at exactly 2 Mbaud, `n2m_system` at 12 1/2 clocks a bit (`UART_FRAC_NUM`/`UART_FRAC_DEN` 1/2) | identify returns project `n2m ` and entry 0x0100; the load (hold, write, read back, entry, release) succeeds; after the release the core retires exactly the program's instructions to its final HALT and its WRAM marker is stored; every received bit holds over its middle 60% (`sample_window_ps` 150 ns) | `n2m_system_uart_mem` | L0 |
| H2 | The same load and boot through the real board tops | `n2m_de10_lite_fixture` and `n2m_de2_115_fixture` (portable PLL, SDRAM device model, board UART pins) with the board bit period; the load starts after PLL lock and SDRAM initialization | as H1, with the program in the SDRAM device | `n2m_de10_lite_uart_mem`, `n2m_de2_115_uart_mem` | L0 |
| H3 | The board smoke's 32 KiB image load | `board_smoke` loads the background program with `uart_mem.load` instead of the cartridge-loader protocol (which still answers its Pings and the frame snapshot) | the load holds the core, the header byte is in the SDRAM device, the release starts the core, the program runs to its final HALT and the VGA frame matches | `n2m_de10_lite`, `n2m_de2_115` | L1 |

H1 was seen to fail with the integer period (`UART_FRAC_NUM` 0, 12 clocks a bit, 4% fast): "UART TX bit 7 unstable". A malformed frame received while the library waits is raised from its next read, so a failing load reports that one error.
