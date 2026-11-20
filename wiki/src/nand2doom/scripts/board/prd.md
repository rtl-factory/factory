# Board build: requirements

## Problem

nand2doom runs on the DE10-Lite and the DE2-115 at 25 MHz. A captain or a crewmate must be able to build either board's image headless with the pinned Quartus, from committed config alone, and know that it fits, meets timing at every corner and raised no warning nobody reviewed, because Verilator accepts RTL that Quartus rejects and a board image that hides a failure produces board evidence nobody can trust. Before a speed change, they also need to see what limits the clock: which paths fail at a faster system clock and which part of the design owns them, without touching the shipped constraints.

## Requirements

1. Build either board's image from the committed board config (device, top, sources, pins, I/O standards, settings, constraints, timeout): the DE10-Lite from its top level, the DE2-115 from its overrides; refuse an invalid or out-of-tree config before anything is staged.
2. Stage each build fresh under the scratch area, with every input copied and hashed; refuse an output outside it or through a link, and a second concurrent build of the same output.
3. Bound every Quartus run by the board's timeout and, on timeout, stop every process it started.
4. Pass the build only if the fit completed and the timing audit shows: non-negative slack and zero TNS at every corner for every required check, the 50 MHz reference and the PLL system clock with the expected relationships, the external SDRAM read and write paths at every corner, no unconstrained or illegal clock or port, no ignored constraint, exactly the reviewed `check_timing` findings, the fitted RAM the config expects, and every warning matching a reviewed identity and count.
5. Write the evidence of a passing build (slack per corner, clocks, SDRAM paths, resources, every warning in order and its summary) beside the image.
6. Offer a fast front-end check that runs only analysis and elaboration, with its own reviewed-warning gate, and reports an explicit skip, never a pass, when Quartus is absent.
7. Analyze a board's timing at one or more requested system clocks: compile with a scratch override of the PLL system-clock period (the shipped constraints stay unchanged), or analyze an already compiled project, and refuse to label results with a clock other than the one analyzed.
8. Report per corner the Fmax, the worst slack and TNS per check, and the worst setup paths, each classed by the part of the design that owns it, as JSON and as Markdown tables.
9. Offer a plan for both that validates the config and prints the outputs without running Quartus or writing anything.

## Out of scope

- Programming a board: the [play command](../host/README.md) programs the DE2-115 with an image built here.
- Installing Quartus or checking the host.
- Choosing which board is attached or queueing jobs on it: the board host owns that ([fpga.md](../../../../bkm/fpga/fpga.md)).
- The board facts and the reviewed warning set themselves: the [board specs](../../fpga/README.md) own them.

## Acceptance

The script's tests check every requirement without Quartus, on fixture reports; requirements 1 to 5 on real hardware are shown by the build evidence in each board spec's budget.

| Requirement | Check |
|---|---|
| 1 | Pins cover every bit once on both boards; the DE10-Lite resolution is the unchanged top level; the DE2-115 closure swaps only the board files; unknown boards and override keys refused; missing source and duplicate pin refused |
| 2 | Output escape and links refused; a concurrent build is refused before touching the output; a stale image cannot satisfy a successful tool exit |
| 3 | The board timeout reaches compile and audit; timeout fails and kills the child; the launcher's child identity is recorded |
| 4 | Timing, clock, SDRAM-path, fitted-RAM, `check_timing`, connectivity, electrical-advisory and warning-identity gates accept the reviewed reports and reject each deviation on both boards |
| 5 | The hardware gate requires artifacts, timing and reviewed warnings and keeps raw warning order |
| 6 | Elaboration runs only analysis and elaboration per board, skips explicitly without Quartus, stays below the scratch area, and gates its own warnings |
| 7 | The what-if override redefines only the PLL system clock period and rejects an unknown SDC or range; `--sta-only` refuses a clock other than the analyzed one; board selection and build directory |
| 8 | Parsing and tables use the worst setup corner; incomplete or unknown records rejected; path classification and owners; table cells survive hierarchy pipes |
| 9 | Both plans write nothing and need no Quartus |
