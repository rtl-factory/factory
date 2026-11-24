# Quartus build: requirements

## Problem

nand2doom and nand2mario each grew their own Quartus script (`quartus.py`, `n2m_quartus.py`) of about 900 lines. Both staged a project from a JSON manifest, compiled it, ran a timing audit and gated the reports, but with different manifest keys, gates and image receipts, and each copied the same board's pins and I/O standards. A fix to one gate did not reach the other project, and a third project would have needed a third script (issue #133).

## Requirements

1. Build any project's image for any board from the project's manifest alone: the tool names no project, design or module path.
2. Keep each board's pins and I/O standards once, in `src/common/fpga/<board>/pins.json`; a manifest names the pins it uses and may wire a port to a board pin of another name.
3. Hand Quartus the same project each project's own script did, so moving to the tool changes no fit.
4. Gate a full compile on every obligation either script gated: artifacts, inputs unchanged since staging, all-corner timing with no unconstrained path or ignored constraint, the fitted clocks, the fitted RAMs and resources, Fmax at the system clock, the manifest's reviewed I/O paths, and every warning reviewed with its exact count.
5. Bind a passing SOF to its commit, inputs, evidence and Quartus version, so programming can refuse an image that is not the reviewed build of the selected manifest.
6. Offer a plan without Quartus, a fast analysis-and-elaboration check that skips explicitly when Quartus is missing, and a rerun of the gate on an existing build.
7. Keep every output below `workdir/`, refuse concurrent builds of one project and kill the whole Quartus process tree on a timeout.
8. Keep the Bazel `<board>_image` targets working.

## Out of scope

- Programming a board (the [board console](../board/README.md) and each project's board flow do).
- Timing what-if analysis at other clocks (nand2doom's `n2d_timing.py` reuses the tool's staging).
- Choosing a board's IDCODE (the board declarations hold it).

## Acceptance

| Requirement | Check |
|---|---|
| 1 | The tool's unit tests build a synthetic project on two synthetic boards and a profile, without Quartus |
| 2 | Both projects' manifests resolve every top port bit to one pin from the board's pin map (project unit tests) |
| 3 | The generated `.qsf` of all five images equals the one each project's script generated (project unit tests, against committed copies) |
| 4 | A passing synthetic build passes, and each obligation fails alone when its report is changed (unit tests); all four project fits pass on a host with Quartus |
| 5 | Changing the SOF, its inputs, its evidence or the manifest is refused before programming (unit tests) |
| 6 | `--plan` writes nothing; `--elaborate-only` without Quartus exits 77 (unit tests) |
| 7 | Output outside `workdir/`, a link component, a held lock and a timeout are refused (unit tests) |
| 8 | `bazel query 'attr(tags, quartus, //...)'` lists the five image targets |
