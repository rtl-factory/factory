# software DV plan

<!-- wiki-budget: the opt-in Doom gate's input-witness and playability rules are its acceptance contract -->

## Scope

The software that runs on the core: the runtime and validation image, checked
against the [runtime PRD](../../sw/runtime/prd.md) and [spec](../../sw/runtime/spec.md),
and the Doom port, checked against the [Doom port PRD](../../sw/doom/prd.md)
and [spec](../../sw/doom/spec.md). The core and platform under it are verified
by the [core](../core/dv-plan.md) and [platform](../platform/dv-plan.md)
plans; cycle and IPC measurement uses the
[native performance harness](../platform/dv-plan.md#native-performance-harness).

## Testbench

| Tier | Evidence | Regression |
| --- | --- | --- |
| 1 | Generated assembly program: reset, retirement, branches, loads/stores | Fast |
| 2 | Owned C image: startup, data/BSS, stack, heap, RV32M, input, framebuffer pattern, signature and frame hash | Fast (`n2d_c_runtime_system`) |
| 3 | Doom-shaped owned program: rendering, palettes, injected keys, working set beyond each cache | Fast |
| 4 | Actual Doom with authorized manifests: boot marker, deterministic first frame, input, later state | L2 (`n2d_doom_system`) |

Builder unit tests check RV32IM/ILP32 flags, non-mutating planning and
`workdir/` confinement; the integration test runs the installed GNU
cross-toolchain and records its version. Engine adaptations are host-tested
against the pinned upstream archive, which the tests fetch into the
`workdir/downloads/nand2doom/` cache on first use.

## Features

Spec section names the requirement in the software PRDs (SW-R1..R4 and SW-R6 in the
[runtime PRD](../../sw/runtime/prd.md), SW-R5..R7 in the [Doom port PRD](../../sw/doom/prd.md))
or the spec section it checks. Level is the target's level in
`src/nand2doom/cfg/targets.json` where the test is a simulation target.

| ID | Spec section | Stimulus | Checker | Coverage point | Test | Level |
|---|---|---|---|---|---|---|
| SW-F01 | SW-R2, R3, validation image | Every image word by UART `LOAD` in groups of at most eight, each acknowledged; Reset-PC and Resume by wire, no preload; trap-free retirement; I/D hits and misses; every framebuffer byte with CRC-32 vs `(3*x + 5*y) & 0xff`; Halt with maintenance; Peek of signature words one to eight (call/stack, multiply, divide, remainder, odd-sized data and BSS, guards, heap); an injected D-cache writeback error gives Halt timeout and a rejected Peek with no memory request, then retry, Halt and coherent Peek; PS/2 and UART make/release digest in word nine, identity and tick in word ten, Poke/readback. Logs image digest, compiler, retirements, CRC, cache events, simulator, seed, bound. The fast integrated target adds a VGA CRC ([platform DV](../platform/dv-plan.md)). | Retirement scoreboard; framebuffer CRC-32 against the pattern; Peek of the signature words | none defined | `n2d_c_runtime_system` (35; 1,600,000 cycles) | L1 |
| SW-F02 | Keyboard map | Compiles `keymap.h` with the archive's `doomkeys.h` (SHA-256 checked); probes all 1,024 events against Doom values and the common JTAG host's independent Set-2 table; break equals make; every local-control action maps; Y/N/Tab/weapon bindings; E0 multimedia keys are not letters. | Doom values from the archive's `doomkeys.h` and the common JTAG host's independent Set-2 table | none defined | `test_n2d_doom_keymap.py` | host test (`src/nand2doom/scripts/tests/`) |
| SW-F03 | Keyboard in game | `--input 0x016` gives `DG_GetKey` marker `0xd00d3931`, ticcmd marker `0xd00d4008` and a switch to the fist; `0x016 0x076 0x275 0x05a 0x035` gives markers `0xd00d3931`, `391b`, `39ad`, `390d`, `3979`, Up wrapping to Quit Game, then exit marker `0xd00de000`; no input passes the pinned scene CRCs. These goldens belong to their firmware and script. | Firmware markers and the pinned scene CRCs | none defined | `n2d_perf.py --input` | script run (`n2d_perf.py`), not in a level |
| SW-F04 | Doom time contract | Production helper and callbacks at `-Os` on `n2d_rv32im_core` via the public runner with an `N2D_CLOCK_TRACE` timestamp source; results at `0x8000`; only terminal ebreak. `clock_vectors.py` oracle: nonzero init, zero elapsed, 24,999/25,000 cycles, repeated fractions, hardware wrap, >172 s, repeated wraps, `UINT32_MAX` deltas, ms rollover from a seeded state; the old fixed-origin formula agrees before a full period and fails at it; zero, fractional, wrapping and 172 s chunked sleeps with exact timestamp-read counts. A host unit test runs the same C probe as a shared library. | `clock_vectors.py` oracle | none defined | `n2d_doom_clock` (67; 300,000 cycles, 90 s) | L0 |
| SW-F05 | Doom gate | See [actual Doom selectable gate](#actual-doom-selectable-gate). | Pinned scene CRCs, input witness and playability bounds | none defined | `n2d_doom_system` (37) | L2 |
| SW-F06 | Gate observer | Byte initialization, contiguous and repeated stores, invalid/trapping/non-memory cases, load/control retirements through the dispatcher with nonzero MAKE/BREAK slots, latched MAKE chain, direct BREAK return; a falling-edge-only driver must fail strict ordering while rising-then-falling succeeds; monitors stopped through `stop_monitors` (cocotb cancels twice). Host tests: `test_input_witness.py`, `test_gameplay_retirement_dispatch.py`. | Strict-ordering observer checks | none defined | `n2d_doom_observer` (67; 128 cycles, 30 s) | L0; host tests |
| SW-F07 | Coherent capture | See [coherent capture](#coherent-capture). | Production capture helper against fake devices and expected patterns | none defined | `n2d_capture`, `n2d_capture_system`, host tests | L1 (`n2d_capture`), L2 (`n2d_capture_system`); host tests |
| SW-F08 | SW-R5 drawers | See [Drawer equivalence](#register-resident-drawer-equivalence). | Pinned upstream and an independent scalar form | none defined | `test_n2d_doom.py` | host test (`src/nand2doom/scripts/tests/`) |
| SW-F09 | SW-R5 wall loop | See [wall loop](#wall-loop-consumer-state-equivalence). | Pinned upstream loop | none defined | `test_n2d_wall_loop.py` (370) | host test (`src/nand2doom/scripts/tests/`) |
| SW-F10 | SW-R5 persistence, visplanes | See [persistence](#persistence-and-visplane-equivalence). | Pinned upstream bodies; whole-frame CRCs | none defined | `test_n2d_doom.py` | host test (`src/nand2doom/scripts/tests/`) |
| SW-F11 | Compiler profiles | See [profiles](#engine-compiler-profile-acceptance). | Planner tests; pinned digests, CRC equality and the render-verification oracle | none defined | Planner tests, builds | host test (`src/nand2doom/scripts/tests/`); builds |

### Actual Doom selectable gate

`n2d_doom_system` (seed 37, L2) rebuilds the pinned production variant with
the `-Os`, no-LTO engine profile into `workdir/software/n2d_doom_gameplay/`,
preloads its two sparse segments under reset and runs the product
CPU/cache/device path. The input witness matches fixed call sites in
`I_StartTic`, `I_GetEvent` and `NetUpdate`, which the default `-O2` LTO
profile inlines, so this gate uses the `-Os` profile, as render verification
does (#100). A passing run takes about 25 minutes (1,494 s of `sim.py` wall
time, 1,476 s in cocotb; Verilator 5.052). The build runs inside the cocotb
process and cannot take `--picolibc-prefix`: extracting the Picolibc package
needs cocotb's Python at 3.14 (`compression.zstd`), unless a complete extracted
toolchain is already at `workdir/software/n2d_doom/toolchain/`, which the build
seeds from (#100 shows how to extract it by hand).

- **Scene.** Trap-free boot, runtime/TLS self-check, engine boot marker, all
  64,000 bytes, the `D_Display` completed-scene marker (an intermediate wipe
  frame is not acceptance), indexed CRC `9da80229` and VGA CRC `516470a8`,
  ≥102,400 nonblack visible pixels on ≥320 rows, committed-palette VGA, and at
  least one I hit, I miss, D hit and D miss. The scene PPM is written under
  `workdir/cocotb/n2d_doom_system/`.
- **Input.** One PS/2 make/release, then the equivalent UART `CMD_INPUT` pair.
  Each make must give a nonzero ticcmd; release is sent before waiting for the
  player update. The physical frame completes with exactly one turn, the
  UART frame with exactly two: upper 12-bit angles `fec` then `fd8` (initial
  slow right turn −320 at 16-bit scale, 20 units per turn). No extra turns.
- **Frames.** Both framebuffer banks are shadowed from the first CPU resume,
  because status-bar-only persistence keeps rows drawn before gameplay
  starts; a gameplay frame is recorded at each bank swap after the first
  scene is checked and the CPU resumed, and must have every byte known.
- **Witness.** A passive retirement observer, started before resume, binds
  the fresh ELF and bin (instruction bytes, symbols, call targets,
  `I_StartTic` → `I_GetEvent`) and rebuilds `gametic`, `maketic`, `ticdup`
  from successful store retirements (unknown bytes never count as zero;
  counters then advance by 0 or 1, `gametic <= maketic`, `ticdup == 1`, wrap
  fails); loads must match mask, MMIO address, register and value.
- **Sequencing.** MAKE is consumed with a later EMPTY STATUS and the
  `DG_GetKey`/`I_GetEvent` returns into `BuildNewTic`; BREAK starts on a
  writable falling edge after that chain and the MAKE ACK, and is consumed in
  slot `m+1` right after MAKE slot `m` by the direct keyup exit, with real
  `D_ProcessEvents`/`G_BuildTiccmd`, one MAKE TURN and a neutral BREAK. The
  barrier `maketic >= m+1` and `gametic >= m+1` precedes the next stimulus
  and each accepted frame. No margin is inferred from ACK timing.
- **Playability (owner decision).** Post-input CRCs are logged, not pinned.
  The settled UART frame differs from the physical frame and every earlier
  frame; the final raster differs from the scene; the settled frame has ≥64
  palette indices, ≥32,000 nonblack indexed pixels on ≥160 rows, and its VGA
  raster ≥48 RGB444 colors and ≥102,400 nonblack pixels on ≥320 rows (each
  bound ≤80% of the lowest observed: 129..136 indices, ~58,000 pixels on 200
  rows, 96 colors, 210,228 pixels on 400 rows). Images are captured before the
  assertions.
- **Bounds.** 200,000,000 cycles to the first scene (measured first scene
  at ~55 M); 50,000,000 per later observation; 3,600 s wall. A passing run is
  ~64 M cycles, ~25 minutes at ~43,000 cycles/s. Cycle bounds define
  acceptance.

Cycle and IPC measurement uses the
[native performance harness](../platform/dv-plan.md#native-performance-harness).

### Coherent capture

The production C helper is shared by the Doom adapter, host probe, core probe
and the UART/cache fixture; no test reimplements the handshake.

- **Host vectors** with fake video/time devices: empty/title paths, unknown
  initial palette, delayed and immediate commits, serialized uploads,
  repeated captures, duplicate/exhausted IDs, pending-wait cancellation,
  preparing/parked replacement, mandatory zero release after error, bank
  mismatch, commit/raster failure bounds, wrapping counters; warnings are
  errors. `test_capture_helper.py`
  also checks the scene adaptation and that acquisition uses the accepted
  sequence.
- **`n2d_capture`** (68; 1,500,000 cycles, 200 s): the probe at `-Os` on the
  public-port core runner, 117 result words, only terminal ebreak.
- **`n2d_capture_system`** (68; 62,500,000 cycles, 3,600 s): integrated core,
  caches, UART and real-geometry video with fast SDRAM; a small renderer with
  production `capture.c`/`clock.c` drawing like Doom. Loads over UART pins,
  requests and cancels during partial frames, then two captures through
  production `n2d_capture.Capture` and `n2d_uart.Client` (bulk capability
  before Halt; sixteen framebuffer blocks 15x4096+2560 and one 1,024-byte
  shadow block) compared to `(3*x+5*y+frame)&255` and `(13*index+frame)&4095`,
  header rechecked, every RGB and sync sample over 800x525 compared, release
  and a new generation verified. cocotb `bridge`/`resume` keeps all pin
  access on the scheduler thread; the observer issues no mailbox command.
- **`test_n2d_capture.py`**
  (host side of the [capture script](../../scripts/host/spec.md#coherent-frame-capture)):
  fragmenting transport, snapshot order, request-only writes, foreign and
  exhausted IDs, generation progress and wrap, partial READY, corruption,
  poison, budget abort; same-image resets around release (no later command
  or output); CLI confinement negatives with no mkdir, write or serial open;
  bulk negatives at each of 17 blocks (CRC, sequence, address, length,
  status, truncation, missing or short response); old endpoints fail after
  capability; cleanup between 105 and 120 s; standard-library planning on
  Linux and Windows.

### Register-resident drawer equivalence

`test_n2d_doom.py` checks the pinned seams, idempotence and rejection of
modified or partial rewrites. A host harness compares 20,000 seeded columns
and 20,000 valid nonempty spans against pinned upstream (empty spans are
never passed to it), and an independent scalar form checks 6,804 boundary
cases: nonpositive counts, all remainders, right/bottom edges, guard bytes,
negative and large steps, wrapping coordinates. Changing the shift or removing
the empty-span guard fails. Whole-frame: at fixed time base, at least 20
gameplay frames plus scene, melt wipe, menu and automap with matching tic,
level time, random indices, fuzz position, mode/view state and CRC, wipe call
witnesses, strict alignment controls; then the render-verification oracle.
ISS results qualify state and pixels only. Production comparisons qualify
start/end game state before claiming savings.

### Wall-loop consumer-state equivalence

`test_n2d_wall_loop.py` (seed 370) compares the pinned upstream loop and the
adaptation over 2,048 combinations of texture tiers, masked columns, plane
marking, clipped/empty columns, signed steps, right-edge spans and
zero/negative counts: every lookup/draw callback's globals, clipping, plane
and masked state, final globals and framebuffer bytes. Omitting index or scale
publication fails; prepared-source tests reject changed upstream, missing
publication, wrong initializer, duplicate or misplaced markers. Full
acceptance adds 24 fixed-time scripted frames plus scene, wipe, menu and
automap, full and half render oracles, and a state-qualified production
measurement with a twenty-frame extension.

### Persistence and visplane equivalence

- The `D_Display` hook copies everything unless every status-bar-only
  condition holds; wipe steps copy everything; an earlier full-copy tree is
  upgraded to the same text.
- The visplane seam replaces exactly the two `r_plane.c` bodies, is
  idempotent and rejects changed or partial bodies. A host harness runs 400
  frames of random `R_FindPlane`, `R_CheckPlane` and `top[]` writes over stale
  memory (>1,000 creations, grows and splits) through both bodies, requiring
  identical planes, fields and `top[minx..maxx]`; dropping a gained-side fill
  or shortening one by a byte fails.
- Whole-frame at fixed time and scripted input: melt wipe, scene, ≥20
  status-bar frames walking, turning and firing, automap, menu, two smaller
  views and full screen; every CRC matches and acquire arguments are
  `viewheight`, 0 (wipe, automap, menu, resize, smaller views) and 200
  (full screen). Then the render-verification oracle.

### Engine compiler profile acceptance

Planner tests cover `Os`/`O2`/`O3` with and without LTO, each variant's
default, overrides including `--no-engine-lto`, local objects at `-Os`
outside LTO, serial LTO link options, strict diagnostics, confinement and
side-effect-free plans. A profile change also needs warning-free builds of
every variant with pinned digests and compiler versions, capture symbol
checks, fixed-time-base CRC equality over wipe, scene and ≥20 scripted
gameplay frames, the render-verification oracle, and an sdram production
measurement of cycles per frame, IPC, I-cache misses and image size. Render
verification resolves witnesses from each ELF and fails closed if `G_Ticker`
or `D_Display` is gone.

## Coverage

No functional coverage points or code coverage numbers were recorded when the
plan came over from nand2anything, so every coverage point above is "none
defined". The equivalence checks keep their mutation controls (a changed
shift, a removed guard, an omitted publication, a dropped fill), each of which
must fail.

## Status

`n2d_doom_system` is an L2 target; L2 has no CI job yet. Physical validation
for simulation-only PRs stays with the board milestone [#126](https://github.com/amichai-bd/nand2anything/issues/126).
