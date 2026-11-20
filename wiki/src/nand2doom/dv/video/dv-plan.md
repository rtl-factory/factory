# video DV plan

## Scope

The video block `n2d_framebuffer_vga` in both geometries, checked against the
[video MAS](../../rtl/video/mas.md). Frames of the whole system are verified
in the [platform DV plan](../platform/dv-plan.md); packing and physical
quality in the [FPGA DV plan](../fpga/dv-plan.md).

## Testbench

Verilator and cocotb 2.1.0 at 25 MHz; every test runs in both geometries
(`n2d_framebuffer_vga*` with `FULL_WIDTH=1`, `n2d_framebuffer_vga_half*` with
`FULL_WIDTH=0`). A Python bank model holds each bank as the displayed
320x200 image (a half-width write stores bytes 0 and 2 and fills both pixels
each represents) and independently computes read-first responses, pattern
indices, RGB444, VGA segments, 2x scaling, borders and CRC32. Tests use only
public ports; output stays under `workdir/`. Smaller timings may speed
development but never replace the 800x525 gate.

## Features

Spec section names the requirement in the [video MAS](../../rtl/video/mas.md#requirements).
Level is the target's level in `src/nand2doom/cfg/targets.json`.

| ID | Spec section | Stimulus | Checker | Coverage point | Test | Level |
|---|---|---|---|---|---|---|
| VID-F01 | VID-R2, R3, R5 | Reset front bank 0, nothing pending, black RGB; full-word and byte back-bank writes with read-first responses, half-width odd-byte drop and read doubling; last active word; zero-reading write-ignoring tail; a write on the front view changes nothing; inactive palette RGB444 write/read ignoring upper bits; commit and swap both pending before blank; a write while pending still targets the back bank. | Python bank model | none defined | `n2d_framebuffer_vga`, `_half` (128 cycles) (seed 32) | L0 |
| VID-F02 | VID-R1..R5 | See [actual frame](#actual-frame). | Python bank model; CRC32 over every 12-bit value of the frame | none defined | `n2d_framebuffer_vga_frame`, `_half_frame` (2,300,000 cycles, 600 s) (seed 32) | L1 |
| VID-F03 | VID-R1, R4 | Swap and commit armed together one clock before, on, and one clock after the first blank clock stay pending together and flip on the same edge: one clock later, that clock, one raster later. | Python bank model | none defined | `n2d_framebuffer_vga_vblank`, `_half_vblank` (1,800,000 cycles) (seed 32) | L1 |
| VID-F04 | System frames | Frames are taken at bank swaps; the front bank after a swap is the submitted frame. | Frame monitors of the platform DV plan | none defined | [Platform DV](../platform/dv-plan.md) | see the platform DV plan |

### Actual frame

- All 16,000 back-bank words through the CPU port with index
  `(3*x + 5*y) mod 256`, and all 256 inactive palette entries with
  red=`i[7:4]`, green=`i[3:0]`, blue=red XOR green.
- The last word is rewritten on the swap-request clock (with a commit) and
  word zero twice while pending; the swap and palette flip exactly at public
  `(799, 479)` with pending held every clock before; front-view reads show all
  those writes.
- One real 800x525 frame checks coordinates, visibility, active-low syncs,
  frame events, black borders, 2x mapping and RGB444 every cycle. CRC32 over
  each 12-bit value as two big-endian bytes for all 420,000 clocks:
  `0xc3aeaaef` full width, `0x39e9750f` half width.
- Row, lane and image-end words of the new back bank are rewritten: back
  reads return new values, front-view reads old, a second frame reproduces
  the CRC, and a final swap exposes the rewritten bank.
- Inactive palette bank 0 is written white and reset pulsed: with bank 0
  active and no commit, the first two image lines (1,280 pixels) stay black.
  Undefined entries are never read.

## Coverage

No functional coverage points or code coverage numbers were recorded when the
plan came over from nand2anything, so every coverage point above is "none
defined"; the actual-frame CRCs cover every pixel and sync sample of one full
800x525 frame in each geometry.

## Status

Packing and physical quality: [FPGA plan](../fpga/dv-plan.md); half-width render
oracle: [native rendering gate](../platform/dv-plan.md#render-verification).
