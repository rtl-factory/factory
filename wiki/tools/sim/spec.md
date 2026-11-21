# Simulation runner: spec

How the runner works, against its [requirements](prd.md). The tool is `tools/sim/sim.py`; it needs Verilator and cocotb from the toolchain (`tools/toolchain/`) on `PATH`.

## Targets

Each project lists its targets in `src/<project>/cfg/targets.json`:

| Field | Meaning |
|---|---|
| `kind` | `cocotb` (default) or `native`: a self-checking SystemVerilog top built with `verilator --binary` and run as is; exit status is the verdict |
| `sources` | HDL files, relative to the repository root, compile order |
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

Tests that check a registry read it through `sim.target_views(root)`: every target as a read-only object, with the fields above that a target leaves out filled with their defaults (`kind` `cocotb`, empty `parameters` and `defines`, no `test_module`, `testcase`, `default_seed` or `max_cycles`), lists as tuples and dictionaries as sorted item tuples. Project target tests use it instead of their own copy.

## Command line

```
python tools/sim/sim.py list
python tools/sim/sim.py test <target>... [--seed N] [--waves]
python tools/sim/sim.py test --project nand2mario [--level L1]
python tools/sim/sim.py test --all
```

`--level L` alone runs every non-manual target at level `L` or below; with `--project` it narrows that project. `--workdir DIR` (default `workdir/sim/`, ignored by git) sets the scratch directory; keep it under the repository's `workdir/`, because wrappers refuse outputs anywhere else. A target runs in a child process under its time budget and writes `DIR/cocotb/<target>/{build,test}/` (wrapped targets: `DIR/wrapped/<target>/`). Verilator flags: `--timing --assert --Wall`, compiled from the repository root so ``` `include ``` paths are root-relative.

Seeds: a wrapped target gets its seed and `--waves` through the `SIM_SEED` and `SIM_WAVES` environment variables, which the wrapper's own `test --direct` call reads, so the printed seed is the one the test used. `test` also reads them when `--seed` or `--waves` is absent, which is how Bazel's `--test_env=SIM_SEED=<n>` reaches a wrapped target. Native targets take no seed and ignore `--waves` (a `trace_fst` harness writes its own FST window when its script asks); their result line shows `seed=-`. A target that runs past its budget is stopped with its whole process group.

## Bazel commands

Bazel ([spec](../bazel/spec.md)) runs every target through two commands that split a run into its cacheable halves; agents run targets through Bazel, not these.

```
python tools/sim/sim.py build-model --spec <target>.spec.json --out <dir> [--waves]
python tools/sim/sim.py run-model --spec <target>.spec.json --model <dir> [--seed N] [--waves]
```

A spec file is `{"name": <target>, "target": <registry entry>}`. `build-model` builds the model into `<dir>` (log beside it as `<dir>.log`) and keeps only its executable. `run-model` tests the target against that model with the verdicts above, writing into `$TEST_UNDECLARED_OUTPUTS_DIR` (else `--workdir`); `SIM_SEED` and `SIM_WAVES` in the environment set the seed and waves when the flags are absent. `SIM_ROOT` replaces the repository root (Bazel sets it to the sandbox), and `SIM_PATH_PREPEND` names files or directories whose directories go first on `PATH` (Bazel's Verilator).

## Origin

Replaces, for this factory, the larger harness in the captain's nand2anything repository (`tools/build/`); nand2mario's targets, wrappers and negative-test oracles were carried over from it (issue #67).
