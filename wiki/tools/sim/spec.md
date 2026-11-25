# Simulation runner: spec

How the runner works, against its [requirements](prd.md). The tool is `tools/sim/sim.py`; it needs cocotb from the toolchain (`tools/toolchain/`) and runs the pinned Verilator ([toolchain spec, Install (Verilator)](../toolchain/spec.md#install-verilator)) whatever is on `PATH`.

## Targets

Each project lists its targets in `src/<project>/cfg/targets.json`:

| Field | Meaning |
|---|---|
| `kind` | `cocotb` (default) or `native`: a self-checking SystemVerilog top built with `verilator --binary` and run as is; exit status is the verdict |
| `libraries`, `flavors` | The libraries the model compiles (default: the library of `top`) and the variants to prefer; resolved into the ordered sources ([Bazel spec, Libraries](../bazel/spec.md#libraries)) |
| `top` | HDL top level |
| `test_module`, `testcase` | cocotb test (Python module path from the repository root); cocotb targets only |
| `defines`, `plusargs` | Native targets only: `-D` defines at build time; plusargs at run time |
| `regression_action` | Native targets only: `test` (default) builds and runs; `compile` only builds, for a harness whose inputs a script prepares (nand2doom's performance harness) |
| `trace_fst` | Native targets only: build with `--trace-fst` so the harness can write an FST window |
| `default_seed` | Seed used unless `--seed` is given |
| `clock_hz`, `max_cycles` | Passed to the test as `DV_CLOCK_HZ`, `DV_MAX_CYCLES` |
| `timeout_seconds` | Time budget of the test run (compile gets a fixed extra allowance) |
| `parameters`, `public_depth` | Top-level parameter overrides; Verilator public-signal depth |
| `level` | Regression level: `L0`, `L1`, `L2` ([CI levels](../../bkm/flow.md#ci-levels)), or `manual` (never run by `--all`) |
| `wrapper` | Optional: module and arguments that prepare the program image and then run the target with `--direct`; `{output}` and `{workdir}` are filled in |
| `expected_fatal` | Optional: makes it a negative test; `file`, `hierarchy`, `message` (or `any_of` a list of them) must appear in the log |

The file's `targets` object holds the targets; its optional `lint` object belongs to the RTL lint, not the simulator ([lint spec](../lint/spec.md#verilator-lint)).

Tests that check a registry read it through `sim.target_views(root)`: every target as a read-only object, with the fields above that a target leaves out filled with their defaults (`kind` `cocotb`, empty `parameters` and `defines`, no `test_module`, `testcase`, `default_seed` or `max_cycles`), lists as tuples and dictionaries as sorted item tuples. Project target tests use it instead of their own copy.

## Command line

```
python tools/sim/sim.py list
python tools/sim/sim.py test <target>... [--seed N] [--waves] [--coverage]
python tools/sim/sim.py test --project nand2mario [--level L1]
python tools/sim/sim.py test --all
```

`--level L` alone runs every non-manual target at level `L` or below; with `--project` it narrows that project. `--workdir DIR` (default `workdir/sim/`, ignored by git) sets the scratch directory; keep it under the repository's `workdir/`, because wrappers refuse outputs anywhere else. A target runs in a child process under its time budget and writes `DIR/cocotb/<target>/{build,test}/` (wrapped targets: `DIR/wrapped/<target>/`). Verilator flags: `--timing --assert --Wall`, compiled from the repository root so ``` `include ``` paths are root-relative.

Seeds: a wrapped target gets its seed, `--waves` and `--coverage` through the `SIM_SEED`, `SIM_WAVES` and `SIM_COVERAGE` environment variables, which the wrapper's own `test --direct` call reads, so the printed seed is the one the test used. `test` also reads them when `--seed` or `--waves` is absent, which is how Bazel's `--test_env=SIM_SEED=<n>` reaches a wrapped target. Native targets take no seed and ignore `--waves` (a `trace_fst` harness writes its own FST window when its script asks); their result line shows `seed=-`. A target that runs past its budget is stopped with its whole process group.

## Bazel commands

Bazel ([spec](../bazel/spec.md)) runs every target through two commands that split a run into its cacheable halves; agents run targets through Bazel, not these.

```
python tools/sim/sim.py build-model --spec <target>.spec.json --out <dir> [--waves] [--coverage]
python tools/sim/sim.py run-model --spec <target>.spec.json --model <dir> [--seed N] [--waves] [--coverage]
```

A spec file is `{"name": <target>, "target": <registry entry>}`. `build-model` builds the model into `<dir>` (log beside it as `<dir>.log`) and keeps only its executable. `run-model` tests the target against that model with the verdicts above, writing into `$TEST_UNDECLARED_OUTPUTS_DIR` (else `--workdir`); `SIM_SEED`, `SIM_WAVES` and `SIM_COVERAGE` in the environment set the seed, waves and coverage when the flags are absent. `SIM_ROOT` replaces the repository root (Bazel sets it to the sandbox), and `SIM_PATH_PREPEND` names files or directories whose directories go first on `PATH` (Bazel's Verilator).

## Coverage

Every run reports what coverage its test folder (`DIR/cocotb/<target>/test/`, or the Bazel test's outputs) holds, on lines starting `coverage` under its result line, and writes the same lines to `coverage.txt` there. `tools/sim/sim_coverage.py` does both halves.

- **Code coverage.** `--coverage` (Bazel: `--//tools/bazel:coverage`) builds the model with Verilator's `--coverage` (line, toggle, branch and expression points) and runs it with `+verilator+coverage+file+<test>/coverage.dat`, for cocotb and native targets alike. The runner counts the points hit per type from `coverage.dat` copies it to `coverage/coverage.dat` with every source path made relative to the repository root (Verilator records the absolute path it resolved at build time, which for a model from Bazel's cache is another checkout's), and runs `verilator_coverage` on that copy from the root to write `coverage/coverage.info` (lcov, for `genhtml` or an editor) and `coverage/annotated/` (each source with too-rarely-hit lines marked `%00`). The numbers cover every module in the model, the bench's fixture included, so a DV plan names which modules its number is about. A run asked for code coverage that leaves no `coverage.dat` says so.
- **Functional coverage.** A cocotb test declares its coverage points with [cocotb-coverage](https://github.com/mciepluc/cocotb-coverage) (`CoverPoint`, `CoverCross`, with `ign_bins` for a bin the spec makes unreachable, cited in the plan) on a sampling function it calls once each checked behaviour passed, and calls `write_functional()` from `tools.sim.sim_coverage` at its end. That saves the database as `functional_coverage.yml` in the folder `SIM_COVERAGE_DIR` names (the runner sets it to the test folder) and logs every bin; the runner reports each top-level covergroup's bins hit over bins. Name groups `<block>.<feature>.<point>`, so a block is one top-level group. It needs no flag: it costs nothing a test does not already do.

The sample target `n2a_uart_wire` shows both: `bazel test //src/common:n2a_uart_wire --//tools/bazel:coverage` prints

```
    coverage code: branch 96.4% (54/56), expr 80.0% (24/30), line 94.1% (32/34), toggle 96.4% (318/330)
    coverage functional: uart_wire 100.0% (33/33)
```

and its holes are explained in its [DV plan](../../src/common/dv/uart/dv-plan.md#coverage).

## Origin

Replaces, for this factory, the larger harness in the captain's nand2anything repository (`tools/build/`); nand2mario's targets, wrappers and negative-test oracles were carried over from it (issue #67).
