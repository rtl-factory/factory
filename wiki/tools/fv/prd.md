# Formal runner: requirements

## Problem

Formal proofs of the repo's SystemVerilog must run the same way for a captain at a terminal, for a crewmate checking a change and for CI, from one pinned tool set, and a failed proof must leave a counterexample someone can open. Without one runner, each block grows its own command and its own idea of where traces go.

## Requirements

1. Discover every formal target of every project from the source tree, without a list kept anywhere else.
2. Run one target, several, all of a project, or all up to a regression level.
3. Run every task of a target (proof, covers) and report each task's verdict in the formal BKM's words: a task passes only when the proof holds or every cover is reached.
4. Stop a target that runs past its time budget, ten minutes unless its file says less, and report it as failed.
5. Keep every log, counterexample and cover trace in a scratch directory outside the source tree, one folder per task.
6. Print one line per task (target, task, time, verdict) and a total; exit nonzero when anything failed.
7. Use the formal tools of the pinned OSS CAD Suite without putting the suite's other tools (its own Verilator and Python) ahead of the pinned ones.

## Out of scope

- Equivalence checking and structural clock-domain-crossing checks.
- Commercial formal tools.

## Acceptance

| Requirement | Check |
|---|---|
| 1 | `list` shows every `.sby` file under `src/*/fv/*/` |
| 2 | One target, `--project` and `--level` each run the expected targets (unit tests) |
| 3 | The nand2doom targets print PASS for `prove` and `cover`; a scratch break of the RTL prints FAIL with a trace path |
| 4 | Every target's budget is at most 600 s (unit test) and a run past it prints TIMEOUT |
| 5 | After a run, the source tree has no new files; each task has its folder in the scratch directory |
| 6 | A run with one failure prints that task's folder and log and exits nonzero |
| 7 | `fv.py` calls `sby` from the suite's install directory; `verilator` on `PATH` is unchanged |
