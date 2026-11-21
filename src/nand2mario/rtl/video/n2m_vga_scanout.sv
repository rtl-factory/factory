`default_nettype none
`include "src/common/rtl/pkg/macros.vh"

// 640x480 VGA scanout of one frame-store bank, scaled 3x and centered: the
// common n2a_vga_timing owns the raster coordinates, sync levels and the
// origin event; this wrapper owns the project policy. Each raster clock in
// the 480x432 image window reads the source pixel ((x - 80) / 3,
// (y - 24) / 3) through counters (no divider): a column counter advances
// every third image clock and a line base advances by one source row every
// third image line. The read returns one edge later and the palette lookup
// registers the RGB one edge after that, so RGB, sync and the frame-start
// pulse leave two clocks after the timing coordinate they describe, all
// delayed together. Blanking and the border are black; the image window
// shows the palette shade while the display is valid and white otherwise.
module n2m_vga_scanout (
  input  logic                           clk_i,
  input  logic                           reset_i,
  // Bank to scan and whether it holds a complete frame (else white).
  input  n2m_display_pkg::t_bank         display_bank_i,
  input  logic                           display_valid_i,
  // Frame store read port; the shade arrives one edge after the index.
  output n2m_display_pkg::t_bank         read_bank_o,
  output n2m_display_pkg::t_frame_index  read_index_o,
  input  n2m_display_pkg::t_shade        read_shade_i,
  // High in a cycle whose store read the scanout consumes (the image
  // window); in any other cycle another reader may take the read port.
  output logic                           read_busy_o,
  // Raster origin (0, 0) of the timing, unregistered, for the frame bridge.
  output logic                           vga_frame_start_o,
  // Output-aligned VGA signals: RGB 4:4:4, active-low syncs and the pulse
  // that accompanies the first clock of the output frame (coordinate (0, 0)).
  output logic [3:0]                     vga_red_o,
  output logic [3:0]                     vga_green_o,
  output logic [3:0]                     vga_blue_o,
  output logic                           vga_hsync_o,
  output logic                           vga_vsync_o,
  output logic                           pixel_start_o
);
  import n2m_display_pkg::*;

  // Raster coordinate widths: $clog2(800) and $clog2(525), the full VGA
  // line and frame of the common n2a_vga_timing defaults.
  localparam int unsigned H_COORD_WIDTH = 10;
  localparam int unsigned V_COORD_WIDTH = 10;
  localparam logic [H_COORD_WIDTH-1:0] IMAGE_LEFT_X   = H_COORD_WIDTH'(IMAGE_LEFT);
  localparam logic [H_COORD_WIDTH-1:0] IMAGE_RIGHT_X  = H_COORD_WIDTH'(IMAGE_RIGHT);
  localparam logic [V_COORD_WIDTH-1:0] IMAGE_TOP_Y    = V_COORD_WIDTH'(IMAGE_TOP);
  localparam logic [V_COORD_WIDTH-1:0] IMAGE_BOTTOM_Y = V_COORD_WIDTH'(IMAGE_BOTTOM);
  localparam logic [1:0] LAST_REPEAT = 2'(SCALE - 1);
  localparam t_frame_index ROW_STRIDE = t_frame_index'(FRAME_WIDTH);

  logic [H_COORD_WIDTH-1:0] timing_x;
  logic [V_COORD_WIDTH-1:0] timing_y;
  logic timing_visible, timing_hsync, timing_vsync;
  logic timing_line_start, timing_line_end, timing_frame_start, timing_frame_end;
  logic h_image, v_image, in_image;

  // Source address generation.
  localparam t_scanout_address ADDRESS_RESET = '{column: '0, repeat_x: '0, line_base: '0, repeat_y: '0};
  t_scanout_address address_q, address_d;

  // Two-stage output pipeline: the frame-store read, then the palette.
  localparam t_scanout_pipe PIPE_RESET = '{in_image_q: 1'b0, visible_q: 1'b0, hsync_q: 1'b1, vsync_q: 1'b1,
                                           frame_start_q: 1'b0, rgb_qq: BLANK_RGB, hsync_qq: 1'b1,
                                           vsync_qq: 1'b1, frame_start_qq: 1'b0};
  t_scanout_pipe pipe_q, pipe_d;

  n2a_vga_timing u_timing (
    .clk_i         (clk_i),
    .reset_i       (reset_i),
    .pixel_x_o     (timing_x),
    .pixel_y_o     (timing_y),
    .visible_o     (timing_visible),
    .hsync_o       (timing_hsync),
    .vsync_o       (timing_vsync),
    .line_start_o  (timing_line_start),
    .line_end_o    (timing_line_end),
    .frame_start_o (timing_frame_start),
    .frame_end_o   (timing_frame_end)
  );

  assign h_image = (timing_x >= IMAGE_LEFT_X) && (timing_x < IMAGE_RIGHT_X);
  assign v_image = (timing_y >= IMAGE_TOP_Y) && (timing_y < IMAGE_BOTTOM_Y);
  assign in_image = h_image && v_image;
  assign vga_frame_start_o = timing_frame_start;

  // Why: column and repeat counters are held at zero outside the image columns,
  // so they name source x 0 on the first image clock of every line; the
  // line base and its repeat counter are held at zero above the image and
  // advance at the end of every image line.
  always_comb begin
    address_d = address_q;
    if (!h_image) begin
      address_d.column = '0;
      address_d.repeat_x = '0;
    end else if (address_q.repeat_x == LAST_REPEAT) begin
      address_d.column = address_q.column + 8'd1;
      address_d.repeat_x = '0;
    end else begin
      address_d.repeat_x = address_q.repeat_x + 2'd1;
    end
    if (timing_y < IMAGE_TOP_Y) begin
      address_d.line_base = '0;
      address_d.repeat_y = '0;
    end else if (timing_line_end && v_image) begin
      if (address_q.repeat_y == LAST_REPEAT) begin
        address_d.line_base = address_q.line_base + ROW_STRIDE;
        address_d.repeat_y = '0;
      end else begin
        address_d.repeat_y = address_q.repeat_y + 2'd1;
      end
    end
  end

  `DFF_RST_VAL(address_q, address_d, clk_i, reset_i, ADDRESS_RESET)

  assign read_bank_o = display_bank_i;
  assign read_index_o = address_q.line_base + t_frame_index'(address_q.column);
  assign read_busy_o = in_image;

  // Why: the display bank and validity change only at the raster origin, hundreds
  // of clocks away from any image read, so the stage-2 choice may sample
  // display_valid_i directly.
  always_comb begin
    pipe_d.in_image_q = in_image;
    pipe_d.visible_q = timing_visible;
    pipe_d.hsync_q = timing_hsync;
    pipe_d.vsync_q = timing_vsync;
    pipe_d.frame_start_q = timing_frame_start;
    pipe_d.rgb_qq = BLANK_RGB;
    if (pipe_q.in_image_q) begin
      pipe_d.rgb_qq = shade_rgb(display_valid_i ? read_shade_i : WHITE_SHADE);
    end else if (pipe_q.visible_q) begin
      pipe_d.rgb_qq = BORDER_RGB;
    end
    pipe_d.hsync_qq = pipe_q.hsync_q;
    pipe_d.vsync_qq = pipe_q.vsync_q;
    pipe_d.frame_start_qq = pipe_q.frame_start_q;
  end

  `DFF_RST_VAL(pipe_q, pipe_d, clk_i, reset_i, PIPE_RESET)

  assign vga_red_o = pipe_q.rgb_qq.red;
  assign vga_green_o = pipe_q.rgb_qq.green;
  assign vga_blue_o = pipe_q.rgb_qq.blue;
  assign vga_hsync_o = pipe_q.hsync_qq;
  assign vga_vsync_o = pipe_q.vsync_qq;
  assign pixel_start_o = pipe_q.frame_start_qq;

  `ASSERT(frame_end_on_line_end, clk_i, reset_i, !timing_frame_end || timing_line_end)
  `ASSERT(origin_on_line_start, clk_i, reset_i, !timing_frame_start || timing_line_start)
  `ASSERT(image_read_in_frame, clk_i, reset_i,
      !in_image || (read_index_o < t_frame_index'(FRAME_PIXELS)))
  `ASSERT(image_column_in_row, clk_i, reset_i,
      !in_image || (address_q.column < 8'(FRAME_WIDTH)))
endmodule
