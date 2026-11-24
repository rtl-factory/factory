# Board build: spec

How the board build meets its [requirements](prd.md). Code: `src/nand2doom/scripts/board/`; tests in its `tests/`. The board facts, the constraints and what each reviewed warning means are in the [DE10-Lite board spec](../../fpga/de10_lite/board-spec.md) (which owns the contracts both boards share, including the [report gate](../../fpga/de10_lite/board-spec.md#report-gate)) and the [DE2-115 board spec](../../fpga/de2_115/board-spec.md); this page says how the scripts apply them.

## Parts

| Part | Role |
|---|---|
| `quartus.py` | The build: loads and validates the board config, stages the Quartus project, runs the compile and a timing audit, gates the reports and writes `evidence.json`. `load_config`, `stage`, `run`, `build_lock` and `confined` are also used by `n2d_timing.py` and the target tests |
| `n2d_timing.py` | Timing analysis at requested system clocks; analysis only, it never gates or programs a board |

## Config

The board config is `src/nand2doom/cfg/n2d_quartus.json`. Its top level is the DE10-Lite, and names the top library and its `intel` flavor, which `load_config` resolves into the sources and headers ([Bazel spec, Libraries](../../../../tools/bazel/spec.md#libraries)); each entry of `boards` overrides only board facts (project, top, family, device, settings, constraints, pins, reviewed warnings, RAM requirements, timeout, reference port, clock module, forwarded clocks, I/O standards) and swaps board files through `source_replacements`, which must name shared sources. `load_config` refuses, before anything is staged: an unknown board or override key; a name that is not a plain identifier; a timeout that is not a positive integer; a clock other than 25 MHz; a source, header, settings or SDC path that is absolute, has `..`, has unusual characters or is not a file in the checkout; a duplicate physical pin or a malformed pin; an I/O standard for an unknown port; inconsistent clock wiring; and a reviewed-warning, unused-object or generated-IP-limit contract of the wrong shape.

## Build

```text
python3 src/nand2doom/scripts/board/quartus.py [--board de10_lite|de2_115] [--config PATH]
        [--quartus PATH] [--output DIR] [--plan] [--elaborate-only]
```

1. Print the board, top, device, clock, source count, timeout and output (default `workdir/quartus/<project>`, or `<project>-elaborate`). `--plan` stops here.
2. Find `quartus_sh` (`--quartus`, else `QUARTUS_SH`, else `PATH`); missing is an error.
3. Take the build lock (`<output>.lock`, created exclusively; a second build of the same output fails), delete any earlier output, and stage: every source, header, settings fragment and SDC copied under the output, a QSF (family, device, top, `SYNTHESIS=1`, `DISABLE_ASSERTIONS=1`, the settings fragment, the SDC, every source, every pin and its I/O standard), the QPF, `inputs.json` (the resolved config and the SHA-256 of every staged input) and `audit.tcl`.
4. Run `quartus_sh --flow compile`, then `quartus_sta -t audit.tcl` (unconstrained paths, `check_timing`, ignored constraints, the worst setup and hold paths, the clock list, and the SDRAM read and write paths at every corner). Each run has the board's timeout, runs in its own process group (killed whole on timeout) with `TBB_MALLOC_DISABLE_REPLACEMENT=1`, logs to `compile.log` or `audit.log`, and records its child's identity in `<log>.owner.json`.
5. Gate the reports (below) and write `evidence.json`; print the reviewed-warning summary, the minimum slack and the evidence path.

`--elaborate-only` runs only `quartus_map --analysis_and_elaboration` into `elaborate.log`, requires the map summary to report success and no error lines, gates its warnings against the elaboration rules, and writes `evidence.json` with stage `analysis_and_elaboration`. Without Quartus it prints `SKIPPED` and exits 77. When Quartus fails, the error names the Quartus error lines.

## Report gate

`verify_reports` fails closed on: a missing, empty or linked Quartus artifact (map, fit, STA, assembler reports and the SOF) or audit file; any corner with negative slack or non-zero TNS, an ambiguous summary, or a missing required check (setup, hold and minimum pulse width of the PLL system clock, recovery and removal of `jtag_tck`, which holds the only asynchronous resets, setup and hold of each forwarded clock, the reference minimum pulse width, at every configured corner); fitted clocks or relationships that differ from the config; a missing or failing SDRAM read or write path at any corner, or one with the wrong endpoints; `check_timing` categories other than exactly one `no_clock` (the PLL lock synchronizer), one `virtual_clock` and one `no_output_delay` per forwarded clock pin; any ignored SDC constraint; fitted resources or RAM geometry that differ from the config; any illegal or unconstrained clock or port; a clock list that differs from the fitted clocks; and any warning in the compile or audit logs that is not a reviewed identity at its exact count. The reviewed identities are the board spec's [report gate](../../fpga/de10_lite/board-spec.md#report-gate).

`evidence.json` holds the warning audit and summary, minimum slack, clock periods, every timing entry, the `check_timing` counts, the clock relationships, the SDRAM timing, resources and every reviewed warning line in log order.

## Timing analysis

```text
python3 src/nand2doom/scripts/board/n2d_timing.py [--board B] [--config PATH] [--quartus PATH]
        [--mhz 25 50 ...] [--paths 20] [--sta-only [--build-dir DIR]] [--plan]
```

- **Outputs.** One directory per requested clock, `workdir/timing/<project>_<mhz>mhz`, or with `--sta-only --build-dir DIR` and exactly one `--mhz`, that compiled project below `workdir/`.
- **What-if clock.** At 25 MHz the shipped SDC is staged unchanged. Any other value (1 to 200) appends, after the SDC's single `derive_pll_clocks`, a `create_generated_clock` that redefines the PLL system clock as `mhz/50` of its reference, in the staged copy only. An SDC without exactly one `derive_pll_clocks` is refused.
- **Run.** Under the build's lock: stage and compile (unless `--sta-only`, which needs an existing `db/`), then run `timing.tcl` with `quartus_sta`, which writes `timing.tsv`: per corner the system clock and its period, `get_clock_fmax_info` rows, worst slack and TNS per clock for setup, hold, recovery and removal, and the `--paths` (1 to 200) worst setup paths over all clocks and over system-clock-only paths.
- **Checks.** The parser refuses an unknown record, an incomplete set, or a system clock that differs across corners; the result is refused when the analyzed period is not the requested clock (within 0.01 MHz), so a silently ignored override cannot be labelled as a faster clock.
- **Classes.** Each path is labelled by the first matching rule (board status pins, SDRAM, multiply, divide, I-cache, D-cache, memory arbiter, core, framebuffer and VGA, UART, PS/2), each with the part of the design that owns it; otherwise "Unclassified". The rules were checked against the 360 worst setup paths of a DE2-115 fit at 25 MHz (every path classified; the status LED outputs then fell under SDRAM and core, which the board-status rule now catches), and the unit test keeps those real endpoints classified.
- **Report.** `timing.json` (the parsed result and the clock) and `timing.md` (Fmax per corner, slack and TNS per check, and the worst paths at the worst setup corner, with hierarchy pipes escaped in code cells), printed after each run.

## Errors and exit

Both scripts print `error: <message>` and exit 1 on a config, file, Quartus-exit, timeout or gate failure, and exit 0 on success; `quartus.py --elaborate-only` exits 77 when Quartus is absent. Every output path must be below `workdir/` with no link component.

## Tests

```text
python3 -m pytest src/nand2doom/scripts/board
```

The tests run on fixture reports and fake Quartus runs; none needs Quartus. The vendor-stub lint test needs Verilator, and the nand2linux pin comparison needs a nand2linux checkout; each is skipped without it. Two timing tests also check that the board spec's timing tables keep their shape.

## Origin

Ported from the captain's nand2anything repository with the design (issue #78), where these were the FPGA build tools; grouped into this folder, with this PRD and spec, in issue #81.
