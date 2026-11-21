# n2a_sdram_ctrl MAS

## Purpose

`n2a_sdram_ctrl` is the one x16 SDRAM controller every project uses for the IS42S16320D (32M x16, CAS latency two) at 25 MHz. One request moves one burst: a 16-bit word (nand2mario) or a 128-bit line (nand2doom). Its parameters select the burst, the row policy, the address map and the idle clocks after each command, so one engine reproduces both projects' timing contracts. `common` has no HAS; each project's HAS names this block at its memory seam. Code: `src/common/rtl/sdram/n2a_sdram_ctrl.sv` and `n2a_sdram_pkg.sv`.

## Interface

### Parameters

| Parameter | Default | Meaning |
|---|---|---|
| `BURST_BEATS` | 1 | Beats per request, 1 or 8. Sets the data width (`16*BURST_BEATS`) and the mode register burst length. |
| `OPEN_ROW` | 0 | 1 keeps one row per bank open and precharges only on a row miss or before a refresh. 0 is close page: every access is ACTIVATE, column command, PRECHARGE. |
| `ROW_XOR_BANK` | 0 | Address map: 0 is bank `[25:24]`, row `[23:11]`; 1 is row `[25:13]`, bank `[12:11]^[14:13]`. Column is `[10:1]` in both. |
| `POWERUP_CLOCKS` | 5000 | NOP clocks after reset before the first PRECHARGE ALL. |
| `INIT_REFRESHES` | 2 | Refreshes in the init sequence, 1 to 15. |
| `INIT_WAIT_MIN` | 2 | Floor on the idle clocks after each init command. |
| `WAIT_RCD`, `WAIT_RP`, `WAIT_RFC`, `WAIT_WR`, `WAIT_MRD` | 0, 0, 0, 1, 0 | Idle (NOP) clocks after ACTIVATE, PRECHARGE, REFRESH, the last write beat (close page only), and MODE REGISTER SET. |
| `REFRESH_DUE` | 163 | Refresh age at which the controller stops accepting and refreshes. |
| `REFRESH_DEADLINE` | 194 | Refresh age the controller must never exceed; checked at elaboration against the longest access and in simulation. |
| `REFRESH_AGE_FROM_MODE` | 0 | 1 starts the refresh age again at MODE REGISTER SET; 0 starts it at the first init refresh. |

The defaults are legal for the IS42S16320D-7 at 25 MHz (40 ns clock: tRFC 63 ns needs one idle clock after REFRESH, tWR 14 ns plus the precharge needs one after the last close-page write beat). Elaboration fails when the init refresh wait or, in close page, `WAIT_WR` is zero.

The project configurations, as every bench, harness and project top passes them:

| Project | `BURST_BEATS` | `OPEN_ROW` | `ROW_XOR_BANK` | `POWERUP_CLOCKS` | `INIT_REFRESHES` | `INIT_WAIT_MIN` | RCD/RP/RFC/WR/MRD | DUE/DEADLINE | `REFRESH_AGE_FROM_MODE` |
|---|---|---|---|---|---|---|---|---|---|
| nand2doom | 8 | 1 | 1 | 5000 | 2 | 2 | 0/0/0/1/0 (WR is unused with open row) | 163/194 | 0 |
| nand2mario | 1 | 0 | 0 | 5001 | 8 | 2 | 2/2/3/2/2 | 160/195 | 1 |

### Ports

All ports are in the `clk_i` domain.

| Port | Dir | Width | Meaning |
|---|---|---|---|
| `clk_i`, `reset_i` | in | 1 | System clock; synchronous active-high reset. |
| `request_valid_i` | in | 1 | A request is presented; hold it and its payload until an edge samples `request_ready_o` high. |
| `request_ready_o` | out | 1 | High in IDLE after init when no refresh is due. |
| `request_write_i` | in | 1 | 1 write, 0 read. |
| `request_address_i` | in | 26 | Byte address, aligned to the burst (`2*BURST_BEATS` bytes). |
| `request_write_data_i` | in | `16*BURST_BEATS` | Write data, beat 0 in the low bits. |
| `request_byte_enable_i` | in | `2*BURST_BEATS` | Write lanes; ignored on reads. |
| `response_valid_o` | out | 1 | One pulse per accepted request, writes included. |
| `response_data_o` | out | `16*BURST_BEATS` | Read data, valid with the pulse. |
| `response_error_o` | out | 1 | With the pulse: the request was misaligned and made no access. |
| `idle_o` | out | 1 | The engine is in IDLE. |
| `init_done_o` | out | 1 | The mode register is set. |
| `sdram_*_o`, `sdram_dq_i` | | | Command, address, bank, DQM, DQ and its output enable; `cke` is `!reset_i` and `cs_n` is `reset_i`. The inverted `clk_i` drives DRAM_CLK: nand2doom makes it in the board top, nand2mario in its SDRAM backing. |

### Latency

Latency counts clocks after the accepting edge, up to and including the clock in which `response_valid_o` is high.

| Configuration | Read | Write | Idle again |
|---|---|---|---|
| Close page (nand2mario) | 9 | 10 | the clock after the response |
| Open row, row hit | 10 | 9 | with the response |
| Open row, bank closed | 11 | 10 | with the response |
| Open row, row miss | 12 | 11 | with the response |
| Misaligned request | 1, with error | 1, with error | with the response |

The nand2doom memory MAS counts edges from acceptance to the registered response, which is one less than this table.

### Reset and errors

Reset returns to POWERUP with every bank closed. A misaligned request is answered in the next clock with `response_error_o` and issues no command. DQM stays `11` until the mode register is set.

## Microarchitecture

One registered state struct (`t_sdram_regs`) and one combinational next-state block. Every pin is decoded from the registered phase, so commands, DQ output enable and DQM change only at the edge.

Phases: POWERUP, INIT_PRECHARGE, INIT_REFRESH (`INIT_REFRESHES` times), INIT_MODE, then IDLE. Access phases are PRECHARGE (one bank), PRECHARGE_ALL, REFRESH, ACTIVATE, READ, READ_CAPTURE (one clock per beat), WRITE (one clock per beat, WRITE command on beat 0 and NOPs after), and RESPOND (close page only). The helper `after()` puts a WAIT phase of N clocks between a command and its successor, so each `WAIT_*` is exactly the NOP clocks on the pins.

From IDLE: a due refresh wins (PRECHARGE_ALL if any bank is open, then REFRESH); else a request goes to ACTIVATE (bank closed), PRECHARGE (row miss, open row only) or straight to the column command (row hit). Open row answers with the return to IDLE after the last beat. Close page goes on to PRECHARGE, waits `WAIT_RP`, and answers in RESPOND.

Read capture: with DRAM_CLK inverted and CAS latency two, the beat for a READ issued at edge N is on DQ during clock N+1+k and is captured at the edge ending it.

A close-page read walked by clock (nand2mario): 1 ACTIVATE, 2 and 3 wait, 4 READ, 5 capture, 6 PRECHARGE, 7 and 8 wait, 9 RESPOND with the data.

The refresh age counts clocks since the last REFRESH (the first init refresh starts it, or MODE REGISTER SET with `REFRESH_AGE_FROM_MODE`) and saturates. `REFRESH_DUE + WORST_TO_REFRESH <= REFRESH_DEADLINE` is checked at elaboration, where `WORST_TO_REFRESH = 6 + 2*WAIT_RP + WAIT_RCD + WAIT_WR + BURST_BEATS` is an upper bound on the clocks from a request accepted one clock before the refresh is due to the REFRESH. `REFRESH_DEADLINE` bounds the age the clock before a REFRESH, so the gap between two REFRESH commands can reach `REFRESH_DEADLINE + 1` clocks.

## Clocks and resets

One domain, `clk_i`. DRAM_CLK is the inverted `clk_i`, driven by the board top; the controller owns no crossing.

## Design choices

- One engine with wait parameters, against keeping two controllers. The two project controllers were the same command sequencer with different burst, row policy and NOP counts; parameterizing the counts keeps each project's existing contract test passing unchanged on the common block.
- Reads ignore byte enables, where the nand2doom controller rejected a read with any enable set: nand2mario drives enables on reads, and an ignored field cannot corrupt memory.
- Close page answers one clock after the precharge wait, to keep nand2mario's fixed 9/10 latency, rather than answering with the last beat.
- Close page precharges with A10 high (all banks), as nand2mario did; open row precharges one bank with A10 low.

Differences from the project controllers it replaced (#141, #142), the only ones the lockstep benches of PR #146 masked:

- nand2doom: `init_done_o` rises with MODE REGISTER SET, two clocks before nand2doom's; DQM is `11` until then, where nand2doom drove it from reset; reads with byte enables are served, not rejected (above). nand2doom has no `idle_o`.
- nand2mario: the address and bank are not held on NOP clocks; `response_data_o` on a write response is not defined (nand2mario repeated the last read, this block gives zero). The refresh age starts at MODE REGISTER SET in both, through `REFRESH_AGE_FROM_MODE`.

## Verification

DV plan: [../../dv/sdram/dv-plan.md](../../dv/sdram/dv-plan.md). FV plan: [../../fv/sdram/fv-plan.md](../../fv/sdram/fv-plan.md): command spacing, refresh deadline, one response within the latency table, and misaligned errors, proven in both configurations. Each project's own controller contract runs on this block in that project's configuration (`n2d_sdram_controller`, `n2m_sdram_ctrl`), alongside the common random-traffic bench (`n2a_sdram_ctrl_line`, `n2a_sdram_ctrl_word`). nand2doom's `n2d_memory_controller` and nand2mario's `n2m_sdram_backing` instantiate it on both boards.
