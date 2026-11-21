`default_nettype none
`include "src/common/rtl/pkg/macros.vh"

// Window state of the DMG PPU: the per-frame WY latch, the per-line window
// decision and the window line counter. The window is enabled for a frame
// once LCDC.5 is set and LY equals WY at the start of a visible line's mode 2
// (the edge ending dot 0); that latch clears at the frame wrap and with the
// LCD off. At the end of dot 80 of every visible line (with the fine scroll)
// the line decision is latched: the window is drawn on this line when LCDC.5
// is set, the WY latch is set and WX is at most 166, from pixel WX - 7 (pixel
// 0 for WX below 7, whose window columns start at 7 - WX). The window line
// counter advances at the end of each line on which a window pixel was
// actually drawn (the renderer reports each one; LCDC.0 clear draws none)
// and resets at the frame wrap, so hidden lines do not consume window rows.
module n2m_ppu_window (
    input  logic                   clk_i,
    input  logic                   reset_i,
    input  logic                   dot_enable_i,
    input  logic                   lcd_enable_i,
    input  n2m_ppu_pkg::t_dot      dot_i,
    input  n2m_ppu_pkg::t_line     ly_i,
    input  logic [7:0]             lcdc_i,
    input  logic [7:0]             wy_i,
    input  logic [7:0]             wx_i,
    // A window pixel is being drawn at this edge (from the renderer).
    input  logic                   pixel_drawn_i,
    // Line decision for the tile renderer, stable from the end of dot 80.
    output logic                   active_o,
    output logic [7:0]             first_pixel_o,
    output logic [7:0]             wx_o,
    output logic [7:0]             line_o
);
    import n2m_ppu_pkg::*;

    logic       visible_line, line_edge, frame_wrap, line_start, decide;
    logic       wy_match, wy_hit_q, wy_hit_d;
    logic       active_d, active_q;
    logic [7:0] first_pixel_d, first_pixel_q, wx_q;
    logic       drawn_d, drawn_q;
    logic [7:0] line_d, line_q;
    logic       window_enable;

    assign window_enable = lcdc_i[LCDC_WINDOW_BIT];
    assign visible_line = ly_i < t_line'(VISIBLE_LINES);
    assign line_edge = dot_enable_i && lcd_enable_i && (dot_i == t_dot'(DOTS_PER_LINE - 1));
    assign frame_wrap = line_edge && (ly_i == t_line'(LINES_PER_FRAME - 1));
    assign line_start = dot_enable_i && lcd_enable_i && visible_line && (dot_i == t_dot'(0));
    assign decide = dot_enable_i && lcd_enable_i && visible_line && (dot_i == t_dot'(MODE2_DOTS));

    // Ref: Pan Docs "Window": the WY latch sets at the start of a visible line
    // while the window is enabled and LY equals WY; it clears at the frame wrap
    // and while the LCD is off.
    assign wy_match = window_enable && (ly_i == wy_i);
    always_comb begin
        wy_hit_d = wy_hit_q;
        if (line_start && wy_match) wy_hit_d = 1'b1;
        if (frame_wrap || !lcd_enable_i) wy_hit_d = 1'b0;
    end
    `DFF_RST(wy_hit_q, wy_hit_d, clk_i, reset_i)

    // Why: the window decision is taken once, at the end of dot 80, so a
    // mid-line WX write cannot start the window part-way through the line.
    assign active_d = window_enable && wy_hit_q && (wx_i <= 8'(WINDOW_LAST_WX));
    assign first_pixel_d = (wx_i > 8'd7) ? (wx_i - 8'd7) : 8'd0;
    `DFF_RST_EN(active_q, active_d, clk_i, decide, reset_i, 1'b0)
    `DFF_RST_EN(first_pixel_q, first_pixel_d, clk_i, decide, reset_i, 8'd0)
    `DFF_RST_EN(wx_q, wx_i, clk_i, decide, reset_i, 8'd0)

    // Ref: Pan Docs "Window": the window line counter advances after each line on
    // which a window pixel was drawn. Why: the drawn flag clears at the line end
    // only after the count has used it, so a window pixel late in the line still
    // advances the counter.
    always_comb begin
        drawn_d = drawn_q || pixel_drawn_i;
        if (line_edge || !lcd_enable_i) drawn_d = 1'b0;
    end
    `DFF_RST(drawn_q, drawn_d, clk_i, reset_i)
    always_comb begin
        line_d = line_q;
        if (line_edge && visible_line && drawn_q) line_d = line_q + 8'd1;
        if (frame_wrap || !lcd_enable_i) line_d = 8'd0;
    end
    `DFF_RST(line_q, line_d, clk_i, reset_i)

    assign active_o = active_q && lcd_enable_i;
    assign first_pixel_o = first_pixel_q;
    assign wx_o = wx_q;
    assign line_o = line_q;

    `ASSERT(window_first_pixel_in_line, clk_i, reset_i,
        !active_q || (first_pixel_q < 8'(VISIBLE_PIXELS)))
    `ASSERT(drawn_only_when_active, clk_i, reset_i, !pixel_drawn_i || active_q)
    `ASSERT_KNOWN(window_controls_known, clk_i, reset_i,
        {dot_enable_i, lcd_enable_i, lcdc_i, wy_i, wx_i, pixel_drawn_i})
endmodule
