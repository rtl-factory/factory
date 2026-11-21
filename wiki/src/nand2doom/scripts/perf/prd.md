# Performance: requirements

## Problem

Every speed change to nand2doom (core, caches, SDRAM controller, compiler profile, renderer) must be measured on the real product RTL, and a faster run must still draw the same frames. The measurement takes hours of simulation, so it must be one command whose inputs are pinned and whose result says what each cycle went to; a wrong image, an injected input or a different framebuffer geometry silently changes both the speed and the pictures. The half-width DE10-Lite framebuffer cannot reuse the full-width pixel pins, so its expected pixels must come from the full-width baseline by an independent projection, never from the run being checked.

## Requirements

1. Run the native harness on a chosen memory backend (fast line model or real SDRAM) and framebuffer geometry (full or half width) for a chosen Doom variant: build the image (or reuse it on request), build the harness through the simulation runner, preload the image, start the CPU by real UART packets with up to eight queued input events, and record a bounded number of gameplay frames.
2. Summarize the report: cycles and IPC to the first scene, cycles per gameplay frame, frames per second at 25 MHz, cache ratios, the exclusive per-cycle stall classes and their root-cause split, which must sum to their parent; write the summary, the raw result and every error to the run directory.
3. Fail the run on a trap, a framebuffer write while a swap is pending, an incomplete run, a simulator error, an inconsistent stall split, or a mismatch with expected scene CRCs.
4. Check, on request, the deterministic image against its pinned CRCs, but only for the reference firmware it was pinned on; check the render-verification image against its state-qualified oracle (game state per frame, single-tic call witnesses, CRCs and raw pixel hashes), after binding the image's sources, defines, ELF and binary to its manifest; refuse a pin check with injected input, a waveform window or a production image.
5. Optionally record a text event trace, a binary memory stream for offline cache studies, a waveform window, and ELF-resolved game state.
6. Attribute a bounded retirement trace of gameplay frames to ELF text symbols, with intervals that sum exactly to each frame, software waits (sleep and swap polling) reported apart, and instruction counts that reconcile with the frame report; refuse a trace that does not cover every frame or is out of order.
7. Reproduce the half-width oracle from hash-checked full-width baseline pixels by the pinned even-column projection, check it against the half oracle fixture, and optionally check a candidate run's report and projected images against it.
8. Keep every output below the checkout's scratch area, and offer a dry run that prints the plan and runs nothing.

## Out of scope

- The harness itself and its targets: the [platform DV plan](../../dv/platform/dv-plan.md) owns them.
- Updating the oracle pins: there is no pin updater; a pin changes only by a reviewed edit of its fixture.
- Building the image: [sw_build](../sw_build/README.md) does that; the harness run calls it.

## Acceptance

| Requirement | Check |
|---|---|
| 1 | The harness builds through the simulation runner as a compile-only target; UART tokens are Reset-PC, queued inputs, then Resume with gaps; preload lines check segment sizes |
| 2 | Report parsing and summary; PMON and cycle lines; stall-split lines and their self-check; reports without PMON lines |
| 3 | The check rejects CRC mismatch, trap, failure and writes while a swap is pending; an FST window end is a successful run |
| 4 | Oracle and pins; production refuses a pin check; injected input refused; legacy pin image checks bytes, ELF and layout; render image identity binds sources, defines, ELF and binary; state, call, CRC and pixel oracle; half geometry uses shared states and independent pixels |
| 5 | State report keeps each frame and scene; state symbols need unique aligned BSS objects; compiled call symbols validated; the stream is compressed in place |
| 6 | Profile tests: resume origin, startup excluded, exclusive symbol ends, swap poll separate from useful copy, sleep descendants, partial or reordered trace refused |
| 7 | Projection tests: even columns and rows, VGA pair projection with RGB444 and black blanking, baseline hash and fixture provenance fail closed, candidate corruption and absence detected |
| 8 | The dry run refuses an output outside the scratch area |
