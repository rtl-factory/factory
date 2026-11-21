`default_nettype none
`include "src/common/rtl/pkg/macros.vh"

// Pixel mixer: at each emission edge the tile-layer color (background or
// window, already 0 when LCDC.0 is clear) meets the candidate object. The
// object is shown when LCDC.1 is set and either its priority attribute is
// clear or the tile color is 0; it takes OBP0 or OBP1 by attribute bit 4.
// Otherwise the tile color goes through BGP, except that a disabled
// background (LCDC.0 clear) is white, shade 0, regardless of BGP. LCDC.0,
// LCDC.1 and the palettes are sampled at each pixel emission. The shade and
// coordinates are registered with a one-cycle valid.
module n2m_ppu_mix (
  input  logic                   clk_i,
  input  logic                   reset_i,
  input  logic                   emit_i,
  input  logic [7:0]             pixel_x_i,
  input  n2m_ppu_pkg::t_line     ly_i,
  input  logic [1:0]             color_i,
  input  logic                   object_valid_i,
  input  logic [1:0]             object_color_i,
  input  logic [7:0]             object_attributes_i,
  input  logic [7:0]             lcdc_i,
  input  logic [7:0]             bgp_i,
  input  logic [7:0]             obp0_i,
  input  logic [7:0]             obp1_i,
  output logic                   pixel_valid_o,
  output logic [1:0]             pixel_shade_o,
  output logic [7:0]             pixel_x_o,
  output logic [7:0]             pixel_y_o
);
  import n2m_ppu_pkg::*;

  logic       object_shown, covered;
  logic [7:0] object_palette;
  logic [1:0] tile_shade, object_shade, shade;

  assign covered = object_attributes_i[ATTR_PRIORITY_BIT] && (color_i != 2'd0);
  assign object_shown = lcdc_i[LCDC_OBJ_ENABLE_BIT] && object_valid_i && !covered;
  assign object_palette = object_attributes_i[ATTR_PALETTE_BIT] ? obp1_i : obp0_i;
  assign tile_shade = lcdc_i[LCDC_BG_ENABLE_BIT] ? palette_shade(bgp_i, color_i) : 2'd0;
  assign object_shade = palette_shade(object_palette, object_color_i);
  assign shade = object_shown ? object_shade : tile_shade;

  `DFF_RST(pixel_valid_o, emit_i, clk_i, reset_i)
  `DFF_RST_EN(pixel_shade_o, shade, clk_i, emit_i, reset_i, 2'd0)
  `DFF_RST_EN(pixel_x_o, pixel_x_i, clk_i, emit_i, reset_i, 8'd0)
  `DFF_RST_EN(pixel_y_o, ly_i, clk_i, emit_i, reset_i, 8'd0)

  `ASSERT(object_candidate_opaque, clk_i, reset_i,
      !object_valid_i || (object_color_i != 2'd0))
  `ASSERT_KNOWN(mix_controls_known, clk_i, reset_i,
      {emit_i, lcdc_i, bgp_i, obp0_i, obp1_i})
endmodule
