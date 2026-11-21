# Bazel build: requirements

## Problem

The L0 regression took about 18 minutes, and every run rebuilt every Verilator model from scratch, even when a change touched one block or only a test. Agents run L0 before every push, so that time is paid over and over. The captain chose Bazel over FuseSoC and Edalize for its caching and parallelism, accepting its complexity because agents maintain the build by following a BKM.

## Requirements

1. Make every simulation and formal target of every project, and every folder of unit tests, a test, without listing targets anywhere but the simulation registry and the formal targets' own files.
2. Select the tests of a regression level and the levels below it in one command.
3. Build each distinct model (top, sources, parameters) once, share it between targets that differ only in their test, and rebuild it only when one of its inputs changes.
4. Rerun a test only when its model, its test code, its data or the runner changed.
5. Run independent model builds and tests in parallel, up to the host's CPUs.
6. Keep the verdicts of the simulation runner: a failing test, a timeout, or a negative test whose fatal did not fire fails; the seed is printed and can be overridden.
7. Use the Verilator version pinned by the toolchain, refuse any other, and treat a different binary as a changed input.
8. Keep results between CI runs, so a pull request reruns only what it changed.
9. Write logs, results and waveforms outside the source tree.
10. Build each board image with one command that runs the project's Quartus script and its timing and warning gate, and rebuild it only when an input changed.

## Out of scope

- A remote cache or remote execution server.
- Building Verilator, the OSS CAD Suite or Quartus from Bazel; the toolchain installs them.
- Running Quartus in CI; board images build on a host with Quartus.

## Acceptance

| Requirement | Check |
|---|---|
| 1 | Every non-manual simulation and formal target appears as a test in `bazel query 'tests(//src/...)'` |
| 2 | `bazel test --config=l0 //src/...` runs exactly the L0 targets |
| 3 | Targets with the same top, sources and parameters share one model; touching a test module verilates nothing |
| 4 | A rerun with no change reports every test cached |
| 5 | A cold L0 run is faster than the serial runner on the same host |
| 6 | Negative targets pass only with their fatal; `--test_env=SIM_SEED=<n>` changes the seed shown |
| 7 | A different Verilator version on `PATH` fails the build with the pin named |
| 8 | A second CI run on an unchanged tree runs no Verilator action |
| 9 | After a run the source tree has no new files |
| 10 | `bazel build //src/<project>:<board>_image` leaves the SOF and gate evidence in `bazel-bin`; a second build runs no Quartus; a failing gate fails the build |
