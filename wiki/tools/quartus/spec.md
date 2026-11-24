# Quartus build: spec

The Quartus build is `tools/quartus/quartus.py`, with the manifest resolution in `manifest.py`, staging and process control in `build.py`, the report gate in `gate.py` and the image identity in `image.py`. It needs Python 3.10+ and, for a build, Quartus Prime Lite at the version the [toolchain](../toolchain/README.md) pins. [Requirements](prd.md).

It is a tool, not a script ([repository layout](../../bkm/layout.md#repository-layout)): every design fact (top, sources, pins it uses, clocks, RAMs, paths, reviewed warnings) comes from the project's manifest, and every board fact from the board's pin map.

## Command

```
python3 tools/quartus/quartus.py --manifest src/<project>/cfg/<manifest>.json [--board B] [--profile P]
    [--quartus PATH] [--output DIR] [--plan | --elaborate-only | --verify-only]
```

| Option | Effect |
|---|---|
| `--manifest` | The project manifest, relative to the checkout |
| `--board` | A board the manifest overrides (default: the manifest's own `board`) |
| `--profile` | A profile that extends the manifest (an image variant of the same board) |
| `--quartus` | The `quartus_sh` executable; else `QUARTUS_SH`, else `PATH`. `quartus_map` and `quartus_sta` are taken from beside it |
| `--output` | The build directory, below `workdir/` (default `workdir/quartus/<project>/`, `-elaborate` appended for `--elaborate-only`) |
| `--plan` | Resolve and validate, print the summary line, write nothing, need no Quartus |
| `--elaborate-only` | Stage and run analysis and elaboration only, then the front-end gate; exit 77 (skipped, never a pass) without Quartus |
| `--verify-only` | Rerun the full report gate on an existing build and rebind its SOF |

Exit 0 is a pass, 1 a failure (the reason on stderr), 77 an explicit skip. The image targets `//src/<project>:<board>_image` run this command through `tools/bazel/quartus_image.py` ([Bazel spec, Board images](../bazel/spec.md#board-images)).

## Board pin maps

Each board has one pin map, `src/common/fpga/<board>/pins.json`, next to its clock module:

| Key | Meaning |
|---|---|
| `board`, `family`, `device` | The board name and its exact FPGA |
| `reference_port`, `reference_hz` | The oscillator pin and its frequency |
| `clock_module` | The board's PLL wrapper module (`n2a_<board>_clock`) |
| `default_io_standard` | The I/O standard of every pin not listed in `io_standards` |
| `io_standards` | Per pin or per bus: a standard, or `null` when the project's settings fragment sets it (the DE10-Lite `KEY` Schmitt trigger) |
| `pins` | Every board pin a project may use, by its board name: a location, or for a bus a list of locations by bit |

A pin map names pins after the board's manual (`ARDUINO_IO`, `GPIO`, `KEY`); a project that calls a pin otherwise wires its port to it in its manifest.

## Manifests

A manifest is `src/<project>/cfg/*_quartus.json`. Required keys: `board`, `project`, `top`, `clock_hz`, `timeout_seconds`, `timing_corners`, `libraries`, `settings`, `constraints`, `pins`, `forwarded_clocks`, `jtag_clock`, `ram_requirements`, `reviewed_warnings`. Optional: `flavors`, `parameters`, `verilog_macros`, `timing_paths`, `require_clean_source`, `boards`. Any other key is refused.

| Key | Meaning |
|---|---|
| `libraries`, `flavors` | The top library and backend flavor, resolved into ordered sources and headers ([Bazel spec, Libraries](../bazel/spec.md#libraries)) |
| `settings`, `constraints` | The project's Quartus settings fragment and SDC |
| `clock_hz` | The system clock; it must divide the board's reference clock |
| `parameters`, `verilog_macros` | Top-level parameters (upper-case names, non-negative integers) and extra macros; `SYNTHESIS=1` is always set |
| `pins` | The top's ports: `X`, `X[3]` or `X[7:0]` take the board pin of the same name, `PORT=PIN` wires one port bit to a board pin of another name (`RESET_N=KEY[0]`) |
| `forwarded_clocks` | Each clock the board drives out on a pin (the inverted system clock), by name: `{"sdram_clock": "DRAM_CLK"}` |
| `jtag_clock` | `true` when the design has a virtual JTAG hub: the gate then requires the 100 ns `jtag_tck` clock and its timing rows |
| `timing_paths` | Named path groups the audit reports and the gate checks (below) |
| `ram_requirements` | Every fitted RAM (below) |
| `reviewed_warnings` | The warning contract (below) |
| `require_clean_source` | `true` refuses a full compile when the checkout differs from its commit |
| `boards` | Overrides for other boards (below) |

**Board overrides.** `boards.<board>` restates only what that board changes: any manifest key except `board`, `boards`, `libraries`, `flavors`, `clock_hz` and `timing_corners`, plus `source_replacements` (a source of the base closure to the board's file). A dictionary value merges one level into the base (`reviewed_warnings`, `forwarded_clocks`, `timing_paths`, `parameters`); any other value replaces the base's.

**Profiles.** A profile is a variant image of one board: `project` and only `parameters`, `ram_requirements` and `reviewed_warnings`, plus an optional `board` it requires. Dictionaries merge, lists extend and other values replace; nothing else of the board can change.

Resolution refuses, before anything is written: a pin the board does not have, a port or physical pin used twice, a top without the board's reference pin, a source outside the checkout or missing, a top without exactly one `<top>.sv`, a malformed path group, RAM requirement or warning contract, and an electrical advisory whose standard disagrees with the pin's assignment.

## Stage

Under the build lock (`<output>.lock`, created exclusively and removed on exit), a full compile or elaboration deletes the output directory and copies every source, header, settings fragment and SDC below it, then writes:

- `<project>.qsf`: `FAMILY`, `DEVICE`, `TOP_LEVEL_ENTITY`, the `VERILOG_MACRO`s, `source` of the settings fragment, `SDC_FILE`, each `SYSTEMVERILOG_FILE`, each `set_parameter`, then per pin its location and, unless `null`, its `IO_STANDARD`;
- `<project>.qpf`;
- `inputs.json`: the resolved config and the SHA-256 of every staged input;
- `audit.tcl`: the timing audit.

Each Quartus tool runs in its own process session with the manifest's `timeout_seconds`, writing its log beside the project and its child's identity to `<log>.owner.json`; on a timeout the whole process group is killed. A full compile records `source.json` (commit, dirty flag, Quartus version) first.

## Timing audit

`audit.tcl` runs in `quartus_sta` after the compile: the unconstrained-path report, `check_timing`, the ignored-constraints report, ten worst setup and hold paths, every clock and its period (`clocks.tsv`), and at every operating condition the clock Fmax summary (`fmax_<corner>.rpt`) and, for each path group, its worst setup and hold path (`<group>_<corner>_<check>.rpt`, `<corner>` like `slow_85`). A path group starts at its ports (`from_ports`) or ends at a clock (`to_clock`).

## Report gate

A full compile passes only when every check holds:

1. The staged inputs are those of this config and unchanged since compilation.
2. The map, fit, timing, assembler reports and the SOF exist and are not empty, and the audit reports exist.
3. Every timing-summary row at every corner has non-negative slack and zero TNS; the rows cover setup, hold and pulse width of the system clock (the one clock matching `u_clock|u_pll|…|clk[0]`), setup and hold of each forwarded clock, the reference clock's pulse width and, when the manifest sets `jtag_clock`, setup, hold, recovery, removal and pulse width of the JTAG clock, whose scan domain holds the only asynchronous resets outside the reset synchronizers.
4. The fitted clocks are exactly the reference, the system clock (reference divided down, master `reference`), each forwarded clock (inverted system clock on its pin) and, when the manifest sets `jtag_clock`, `jtag_tck` (100 ns on `altera_reserved_tck`), and `clocks.tsv` agrees.
5. `check_timing` finds exactly one `no_clock` (the PLL lock synchronizer), one `virtual_clock` and one `no_output_delay` per forwarded clock pin, and nothing else; no constraint is ignored; no clock, port or path is unconstrained.
6. Exactly one PLL; every fitted RAM matches one requirement (by `name` substring, or `prefix[i].array` for an indexed array): its mode, single clock, port A and B depth and width, M9K count and "Old data" read-during-write on the ports it uses; the requirements account for every fitted M9K.
7. The system clock's restricted Fmax is at least `clock_hz` at every corner.
8. Each path group's worst setup and hold path at every corner has non-negative slack, the group's launch and latch clocks (`system` names the system clock), and endpoints one of its `{from, to}` regular expressions match in full.
9. Every warning line of the compile and audit logs matches exactly one reviewed rule, and each rule's count is within its bounds (a line two rules match is refused as ambiguous).

The warning contract (`reviewed_warnings`):

| Key | Rule |
|---|---|
| `unused_objects` | `[{source, object}]`: one 10036 "assigned a value but never read" each, bound to that file |
| `connectivity` | `{hierarchy, unused_outputs}`: the map report's port-connectivity table of that hierarchy holds exactly these unconnected outputs, with one 12241 aggregate |
| `logiclock_count` | The exact count of the 292013 LogicLock licence notice |
| `constant_outputs` | `{pin: VCC or GND}`: one 13410 each, with one 13024 summary |
| `pll_output` | The forwarded clock pin of the one 15064 non-dedicated PLL routing warning |
| `electrical_pins` | `{pin: standard}`: the one 169177 AN 447 advisory, whose 169178 details in the fit report are exactly these pins, pads and standards |
| `generated_ip_limits` | `{family: count}`: at most that many 12125 warnings per generated `db/<family>_*.tdf`, each listed once in the map report as an auto-generated megafunction whose file defines that entity |
| `patterns` | A string: a regular expression any number of lines may match; `{pattern, count}`: exactly `count` lines |

On a pass the gate writes `evidence.json` (schema `quartus-evidence/1`): identity, timing rows and minimum slack, clocks, Fmax, path groups, `check_timing` counts, resources and RAM summary, the connectivity and electrical findings, and every reviewed warning with a per-code and per-category summary. `gate.check_evidence` rechecks every one of these obligations from the evidence alone.

Elaboration passes when the map summary says successful, the log has no error and every warning matches a rule (counts are upper bounds only, since elaboration sees a subset).

## Image identity

After a passing full compile (or `--verify-only`), `image.write_identity` copies `inputs.json` and `evidence.json` beside the SOF as `<project>.inputs.json` and `<project>.evidence.json` and writes the receipt `<project>.json` (schema `quartus-sof/1`): project, board, device, `source_sha`, `source_dirty`, `quartus_version` and the SHA-256 of the SOF and of the two files. Transfer the four files together.

`image.verify_image(sof, config)` is what a board flow calls before programming. It refuses a missing or linked receipt, any malformed field, another project, board or device, a hash mismatch or missing file, transferred inputs whose config differs from the selected manifest's (rebuild the image) or whose source hashes are malformed, and evidence that `check_evidence` refuses.

## Users

| User | How |
|---|---|
| nand2doom | `src/nand2doom/cfg/n2d_quartus.json` (DE10-Lite, DE2-115 override); `n2d_timing.py` reuses the staging and process control |
| nand2mario | `src/nand2mario/cfg/n2m_quartus.json`, `n2m_de2_115_quartus.json` and the library profile; `n2m_board.py compile` runs the tool and `program` calls `verify_image` |
| Bazel | `quartus_image()` targets of both projects |

## Tests

`tools/quartus/tests/` builds a synthetic project on two synthetic boards (`fixture.py`) and checks resolution, staging, the process controls, every gate obligation alone, evidence tampering, the image identity and the command line, all without Quartus. Each project's tests check its manifests: pins against its tops, closures against its simulation targets, and the generated `.qsf` against committed copies of what its own script generated before the move.
