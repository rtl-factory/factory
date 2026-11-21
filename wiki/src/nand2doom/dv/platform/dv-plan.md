# platform DV plan

<!-- wiki-budget: the native performance harness CLI, CPI classification and stall split are the measurement contract every performance record cites -->

## Scope

The platform block `n2d_system` through its simulation wrappers and the
product closure, checked against the [platform MAS](../../rtl/platform/mas.md)
and the platform requirements of the [HAS](../../rtl/has.md#purpose); this
plan also owns the system-level evidence tiers and the native performance
harness. Each block inside is verified by its own plan ([core](../core/dv-plan.md),
[memory](../memory/dv-plan.md), [video](../video/dv-plan.md), [I/O](../io/dv-plan.md)),
the board tops by the [FPGA plan](../fpga/dv-plan.md) and the programs by the
[software plan](../software/dv-plan.md).

## Testbench

| Tier | Required evidence | Speed policy |
| --- | --- | --- |
| Sub-IP | Focused core, M-unit, cache, arbiter, framebuffer, UART and PS/2 tests against independent Python models | Default edit loop; compile only the changed cone. |
| Compiled C | Fresh image over serial UART, fast line memory, retirement scoreboard, signature, cache activity, framebuffer hash | Bounded default smoke; no backdoor preload. |
| Integrated system | Core, caches, controller, framebuffer, real serial UART and PS/2 pins into one FIFO | Portable memories. |
| Pin protocols | SDRAM BFM, UART timing, PS/2 frames, one actual-size VGA frame | Focused, not multiplied into software tests. |
| Doom E2E | Authorized manifests, boot marker, deterministic frame, key press/release, later marker | Opt-in, explicit timeout and digest. |
| FPGA | Structural checks, 25 MHz Quartus evidence, separately authorized physical observation | Never replaces Verilator. |

Tests fail on timeout, warning, unknown result, duplicate or missing
retirement, protocol instability, signature or frame-hash mismatch. Seeds are
printed. Fast targets preload exact manifest bytes; one distinct UART target
proves serial loading.

### Simulation wrappers

| Wrapper | Memory | Loading |
| --- | --- | --- |
| `n2d_c_runtime_system` | Fast model | Serial UART Load of the manifest |
| `n2d_integrated_system` | Fast model | Simulation-only 256-byte byte-enabled preload of the exact manifest while product reset is asserted; excluded under `SYNTHESIS`; assertion rejects use after release |
| `n2d_sdram_system` | Product `n2d_memory_controller` and the common `n2a_sdram_model` | Preload; system held in reset until SDRAM initialization |

Both fast wrappers run the same generated RV32IM/ILP32 image.
`n2d_system_product` compiles only the synthesizable closure and structurally
excludes every wrapper. The wrappers are in `src/nand2doom/dv/platform/`.

### Native performance harness

`n2d_perf_tb` runs Doom on the unchanged `n2d_system` as native Verilator
targets: it preloads the pinned manifest's sparse segments under reset,
starts with UART Reset-PC and Resume, and records boot and scene markers,
per-frame cycles and retirements, cache events, framebuffer CRCs and VGA
rasters (cocotb CRC definitions). A trap, failure marker or cycle bound exits
nonzero. It runs about 30,000 cycles/s on a loaded 4-core host and does not
replace the functional gate.

| Target | Memory backend | Framebuffer geometry |
| --- | --- | --- |
| `n2d_perf_fast` | `n2d_integrated_system`: the arbiter plus the fast line model, three-cycle latency | full width (DE2-115) |
| `n2d_perf_sdram` | `n2d_sdram_system`: the product `n2d_memory_controller` (arbiter plus the common `n2a_sdram_ctrl`) driving the common `n2a_sdram_model` | full width (DE2-115) |
| `n2d_perf_fast_half`, `n2d_perf_sdram_half` | as above | half width (DE10-Lite), `N2D_PERF_HALF_WIDTH` |

Full width is the IPC measurement. Pinned first-scene CRCs: framebuffer
`9da80229`, VGA `516470a8` (full); `78ddab35`, `98f7169b` (half, framebuffer
CRC over the doubled 320x200 image).

- **Frames.** A frame ends at a bank swap. The harness shadows both banks
  (writes go to the bank opposite `framebuffer_front_bank_o`; a half-width
  write fills both pixels of an even-x byte) and reports the new front bank's
  CRC, `wait` (swap request to swap) and `spin` (polling). The scene is taken
  after the scene marker with no swap pending and a fully written front bank.
  A framebuffer write while a swap is requested or pending ends the run with
  `write-while-swap-pending`.
- **SDRAM device model.** The common [`n2a_sdram_model`](../../../common/dv/sdram/dv-plan.md) with burst eight and the row-XOR-bank [bank map](../../rtl/memory/mas.md#sdram-controller), no fill: mode must be burst
  eight, sequential, CAS two; READ returns eight beats (beat k sampled on the
  (1+k)th edge); WRITE takes eight beats with per-beat DQM; per-bank open row;
  spacing, interruption, turnaround and row violations are fatal. A traced line read takes 10,
  9 or 11 clocks plus one arbiter cycle.

#### Command line

`src/nand2doom/scripts/perf/n2d_perf.py` builds or reuses the image, compiles and
runs the target, and writes `report.txt`, `summary.json` and VGA PPM/PNG
under `workdir/perf/<backend>/` (`<backend>-half/`).

| Option | Behavior |
| --- | --- |
| `--backend fast\|sdram`, `--geometry full\|half` | Target selection (default full). |
| `--variant deterministic\|production\|render-verification` | Default deterministic (instruction-count clock); production uses real time; the tool rejects a manifest of another variant. `summary.json` records `image_variant`, `performance_workload` and the CRC `oracle`. |
| `--frames N` | Gameplay frames; one VGA raster captured per frame plus the scene (`doom-scene-vga.ppm`, `doom-frame-NNN-vga.ppm`). |
| `--check-pins` | Legacy: only reference binary `b00113ee9f38aec8cfdeb1d6d24df4784a331c41b05c7d1391a6c8c36b750b98`, its ELF, full width, no input; with `--input`, `--fst` or more than two frames it exits 2 before building. |
| `--expect-scene-crc`, `--expect-scene-vga-crc` | Check one scene pair for either variant. |
| `--input EVENT` | Up to eight Set-2 events sent by UART Input between Reset-PC and Resume. |
| `--trace-cycles START:END` | Text trace of retirements, cache events and line traffic. |
| `--stream`, `--stream-start CYCLES` | `stream.bin.gz`: retired loads/stores (address, mask, PC, data), I-cache line changes, miss events, line requests/responses, swaps, with cycle and low 16 bits of retired count; layout in `n2d_perf_tb.sv`; starts at the scene marker. The [cache study](https://github.com/amichai-bd/nand2anything/blob/aeebe92/wiki/src/nand2doom/performance-roadmap.md#cache-study) converts it for the [cache model](https://github.com/amichai-bd/nand2anything/blob/aeebe92/wiki/tools/cache-model/SPEC.md). |
| `--fst START:END` | Full-width `n2d_perf_fast_fst`/`n2d_perf_sdram_fst`; `$dumpvars` from the first sampled cycle ≥START, run ends at ≥END (`$dumpon`/`$dumpoff` unsupported); `fst-window-not-reached` if the run ends first; ~60 kB per 500 cycles. |
| `--game-state` | Resolves `gametic`, local `maketic`, `ticdup`, `leveltime`, `prndindex`, `rndindex`, `fuzzpos` with `nm -S` and rebuilds them from retired stores (byte-masked BSS included); unobserved bytes fail; records under `result.states`. |

Verification throughput is `verification_fps_at_25mhz`, never ordinary or
physical FPS.

#### Render verification

```sh
python3 src/nand2doom/scripts/perf/n2d_perf.py --variant render-verification --backend fast --geometry full --frames 2
```

Writes to `workdir/perf/<backend>-render-verification/`, enables
state-qualified pins and state observation, and rejects input, other frame
counts and FST. The render oracle (`src/nand2doom/dv/software/render_oracle.json`)
owns the scene and two gameplay states, framebuffer/VGA CRCs, raw pixel
SHA-256 and provenance: every seven-word state, raw framebuffer and VGA PPM
must match. ELF symbols `G_Ticker`, `D_Display` and `singletics` with retired
PC and store witnesses require enabled mode, two ticker calls before the
first display, then one per display. Source/define hashes, ELF bytes and the
embedded identity must match the capture manifest. `--geometry half` uses the
half fixture (`src/nand2doom/dv/software/render_oracle_half.json`),
which owns only projected hashes and CRCs. Both require a zero swap-guard
count. The projection checker (`src/nand2doom/scripts/perf/n2d_render_projection.py`)
verifies the six archived full images, projects them (even columns, first VGA
pixel pair per four, black blanking) and checks tracked half expectations
before reading candidates; it has no update operation:

```sh
python3 src/nand2doom/scripts/perf/n2d_render_projection.py --baseline workdir/reference-full --output workdir/projected-half --candidate workdir/perf/fast-half-render-verification
```

Legacy deterministic pins (reference image, full width only; identical across
backends and core generations):

| Frame | Framebuffer CRC | VGA raster CRC |
| --- | --- | --- |
| First completed scene | `9da80229` | `516470a8` |
| Gameplay frame 1 | `53dfbd59` | `d4cec909` |
| Gameplay frame 2 | `a4c3aa79` | `855eb305` |

#### Reports and CPI classification

The harness counts every supported PMON event from `u_system.pmon_events`
([PMON](../../rtl/core/mas.md#performance-monitor)) with no firmware support.
`report.txt` has `pmon`, `cycles` and `split` lines for `scope=total` and each
swap interval (`scope=frame` or `scope=gameplay`), and a final `swap` line
(geometry, count, total `wait` and `spin`, guard violations, which fail).
`summary.json` leads with `overall_ipc`, `scene_ipc`, `gameplay_ipc`,
`gameplay_frame_ipc`, then `pmon_total` and `pmon_gameplay` (IPC, totals,
`cycle_percent`, `coverage_percent`, `stall_split`, hit ratios, cycles per
I-miss (IFETCH_WAIT less two per BRANCH_REDIRECT) and per D-miss, line-read
service, uncached wait per access), `swap_wait_cycles_per_frame`,
`swap_spin_cycles_per_frame`; raw values under `result.pmon`.

Every clock goes to the first matching class. A non-retiring clock is charged
to the stall that emptied its retirement slot: a memory hold one clock
earlier, an execute stall two earlier, fetch starvation three earlier.

| Class | Rule |
| --- | --- |
| `retire` | INSTRET (an instruction retired) |
| `divide` | MULDIV_HOLD two clocks earlier |
| `uncached_wait` | UNCACHED_WAIT one clock earlier: an accepted framebuffer or MMIO access awaiting its response |
| `dmiss_wait` | DATA_HOLD one clock earlier otherwise: a cacheable access awaiting its D-cache response |
| `load_use` | LOAD_USE_HOLD two clocks earlier |
| `mul_use` | MUL_USE_HOLD two clocks earlier |
| `data_admission` | DATA_ADMISSION_HOLD two clocks earlier |
| `serialize` | SYSTEM_SERIALIZE_HOLD two clocks earlier |
| `ifetch_wait` | IFETCH_WAIT three clocks earlier, including the refill bubbles after a taken branch or jump |
| `halted` | DEBUG_HALTED |
| `other` | none of the above; this is the unattributed remainder |

Coverage is 100% minus `other` (divide issue clocks, serialized commits,
traps). Occurrence events, MEM_READ_SERVICE, MEM_BACKPRESSURE,
MEM_MASTER_WAIT and write-buffer events 94/95 overlap and are reported as
totals only.

#### Stall split

Each `ifetch_wait` and `dmiss_wait` clock falls in exactly one split class
from the same delayed sample; each split must sum exactly to its parent in the
total and every interval, or the check fails.

| Class | Rule (first match) |
| --- | --- |
| `ifetch_predict` | IFETCH_PREDICT_BUBBLE (83): the one refill clock of a correct prediction |
| `ifetch_mispredict` | IFETCH_MISPREDICT_REFILL (84): the two refill clocks of a mispredict |
| `ifetch_redirect` | IFETCH_REDIRECT_REFILL (85): trap, MRET or resume refill |
| `ifetch_miss` | IFETCH_SUPPLY_WAIT (86): fetch owed or not accepted, i.e. an I-cache miss or fill |
| `ifetch_other` | none of these: no fetch owed or presented |
| `dmiss_service` | MEM_OWNER_DATA_READ (88): the D-cache's own demand line read, from acceptance through the response (SDRAM activate, column and burst) |
| `dmiss_fetch` | MEM_OWNER_FETCH (87): queued behind an I-cache line read |
| `dmiss_prefetch` | MEM_OWNER_DATA_PREFETCH (89): queued behind the D-cache's next-line prefetch |
| `dmiss_writeback` | MEM_OWNER_DATA_WRITE (90): queued behind a D-cache line write (victim writeback) |
| `dmiss_debug` | MEM_OWNER_DEBUG (91): queued behind the debug master |
| `dmiss_refresh` | MEM_BACKPRESSURE (33): the SDRAM controller refuses a request only for refresh (and initialization) |
| `dmiss_cache` | none of these: D-cache lookup, miss-to-request and install-to-response clocks with memory idle |

Framebuffer stores are `uncached_wait` and never queue a D-miss; the fast
backend has no refresh, so `dmiss_refresh` is zero there.

## Features

Spec section names the requirement in the [HAS](../../rtl/has.md#purpose).
Level is the target's level in `src/nand2doom/cfg/targets.json`.

| ID | Spec section | Stimulus | Checker | Coverage point | Test | Level |
|---|---|---|---|---|---|---|
| PLAT-F01 | PLAT-R2..R4, R6 | Preload while reset, UART Reset-PC/Resume, owned C image through the real core, caches, arbiter, fast SDRAM, decoder, framebuffer, palette, VGA and PS/2. A PS/2 `0x1c` make/release then the same pair via `CMD_INPUT`: four pops in order, digest in SDRAM, no debug-memory request. Ten-word signature via Peek (identity, advancing tick); Poke/readback of the input word, two framebuffer words and an inactive palette entry. Software swaps, waits, copies the front view into the back bank. I/D hits and misses; all 64,000 bytes; framebuffer write-stream CRC `0x8f603e6e` and displayed 800x525 RGB444 CRC `0xc1e70079`. | Retirement scoreboard, Peek signature, framebuffer write-stream and displayed RGB444 CRCs | none defined | `n2d_integrated_system` (36) | L1 |
| PLAT-F02 | PLAT-R3, R5 | 14-instruction program: front-view read retires; front-view store and a byte store to `0x2000_1009` are store access faults with no swap; word write 3 to `0x2000_1008` sets pending and swaps at the next blank. | Retirement and trap observation; swap state | none defined | `n2d_video_control` (37; 1,000,000 clocks) | L1 |
| PLAT-F03 | Serial load, PLAT-R6 | Real UART Load of the manifest; maintenance-error timeout, debug exclusion, automatic retry, coherent signature, Poke/readback; the same input and framebuffer result. | Retirement scoreboard, coherent signature, Peek readback | none defined | `n2d_c_runtime_system` (35) | L1 |
| PLAT-F04 | PLAT-R7 | Warning-clean compile of the synthesizable closure, without and with `SYNTHESIS`; registry unit-checked free of fast-memory and preload sources. | Warning-fatal compile; source registry unit check | none defined | `n2d_system_product`, `n2d_system_product_synthesis` | L0 |

The integrated and serial targets use Verilator 5.052, warning-fatal, 2,400,000
clocks and 480 s wall.

### Neutral ASIC core

The ASIC flow stayed in nand2anything, so these targets are not in this
repository's `targets.json`; they are kept as the contract
[`n2d_system`](../../rtl/has.md#padless-asic-core) serves.

| Target | Evidence |
| --- | --- |
| `n2d_asic_soc` | Warning-clean full source closure without PLL, inert TAP, simulation memory or external SDRAM model. |
| `n2d_asic_soc_integration` (59) | Reuses the unchanged DE2-115 external-pin/software oracle against the ASIC core through a DV-only board-shaped clock/pin adapter: UART load, CPU/cache execution, four-bank burst-eight SDRAM, masks and refresh, PS/2 delivery, framebuffer/palette swaps and VGA. Split DQ output-enable is checked against BFM drive. |
| `n2d_asic_soc_scan` (59) | Neutral scan signals reach the live keyboard endpoint inside the full SoC; status, delivery count and replay-safe sequence are checked. |
| `test_asic_core.py` | Required blocks and fixed full geometry, no vendor clock/TAP in the source closure, reproducible source hashes and fail-closed backend selection. |

The full-core targets initially select portable RAMs. Memory-mapped ASIC
runs require the same oracle with the qualified macro models, plus the
common memory contract matrix; a synthesis black box without verified
physical/timing views is not a qualified memory. No board behavior is
inferred from these simulations.

### Structure and macro compatibility checks

- Source moves: byte-compare moved files and validate source lists, header
  staging, links and plans; not hardware evidence.
- Macro adoption: expand the common definitions and compare token sequences
  with the pre-conversion RTL, normal and `SYNTHESIS`, covering synchronous
  and asynchronous processes, array writes, multiple writes and the debug
  response macro; keep assertion sampling and failure actions; empty macros
  must leave valid syntax.
- Run the smallest asserted compile/elaboration checks for the board closure
  and retained compositions, each within 30 s. Macro behavior belongs to the
  [common DV](https://github.com/amichai-bd/nand2anything/blob/aeebe92/wiki/src/common/clock_reset/DV.md).

## Coverage

No functional coverage points or code coverage numbers were recorded when the
plan came over from nand2anything, so every coverage point above is "none
defined". The pinned framebuffer and VGA CRCs cover every pixel of their
frames.

## Status

No open gaps with an issue are recorded for this plan.
