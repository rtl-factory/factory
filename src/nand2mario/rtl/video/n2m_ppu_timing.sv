`default_nettype none
`include "src/common/rtl/pkg/macros.vh"

// DMG LCD timing: dot and line counters, the STAT mode, LY, the LY=LYC
// coincidence, the STAT interrupt line edge, the VBlank entry pulse and the
// CPU access blocking levels. One dot advances per dot_enable_i while LCDC.7
// is set; with the LCD off the counters hold line 0, dot 0 in mode 0. Mode 3
// of a visible line lasts 172 dots plus the SCX[2:0] value latched at the end
// of dot 80 (the fine-scroll penalty); the renderer consumes that latch.
module n2m_ppu_timing (
  input  logic                   clk_i,
  input  logic                   reset_i,
  input  logic                   dot_enable_i,
  input  logic                   lcd_enable_i,
  input  logic [2:0]             scx_fine_i,
  input  logic [7:0]             lyc_i,
  input  logic [3:0]             stat_enables_i,
  output n2m_ppu_pkg::dot_t      dot_o,
  output n2m_ppu_pkg::line_t     ly_o,
  output n2m_ppu_pkg::line_t     ly_read_o,
  output n2m_ppu_pkg::mode_t     mode_o,
  output logic [2:0]             fine_scroll_o,
  output logic                   coincidence_o,
  output logic                   vblank_irq_o,
  output logic                   stat_irq_o,
  output logic                   vram_blocked_o,
  output logic                   oam_blocked_o
);
  import n2m_ppu_pkg::*;

  dot_t        dot_q, dot_d;
  line_t       ly_q, ly_d;
  logic [2:0]  fine_q;
  logic        latch_fine;
  logic        visible_line, last_dot, last_line;
  logic [8:0]  transfer_end;
  mode_t       mode;
  logic        vblank_entry_d, vblank_entry_q;
  logic        coincidence_live, coincidence_retained_q, coincidence;
  logic        mode0_source, mode1_source, mode2_source, lyc_source;
  logic        stat_line, stat_line_q;

  assign visible_line = ly_q < line_t'(VISIBLE_LINES);
  assign last_dot = dot_q == dot_t'(DOTS_PER_LINE - 1);
  assign last_line = ly_q == line_t'(LINES_PER_FRAME - 1);

  // Counters: LCD off forces line 0, dot 0 on the next edge; otherwise one
  // dot per enable, wrapping 455 -> 0 with the line and 153 -> 0 with the frame.
  always_comb begin
    dot_d = dot_q;
    ly_d = ly_q;
    if (!lcd_enable_i) begin
      dot_d = '0;
      ly_d = '0;
    end else if (dot_enable_i) begin
      if (last_dot) begin
        dot_d = '0;
        ly_d = last_line ? '0 : ly_q + line_t'(1);
      end else begin
        dot_d = dot_q + dot_t'(1);
      end
    end
  end
  `N2A_DFF_RST(dot_q, dot_d, clk_i, reset_i)
  `N2A_DFF_RST(ly_q, ly_d, clk_i, reset_i)

  // Fine scroll of the current line: SCX[2:0] sampled at the end of dot 80.
  assign latch_fine = dot_enable_i && lcd_enable_i && visible_line &&
                      (dot_q == dot_t'(MODE2_DOTS));
  `N2A_DFF_RST_EN(fine_q, scx_fine_i, clk_i, latch_fine, reset_i, 3'd0)
  assign fine_scroll_o = fine_q;

  // Mode from the counters: the transfer ends at dot 252 + fine (exclusive).
  // STAT reports this same mode; the blocking levels follow it.
  assign transfer_end = 9'(MODE2_DOTS + MODE3_BASE_DOTS) + 9'(fine_q);
  always_comb begin
    mode = MODE_HBLANK;
    if (lcd_enable_i) begin
      if (!visible_line) mode = MODE_VBLANK;
      else if (dot_q < dot_t'(MODE2_DOTS)) mode = MODE_OAM_SCAN;
      else if (dot_q < transfer_end) mode = MODE_TRANSFER;
    end
  end

  // VBlank entry: one system cycle starting at the edge where LY becomes 144.
  assign vblank_entry_d = dot_enable_i && lcd_enable_i && last_dot &&
                          (ly_q == line_t'(VISIBLE_LINES - 1));
  `N2A_DFF_RST(vblank_entry_q, vblank_entry_d, clk_i, reset_i)

  // LY=LYC: live while the LCD runs, retained across LCD off.
  assign coincidence_live = ly_q == lyc_i;
  assign coincidence = lcd_enable_i ? coincidence_live : coincidence_retained_q;
  `N2A_DFF_RST(coincidence_retained_q, coincidence, clk_i, reset_i)

  // STAT line: the OR of the enabled sources; the interrupt is its rising
  // edge, sampled every system cycle. The mode 2 source also covers the
  // VBlank entry cycle (documented DMG behavior); mode sources need the LCD on.
  assign mode0_source = stat_enables_i[STAT_MODE0_ENABLE] && (mode == MODE_HBLANK) &&
                        !(visible_line && dot_q < dot_t'(transfer_end + 9'd3));
  assign mode1_source = stat_enables_i[STAT_MODE1_ENABLE] && (mode == MODE_VBLANK);
  assign mode2_source = stat_enables_i[STAT_MODE2_ENABLE] &&
                        ((mode == MODE_OAM_SCAN) || vblank_entry_q);
  assign lyc_source = stat_enables_i[STAT_LYC_ENABLE] && coincidence;
  assign stat_line = (lcd_enable_i && (mode0_source || mode1_source || mode2_source)) ||
                     lyc_source;
  `N2A_DFF_RST(stat_line_q, stat_line, clk_i, reset_i)

  assign dot_o = dot_q;
  assign ly_o = ly_q;
  // The CPU-readable LY advances at dot 452, four dots before the raster
  // line, and wraps 153 -> 0 there; LYC, VBlank and the renderer use ly_q.
  assign ly_read_o = (dot_q >= dot_t'(LY_READ_DOT)) ? (last_line ? '0 : ly_q + line_t'(1)) : ly_q;
  assign mode_o = mode;
  assign coincidence_o = coincidence;
  assign vblank_irq_o = vblank_entry_q;
  assign stat_irq_o = stat_line && !stat_line_q;
  assign vram_blocked_o = lcd_enable_i && (mode == MODE_TRANSFER);
  assign oam_blocked_o = lcd_enable_i &&
                         ((mode == MODE_OAM_SCAN) || (mode == MODE_TRANSFER));

  `N2A_ASSERT(dot_in_range, clk_i, reset_i, dot_q < dot_t'(DOTS_PER_LINE))
  `N2A_ASSERT(line_in_range, clk_i, reset_i, ly_q < line_t'(LINES_PER_FRAME))
  // One edge after LCDC.7 falls the counters are back at the origin.
  `N2A_ASSERT(lcd_off_holds_origin, clk_i, reset_i,
      lcd_enable_i || $past(lcd_enable_i) || ((dot_q == '0) && (ly_q == '0)))
  `N2A_ASSERT_KNOWN(timing_controls_known, clk_i, reset_i,
      {dot_enable_i, lcd_enable_i, scx_fine_i, lyc_i, stat_enables_i})
endmodule
