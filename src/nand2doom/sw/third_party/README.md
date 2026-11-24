# Pinned public Doom inputs

The [manifest](manifest.json) binds these exact original distribution files:

- DoomGeneric source at `dcb7a8dbc7a16ce3dda29382ac9aae9d77d21284`,
  GPL-2.0-or-later: [license](DOOMGENERIC-LICENSE).
- Freedoom 0.13.0, BSD-3-Clause: [license](FREEDOOM-COPYING.txt),
  [credits](FREEDOOM-CREDITS.txt), [music credits](FREEDOOM-CREDITS-MUSIC.txt).
  This is not commercial Doom data.
- Picolibc 1.8.6 source release:
  [component copyright and license terms](PICOLIBC-COPYRIGHT) (its `COPYING.picolibc`).
  The Doom build compiles it with the pinned RISC-V GCC (#231).

The original archives, including their embedded notices, are not committed to
this repository. The build script fetches each one from its pinned origin on
first use, fails closed on any SHA-256 mismatch, and keeps a verified copy in
the ignored `workdir/downloads/nand2doom/` cache for later builds.

The [platform adaptation](../doom/README.md) and
[build script](../../scripts/sw_build/n2d_doom.py) retain local corresponding-source changes
and their recipe. Firmware/build output stays in ignored `workdir/`. The
RISC-V GCC/binutils are pinned in `cfg/toolchain.json` and fetched by Bazel; a direct
run outside Bazel uses the installed ones. No proprietary WAD or game ROM is included.
