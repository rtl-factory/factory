# Software build: spec

How the software build meets its [requirements](prd.md). Code: `src/nand2doom/scripts/sw_build/`; tests in its `tests/`. What the images contain (layout, platform contract, time policy, capture mailbox) is the `sw` discipline's: the [runtime spec](../../sw/runtime/spec.md) and the [Doom port spec](../../sw/doom/spec.md).

## Parts

| Part | Role |
|---|---|
| `n2d_image.py` | Builds the validation image. `build_image()` and the helpers `_tool`, `_version` and `_confined_output` are also used by the software DV builds |
| `n2d_doom.py` | Fetches the Doom inputs and builds the Doom image (`prepare`, `build`). Its pins (`ENGINE_*`, `WAD_*`, `VARIANTS`) are read by the UART host and the performance harness, and its output confinement by the capture |

## Validation image

```text
python3 src/nand2doom/scripts/sw_build/n2d_image.py [--output workdir/software/n2d_validation] [--plan]
```

- **Toolchain.** `riscv64-unknown-elf-gcc`, `-objcopy` and `-readelf`, or the executables named by `N2D_RISCV_GCC`, `N2D_RISCV_OBJCOPY` and `N2D_RISCV_READELF`. A binutil whose variable is unset defaults to the sibling of `N2D_RISCV_GCC` with the same prefix (`.../riscv64-elf-gcc` gives `.../riscv64-elf-nm`); the Doom build resolves its compiler and objcopy the same way. A missing one is an error naming its variable.
- **Output under Bazel.** Under `bazel test` an output may also lie in the test's `TEST_UNDECLARED_OUTPUTS_DIR` or `TEST_TMPDIR`, where tests put it, because the sandbox mounts the checkout read-only.
- **Commands.** Compiles `crt0.S`, `runtime.c` and `validation.c` of `src/nand2doom/sw/runtime/` with `-march=rv32im -mabi=ilp32 -O2 -g0 -ffreestanding -fno-builtin -fno-pic -fno-stack-protector -msmall-data-limit=0 -mno-relax`, links them with `-nostdlib -nostartfiles`, the runtime linker script, `--build-id=none` and `--gc-sections`, and converts the ELF to a raw binary. Commands run from the checkout root with output paths relative to it.
- **Checks.** `readelf` must report an ELF32 RISC-V file and the binary must be non-empty; it is padded with zeros to a whole word.
- **Outputs.** `n2d_validation.elf`, `.bin`, `.map` (the output prefix replaced by `${OUTPUT}`) and `manifest.json`: schema 1, `rv32im`, `ilp32`, little endian, entry 0, memory-map revision `n2d-platform-v1`, the completion marker `0xD00D0035` at `0x00004000`, one segment at 0 with length and SHA-256, the ELF and map digests, the compiler and objcopy versions and the compiler contract. Existing outputs are deleted first; a link or a non-file in their place is an error.
- **Plan.** `--plan` prints the architecture, ABI, output and every command, and writes nothing.
- **Exit.** 0 on success; 2 on a confinement, toolchain, file or command failure, with the error on standard error.
- **Determinism.** Fixed flags, fixed object names, no build ID and the normalized map give the same bytes for any output directory.

## Doom build

```text
python3 src/nand2doom/scripts/sw_build/n2d_doom.py prepare [--variant V] [--output DIR] [--plan]
python3 src/nand2doom/scripts/sw_build/n2d_doom.py build [--variant production|deterministic|render-verification|all]
        [--output DIR] [--picolibc-prefix DIR] [--engine-opt Os|O2|O3] [--[no-]engine-lto] [--plan]
```

### Variants

| Variant | Defines | Time source | Engine profile | Default output |
|---|---|---|---|---|
| `production` | none | cycle counter at `0x20000008` | `-O2`, engine-only LTO | `workdir/software/n2d_doom` |
| `deterministic` | `N2D_DETERMINISTIC_TIME=1` | `minstret`, 25,000 per ms, video waits paused | `-O2`, engine-only LTO | `workdir/software/n2d_doom_deterministic` |
| `render-verification` | also `N2D_VERIFY_SINGLETICS=1` | as deterministic | `-Os`, no LTO | `workdir/software/n2d_doom_render_verification` |

`build` with no `--variant` builds production and deterministic; `all` adds render verification. `--output` builds one variant (production when none is named) and is refused with `all`. `prepare` always works on one directory. `--engine-opt` and `--engine-lto` override the variant's engine profile for `build` only; platform and capture objects stay `-Os` and outside LTO, and an LTO link is `-flto=1 -flto-partition=one`.

### Inputs and cache

| Input | Pin |
|---|---|
| DoomGeneric | `ozkl/doomgeneric` at `dcb7a8db…`, archive SHA-256 pinned, GPL-2.0-or-later |
| Freedoom Phase 1 | v0.13.0 release ZIP, its SHA-256 and the `freedoom1.wad` SHA-256 and length (28,795,076 bytes) pinned, BSD-3-Clause |
| Picolibc | `picolibc-riscv64-unknown-elf` 1.8.6-2 Ubuntu package, its SHA-256, the SHA-256 of each consumed library and the copyright file, and the include tree's SHA-256 pinned |

Each download lands in `<output>/downloads/`. A file already there with the pinned digest is kept. Otherwise the copy in `workdir/downloads/nand2doom/` is used if present; it must be a regular file with the pinned digest, else the build stops ("cached input changed"). With no cached copy the input is fetched from its URL (60 s timeout) and, once its digest checks, copied into the cache when the cache can be written (under `bazel test` it cannot, and the fetch is still used). A destination or temporary that is a link is refused. Another variant's output first hard-links (or copies) the production output's `downloads/` and complete `toolchain/` trees, and every file is checked again.

`--picolibc-prefix` uses an installed Picolibc instead of the package; it must hold the pinned consumed artifacts and include tree. The package is unpacked in Python (`ar`, then `data.tar`, `.gz`, `.xz` or, with Python 3.14, `.zst`), with no host package manager.

### Prepare

`prepare` fetches the engine and Freedoom, extracts the engine into `<output>/source/` (tar `data` filter), extracts and checks `freedoom1.wad`, copies the Freedoom licence and credits, and applies the engine adaptations, then writes `inputs.json` (engine and game-data identity, the WAD's length and address `0x02000000`). Each adaptation replaces one exact upstream seam, accepts its own earlier result unchanged, and raises when the seam differs; the seams and what each changes are in the [Doom port spec](../../sw/doom/spec.md#doom-platform-contract).

### Build

`build` prepares, gets and checks Picolibc, then:

1. Computes the identity inputs before linking: the SHA-256 of every file in `src/nand2doom/sw/doom/`, of this script (key `src/nand2doom/scripts/sw_build/n2d_doom.py`), of the adapted engine tree, the libc artifacts, the compiler version, the engine archive and WAD digests, the compiler options and any variant defines. Their canonical JSON's SHA-256 is compiled into `capture_identity.c` as `n2d_capture_identity`.
2. Compiles the identity, the engine objects (upstream flags plus the reviewed warning exceptions) and the platform sources with `-march=rv32im -mabi=ilp32 -ffreestanding -fno-builtin -fno-pic -fno-pie -mno-relax -msmall-data-limit=0 -Wall -Wextra -Werror`, links with the Doom linker script against Picolibc and libgcc, and converts the ELF to `n2d_doom.bin`.
3. Resolves the capture mailbox and identity symbols with `riscv64-unknown-elf-nm`, requires each once with the right kind and size, and requires the identity bytes in the binary to equal the computed identity.
4. Writes `manifest.json`: the inputs, schema 1, `rv32im`, `ilp32`, entry 0, memory-map revision, the variant (name, defines, time source), compiler options, the capture block (ABI 1, 280 words, mailbox and identity addresses, identity inputs, ELF path and digest), toolchain versions, libc identity and notice, two segments (the binary at 0 and the WAD at `0x02000000`, each with length and SHA-256), and the boot, frame and scene markers.

Outputs: `n2d_doom.elf`, `.bin`, `.map`, `manifest.json`, `inputs.json`, `freedoom1.wad`, the Freedoom and Picolibc notices, and the `downloads/`, `source/`, `toolchain/` and `build/` trees, all under the output directory.

**Determinism.** `-ffile-prefix-map` and `-fmacro-prefix-map` replace the checkout and output paths with fixed names, so the firmware bytes do not depend on where the checkout or output is. The identity covers this script's bytes, so any edit to it changes the identity and therefore the binary.

**Plan.** `--plan` prints, per variant, the command, output, defines, compiler options, engine objects, platform sources and identity object; it downloads, discovers and writes nothing.

**Errors.** A bad variant selection, confinement failure or option misuse exits 2 through the argument parser. A digest mismatch, changed seam, missing toolchain or Picolibc, compiler failure or symbol mismatch raises and exits non-zero with its message.

## Confinement

Both scripts resolve the output against the checkout root and require it below `workdir/` (`n2d_image.py` also accepts the scratch directories of the Bazel test running it, `TEST_UNDECLARED_OUTPUTS_DIR` and `TEST_TMPDIR`), with no link component below that directory; `n2d_image.py` also refuses a `workdir` that is itself a link, and `n2d_doom.py` rejects any link or escaping path already inside the output tree, before and after extraction.

## Tests

```text
python3 -m pytest src/nand2doom/scripts/sw_build
```

The Doom tests need the pinned engine archive, which they read from `workdir/downloads/nand2doom/` when it holds the pinned digest and otherwise fetch once into their temporary folder (`scratch.pinned`, with `_download`'s `cache` set to that folder), never filling the cache. Both scripts' `main` take the checkout as a `root` keyword (default: this checkout); the tests run every plan, confinement and image build against a [scratch checkout](../../../../tools/bazel/spec.md#unit-tests) or the test's temporary folder, so they write nothing into the checkout's `workdir/` and pass whatever it holds. `test_n2d_wall_loop.py` compiles the pinned wall-loop seam and its adaptation with the host compiler and compares pixels and final state.

## Origin

Ported from the captain's nand2anything repository with the design (issue #78), where these were the build tools; regrouped into this folder, with this PRD and spec, in issue #81.
