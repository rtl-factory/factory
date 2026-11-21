# Board build

Builds and gates nand2doom's Quartus image for either board, and analyzes a board's timing at other system clocks. Code: `src/nand2doom/scripts/board/` (`quartus.py` is the build and report gate; `n2d_timing.py` is the timing analysis, which reuses its staging and runner).

- [Requirements](prd.md)
- [Spec](spec.md)
