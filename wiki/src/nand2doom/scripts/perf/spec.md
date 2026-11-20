# Performance: spec

How the performance scripts meet their [requirements](prd.md). Code: `src/nand2doom/scripts/perf/`; tests in its `tests/`. The harness (`n2d_perf_tb`), its targets and its report format belong to the [platform DV plan](../../dv/platform/dv-plan.md); the performance monitor events to the [core MAS](../../rtl/core/mas.md#performance-monitor).

## Parts

| Part | Role |
|---|---|
| `n2d_perf.py` | Runs the harness and summarizes and checks its report. `render_oracle`, `parse_report` and `check_render` are also used by `n2d_render_projection.py` |
| `n2d_sw_profile.py` | Attributes a harness retirement trace to ELF symbols |
| `n2d_render_projection.py` | Reproduces and checks the half-width render oracle |

## Harness run

```text
python3 src/nand2doom/scripts/perf/n2d_perf.py [--backend fast|sdram] [--geometry full|half]
        [--variant production|deterministic|render-verification] [--frames 2] [--max-cycles 400000000]
        [--image-dir DIR] [--reuse-image] [--output DIR] [--input EVENT ...] [--check-pins]
        [--game-state] [--trace-cycles START:END] [--stream [--stream-start N]] [--fst START:END]
        [--expect-scene-crc CRC] [--expect-scene-vga-crc CRC] [--timeout 21600] [--dry-run]
```

- **Target.** `n2d_perf_fast` or `n2d_perf_sdram`, with `_half` for the half geometry and `_fst` with `--fst` (full geometry only). The target is built by `python3 tools/sim/sim.py test <target>`, a compile-only target, and the binary run is `workdir/sim/cocotb/<target>/obj/<target>_sim`.
- **Defaults.** Variant `deterministic`; image directory the variant's [Doom build](../sw_build/spec.md#variants) output; run directory `workdir/perf/<backend>`, with `-half` and `-render-verification` suffixes. Both must be below `workdir/`.
- **Rules.** `render-verification` forces `--check-pins` and `--game-state` and requires two frames, no input and no FST window. `--check-pins` is refused for production, with input or an FST window, with more than two frames, and (outside render verification) for the half geometry. At most eight input events, each 0 to `0x3ff`.
- **Dry run.** `--dry-run` prints the target, variant, input, geometry, image and run directories, binary, frames, cycle bound and checks as JSON, and runs nothing.
- **Image.** Unless `--reuse-image` finds a manifest, the image is built with the Doom build's `build()`. The manifest's variant must be the requested one. With pins, the image is validated before anything is compiled: for deterministic, the segment bytes, the WAD and the reference firmware and ELF digests (`LEGACY_IMAGE_SHA256`, `LEGACY_ELF_SHA256`); for render verification, the engine revision against the oracle, the defines, every identity source's current digest (the platform source and the Doom build script must be among them), the recomputed identity, the ELF digest and the identity bytes in the binary.
- **Stimulus.** `preload.txt` (each segment's bytes for the harness to load) and `uart.txt` (Reset-PC 0, the queued input events, then Resume, as byte tokens with a gap token after each packet). With `--game-state` the ELF addresses of `gametic`, `maketic`, `ticdup`, `leveltime`, `prndindex`, `rndindex` and `fuzzpos`; for render verification also `G_Ticker`, `D_Display` and `singletics`, and `experiment-identity.json`.
- **Run.** The binary runs with plusargs for the preload, UART, report, frames, cycle bound, PPM directory and the optional trace, stream and FST windows, bounded by `--timeout`.
- **Summary.** `parse_report` reads `report.txt`; `summarize` derives scene cycles and IPC, gameplay cycles per frame (between consecutive bank swaps), FPS at 25 MHz, gameplay IPC, swap wait and spin, PMON event counts by name, the exclusive cycle classes (retire, divide, uncached wait, D-miss wait, load-use, multiply-use, data admission, serialize, I-fetch wait, halted, other) and the I-fetch and D-miss splits. For render verification the FPS is reported as verification FPS. The stream is gzipped in place; each PPM gets a PNG when Pillow is present.
- **Checks.** A trap, swap-pending writes, a run that is neither `pass` nor an FST window end, a scene CRC differing from `--expect-scene-crc` or `--expect-scene-vga-crc`, a stall split that does not sum to its class, the pins or render oracle, and a non-zero simulator status each add an error. `summary.json` holds the summary, the raw result and the errors; the summary is printed and each error goes to standard error.
- **Exit.** 0 with no error, 1 otherwise; argument misuse exits 2, and a bad path or image prints `error:` and exits non-zero.

## Software profile

```text
python3 src/nand2doom/scripts/perf/n2d_sw_profile.py --trace TRACE --symbols NM --summary SUMMARY
        [--wait-range START:END ...] [--output FILE]
```

Inputs: the harness's text trace (`<cycle> R pc=<hex> insn=<hex>` lines), an `nm`-style symbol listing (sized text symbols), and a passing run's `summary.json`. The gameplay windows are the report's frames after the first scene, moved from resume-relative to absolute cycles. Each interval from one retirement to the next is charged to the last retired PC's symbol, clipped to the windows, so each window's cycles sum exactly to its length. Retirements inside `DG_SleepMs`, its call descendants (tracked through `ra`/`t0` calls and returns) or a `--wait-range` are charged to `<software-wait>`. Per symbol: cycles, instructions, loads, stores and branches; per window: cycles, instructions, wait cycles, the reported instruction count and its delta (at most 1), and the swap request-to-flip and poll-to-flip cycles; and instruction counts per PC inside `R_DrawColumn` and `R_DrawSpan`. A run that did not pass, a trace out of order or not covering every window, or a count that does not reconcile exits 1 with `error:`. The JSON goes to `--output` (below `workdir/`) or standard output. The attribution is last-retired-PC intervals, not causal per-instruction stall costs.

## Render projection

```text
python3 src/nand2doom/scripts/perf/n2d_render_projection.py --baseline DIR [--output DIR] [--candidate DIR]
```

The baseline is a full-width render-verification run directory. Each of its framebuffer and VGA PPM files must match the full oracle's SHA-256. The half fixture must record the `even-column-v1` method and that no candidate output was used. The framebuffer projection repeats each even column; the VGA projection keeps, for each group of four pixels, the first two and repeats them, requires RGB444-expanded channels, and computes each VGA CRC over an 800x525 RGB444 raster with black blanking. The reproduced CRCs and pixel hashes must equal the half fixture's. `--output` writes the six projected files into a fresh directory below `workdir/`; `--candidate` runs the render check on the candidate's report and compares its six files byte for byte. The script prints `PASS` or `FAIL` with the errors and the projected SHA-256s, and exits 0 on pass, 1 otherwise. There is no pin updater.

## Determinism

The deterministic and render-verification images derive Doom time from retired instructions, so a run's frames are fixed by the image and the stimulus, not by the simulator's speed. Pins are scoped to the firmware they were taken on: any change to the firmware or to the Doom build script changes the image identity, so the deterministic pins then refuse the image rather than compare its frames.

## Tests

```text
python3 -m pytest src/nand2doom/scripts/perf
```

The tests use synthetic reports, traces and pixels; none runs the harness.

## Origin

Ported from the captain's nand2anything repository with the design (issue #78), where these were performance tools; grouped into this folder, with this PRD and spec, in issue #81.
