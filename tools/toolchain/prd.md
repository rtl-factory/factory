# Toolchain: requirements

## Problem

Captains, crewmates and CI must lint, simulate, formally verify and build RTL for the two target FPGA boards, and get the same result on every machine. Hand-installed tools drift between machines, and a result that only reproduces on one machine cannot be trusted or debugged. The stack is decided in ADR 0009.

## Requirements

1. Provide every tool the RTL flow needs in pinned container images that run on a Linux host: one for linting, simulation and formal verification, one for FPGA builds.
2. Use the same images, at the same versions, in CI, on the self-hosted runner, for agents, and in a devcontainer for engineers.
3. Lint RTL with Verilator, and fail on code outside the SystemVerilog subset that both Verilator and Quartus accept.
4. Simulate with Verilator and run cocotb testbenches headless, with a fixed seed when asked, and write waveforms that can be opened after the run.
5. Run formal proofs and covers with SymbiYosys, headless, and keep the counterexample traces.
6. Build an FPGA image for each target board, the DE2-115 and the DE10-Lite, headless, producing a programming file and the fit and timing reports.
7. Program each board from a Linux host over its on-board USB-Blaster.
8. Record every tool version in one place; change a version only through an issue, and rebuild the images when it changes.
9. Respect each tool's licence, including whether a Quartus Prime Lite image may be published or must only be built and kept privately.

## Out of scope

- Windows and macOS hosts (a devcontainer on them is fine; native installs are not supported).
- Commercial simulators and formal tools, and Quartus Prime Pro or Standard.
- Boards other than the DE2-115 and DE10-Lite, until a requirement adds one.

## Acceptance

Each requirement is verified in the images themselves, by a smoke job that runs on the self-hosted runner.

| Requirement | Check |
|---|---|
| 1 | Both images build from their definitions with pinned versions; a fresh Linux host runs them with nothing else installed but the container runtime |
| 2 | CI, the runner and the devcontainer report identical tool versions |
| 3 | Linting a sample block passes; linting a file using a construct outside the subset fails with that construct named |
| 4 | A sample cocotb test passes, fails when the block is broken, reruns identically with the same seed, and leaves a waveform file |
| 5 | A sample proof passes; breaking the block yields a counterexample trace on disk |
| 6 | A sample design builds for both boards and produces a programming file and fit and timing reports |
| 7 | The sample design is programmed onto each board from the runner and its LED pattern is seen |
| 8 | All versions are listed in one file; changing one and rebuilding changes the reported version |
| 9 | The licence position for each image is recorded, with where its image is stored |
