# nand2doom Doom platform adaptation

These files adapt the GPL-2.0-or-later DoomGeneric engine to nand2doom. They are
distributed under GNU GPL version 2 to remain compatible with the combined
firmware. The complete GPL text and pinned engine source archive are retained
in [project-local public inputs](../third_party/README.md). Generated binary
firmware remains below ignored `workdir/`.

Upstream engine: `ozkl/doomgeneric` commit
`dcb7a8dbc7a16ce3dda29382ac9aae9d77d21284`, archive SHA-256
`1bd3f7f26220494159a38d71f2847ec81b58d6bbd7c7c8d81b08993018001148`.
See `src/nand2doom/scripts/sw_build/n2d_doom.py` for the reproducible corresponding-source recipe.

The supported data is Freedoom Phase 1 v0.13.0 under its three-clause BSD license.
Its original distribution is retained with the project-local inputs; the build
extracts and checks the Phase 1 WAD. Commercial Doom WADs are excluded.

The adaptation retains real WAD parsing, rendering, and gameplay. Because the
platform deliberately has no networking, its generated upstream-source patch
replaces the multiplayer compatibility WAD checksum with a deterministic zero
digest. The generated patch also makes Doom draw directly into the back
framebuffer bank: `DG_ScreenBuffer` and `I_VideoBuffer` point at the
framebuffer, `I_FinishUpdate` only requests the bank swap through
`DG_DrawFrame`, and `D_Display` and the melt-wipe loop call
`N2D_FramebufferAcquireFrom`/`N2D_FramebufferAcquire` before drawing (a
full-width level view keeps only the status-bar rows). `r_plane.c` fills
visplane columns lazily. The linker also owns explicit, asserted libc heap, static TLS,
16 MiB Doom zone, stack, and fixed-memory WAD regions.

The explicit no-input rendering correctness workload is built with
`python3 src/nand2doom/scripts/sw_build/n2d_doom.py build --variant render-verification`.
It uses the typed upstream single-tic mode before engine startup and is gated
by state, call and raw pixel witnesses described in the
[Doom port spec](../../../../wiki/src/nand2doom/sw/doom/spec.md#deterministic-time-verification-build).
Default builds still produce production and ordinary deterministic images.
Verification FPS is not production performance evidence.
