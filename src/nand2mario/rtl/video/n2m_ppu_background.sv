`default_nettype none
`include "src/common/rtl/pkg/macros.vh"

// Tile-layer renderer for one visible line: the background, and the window
// from its first pixel on. One fetch machine serves both layers. A
// background tile fetch k (k = 0..20) is launched at the dot-enable edge
// that ends dot 91 + 8k, only while the tile has a visible pixel left of
// the window; a window tile fetch is launched at the edge that ends dot
// 91 + SCX[2:0] + p for every pixel p that starts a window tile (the first
// window pixel, then every eighth window column). Each fetch reads the map
// byte, low plane and high plane from VRAM in the following four system
// cycles, so the tile row is ready before the next dot ends; the schedule
// never launches two fetches in one dot because the last background tile
// ends before the first window pixel. Pixel x is emitted at the edge that
// ends dot 92 + SCX[2:0] + x: a background pixel uses tile column
// (dot - 92) mod 8, a window pixel column (x + 7 - WX) mod 8. SCY, coarse
// SCX, the window line and the LCDC map/data selects are sampled at each
// fetch launch; LCDC.0 at each pixel emission. A disabled background
// (LCDC.0 clear) gives color 0 for both layers (DMG behavior).
module n2m_ppu_background (
  input  logic                   clk_i,
  input  logic                   reset_i,
  input  logic                   dot_enable_i,
  input  logic                   lcd_enable_i,
  input  n2m_ppu_pkg::t_dot      dot_i,
  input  n2m_ppu_pkg::t_line     ly_i,
  input  logic [2:0]             fine_scroll_i,
  input  logic [7:0]             lcdc_i,
  input  logic [7:0]             scy_i,
  input  logic [4:0]             scx_coarse_i,
  // Window decision of the line from the window block.
  input  logic                   window_active_i,
  input  logic [7:0]             window_first_pixel_i,
  input  logic [7:0]             window_wx_i,
  input  logic [7:0]             window_line_i,
  // VRAM read port: one registered read per cycle, data valid the next cycle.
  output logic                   vram_read_o,
  output logic [12:0]            vram_address_o,
  input  logic                   vram_valid_i,
  input  logic [7:0]             vram_data_i,
  // Emission of one pixel at this edge: its x and the layer color, and
  // whether it is a window pixel drawn with the tile layers enabled.
  output logic                   emit_o,
  output logic [7:0]             pixel_x_o,
  output logic [1:0]             color_o,
  output logic                   window_drawn_o
);
  import n2m_ppu_pkg::*;

  t_background_fetch_state state_q, state_d;
  logic         visible_line, fetch_busy;
  logic         background_dot, background_needed, background_launch;
  logic         window_dot, window_boundary, window_launch, fetch_launch;
  logic [7:0]   fetch_offset;
  logic [8:0]   window_pixel, pixel_offset;
  logic [2:0]   fetch_phase;
  logic [4:0]   tile_index, last_tile;
  logic [8:0]   tile_first_pixel, window_cover;
  logic [7:0]   background_y, launch_column;
  logic [4:0]   map_column;
  logic [12:0]  map_address_d, map_address_q;
  logic [2:0]   tile_row_q, tile_row_d;
  logic         unsigned_ids_q, unsigned_ids_d;
  logic [7:0]   tile_q, row_low_q, row_high_q;
  logic         tile_capture, low_capture, high_capture;
  logic [12:0]  low_address, high_address;
  logic         emit, window_pixel_now;
  logic [7:0]   pixel_x;
  logic [2:0]   background_column, window_column, column;
  logic [1:0]   raw_color;

  assign visible_line = ly_i < t_line'(VISIBLE_LINES);

  // Why: tiles launch every eight dots from dot 91 (91, 99, ..., 251) so each
  // tile's bytes are captured before its first pixel; only tiles with a
  // visible pixel (tile 20 exists only with a nonzero fine scroll) left of the
  // window's first pixel launch.
  assign fetch_offset = 8'(dot_i - t_dot'(FETCH_DOT));
  assign tile_index = fetch_offset[7:3];
  assign fetch_phase = fetch_offset[2:0];
  assign last_tile = 5'((9'd159 + 9'(fine_scroll_i)) >> 3);
  assign background_dot = (dot_i >= t_dot'(FETCH_DOT)) && (dot_i <= t_dot'(LAST_FETCH_DOT)) &&
                          (fetch_phase == 3'd0) && (tile_index <= last_tile);
  assign tile_first_pixel = {1'b0, tile_index, 3'd0};
  assign window_cover = 9'(window_first_pixel_i) + 9'(fine_scroll_i);
  assign background_needed = !window_active_i ||
                             ((window_first_pixel_i != 8'd0) && (tile_first_pixel < window_cover));
  assign background_launch = background_dot && background_needed;

  // Why: a window tile starts at the first window pixel and at each window
  // column multiple of 8 (pixel p = dot - 91 - fine), so window tiles align to
  // the window, not to the background scroll.
  assign window_pixel = dot_i - t_dot'(FETCH_DOT) - t_dot'(fine_scroll_i);
  assign window_dot = window_active_i && (dot_i >= t_dot'(FETCH_DOT) + t_dot'(fine_scroll_i)) &&
                      (window_pixel < 9'(VISIBLE_PIXELS)) &&
                      (window_pixel >= 9'(window_first_pixel_i));
  assign launch_column = window_pixel[7:0] + 8'd7 - window_wx_i;
  assign window_boundary = (window_pixel == 9'd0) || (launch_column[2:0] == 3'd0);
  assign window_launch = window_dot && window_boundary;

  assign fetch_launch = dot_enable_i && lcd_enable_i && visible_line &&
                        (background_launch || window_launch);

  // Ref: Pan Docs "Tile Maps": background row (SCY + LY), column SCX[7:3] + k;
  // window row from the window line counter, column from the window column of
  // the launching pixel.
  assign background_y = scy_i + ly_i;
  assign map_column = scx_coarse_i + tile_index;
  always_comb begin
    if (window_launch) begin
      map_address_d = map_offset(lcdc_i[LCDC_WINDOW_MAP_BIT], window_line_i[7:3], launch_column[7:3]);
      tile_row_d = window_line_i[2:0];
    end else begin
      map_address_d = map_offset(lcdc_i[LCDC_BG_MAP_BIT], background_y[7:3], map_column);
      tile_row_d = background_y[2:0];
    end
  end

  always_comb begin
    state_d = state_q;
    case (state_q)
      FETCH_IDLE: if (fetch_launch) state_d = FETCH_MAP;
      FETCH_MAP:  state_d = FETCH_LOW;
      FETCH_LOW:  state_d = FETCH_HIGH;
      FETCH_HIGH: state_d = FETCH_LAST;
      default:    state_d = FETCH_IDLE;
    endcase
    if (!lcd_enable_i) state_d = FETCH_IDLE;
  end
  `DFF_RST_VAL(state_q, state_d, clk_i, reset_i, FETCH_IDLE)
  assign fetch_busy = state_q != FETCH_IDLE;

  `DFF_RST_EN(map_address_q, map_address_d, clk_i, fetch_launch, reset_i, 13'd0)
  assign unsigned_ids_d = lcdc_i[LCDC_TILE_DATA_BIT];
  `DFF_RST_EN(tile_row_q, tile_row_d, clk_i, fetch_launch, reset_i, 3'd0)
  `DFF_RST_EN(unsigned_ids_q, unsigned_ids_d, clk_i, fetch_launch, reset_i, 1'b0)

  assign tile_capture = state_q == FETCH_LOW;
  assign low_capture = state_q == FETCH_HIGH;
  assign high_capture = state_q == FETCH_LAST;
  `DFF_RST_EN(tile_q, vram_data_i, clk_i, tile_capture, reset_i, 8'h00)
  `DFF_RST_EN(row_low_q, vram_data_i, clk_i, low_capture, reset_i, 8'h00)
  `DFF_RST_EN(row_high_q, vram_data_i, clk_i, high_capture, reset_i, 8'h00)

  // Why: the map byte is live on the port for one cycle only, so the low-plane
  // address uses it directly and the high-plane address the captured copy one
  // cycle later. LCD off aborts a fetch in flight: no read leaves once LCDC.7
  // is clear.
  assign low_address = tile_offset(unsigned_ids_q, vram_data_i, tile_row_q, 1'b0);
  assign high_address = tile_offset(unsigned_ids_q, tile_q, tile_row_q, 1'b1);
  always_comb begin
    vram_read_o = 1'b0;
    vram_address_o = map_address_q;
    case (state_q)
      FETCH_MAP: begin
        vram_read_o = 1'b1;
        vram_address_o = map_address_q;
      end
      FETCH_LOW: begin
        vram_read_o = 1'b1;
        vram_address_o = low_address;
      end
      FETCH_HIGH: begin
        vram_read_o = 1'b1;
        vram_address_o = high_address;
      end
      default: begin
        vram_read_o = 1'b0;
        vram_address_o = map_address_q;
      end
    endcase
    if (!lcd_enable_i) begin
      vram_read_o = 1'b0;
    end
  end

  // Why: emission (end of dots 92 + fine .. 251 + fine) trails the first fetch
  // launch by one dot so a tile's bytes are captured before its first pixel. A
  // window pixel is one at or beyond the first window pixel of an active line.
  assign pixel_offset = dot_i - t_dot'(PIXEL_DOT) - t_dot'(fine_scroll_i);
  assign emit = dot_enable_i && lcd_enable_i && visible_line &&
                (dot_i >= t_dot'(PIXEL_DOT) + t_dot'(fine_scroll_i)) &&
                (pixel_offset < 9'(VISIBLE_PIXELS));
  assign pixel_x = pixel_offset[7:0];
  assign window_pixel_now = window_active_i && (pixel_x >= window_first_pixel_i);
  assign background_column = dot_i[2:0] - 3'(PIXEL_DOT);
  assign window_column = 3'(pixel_x + 8'd7 - window_wx_i);
  assign column = window_pixel_now ? window_column : background_column;
  assign raw_color = row_color(row_low_q, row_high_q, column, 1'b0);

  assign emit_o = emit;
  assign pixel_x_o = pixel_x;
  assign color_o = lcdc_i[LCDC_BG_ENABLE_BIT] ? raw_color : 2'd0;
  assign window_drawn_o = emit && window_pixel_now && lcdc_i[LCDC_BG_ENABLE_BIT];

  `ASSERT(fetch_fits_in_dot, clk_i, reset_i, !(dot_enable_i && fetch_busy))
  `ASSERT(one_launch_per_dot, clk_i, reset_i, !(background_launch && window_launch))
  `ASSERT(vram_answers_reads, clk_i, reset_i,
      !(tile_capture || low_capture || high_capture) || vram_valid_i)
  `ASSERT(vram_data_known, clk_i, reset_i,
      !(tile_capture || low_capture || high_capture) || !$isunknown(vram_data_i))
  `ASSERT(no_fetch_while_lcd_off, clk_i, reset_i,
      lcd_enable_i || $past(lcd_enable_i) || !fetch_busy)
endmodule
