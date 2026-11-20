# Toolchain: requirements

## Problem

Captains, crewmates and CI must lint, simulate, formally verify and build RTL for the two target FPGA boards, and get the same result on every machine. Hand-installed tools drift between machines, and a result that reproduces on only one machine cannot be trusted or debugged. The stack is decided in ADR 0009.

## Requirements

1. Install every tool the RTL flow needs natively on a supported Linux host, at versions pinned in one versions file, with each downloaded file verified against a pinned checksum.
2. Provide a readiness check that reads the versions file and reports, per tool, pass, warning or fail with the exact fix; it never changes the host, and it fails when a required tool is missing or at the wrong version.
3. Set up the same pinned tools in a cloud agent session automatically at session start, without committing anything licensed.
4. Lint RTL with Verilator, and fail on code outside the SystemVerilog subset that both Verilator and Quartus accept.
5. Simulate with Verilator and run cocotb testbenches headless; print the seed of every run and rerun any run from a given seed; write waveforms that can be opened after the run.
6. Measure code coverage of the RTL under simulation and functional coverage in the cocotb testbenches, and report both.
7. Run formal proofs and covers with SymbiYosys, headless, and keep the counterexample traces.
8. Build an FPGA image for each target board, the DE2-115 and the DE10-Lite, headless, producing a programming file and the fit and timing reports.
9. Program each board from the Linux host it is wired to, over its on-board USB-Blaster.
10. Change a version only through an issue; the readiness check then fails on every host until it is updated.
11. Respect each tool's licence: Quartus installers and device files are fetched only from Altera or from private storage, never committed or republished.

## Out of scope

- Windows and macOS hosts; WSL2 counts as Linux.
- Container images (ADR 0009).
- Commercial simulators and formal tools, and Quartus Prime Pro or Standard.
- Formal equivalence and structural clock-domain-crossing checkers, until a requirement adds them.
- Boards other than the DE2-115 and DE10-Lite.

## Acceptance

Each requirement is verified on real hosts by the readiness check and a smoke run of a sample design.

| Requirement | Check |
|---|---|
| 1 | Following the install procedure on a fresh supported host makes the readiness check print READY; a corrupted download fails its checksum |
| 2 | Removing a tool or installing a wrong version makes the check fail with that tool named and its fix printed; the check leaves the host unchanged |
| 3 | A new cloud agent session reaches READY without manual steps, and the repository contains no licensed file |
| 4 | Linting a sample block passes; a file using a construct outside the subset fails with that construct named |
| 5 | A sample cocotb test passes, fails when the block is broken, prints its seed, and reruns identically from that seed; its waveform opens |
| 6 | The sample run reports code coverage and functional coverage numbers |
| 7 | A sample proof passes; breaking the block yields a counterexample trace on disk |
| 8 | A sample design builds for both boards and produces a programming file and fit and timing reports |
| 9 | The sample design is programmed onto each board from its board host and its LED pattern is seen |
| 10 | Changing a pin in the versions file makes the readiness check fail on a host that has the old version |
| 11 | The repository and its history contain no Quartus installer, device file or image |
