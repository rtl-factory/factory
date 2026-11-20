# Board flow: requirements

## Problem

nand2mario runs on two boards, the DE10-Lite and the DE2-115. A crewmate or a captain must be able to build an image that is known to meet timing, put exactly that image on the right board, load a cartridge, see what the screen shows and press buttons, all from a script and with evidence, because the board host may be remote and nobody may be watching the board. A build that hides a warning, a program step that picks the wrong cable, or a loader that accepts a corrupted frame produces board evidence nobody can trust.

## Requirements

1. Build the image for either board, and for the game-library profile, headless with the pinned Quartus, from the board's committed config (device, top, parameters, sources, pins, settings, constraints); keep every generated file under `workdir/`.
2. Fail the build on: a fit that does not complete, timing not met at any corner, a clock that is not the one the config names, an unconstrained or illegal clock or port, fitted RAM other than the config expects, and any warning the config does not list as reviewed. Write the evidence (resources, slack per corner, clocks, warnings, Quartus version, source commit) to `evidence.json` beside the image.
3. Program a board only on an explicit `--yes`, with an exact cable name, and only an image whose identity (project, device, source commit, Quartus version) matches its evidence; without `--yes`, print the exact command and run nothing.
4. Load a cartridge image into the board's cartridge backing over UART while the console is held in reset, optionally read it back to verify, then release the console.
5. Frame every loader request and response with a CRC; retry a lost or corrupted reply a bounded number of times; never accept a reply whose echo, address or sequence does not match the request.
6. Read back the displayed 160x144 frame from the frame store, and decode it offline to PNG (raw and VGA-reconstructed) and to an animated GIF with per-frame labels.
7. Press, hold and release joypad buttons by name through the `common` JTAG keyboard host, with an exact cable and device; refuse unknown buttons.
8. Bound every board operation by a deadline, and on any error, timeout or interrupt release the console and the frame pin before exiting.
9. Without `--yes`, never open a port or cable: write the planned request frames to `workdir/` instead, so a dry run is safe on any host.

## Out of scope

- Installing Quartus or checking the host: the toolchain tool does that.
- Choosing which board is attached, or running more than one job per board: the board host's queue owns that ([fpga.md](../../../../bkm/fpga/fpga.md)).
- Building the cartridge images themselves: [sw_build](../sw_build/README.md) does that.

## Acceptance

The script's tests check every requirement without hardware; requirements 1 to 3 on real hardware are shown by the board evidence recorded in each FPGA pull request.

| Requirement | Check |
|---|---|
| 1 | Staging tests: the plan writes nothing; staged projects carry the config's parameters, pins and synthesis macro; output stays under `workdir/`; Quartus lists only Intel backends and simulation only portable ones |
| 2 | Report-gate tests on fixture reports: fit, timing, clock, constraint, RAM and warning rejections; evidence fields |
| 3 | Program dry run and refusals; with `--yes` the exact command runs; image identity checked |
| 4, 5 | Loader tests: CRC check value, encode and decode, retries of truncated and corrupted frames, mismatched echoes and stale acks never accepted |
| 6 | Snapshot codec against an independent packer, PNG and GIF output, manifest checks |
| 7 | Button translation matches the board decoder; tap order; unknown button and missing cable refused |
| 8 | Deadline, release on transport error and interrupt, cleanup when the port fails |
| 9 | Dry runs of `load`, `snapshot` and `joypad` open nothing |
