# nand2doom indexed-framebuffer video architecture

!!! note "Ported, not yet in the standard template"
    Ported from nand2anything with paths rewritten (#78). Rearranging it into the [standard template](../../../../bkm/design/standard-docs.md) is #91.

`n2d_framebuffer_vga` presents a double-buffered 320x200 framebuffer of
eight-bit palette indices (stored at half horizontal resolution on the
DE10-Lite) and a programmable RGB444 palette, scanned out 2x on a 640x480 VGA
raster at the 25 MHz system clock. No character cells, glyphs or fonts. The
common `n2a_vga_timing` block owns only counters and sync timing; this block
owns geometry, storage, palette, scaling, borders and video control. The
[platform](../has.md#address-map) owns the windows; the
[DV plan](../../dv/video/dv-plan.md) owns verification.

## Requirements

| ID | Requirement |
| --- | --- |
| VID-R1 | Software draws the back bank while VGA scans the front bank; a swap takes effect only at the next vertical blank, so a displayed frame never tears. |
| VID-R2 | The CPU and host see one row-major 320x200 image, `address = y * 320 + x`, in a 64 KiB window; offsets 64,000..65,535 read zero and ignore writes. |
| VID-R3 | The framebuffer and palette ports are always ready and respond exactly one clock after every accepted request; stores never stall the core and take effect in program order with swaps. |
| VID-R4 | A palette commit swaps active and inactive banks only at vertical blank; a commit and swap armed on the same clock flip on the same edge. |
| VID-R5 | The display is black from reset until the first palette commit takes effect, including after a warm reset. |
| VID-R6 | Scanout uses only embedded RAM and never consumes SDRAM bandwidth. |

## Framebuffer and palette

| Geometry | Bank contents | Board | Embedded RAM |
| --- | --- | --- | --- |
| Full width | 320x200 indices per bank | DE2-115 | two 64,000-byte banks |
| Half width | 160x200 indices per bank | DE10-Lite | two 32,000-byte banks, the size of one full-width image |

The board top selects the geometry (`FULL_WIDTH`, from `n2d_system`
`FRAMEBUFFER_FULL_WIDTH`); the portable platform defaults to full width. At
half width a bank keeps only even-x pixels: a write to an odd-x byte is
dropped, an even-x byte (word bytes 0 and 2, each with its own strobe) stores
the pixel shown at `x` and `x + 1`, and a read of any byte returns the pixel
displayed there, so software always sees a doubled 320-wide image. The
[memory budget](../memory/mas.md#framebuffer-placement) selects half width
on the DE10-Lite.

### Interfaces

| Interface | Direction from video | Contract |
| --- | --- | --- |
| CPU/host framebuffer port | input/output | Word-aligned 16-bit byte offset, 32-bit data, four strobes, front-view select; one response next clock. Writes and ordinary reads use the back bank, read-first; a front-view request reads the scanned bank and never writes. |
| Bank control | input/output | One-clock swap request pulse; status front bank and swap pending. |
| Palette port | input/output | Eight-bit index, one response next clock; strobes 0 and 1 update the twelve stored bits, 2 and 3 none; readback names the inactive bank, or the active bank with read-only `palette_request_active_i` (the [UART memory endpoint](../io/mas.md#uart-memory-endpoint) view); `{20'b0, high, low}` defined only while `palette_response_valid_o`. |
| Palette control | input/output | `palette_commit_i` one-clock pulse; status active bank, commit pending, 32-bit frame sequence. |
| VGA | output | RGB444, active-low HSYNC and VSYNC. |
| Frame observation | output | Frame-start/end pulses and displayed-frame sequence for DV. |

Palette entries are 32-bit words `{20'b0, red[3:0], green[3:0], blue[3:0]}`.
Software writes all 256 inactive entries before each commit.

### Storage

- Each bank holds `LANES` byte RAMs of 16,384 entries indexed by
  `address[15:2]`: four lanes (bytes 0..3) at full width, two (bytes 0 and 2)
  at half width. Words ≥16,000 read zero and write nothing; an assertion
  checks word alignment.
- Each lane has two identical copies with identical writes: CPU copy
  `g_framebuffer[k].u_cpu_ram`, single-port `n2a_sync_ram`, and scan copy
  `g_framebuffer[k].u_scan_ram`, simple-dual-port `n2a_sdp_ram`, both
  16,384x8 ([common contracts](https://github.com/amichai-bd/nand2anything/blob/aeebe92/wiki/src/common/memory/HAS.md#same-clock-cycle-model));
  `k` holds bank `k / LANES`, lane `k % LANES`. Sixteen RAMs at full width,
  eight at half ([packing](../../fpga/de10_lite/board-spec.md#memory-implementation)).
- A lane is written for a back-bank write with its strobe set to a live
  word. The registered response selects the bank and lane `i` (full) or
  `i / 2` (half) per byte.
- The bank-control register holds the front bank and swap pending. A request
  is retained until horizontal 0 of line 480 (the first vertical-blank line),
  where the front bank toggles and pending clears; a request on that clock
  also toggles. A write accepted on the toggle clock goes to the old back
  bank, which becomes visible.
- Palette: two banks in `u_palette_low` (512x8, bits 7:0, strobe 0) and
  `u_palette_high` (512x4, bits 11:8, strobe 1), true-dual-port `n2a_tdp_ram`
  addressed `{bank, index}`. Port A (software) reads and writes
  `{~active_bank, index}`; port B reads `{active_bank, scan_index}` and never
  writes, so the ports never collide. Writes are disabled under reset. A
  commit is retained until horizontal 0 of line 480.
- Palette RAM is not reset (zero after configuration, old value after a warm
  reset). A `committed` flag, cleared by reset and set when a commit takes
  effect, forces image pixels black until then, so output equals black-reset
  banks for software that writes each entry before showing it.
- Control records are packed structs in `n2d_types_pkg`; the
  `FULL_WIDTH`-sized scan-lane select is its own zero-reset register.

## VGA timing and scaling

| Dimension | Active | Front porch | Active-low sync | Back porch | Total |
| --- | ---: | ---: | ---: | ---: | ---: |
| Horizontal | 640 | 16 | 96 | 48 | 800 |
| Vertical | 480 | 10 | 2 | 33 | 525 |

About 59.52 frames per second at 25 MHz; every rising edge advances one
position (no derived clock or enable). Source pixel `(x, y)` is the 2x2 square
at `(2*x, 40+2*y)`: a 640x400 image with black lines 0..39 and 440..479.
Porches, sync and borders drive zero; sync never stops.

The scan copy reads word `(((output_y - 40) >> 1) * 320 + (output_x >> 1)) >> 2`
every clock; a registered lane select (low two address bits full width, bit 1
half width) picks the byte. The front bank is never written, so there is no
collision or hold register. The pipeline is exactly two clocks:

1. synchronous indexed fetch from the front bank;
2. synchronous RGB444 lookup on palette port B at that index and the active
   bank.

Coordinates, visibility, syncs and frame events (and `committed`, with stage
2) are delayed to match. RGB is black unless inside the image rectangle with a
commit taken effect. The frame sequence increments when frame start enters
stage 2 and wraps.

## Reset and invariants

Synchronous active-high reset clears palette control (bank 0 active, nothing
pending, not committed), response state, front bank 0, swap pending, the scan
pipeline and the frame sequence, not storage. Outputs are black with idle
syncs while the pipeline refills. Assertions: the timing block's frame end
coincides with a line end; every framebuffer request is word aligned.

## Files

| Path | Role |
| --- | --- |
| `src/nand2doom/rtl/video/n2d_framebuffer_vga.sv` | Framebuffer, palette, scanout |
| `src/nand2doom/dv/video/test_n2d_framebuffer_vga.py` | Python DV |
| `src/nand2doom/cfg/targets.json` | `n2d_framebuffer_vga*` (full) and `n2d_framebuffer_vga_half*` (half) targets |
