# Regression: spec

How the regression script meets its [requirements](prd.md). Code: `src/nand2mario/scripts/regression/`; tests in its `tests/`.

## Parts

| Part | Role |
|---|---|
| `cpu_regression.py` | Pre-step of the program-driven targets in `src/nand2mario/cfg/targets.json`: builds the target's program with `sw_build/cpu_sw.py` (and expands it with `sw_build/mbc1_image.py` for the MBC1 target), then runs the simulation |
| `system_regression.py` | The same pre-step for the `n2m_system` family of targets, with the system program |
| `mooneye.py` | `fetch`, `run`, `smoke` and `trace-smoke` for the Mooneye acceptance suite |
| `game_qualification.py` | `fetch`, `run` and `reference` for one licensed game or `all` |

## Program images

The registry names the pre-step module for each target. The pre-step builds the image into `workdir/`, with each build bounded to 30 s. The runner's outer wall bound is the target's `timeout_seconds` plus 960 s of headroom, so the simulator's own timeout fires first. A non-zero build exit or a missing image ends the target before the simulator starts.

## Mooneye

`src/nand2mario/cfg/mooneye.json` pins the release (archive URL and digest, licence digest), the model, `max_cycles`, the `selected` ROMs with their digests, the `excluded` ones and the `known_failures`, each with an owner. `fetch` checks the archive and licence before reading any ROM, then each selected ROM, and only then extracts. `run` simulates each ROM on the `n2m_system_mooneye` target, one compiled model per lane (`--jobs` 1 to 4), and reads the verdict from the breakpoint registers (the Mooneye `LD B,B` convention). It writes one JSON per ROM, a summary and a Markdown table. A failure on the known list is reported; any other failure fails the run. `trace-smoke` checks the PPU timing trace aid of `dv/conformance/test_ppu_trace.py`.

## Licensed games

`src/nand2mario/cfg/games.json` pins each game's source, header and SHA-256 and its finite scenario. `fetch` admits an image only when its header and digest match, and refuses a bad notice before downloading any binary. `run` drives the scenario on the RTL (`dv/system/test_system_games.py`). `reference` runs the same scenario on the PyBoy 2.7.0 software oracle, in a separate environment. Suite failures and per-game frame evidence are written separately, so the evidence of a passing game survives a failing one.

## Output

Everything is written below `workdir/`, and any output path outside it is refused. No ROM, image or frame is committed. The tests point the module `ROOT` at a temporary folder or a [scratch checkout](../../../../tools/bazel/spec.md#unit-tests), so they write nothing into the checkout's `workdir/`.

## Origin

Ported from nand2anything with the design (issue #67); regrouped into this folder in issue #74.
