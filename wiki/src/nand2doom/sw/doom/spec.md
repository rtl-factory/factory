# nand2doom Doom port: spec

How the Doom port meets its [requirements](prd.md), on the [runtime](../runtime/spec.md). Code: `src/nand2doom/sw/doom/`.

## Doom source and assets

The Doom gate uses `ozkl/doomgeneric` revision
`dcb7a8dbc7a16ce3dda29382ac9aae9d77d21284` (GPL-2.0-or-later) and Freedoom
Phase 1 v0.13.0 (three-clause BSD). The manifest records URLs plus engine
archive SHA-256 `1bd3f7f2...18001148`, release ZIP `3f9b264f...b9688f59` and
`freedoom1.wad` `7323bcc1...f9e703d`. The builder verifies full digests before
extraction and keeps Freedoom COPYING/credits, Doom license and libc notices
under `workdir/`. The checked-in adapter and recipe are corresponding source
for the GPL firmware; distributing an image carries the same obligations.
Tests may use only an authorized redistributable fixture or a user-supplied
file whose digest is recorded outside source history.

## Doom image

- **Libraries.** Picolibc 1.8.6 built from its pinned source with the pinned
  GCC, without thread-local storage, with fail-closed host calls; `_sbrk`
  exposes only the linker-bounded heap. `-msmall-data-limit=0`, explicit
  RV32IM libgcc, non-PIC, no relaxation.
- **Layout.** Text, data, TLS and BSS below the libc heap end `0x0070_0000`; a
  non-cleared 16 MiB Doom zone `0x0080_0000`..`0x017f_ffff`; stack
  `0x01e0_0000`..`0x01fe_ffff` (top `0x01ff_0000`); read-only Freedoom from
  `0x0200_0000` to exclusive end `0x03b7_60c4`. The fixed-memory WAD backend
  accepts only `freedoom1.wad` and bounds every read.
- **TLS.** `.tdata`/`.tbss` precede BSS; startup points `tp` at the TLS block
  and zeroes TLS and BSS; a pre-engine self-check proves `errno` and heap
  sentinels independent, else publishes a distinct failure marker. Picolibc
  is built without TLS, so `errno` is a plain global and the block is empty.
- **Networking** is out of scope: the adapter returns the zero WAD-directory
  checksum instead of peer hashing.

### Engine compiler experiments

The [build specification](https://github.com/amichai-bd/nand2anything/blob/aeebe92/wiki/tools/build/SPEC.md) owns
`--engine-opt`/`--engine-lto`.

| Variant | Engine profile | Why |
| --- | --- | --- |
| production, deterministic | `-O2` with engine-only LTO | Measured speed winner ([#75](https://github.com/amichai-bd/nand2anything/issues/75)): cross-file inlining lowers instructions and I-cache misses |
| render-verification | `-Os`, no LTO | LTO would inline away the `G_Ticker`/`D_Display` call witnesses |

Local adapters (platform, capture, startup, clock, memory, identity) stay
`-Os` outside LTO; the mailbox stays a 1,120-byte aligned BSS symbol and its
identity a 32-byte read-only symbol (post-link checks). Options enter the
pre-link identity and manifest. At fixed game time `-O2`+LTO and `-Os` draw
identical frames ([DV](../../dv/software/dv-plan.md#engine-compiler-profile-acceptance)); production
time follows the cycle clock, so faster images reach different tics.

## Doom platform contract

### Direct framebuffer rendering

The engine uses `CMAP256` at 320x200 and draws directly into the back
[framebuffer bank](../../rtl/video/mas.md#framebuffer-and-palette). `n2d_doom.py`
patches the extracted engine:

- `doomgeneric.c`: `DG_ScreenBuffer` is the back window `0x1000_0000`.
- `i_video.c`: `I_VideoBuffer` (`screens[0]`) points at it, its zone
  allocation is dropped and `I_FinishUpdate` is just `DG_DrawFrame`.
- `d_main.c`: `N2D_FramebufferAcquireFrom` right after the `nodrawers`
  return at the start of `D_Display`; `N2D_FramebufferAcquire` before each
  melt-wipe step.

In `src/nand2doom/sw/doom/platform.c`,
`DG_DrawFrame` uploads a changed palette (converted to RGB444), writes the
swap to `0x2000_1008` with bit 1 set when a palette was uploaded, records an
outstanding swap and returns without waiting; `N2D_CaptureCommit` only marks
the commit for that write, so frame and palette flip at the same vertical
blank. `N2D_FramebufferAcquireFrom(first_row)` does nothing without an
outstanding swap; otherwise it polls swap-pending, copies rows
`first_row`..199 of the front view `0x1002_0000` into the back window in
four-word groups (80 words per row; outside 0..200 copies all 16,000 words)
and clears the flag. `N2D_FramebufferAcquire` is row 0. Ticks between request
and acquire never draw. An eligible capture calls `N2D_CaptureAcquire` after
accepting its sequence at the gameplay seam, before READY. The same image
runs on both boards; half-width banks keep even-x pixels. A `D_Display` hook
marks scene readiness only after the final normal update or completed melt
wipe.

The back bank must start as the displayed image wherever the frame does not
redraw every pixel: the status bar, view border and melt columns are redrawn
only on change, the wipe reads the screen as its start image, and fuzz reads
back pixels it draws over.

### Status-bar-only persistence

The `D_Display` hook passes `viewheight` (168 with the status bar, 200 in
full-screen, which copies nothing) when all hold at frame start, else 0 (full
64,000-byte copy):

- `gamestate == GS_LEVEL` and `gametic` nonzero;
- `gamestate == wipegamestate`;
- no pending resize (`setsizeneeded`);
- neither automap nor any menu or prompt is up;
- `scaledviewwidth == SCREENWIDTH` (no border).

A full-width level view redraws rows `[0, viewheight)` completely, so the copy
falls from 16,000 to 2,560 words in a status-bar frame. Wipe steps and
capture still copy everything. Assumption: no missing-texture holes (a hole
would show a frame two swaps old instead of one).

### Lazy visplane fill

Upstream `R_FindPlane`/`R_CheckPlane` fill all 320 `top[]` bytes with `0xff`,
but only `top[minx..maxx]` is read (`R_DrawPlanes` reads `[minx-1, maxx+1]`
after setting both edges). `n2d_doom.py` replaces both active `r_plane.c`
bodies (pinned SHA-256) so a new plane starts empty and `R_CheckPlane` marks
only gained columns: the whole range of a new or empty plane, and the
extension on either side when it grows. Selection, ranges and every read value
are unchanged.

### Register-resident drawers

`R_DrawColumn` and `R_DrawSpan` (about 36% of gameplay cycles in the #75
profile) reload `dc_source`/`dc_colormap` (`ds_*`) after every byte store
because stores may alias them. The replacement bodies in the extracted
`r_draw.c` (the `#if 0` variants untouched):

- copy source and colormap pointers into locals once per column or span;
- handle the zero-to-three-pixel remainder first, then four texels per step
  in original store order;
- keep column position and step as unsigned modulo-32-bit values shifted left
  nine, so `frac >> 25` equals the prior `(signed_frac >> 16) & 127`,
  preserving negative steps and the 128-texel wrap.

Span 6.10 arithmetic, byte stores and `RANGECHECK` errors are preserved;
nonpositive counts write nothing. Only exact pinned or previous maintained
bodies are accepted. Measurements: [#368 study](https://github.com/amichai-bd/nand2anything/blob/aeebe92/wiki/src/nand2doom/performance-drawers-368.md).

### Wall-loop global contract

The hash-pinned `R_RenderSegLoop` adaptation keeps the wall index and
`rw_scale`, `topfrac`, `bottomfrac` local with their original signed types;
step/stop globals are still read. It publishes scale and fractions at each
original column-end update and `rw_x` on increment; `pixhigh`/`pixlow` keep
their pre-callback publications; zero-iteration calls change nothing. All
consumers of these globals are in `r_segs.c`, and no write is deferred across
an observable boundary. Prepared bodies must round-trip to the upstream hash
with one adjacent marker. Measurements and audit:
[#370 study](https://github.com/amichai-bd/nand2anything/blob/aeebe92/wiki/src/nand2doom/performance-wall-loop-370.md).

### Doom time contract

`src/nand2doom/sw/doom/clock.h` and
`clock.c` own the clock;
`DG_Init` initializes it from the 25 MHz MMIO tick `0x20000008`.

- `DG_GetTicksMs` returns floor(elapsed cycles / 25,000) modulo 2^32. State:
  last uint32 tick, uint32 elapsed ms, remainder 0..24,999. Each sample adds
  the delta quotient and combines the delta remainder with the saved fraction
  (bounded at 49,998, at most one carry). Crossing hardware zero does not
  reset elapsed time; the 32-bit ms result wraps after about 49.71 days.
- Consecutive samples **must be less than 2^32 cycles apart** (171.79869184 s);
  missed full periods (a long debugger halt) cannot be inferred and require
  reinitialization.
- `DG_SleepMs` keeps cycle-based deadlines, services the clock even for zero
  duration, and splits requests into chunks of at most 85,899 ms
  (2,147,475,000 cycles) so signed deadline comparison holds.
- `N2D_CLOCK_TRACE` (focused verification only) links a software timestamp
  source instead of MMIO.

### Doom keyboard map

`src/nand2doom/sw/doom/keymap.h`: `DG_GetKey` pops
one `{extended, break, scan_code}` event from the
[input FIFO](../../rtl/io/mas.md#ps2-keyboard-receiver), looks up
`{extended, scan_code}` in a 512-entry static byte table (plain, then `E0`),
reports press when break is clear, and consumes and ignores zero entries.
Values are the pinned `doomkeys.h` constants and lowercase ASCII.

| Keys | Set-2 (plain unless E0) | Doom key | Default use |
| --- | --- | --- | --- |
| Arrows | E0 75/72/6b/74, keypad 75/72/6b/74 | `KEY_UP/DOWN/LEFT/RIGHTARROW` | move, turn, menu |
| Ctrl left/right | 14, E0 14 | `KEY_FIRE` | fire |
| Space | 29 | `KEY_USE` | use |
| Shift left/right | 12, 59, E0 12 | `KEY_RSHIFT` | run |
| Alt left/right | 11, E0 11 | `KEY_LALT` | strafe |
| Enter, keypad Enter | 5a, E0 5a | `KEY_ENTER` | menu select |
| Escape | 76 | `KEY_ESCAPE` | menu |
| Tab | 0d | `KEY_TAB` | automap |
| Backspace | 66 | `KEY_BACKSPACE` | menu back |
| `-`, `=` | 4e, 55 | `KEY_MINUS`, `KEY_EQUALS` | automap zoom, view size |
| `0`-`9` | 45, 16, 1e, 26, 25, 2e, 36, 3d, 3e, 46 | `'0'`-`'9'` | weapons 1-7, automap max zoom |
| `a`-`z` | standard Set-2 letter codes | `'a'`-`'z'` | Y/N prompts, automap f/g/m/c, cheats |

Other E0 keys, including multimedia keys sharing a letter's low byte, map to
nothing. Every [local control](../../scripts/host/spec.md#local-controls) action has
a nonzero entry.

## Deterministic-time verification build

`n2d_doom.py` builds production (`workdir/software/n2d_doom`) and
deterministic (`workdir/software/n2d_doom_deterministic`) by default;
`--variant render-verification` adds the rendering-oracle workload and
`--variant all` builds all three. The manifest's `variant` object records
name, defines and time source; defines and port bytes are capture identity
inputs.

- **Deterministic** (`-DN2D_DETERMINISTIC_TIME=1`): the tick is `minstret`
  plus a software offset at 25,000 per ms (IPC 1 at 25 MHz). `DG_SleepMs`
  advances the offset instead of spinning. Hardware waits run paused
  (`N2D_ClockPause`/`N2D_ClockResume` subtract instructions retired while
  waiting; pauses nest): capture palette settle, capture PREPARING-to-release
  and the three video waits below. Their 100 ms timeouts use real time
  (`N2D_ClockRealMs`). This is a reproducible time policy, not identical
  frame ordinals across schedules or firmware changes.
- **Video waits.** Video status `0x2000_1004` is palette bank (bit 0), pending
  commit (bit 1), frame-start sequence (bits 31:2). `DG_DrawFrame` waits until
  neither commit nor swap is pending; before the next swap it waits two frame
  starts after the recorded end of the previous frame (immediate when
  rendering takes longer than two rasters); the acquire poll then ends at once.
- **Render verification** adds `-DN2D_VERIFY_SINGLETICS=1`: the port includes
  `d_loop.h` and sets the typed `singletics` before `doomgeneric_Create`, giving
  two `G_Ticker` calls before the first display and one per display after. The
  engine source is unchanged. Its FPS is verification throughput only.

Input arrival is set by the test bench, so frames after injected input stay
timing dependent; the cocotb gate accepts post-input gameplay on
[playability](../../dv/software/dv-plan.md#actual-doom-selectable-gate).

## Coherent capture

The optional host request selects one **completed gameplay `D_Display`**
(after its final normal update or completed melt wipe) with
`gamestate == GS_LEVEL` at that seam. Unrequested frames keep the same bytes,
markers and gameplay contract. Capture needs a known, fully uploaded palette
whose expected bank is active with no commit pending. Firmware finishes all
stores, settles the bank, waits two VGA sequence increments and publishes
READY sequence last, then permits no tick, framebuffer write, palette upload
or commit until release, while still sampling the clock. Only a successful
maintained Halt makes cached mailbox writes durable and host writes visible on
resume. The [host SPEC](../../scripts/host/spec.md#coherent-frame-capture) owns deadlines,
continuity, provenance and the
[identity and addresses](../../scripts/host/spec.md#identity-and-exact-addresses).

### Capture mailbox

`src/nand2doom/sw/doom/capture.c` and
`capture.h`: one 32-byte-aligned
1,120-byte BSS object located by its ELF symbol and linker assertions. Startup
clears it; `DG_Init` initializes every word after the clock and writes magic
last. Little-endian u32 words, ABI 1:

| Word | Owner | Meaning |
| --- | --- | --- |
| 0..2 | firmware | Magic `0x4332444e`, ABI=1, object words=280. |
| 3 | host | Request sequence; zero releases/cancels. |
| 4 | firmware | IDLE=0, PREPARING=1, READY=2, ERROR=3. |
| 5 | firmware | Ready sequence; zero invalid; published last. |
| 6 | firmware | Last accepted nonzero sequence, never decreased in a boot. |
| 7 | firmware | Completed DrawFrame count, modulo 2^32. |
| 8 | firmware | Captured video status after two-frame guard. |
| 9 | firmware | Generation advanced after an actual clock sample. |
| 10 | firmware | Error: none=0, unknown palette=1, bank=2, timeout=3, sequence=4. |
| 11 | firmware | Fully uploaded palette known flag. |
| 12..19 | firmware | 32-byte input build identity, byte-preserving u32 storage. |
| 20..23 | firmware | Reserved zero. |
| 24..279 | firmware | Last fully uploaded 256-entry RGB444 shadow. |

- **Palette serialization.** Before every upload wait ≤100 sampled ms for
  pending=0 (and the expected bank when the shadow is known), upload all 256
  masked values, copy them to the shadow, record the opposite bank, commit
  once. A timeout or wrong bank latches a fatal palette error. A cancelled
  upload still completes. The shadow changes only outside READY.
- **Acceptance.** A request above `last` is accepted only at an eligible
  seam; IDs never repeat or wrap (`UINT32_MAX` exhausts). The host never
  replaces an outstanding nonzero request or writes firmware words.
- **Preparing.** Settle the palette, then wait for two advances of the
  30-bit video sequence within 100 sampled ms with the bank expected and
  nothing pending; replacement enters ERROR. Metadata precedes a compiler
  barrier and `fence iorw,iorw`; ready sequence is stored last. Success needs
  request == ready == owned sequence; the acquire uses that sequence.
- **Release.** The park loop rereads the request and samples the clock;
  release invalidates the ready sequence, orders it, then publishes IDLE (or
  fatal ERROR) before drawing resumes. Request 0 at a later seam clears a
  nonfatal error. Generation advances only after `DG_GetTicksMs`.
