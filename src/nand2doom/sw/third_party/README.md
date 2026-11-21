# Pinned public Doom inputs

The [manifest](manifest.json) binds these exact original distribution files:

- DoomGeneric source at `dcb7a8dbc7a16ce3dda29382ac9aae9d77d21284`,
  GPL-2.0-or-later: [license](DOOMGENERIC-LICENSE).
- Freedoom 0.13.0, BSD-3-Clause: [license](FREEDOOM-COPYING.txt),
  [credits](FREEDOOM-CREDITS.txt), [music credits](FREEDOOM-CREDITS-MUSIC.txt).
  This is not commercial Doom data.
- Picolibc 1.8.6-2 Ubuntu library/header package:
  [component copyright and license terms](PICOLIBC-COPYRIGHT).
  This is a pinned prebuilt dependency, not a claim that the compiler or library
  was rebuilt locally from source.

The original archives, including their embedded notices, are not committed to
this repository. The build script fetches each one from its pinned origin on
first use, fails closed on any SHA-256 mismatch, and keeps a verified copy in
the ignored `workdir/downloads/nand2doom/` cache for later builds.

The [platform adaptation](../doom/README.md) and
[build script](../../scripts/sw_build/n2d_doom.py) retain local corresponding-source changes
and their recipe. Firmware/build output stays in ignored `workdir/`. Installed
RISC-V GCC/binutils remain prerequisites. No proprietary WAD or game ROM is included.
