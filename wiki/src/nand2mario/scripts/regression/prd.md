# Regression: requirements

## Problem

nand2mario's simulation targets run programs: a CPU smoke program, the system program, the background, VGA and MBC1 programs, the Mooneye acceptance ROMs and licensed games. A target that runs a stale image, or a suite that quietly skips a failing ROM, reports a pass for a design nobody tested. Licensed ROMs may never enter the repository, so the suites must fetch and check them each time.

## Requirements

1. Before each program-driven simulation target, build its program image fresh from committed source, and stop the target if the build fails or the image is missing.
2. Bound each image build by a short fixed deadline, and the whole target by the registry's own timeout for it, so a hung build never stalls the run.
3. Fetch the pinned Mooneye release, check the archive, its licence and every selected ROM against the pinned Mooneye config, and refuse any mismatch before extracting.
4. Run each selected Mooneye ROM on the system RTL, read the verdict from the breakpoint registers, and write a result per ROM, a summary and a Markdown table. Known failures are listed and owned in the config; any other failure fails the suite.
5. Acquire pinned licensed games from outside the repository, check each against its pinned header and digest, and run its finite compatibility scenario; keep game evidence apart from suite failures.
6. Keep every downloaded ROM, image and result under `workdir/`; commit no binary and no screenshot.

## Out of scope

- The simulation runner itself: the sim tool runs the targets ([its spec](../../../../tools/sim/spec.md)).
- Building the design's own programs: [sw_build](../sw_build/README.md) does that; this script calls it.
- Installing the PyBoy software reference: its optional `reference` command needs its own environment with PyBoy.

## Acceptance

| Requirement | Check |
|---|---|
| 1 | Adapter tests: a fresh image precedes each target (CPU, system, background, VGA, MBC1); a failing build or missing image stops the simulator |
| 2 | The runner's bound follows the registry timeout |
| 3 | Committed pins are valid; bad pins, a changed archive, licence or ROM are refused |
| 4 | Breakpoint-register verdicts; table counts; known failures pin the passing set and each is owned |
| 5 | Image admission and header checks independent of the digest; a bad notice is refused before any binary; failures and game evidence stay separate |
| 6 | Output must be below `workdir/` |
