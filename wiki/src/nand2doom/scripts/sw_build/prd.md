# Software build: requirements

## Problem

Every nand2doom simulation target and board run executes an image: the validation program that proves the CPU, memory and framebuffer, or Doom itself. Doom needs three public inputs that are not committed (the DoomGeneric engine, the Freedoom game data and a C library), a small set of adaptations to the engine source, and a manifest the loaders, the capture and the performance harness can trust. An image nobody can rebuild byte for byte, built from an input that silently changed or from a stale adaptation, makes every result that ran it meaningless. Doom is GPL firmware, so the recipe that builds it must also be its corresponding source.

## Requirements

1. Build the validation image from the committed runtime sources with the RV32IM cross toolchain alone (freestanding, no libraries, no PIC, no relaxation), and write its ELF, binary, map and manifest.
2. Build the same validation image byte for byte from any checkout or output directory.
3. Fetch each Doom input from its pinned origin once, check its full SHA-256 before use, keep it in a local cache outside the repository, and refuse a cached or downloaded input whose digest differs; never fetch again when the cache holds the right bytes.
4. Apply each engine adaptation (palette, checksum, direct framebuffer, scene hook, draw loops, wall loop, lazy visplanes) only at its exact pinned seam, idempotently, and fail when the seam has changed.
5. Build the Doom image in three variants (production, deterministic time, render verification), each with its own defines, time source, engine compiler profile and output directory, and allow explicit engine optimization and LTO overrides for experiments.
6. Bind each Doom image to its inputs: compile a 32-byte identity of the firmware sources, the build recipe, the adapted engine tree, the pinned input digests, the libc and the compiler into the image, check it in the linked binary, and record it, the capture mailbox address and the ELF digest in the manifest.
7. Build the same Doom firmware bytes from any checkout or output directory.
8. Keep the licence notices of every input beside the image.
9. Write only below the checkout's scratch area, reject outputs with link components, and offer a plan that downloads, discovers and writes nothing.

## Out of scope

- The runtime, the validation program and the Doom platform code themselves: they are the `sw` discipline ([runtime](../../sw/runtime/prd.md), [Doom port](../../sw/doom/prd.md)).
- Installing the cross toolchain.
- Loading an image onto a board: [host](../host/README.md) does that.

## Acceptance

The script's tests check every requirement without a board; requirements 1, 5 and 6 are also exercised by every simulation target that runs these images ([software DV plan](../../dv/software/dv-plan.md)).

| Requirement | Check |
|---|---|
| 1 | Validation-image tests: explicit compiler contract; a plan is non-mutating |
| 2 | Clean builds into two different outputs are identical |
| 3 | The local archive is preferred and a corrupted one never fetches; inputs are immutable and licensed; the hash is byte exact |
| 4 | Seam tests for the scene hook, direct render hooks, draw loops, wall loop and visplanes: pinned, idempotent, rejected when changed, and pixel- and state-equivalent to upstream |
| 5 | Variant selection and defaults, compiler profiles, deterministic time compiled out of production, one output refused for all variants |
| 6 | The performance script's render-image identity test binds sources, defines, ELF and binary to the manifest; the link-time symbol and identity checks run in every build, but their failure paths have no unit test yet |
| 7 | Disposable paths are mapped out of the firmware |
| 8 | Inputs carry their licences; the Picolibc override must carry its pinned artifacts |
| 9 | Output confined before prepare or build; existing link components and redirected nested outputs rejected before any download; the plan never downloads, discovers compilers or writes |
