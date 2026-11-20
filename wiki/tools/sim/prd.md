# Simulation runner: requirements

## Problem

Every project's cocotb tests must run the same way on every host, for a captain at a terminal, for a crewmate checking its change, and later for CI, with results that reproduce from a seed. Without one runner, each project grows its own scripts and each test is run differently.

## Requirements

1. Discover every simulation target of every project from the project's target file, without a list kept anywhere else.
2. Run one target, several, all of a project, or all up to a regression level, on Verilator with cocotb.
3. Print the seed of every run; rerun any target from a given seed with the same result.
4. Fail the run when a test fails, when a target runs past its time budget, or when a negative test's expected fatal does not fire; pass a negative test only when its expected fatal fires.
5. Run a target through its wrapper when it needs a prepared program image, and let the wrapper run the target itself.
6. Keep every build, log, result and waveform in a scratch directory outside the source tree, one folder per target.
7. Print one line per target (name, seed, time, verdict) and a total; exit nonzero when anything failed.

## Out of scope

- Simulators other than Verilator.
- Distributing runs across hosts and keeping run history; CI and the tracker own those.

## Acceptance

Each requirement is verified by running the tool on the nand2mario targets.

| Requirement | Check |
|---|---|
| 1 | `list` shows every target of every project's target file |
| 2 | One target, a list, `--project` and `--level` each run the expected targets |
| 3 | Every result line shows a seed; rerunning with `--seed` reproduces the verdict |
| 4 | A broken block fails its test; a negative test passes only when its expected fatal fires |
| 5 | A wrapper target assembles its program and passes |
| 6 | After a run, the source tree has no new files; each target has its folder in the scratch directory |
| 7 | A run with one failure prints that target's log path and exits nonzero |
