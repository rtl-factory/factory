# fpga DV plan

## Scope

The two board tops (`n2d_de10_lite`, `n2d_de2_115`) in simulation, and the
Quartus build and report gate of the script `quartus.py`, checked against the
[DE10-Lite board spec](../../fpga/de10_lite/board-spec.md) (which holds the
FPGA-R requirements both boards share) and the
[DE2-115 board spec](../../fpga/de2_115/board-spec.md). The blocks inside the
tops are verified by their own plans; physical programming and monitor
observation are separate, authorized work.

## Testbench

Board tops run in Verilator with their portable clock models and a Python
SDRAM BFM on the exported clock and resolved DQ pins, using the shared
burst-eight pin model for command and timing checks. No Quartus simulation
model is used; board simulation compiles warning-fatally and a
`SYNTHESIS`-defined Verilator compile is never FPGA evidence. Quartus report
gates are tested with sanitized real-report fixtures and negative controls.

### Evidence

A hardware PR records commands, tool version, exact commit, utilization,
worst timing, warning ownership and limitations; reports stay under the
worktree's `workdir/`. Physical programming and monitor observation are
separate, authorized work.

## Features

Spec section names the requirement in the
[board requirements](../../fpga/de10_lite/board-spec.md#requirements).
Level is the target's level in `src/nand2doom/cfg/targets.json` where the
test is a simulation target.

| ID | Spec section | Stimulus | Checker | Coverage point | Test | Level |
|---|---|---|---|---|---|---|
| FPGA-F01 | FPGA-R2..R4 (DE10-Lite) | Initialization, reset hold, UART Poke/Peek in all four banks, row crossing, masked writes, 195-clock refresh deadline. The first CAS-two beat appears 46 ns after the external READ edge (device edge n+1 plus 6 ns tAC; valid by n+2), later beats one clock apart, so a wrong phase fails readback. | Python SDRAM BFM with the shared burst-eight pin model; UART Peek readback | none defined | `n2d_de10_lite` (seed 59, 8 ms sim, 120 s wall) | L1 |
| FPGA-F02 | FPGA-R2..R5 (DE2-115) | The DE10-Lite checks over the JP5 UART pins with `DQM[1:0]`, plus: `DQM[3:2]` high on every command edge; `LEDR[17:4]`, `LEDG`, digits off, `VGA_BLANK_N` high, `VGA_SYNC_N` low; a Set-2 make code on the on-board PS/2 pins popped by a loaded program and read back by Halt/Peek; a poked palette entry and pixels reach the DAC next frame with `{c, c}` channels, black before output line 40, the first 16 scaled pixels exact, RGB never changing at a rising `VGA_CLK`. | Python SDRAM BFM with the shared burst-eight pin model; pin and DAC monitors | none defined | `n2d_de2_115` via `src/nand2doom/dv/fpga/n2d_de2_115_fixture.sv` (seed 59, 25 ms sim, 300 s wall) | L1 |
| FPGA-F03 | Subsystems | Owned by their [memory](../memory/dv-plan.md), [video](../video/dv-plan.md), [I/O](../io/dv-plan.md) and [platform](../platform/dv-plan.md) plans. | Owned by those plans | none defined | Cache, framebuffer, `n2d_sdram_controller`, `n2d_debug_memory`, integrated and C-runtime targets, common reset/CDC/UART/VGA | see those plans |
| FPGA-F04 | Pin and board selection | Confinement, exact pin coverage, duplicate pins, source exclusions, planning, failed runs, missing/stale artifacts. DE10-Lite resolves exactly the manifest top with unchanged standards; DE2-115 covers every top-level bit once, matches the nand2linux pins and standards, swaps only clock backend and top, selects Cyclone IV E and 2.5 V banks; `--plan --board de2_115` is tool- and write-free; unknown boards and bad overrides are rejected. | Expected pin sets and standards from the board config | none defined | Builder unit tests | host unit test (`src/nand2doom/scripts/tests/`), not a simulation target |
| FPGA-F05 | RAM and clock report gate | AUTO RAM allocations, both port geometries, role-dependent OLD_DATA and Single Clock, generated-clock master/source/target/waveform. Negative controls corrupt every RAM role, the SDRAM clock phase and pin, the DE2-115 `vga_dac_clock`, and each of the twelve SDRAM path reports (missing, empty, zero paths, violation, negative or inconsistent margin, wrong corner, endpoint or clock). | Report parser against sanitized real-report fixtures and negative controls | none defined | Report fixtures | host unit test (`src/nand2doom/scripts/tests/`), not a simulation target |
| FPGA-F06 | Warning gate | Every ordinary warning deleted and duplicated (including a missing set and a split duplicate) fails; same-count substitutions of constant pin/value/source, PLL output/hierarchy/file, advisory family/count and severity fail; source-line and staging-root changes pass; evidence keeps raw order and multiplicity. | Warning audit against sanitized fixtures and negative controls | none defined | Warning-audit fixtures, both boards | host unit test (`src/nand2doom/scripts/tests/`), not a simulation target |
| FPGA-F07 | AN447 | Changed child pin, pad, standard, source or severity; removed, duplicated or detached children; changed headline fail. The exact 20- and 21-pin sets pass; `RESET_N` Schmitt trigger passes in mixed case and extra spacing; another standard fails. | AN447 parser against report fixtures | none defined | Report fixtures | host unit test (`src/nand2doom/scripts/tests/`), not a simulation target |
| FPGA-F08 | Generated discovery | All four families, absence, duplicates, family limits, unknown/missing files, wrong provenance, mismatched primitive or `SUBDESIGN`. | Generated-file discovery against report fixtures | none defined | Report fixtures | host unit test (`src/nand2doom/scripts/tests/`), not a simulation target |
| FPGA-F09 | Elaboration check | Runs only `quartus_map --analysis_and_elaboration` per board with the board timeout under `workdir/`; passes with zero warnings and no fitter evidence; rejects error lines (module-scope `if` without `generate`; elaboration `$error` in a generate block), failed status, unreviewed or truncation warnings, bad connectivity, missing reports, fitted-only diagnostics. Without Quartus it exits 77 `SKIPPED`. | Elaboration report gate | none defined | Builder tests, `--elaborate-only` ([contract](../../fpga/de10_lite/board-spec.md#tool-compatibility-and-warnings)) | L1 (`check:doom:quartus-elaborate:de10-lite`, `check:doom:quartus-elaborate:de2-115`); builder tests on the host |
| FPGA-F10 | FPGA-R6 | Map RAM summaries, fitter resources and pins, all-corner STA, setup/hold paths, unconstrained paths, ignored constraints, assembly artifacts. | The Quartus report gate on the real reports | none defined | Full Quartus build | not in a level: needs Quartus, run per hardware pull request |

## Coverage

No functional coverage points or code coverage numbers were recorded when the
plan came over from nand2anything, so every coverage point above is "none
defined". The report gates keep a negative control for every rejected
condition.

## Status

No open gaps with an issue are recorded for this plan. Physical programming
and monitor observation stay separate, authorized work ([evidence](#evidence)).
