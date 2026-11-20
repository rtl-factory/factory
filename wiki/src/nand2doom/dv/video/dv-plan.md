# nand2doom indexed-framebuffer video verification

!!! note "Ported, not yet in the standard template"
    Ported from nand2anything with paths rewritten (#78). Rearranging it into the [standard template](../../../../bkm/design/standard-docs.md) is #91.

## Strategy

Verilator and cocotb 2.1.0 at 25 MHz; every test runs in both geometries
(`n2d_framebuffer_vga*` with `FULL_WIDTH=1`, `n2d_framebuffer_vga_half*` with
`FULL_WIDTH=0`). A Python bank model holds each bank as the displayed
320x200 image (a half-width write stores bytes 0 and 2 and fills both pixels
each represents) and independently computes read-first responses, pattern
indices, RGB444, VGA segments, 2x scaling, borders and CRC32. Tests use only
public ports; output stays under `workdir/`. Smaller timings may speed
development but never replace the 800x525 gate.

## Test map

| Requirement | Target (seed 32) | Checks |
| --- | --- | --- |
| VID-R2, R3, R5 | `n2d_framebuffer_vga`, `_half` (128 cycles) | Reset front bank 0, nothing pending, black RGB; full-word and byte back-bank writes with read-first responses, half-width odd-byte drop and read doubling; last active word; zero-reading write-ignoring tail; a write on the front view changes nothing; inactive palette RGB444 write/read ignoring upper bits; commit and swap both pending before blank; a write while pending still targets the back bank. |
| VID-R1..R5 | `n2d_framebuffer_vga_frame`, `_half_frame` (2,300,000 cycles, 600 s) | See [actual frame](#actual-frame). |
| VID-R1, R4 | `n2d_framebuffer_vga_vblank`, `_half_vblank` (1,800,000 cycles) | Swap and commit armed together one clock before, on, and one clock after the first blank clock stay pending together and flip on the same edge: one clock later, that clock, one raster later. |
| System frames | [Platform DV](../platform/dv-plan.md) | Frames are taken at bank swaps; the front bank after a swap is the submitted frame. |

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

## Gaps

Packing and physical quality: [FPGA plan](../fpga/dv-plan.md); half-width render
oracle: [native rendering gate](../platform/dv-plan.md#render-verification).
