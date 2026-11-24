# Board build: spec

How the board build meets its [requirements](prd.md). Code: `src/nand2doom/scripts/board/`; tests in its `tests/`. The board facts, the constraints and what each reviewed warning means are in the [DE10-Lite board spec](../../fpga/de10_lite/board-spec.md) (which owns the contracts both boards share, including the [report gate](../../fpga/de10_lite/board-spec.md#report-gate)) and the [DE2-115 board spec](../../fpga/de2_115/board-spec.md); this page says how the manifest and the timing script apply them.

## Parts

| Part | Role |
|---|---|
| `n2d_timing.py` | Timing analysis at requested system clocks; analysis only, it never gates or programs a board |

The build and report gate are the shared [Quartus build tool](../../../../tools/quartus/README.md) since issue #133: nand2doom keeps only its manifest and its tests here.

## Config

The manifest is `src/nand2doom/cfg/n2d_quartus.json` ([tool spec, Manifests](../../../../tools/quartus/spec.md#manifests)). Its base board is the DE10-Lite; `boards.de2_115` overrides only what the DE2-115 changes (project, top, timeout, settings, constraints, pins, forwarded clocks, RAM requirements, reviewed warnings) and swaps the board files through `source_replacements`. Pin locations and I/O standards come from each board's `src/common/fpga/<board>/pins.json`; the manifest wires `RESET_N` to `KEY[0]`, the DE10-Lite `UART_RXD`/`UART_TXD` to `ARDUINO_IO[0]`/`[1]`, its `PS2_CLK`/`PS2_DAT` to `GPIO[0]`/`[1]`, and the DE2-115 `UART_RX`/`UART_TX` to `GPIO[0]`/`[1]`. Its two path groups are `sdram_read` (from `DRAM_DQ[*]` to the controller's `regs_q.read_data`) and `sdram_write` (to the `sdram_clock` pin, from the memory controller, or from the reset synchronizer for `DRAM_CKE` and `DRAM_CS_N`, which follow reset).

## Build

```text
python3 tools/quartus/quartus.py --manifest src/nand2doom/cfg/n2d_quartus.json [--board de2_115]
        [--quartus PATH] [--plan | --elaborate-only | --verify-only]
bazel build //src/nand2doom:de10_lite_image      # or de2_115_image
```

The build stages `workdir/quartus/<project>/`, compiles, audits timing, applies the [report gate](../../../../tools/quartus/spec.md#report-gate) and binds the SOF to its commit ([image identity](../../../../tools/quartus/spec.md#image-identity)). What each reviewed warning means is the board spec's [report gate](../../fpga/de10_lite/board-spec.md#report-gate).

## Timing analysis

```text
python3 src/nand2doom/scripts/board/n2d_timing.py [--board B] [--config PATH] [--quartus PATH]
        [--mhz 25 50 ...] [--paths 20] [--sta-only [--build-dir DIR]] [--plan]
```

- **Outputs.** One directory per requested clock, `workdir/timing/<project>_<mhz>mhz`, or with `--sta-only --build-dir DIR` and exactly one `--mhz`, that compiled project below `workdir/`.
- **What-if clock.** At 25 MHz the shipped SDC is staged unchanged. Any other value (1 to 200) appends, after the SDC's single `derive_pll_clocks`, a `create_generated_clock` that redefines the PLL system clock as `mhz/50` of its reference, in the staged copy only. An SDC without exactly one `derive_pll_clocks` is refused.
- **Run.** Under the Quartus tool's build lock: stage and compile (unless `--sta-only`, which needs an existing `db/`), then run `timing.tcl` with `quartus_sta`, which writes `timing.tsv`: per corner the system clock and its period, `get_clock_fmax_info` rows, worst slack and TNS per clock for setup, hold, recovery and removal, and the `--paths` (1 to 200) worst setup paths over all clocks and over system-clock-only paths.
- **Checks.** The parser refuses an unknown record, an incomplete set, or a system clock that differs across corners; the result is refused when the analyzed period is not the requested clock (within 0.01 MHz), so a silently ignored override cannot be labelled as a faster clock.
- **Classes.** Each path is labelled by the first matching rule (board status pins, SDRAM, multiply, divide, I-cache, D-cache, memory arbiter, core, framebuffer and VGA, UART, PS/2), each with the part of the design that owns it; otherwise "Unclassified". The rules were checked against the 360 worst setup paths of a DE2-115 fit at 25 MHz (every path classified; the status LED outputs then fell under SDRAM and core, which the board-status rule now catches), and the unit test keeps those real endpoints classified.
- **Report.** `timing.json` (the parsed result and the clock) and `timing.md` (Fmax per corner, slack and TNS per check, and the worst paths at the worst setup corner, with hierarchy pipes escaped in code cells), printed after each run.

## Errors and exit

`n2d_timing.py` prints `error: <message>` and exits 1 on a config, file, Quartus-exit or timeout failure, and exits 0 on success. Every output path must be below `workdir/` with no link component.

## Tests

```text
python3 -m pytest src/nand2doom/scripts/board
```

The tests need no Quartus. `test_n2d_quartus.py` checks the manifest: every top port bit has one pin, the Quartus closure is the board simulation's with the Intel backends, the SDRAM path rules, and the generated `.qsf` of both boards against `tests/qsf/`, the files the former `quartus.py` generated. The vendor-stub lint test needs Verilator and is skipped without it. Two timing tests also check that the board spec's timing tables keep their shape.

## Origin

Ported from the captain's nand2anything repository with the design (issue #78), where these were the FPGA build tools; grouped into this folder, with this PRD and spec, in issue #81.
