# SDRAM DV plan

This plan covers two blocks: the device model `n2a_sdram_model` and the controller `n2a_sdram_ctrl` ([MAS](../../rtl/sdram/mas.md)).

## Scope

`n2a_sdram_model` (`src/common/dv/sdram/n2a_sdram_model.sv`) is the one cycle-level model of the x16 IS42S16320D that every project's SDRAM benches use: nand2doom's `n2d_sdram_system` (burst eight, row-XOR-bank map, no fill) and nand2mario's controller, backing, system and board-top fixtures (burst one, bank-row-column map, seeded fill, eight initial refreshes). This plan verifies the model itself; each controller is verified against it by its project's benches. It replaces `n2d_sdram_device_model` and `n2m_sdram_model` (#139, part of the common SDRAM plan in #136).

## Testbench

`n2a_sdram_model_fixture` instantiates the model with `INIT_REFRESHES` 2 and `POWERUP_CLOCKS` 8 so a test reaches the idle device in a few clocks. `test_sdram_model.py` drives the pins one command per clock from the falling edge and samples DQ after each rising edge, so a READ presented in clock N returns beat k after edge N+1+k. The positive test keeps a shadow from the datasheet behaviour, not from the model: preloaded bytes, then random masked writes read back against the old contents.

| Parameter | Meaning |
| --- | --- |
| `BURST_BEATS` | 1 or 8; sets the mode register value the model requires (`0x020`, `0x023`) and the burst length |
| `ROW_XOR_BANK` | byte-address map of the preload port: 0 bank `[25:24]`, row `[23:11]`; 1 row `[25:13]`, bank `[12:11]` XOR `[14:13]`; column `[10:1]` in both |
| `INIT_REFRESHES` | refreshes required before the mode register set |
| `POWERUP_CLOCKS` | NOP clocks required after CKE before the first command |
| `REFRESH_GAP_MAX` | largest gap between refreshes once the mode is set (195) |
| `FILL`, `FILL_SEED` | seed nonzero garbage at time zero |

## Features

| ID | Spec section | Stimulus | Checker | Coverage point | Test | Level |
|---|---|---|---|---|---|---|
| M1 | data, masks, burst order | preload 64 bytes; random masked writes | shadow compare | both maps, both burst lengths | `n2a_sdram_model_bl1`, `n2a_sdram_model_bl8` | L0 |
| M2 | DQ ownership | DQ driven on NOP; WRITE one idle clock after READ | fatal message | `dq_outside_write`, `dq_turnaround` | `n2a_sdram_model_*_fail` | L0 |
| M3 | power-up and init | command in power-up; REFRESH first; ACTIVATE or column before MRS; MRS with too few refreshes, right after REFRESH, with an open row, wrong value | fatal message | one target per check | `n2a_sdram_model_*_fail` | L0 |
| M4 | command spacing | ACTIVATE to an open bank, after REFRESH (tRFC), after MRS (tMRD); PRECHARGE before tRAS, before tWR (after a single beat and after the last of eight); REFRESH back to back, with an open row | fatal message | one target per check | `n2a_sdram_model_*_fail` | L0 |
| M5 | column commands | no open row, auto-precharge, masked READ, WRITE without DQ | fatal message | one target per check | `n2a_sdram_model_*_fail` | L0 |
| M6 | bursts | command inside a read or write burst, undriven write beat, unaligned start | fatal message | burst eight only | `n2a_sdram_model_bl8_*_fail` | L0 |
| M7 | refresh interval | 200 idle clocks after MRS | fatal message | `refresh_interval` | `n2a_sdram_model_refresh_interval_fail` | L0 |

## Coverage

Every fatal check that a legal-format command stream can reach has a negative target. Seven have none, each for a reason:

- tRCD, tRP (ACTIVATE after PRECHARGE), refresh tRP and READ to PRECHARGE are one clock, which any two commands to the same bank already meet at a 40 ns clock.
- tRC (two clocks) is implied by tRAS plus tRP.
- REFRESH before the initial PRECHARGE ALL is caught first by the first-command check.
- The `BURST_BEATS` parameter check fires at elaboration, which a target cannot reach without failing its build.

tWR is checked per bank, on a PRECHARGE of a bank that is open, from that bank's last write beat, as the datasheet states it. The old nand2doom model checked it on every PRECHARGE against the last write to any bank; that was stricter than the device and is dropped.

## Controller

`n2a_sdram_ctrl` is checked against the model and against each project's own contract test, with no edits to the project tests beyond one documented difference.

| ID | MAS section | Stimulus | Checker | Test | Level |
|---|---|---|---|---|---|
| C1 | Latency, row policy | 600 random reads and writes over three rows of every bank, 5% misaligned, random gaps | latency table by row state (taken from the command pins), byte shadow, error with no ACTIVATE on misaligned, close-page idle timing, at least 18 refreshes; the model checks every pin rule | `n2a_sdram_ctrl_line` (nand2doom configuration), `n2a_sdram_ctrl_word` (nand2mario configuration) | L0 |
| C2 | nand2doom configuration | nand2doom's `sdram_pin_timing_contract` on the bare controller | that test's pin model, init order, latency and DQM checks; a read with byte enables must answer without error here (the project controller rejects it) | `n2a_sdram_ctrl_n2d_contract` | L0 |
| C3 | nand2mario configuration | nand2mario's `sdram_ctrl_contract` through `n2a_sdram_ctrl_n2m_fixture`, which gives the controller the project fixture's port names | that test's init clock numbers, latencies, refresh cadence and shadow | `n2a_sdram_ctrl_n2m_contract` | L0 |
| C4 | Lockstep with each project controller | 3000 random requests over three rows of every bank (3% misaligned in the line configuration), timed by the project controller's ready, random DQ input every clock, both controllers side by side in `n2a_sdram_ctrl_lockstep_fixture` | every pin, ready, idle and response identical each clock, apart from the differences the MAS lists, which are masked in the test and nowhere else | `n2a_sdram_ctrl_lockstep_line` (against `n2d_sdram_controller`), `n2a_sdram_ctrl_lockstep_word` (against `n2m_sdram_ctrl`) | L0 |

In-RTL assertions: DQ is driven only in WRITE, ACTIVATE only to a closed bank, REFRESH only with every bank closed, close page leaves no bank open in IDLE, the refresh deadline holds, and a stalled request is stable.

## Status

The project controllers' own benches cover the model on their real command streams. C2 and C3 show the common controller is a drop-in for both project controllers, and C4 that it matches them clock for clock; stages 3 and 4 (#141, #142) switch the projects over.
