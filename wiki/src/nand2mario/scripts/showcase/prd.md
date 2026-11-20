# Showcase: requirements

## Problem

Readers want to see nand2mario run without a board. The game qualification already captures frames from the RTL simulation, and the showcase turns them into pictures. It must show only what the simulation produced, and say exactly where each picture came from, so it is evidence, not marketing.

## Requirements

1. Read one or more per-game results directories written by the system game qualification, and fail if a game's results are missing.
2. Write each captured frame as a PNG scaled by exactly 2, with no smoothing.
3. Write one animated GIF per game from sampled frames, merging consecutive identical frames into one with their summed delay, so the GIF matches the frame sequence.
4. Write a title strip, a gallery and a `provenance.json` that records the source commit, the hash of every input and output, and a label saying these are simulation captures, not board captures.
5. Take the game catalogue (name, author, licence, credits) from a given JSON file, defaulting to the qualified games.

## Out of scope

- Running the simulation or capturing the frames: [regression](../regression/README.md) does that.
- Board photos or captures: those come from the [board flow](../board/README.md).

## Acceptance

| Requirement | Check |
|---|---|
| 1 | Missing results are rejected |
| 2 | PNGs are scaled exactly |
| 3 | The GIF merges duplicates and matches the frames |
| 4 | Provenance hashes and label |
| 5 | Not tested yet: the default catalogue is used by every test |
