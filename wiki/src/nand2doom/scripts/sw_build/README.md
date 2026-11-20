# Software build

The host builds of the software that runs on nand2doom's own CPU: the freestanding RV32IM validation image, and the Doom image with its pinned engine, game data and C library. They run on the host, so they are scripts; the sources they build live in the `sw` discipline (`src/nand2doom/sw/`, ADR 0012). Code: `src/nand2doom/scripts/sw_build/` (`n2d_image.py` and `n2d_doom.py`).

- [Requirements](prd.md)
- [Spec](spec.md)
