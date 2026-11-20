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
| `default_seed` | Seed used unless `--seed` is given |
| `clock_hz`, `max_cycles` | Passed to the test as `DV_CLOCK_HZ`, `DV_MAX_CYCLES` |
| `timeout_seconds` | Time budget of the test run (compile gets a fixed extra allowance) |
| `parameters`, `public_depth` | Top-level parameter overrides; Verilator public-signal depth |
| `level` | Regression level: `L0`, `L1`, `L2` (`ci-levels.md`), or `manual` (never run by `--all`) |
| `wrapper` | Optional: module and arguments that prepare the program image and then run the target with `--direct`; `{output}` and `{workdir}` are filled in |
| `expected_fatal` | Optional: makes it a negative test; `file`, `hierarchy`, `message` (or `any_of` a list of them) must appear in the log |

## Command line

```
python tools/sim/sim.py list
python tools/sim/sim.py test <target>... [--seed N] [--waves]
python tools/sim/sim.py test --project nand2mario [--level L1]
python tools/sim/sim.py test --all
```

`--workdir DIR` (default `workdir/sim/`, ignored by git) sets the scratch directory. A target runs in a child process under its time budget and writes `DIR/cocotb/<target>/{build,test}/` (wrapped targets: `DIR/wrapped/<target>/`). Verilator flags: `--timing --assert --Wall`, compiled from the repository root so ``` `include ``` paths are root-relative.

## Origin

Replaces, for this factory, the larger harness in the captain's nand2anything repository (`tools/build/`); nand2mario's targets, wrappers and negative-test oracles were carried over from it (issue #67).
