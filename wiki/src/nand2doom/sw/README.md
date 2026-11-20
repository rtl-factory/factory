# nand2doom software

The `sw` discipline of nand2doom: code that runs on the design's own RV32IM CPU. Code: `src/nand2doom/sw/`. The host scripts that build it (`n2d_image.py`, `n2d_doom.py`) are in [scripts](../scripts/README.md).

| Software | Documents | Code |
|---|---|---|
| Runtime and validation image | [PRD](runtime/prd.md), [spec](runtime/spec.md) | `sw/runtime/` |
| Doom port | [PRD](doom/prd.md), [spec](doom/spec.md) | `sw/doom/`, with the third-party notices and pinned-input manifest in `sw/third_party/` |
