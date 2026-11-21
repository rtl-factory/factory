`default_nettype none
`include "src/common/rtl/pkg/macros.vh"

// DMG picture processor: composes the LCD register file, the mode/LY
// timing, the window state, the object scan/fetch/select, the tile-layer
// renderer and the pixel mixer. It consumes the dot enable from the
// timebase, answers its LCD registers on the peripheral port, reads VRAM
// and OAM through one read port each into the memory map's stores, tells
// the memory map when CPU VRAM/OAM access is blocked, emits the 160x144
// two-bit pixel stream with line/frame markers and raises the VBlank and
// STAT interrupt pulses. The VRAM port is shared: the object row fetch owns
// it at the start of mode 3, the tile fetches afterwards.
module n2m_ppu (
  input  logic                                clk_i,
  input  logic                                reset_i,
  input  logic                                dot_enable_i,
  // LCD registers on the peripheral port.
  input  n2m_interfaces_pkg::bus_request_t    io_request_i,
  output n2m_interfaces_pkg::bus_response_t   io_response_o,
  output logic                                io_selected_o,
  // VRAM read port into the memory map's VRAM store.
  output logic                                vram_read_o,
  output logic [12:0]                         vram_address_o,
  input  logic                                vram_valid_i,
  input  logic [7:0]                          vram_data_i,
  // OAM read port into the memory map's OAM store.
  output logic                                oam_read_o,
  output logic [7:0]                          oam_address_o,
  input  logic                                oam_valid_i,
  input  logic [7:0]                          oam_data_i,
  // CPU access blocking levels consumed by the memory map.
  output logic                                vram_blocked_o,
  output logic                                oam_blocked_o,
  // Interrupt sources: one-cycle pulses for the IF register.
  output logic                                vblank_irq_o,
  output logic                                stat_irq_o,
  // Pixel stream and markers.
  output logic                                pixel_valid_o,
  output logic [1:0]                          pixel_shade_o,
  output logic [7:0]                          pixel_x_o,
  output logic [7:0]                          pixel_y_o,
  output logic                                line_start_o,
  output logic                                frame_start_o,
  // LCD enable level (LCDC.7) for the frame bridge's display policy.
  output logic                                lcd_enable_o,
  // Observation of the raster state.
  output n2m_ppu_pkg::dot_t                   dot_o,
  output n2m_ppu_pkg::line_t                  ly_o,
  output n2m_ppu_pkg::mode_t                  mode_o
);
  import n2m_ppu_pkg::*;

  logic [7:0] lcdc, scy, scx, lyc, bgp, obp0, obp1, wy, wx;
  logic [3:0] stat_enables;
  logic       lcd_enable, coincidence;
  logic [2:0] scx_fine, fine_scroll;
  logic [4:0] scx_coarse;
  dot_t       dot;
  line_t      ly, ly_read;
  mode_t      mode;
  logic       window_active;
  logic [7:0] window_first_pixel, window_wx, window_line;
  logic       tiles_read, objects_read, objects_fetch_busy;
  logic [12:0] tiles_address, objects_address;
  logic       emit, window_drawn;
  logic [7:0] pixel_x;
  logic [1:0] tile_color;
  logic       object_valid;
  logic [1:0] object_color;
  logic [7:0] object_attributes;

  assign lcd_enable = lcdc[LCDC_ENABLE_BIT];
  assign scx_fine = scx[2:0];
  assign scx_coarse = scx[7:3];

  n2m_ppu_registers u_registers (
    .clk_i          (clk_i),
    .reset_i        (reset_i),
    .io_request_i   (io_request_i),
    .io_response_o  (io_response_o),
    .io_selected_o  (io_selected_o),
    .mode_i         (mode),
    .coincidence_i  (coincidence),
    .ly_i           (ly_read),
    .lcdc_o         (lcdc),
    .stat_enables_o (stat_enables),
    .scy_o          (scy),
    .scx_o          (scx),
    .lyc_o          (lyc),
    .bgp_o          (bgp),
    .obp0_o         (obp0),
    .obp1_o         (obp1),
    .wy_o           (wy),
    .wx_o           (wx)
  );

  n2m_ppu_timing u_timing (
    .clk_i          (clk_i),
    .reset_i        (reset_i),
    .dot_enable_i   (dot_enable_i),
    .lcd_enable_i   (lcd_enable),
    .scx_fine_i     (scx_fine),
    .lyc_i          (lyc),
    .stat_enables_i (stat_enables),
    .dot_o          (dot),
    .ly_o           (ly),
    .ly_read_o      (ly_read),
    .mode_o         (mode),
    .fine_scroll_o  (fine_scroll),
    .coincidence_o  (coincidence),
    .vblank_irq_o   (vblank_irq_o),
    .stat_irq_o     (stat_irq_o),
    .vram_blocked_o (vram_blocked_o),
    .oam_blocked_o  (oam_blocked_o)
  );

  n2m_ppu_window u_window (
    .clk_i          (clk_i),
    .reset_i        (reset_i),
    .dot_enable_i   (dot_enable_i),
    .lcd_enable_i   (lcd_enable),
    .dot_i          (dot),
    .ly_i           (ly),
    .lcdc_i         (lcdc),
    .wy_i           (wy),
    .wx_i           (wx),
    .pixel_drawn_i  (window_drawn),
    .active_o       (window_active),
    .first_pixel_o  (window_first_pixel),
    .wx_o           (window_wx),
    .line_o         (window_line)
  );

  n2m_ppu_objects u_objects (
    .clk_i                  (clk_i),
    .reset_i                (reset_i),
    .dot_enable_i           (dot_enable_i),
    .lcd_enable_i           (lcd_enable),
    .dot_i                  (dot),
    .ly_i                   (ly),
    .lcdc_i                 (lcdc),
    .oam_read_o             (oam_read_o),
    .oam_address_o          (oam_address_o),
    .oam_valid_i            (oam_valid_i),
    .oam_data_i             (oam_data_i),
    .vram_read_o            (objects_read),
    .vram_address_o         (objects_address),
    .vram_valid_i           (vram_valid_i),
    .vram_data_i            (vram_data_i),
    .fetch_busy_o           (objects_fetch_busy),
    .pixel_x_i              (pixel_x),
    .candidate_valid_o      (object_valid),
    .candidate_color_o      (object_color),
    .candidate_attributes_o (object_attributes)
  );

  n2m_ppu_background u_background (
    .clk_i                (clk_i),
    .reset_i              (reset_i),
    .dot_enable_i         (dot_enable_i),
    .lcd_enable_i         (lcd_enable),
    .dot_i                (dot),
    .ly_i                 (ly),
    .fine_scroll_i        (fine_scroll),
    .lcdc_i               (lcdc),
    .scy_i                (scy),
    .scx_coarse_i         (scx_coarse),
    .window_active_i      (window_active),
    .window_first_pixel_i (window_first_pixel),
    .window_wx_i          (window_wx),
    .window_line_i        (window_line),
    .vram_read_o          (tiles_read),
    .vram_address_o       (tiles_address),
    .vram_valid_i         (vram_valid_i),
    .vram_data_i          (vram_data_i),
    .emit_o               (emit),
    .pixel_x_o            (pixel_x),
    .color_o              (tile_color),
    .window_drawn_o       (window_drawn)
  );

  n2m_ppu_mix u_mix (
    .clk_i               (clk_i),
    .reset_i             (reset_i),
    .emit_i              (emit),
    .pixel_x_i           (pixel_x),
    .ly_i                (ly),
    .color_i             (tile_color),
    .object_valid_i      (object_valid),
    .object_color_i      (object_color),
    .object_attributes_i (object_attributes),
    .lcdc_i              (lcdc),
    .bgp_i               (bgp),
    .obp0_i              (obp0),
    .obp1_i              (obp1),
    .pixel_valid_o       (pixel_valid_o),
    .pixel_shade_o       (pixel_shade_o),
    .pixel_x_o           (pixel_x_o),
    .pixel_y_o           (pixel_y_o)
  );

  // Why: the PPU has one VRAM read port, so the object row fetch owns it while
  // busy and the tile fetch otherwise; the schedule keeps the two apart.
  assign vram_read_o = objects_read || tiles_read;
  assign vram_address_o = objects_fetch_busy ? objects_address : tiles_address;

  assign lcd_enable_o = lcd_enable;
  assign line_start_o = pixel_valid_o && (pixel_x_o == 8'd0);
  assign frame_start_o = line_start_o && (pixel_y_o == 8'd0);
  assign dot_o = dot;
  assign ly_o = ly;
  assign mode_o = mode;

  `N2A_ASSERT(vram_reads_in_mode3, clk_i, reset_i, !vram_read_o || vram_blocked_o)
  `N2A_ASSERT(oam_reads_while_blocked, clk_i, reset_i, !oam_read_o || oam_blocked_o)
  `N2A_ASSERT(one_vram_reader, clk_i, reset_i, !(objects_read && tiles_read))
  `N2A_ASSERT(pixels_in_frame, clk_i, reset_i,
      !pixel_valid_o || ((pixel_x_o < 8'd160) && (pixel_y_o < 8'd144)))
endmodule
