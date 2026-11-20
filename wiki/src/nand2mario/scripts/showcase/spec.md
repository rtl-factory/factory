# Showcase: spec

How the showcase meets its [requirements](prd.md). Code: `src/nand2mario/scripts/showcase/sim_showcase_render.py`; tests in its `tests/`.

## Command line

`sim_showcase_render.py --results DIR [--results DIR ...] [--catalog JSON] --output DIR --source-commit SHA`

- `--results`: a per-game results directory of the system game qualification (`dv/system/test_system_games.py`); repeatable.
- `--catalog`: a JSON list of `{slug, name, author, license, credits}`; without it, the three qualified games built into the script.
- `--output`: where the PNGs, GIFs, title strip, gallery and `provenance.json` go.
- `--source-commit`: the commit the frames were simulated at, recorded in the provenance.

## Output

Each 160x144 four-shade frame is scaled 2x (320x288) with nearest-neighbour sampling. Each game's GIF plays five input-step samples (title, start, release, play, right) at 1000 ms each, and consecutive identical samples become one longer frame; these are editorial samples, not a measured frame rate. A title strip of the first three games and a gallery follow. `provenance.json` records the source commit, the SHA-256 of every source PNG, output PNG, GIF and ROM, and the label "Verilator n2m_system simulation frame capture; not a physical board or VGA capture".

## Origin

Ported from nand2anything with the design (issue #67, originally its issue #395); regrouped into this folder in issue #74.
