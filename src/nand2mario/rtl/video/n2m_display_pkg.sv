`default_nettype none

// nand2mario display facts owned by the display tier: the DMG frame geometry
// the frame store holds, the three-bank frame store size, the 3x scaled and
// centered image window inside the 640x480 VGA raster, the DMG shade palette
// on 4:4:4 RGB, the border and blanking colours, the bridge, scanout and
// snapshot-reader register structs and the pure frame-index and palette
// helpers. The snapshot record and its port types are n2m_snapshot_pkg.
package n2m_display_pkg;
  // Source frame: one DMG LCD image of two-bit shades, 23040 pixels;
  // FRAME_INDEX_WIDTH is $clog2(FRAME_PIXELS).
  localparam int unsigned FRAME_WIDTH  = 160;
  localparam int unsigned FRAME_HEIGHT = 144;
  localparam int unsigned FRAME_PIXELS = FRAME_WIDTH * FRAME_HEIGHT;
  localparam int unsigned FRAME_INDEX_WIDTH = 15;

  // Frame store: writing, ready and displayed banks, each one frame padded
  // to the power-of-two depth the common RAM contract requires.
  localparam int unsigned BANK_COUNT = 3;
  localparam int unsigned BANK_DEPTH = 32768;

  // VGA raster (the common n2a_vga_timing defaults) and the scaled window:
  // a 480x432 image at (80, 24); IMAGE_RIGHT (560) and IMAGE_BOTTOM (456)
  // are exclusive.
  localparam int unsigned VGA_WIDTH  = 640;
  localparam int unsigned VGA_HEIGHT = 480;
  localparam int unsigned SCALE = 3;
  localparam int unsigned IMAGE_WIDTH  = FRAME_WIDTH * SCALE;
  localparam int unsigned IMAGE_HEIGHT = FRAME_HEIGHT * SCALE;
  localparam int unsigned IMAGE_LEFT   = (VGA_WIDTH - IMAGE_WIDTH) / 2;
  localparam int unsigned IMAGE_TOP    = (VGA_HEIGHT - IMAGE_HEIGHT) / 2;
  localparam int unsigned IMAGE_RIGHT  = IMAGE_LEFT + IMAGE_WIDTH;
  localparam int unsigned IMAGE_BOTTOM = IMAGE_TOP + IMAGE_HEIGHT;

  typedef logic [FRAME_INDEX_WIDTH-1:0] frame_index_t;
  typedef logic [1:0] bank_t;
  typedef logic [1:0] shade_t;

  typedef struct packed {
    logic [3:0] red;
    logic [3:0] green;
    logic [3:0] blue;
  } rgb_t;

  // Frame bridge bank ownership: the banks being written, holding the newest
  // complete frame and being displayed, and the ready/display validity.
  typedef struct packed {
    bank_t     write_bank;
    bank_t     ready_bank;
    bank_t     display_bank;
    logic      ready_fresh;     // the ready bank holds a complete frame not yet displayed
    logic      display_valid;   // the displayed bank holds a complete frame
    logic      pinned;          // a snapshot holds the displayed bank
    n2m_snapshot_pkg::frame_id_t frame_count;   // ID of the newest completion (completions since reset)
    n2m_snapshot_pkg::frame_id_t display_id;    // ID of the frame in the displayed bank
  } frame_bridge_state_t;

  // Snapshot reader: one request in progress, its four frame-store reads
  // and the response register.
  typedef struct packed {
    logic               busy;       // a pixel byte is being assembled
    logic [2:0]         issued;     // frame-store reads presented (0..4)
    logic [2:0]         captured;   // shades captured (0..4)
    logic               capture;    // the store's output this cycle is ours
    frame_index_t       index;      // first pixel of the byte's quad
    logic [7:0]         data;       // shades assembled so far
    n2m_snapshot_pkg::snapshot_response_t response;
  } frame_snapshot_state_t;

  // Scanout source address generation.
  typedef struct packed {
    logic [7:0]   column;     // source x of the current image clock
    logic [1:0]   repeat_x;   // image clocks already spent on this column
    frame_index_t line_base;  // index of source pixel (0, source y)
    logic [1:0]   repeat_y;   // image lines already spent on this source row
  } scanout_address_t;

  // Scanout two-stage output pipeline: the frame-store read, then the palette.
  typedef struct packed {
    logic in_image_q, visible_q, hsync_q, vsync_q, frame_start_q;
    rgb_t rgb_qq;
    logic hsync_qq, vsync_qq, frame_start_qq;
  } scanout_pipe_t;

  // Outside the image window and during blanking the output is black.
  localparam rgb_t BORDER_RGB = '{red: 4'h0, green: 4'h0, blue: 4'h0};
  localparam rgb_t BLANK_RGB  = '{red: 4'h0, green: 4'h0, blue: 4'h0};
  // The image window shows shade 0 (white) until a complete frame exists.
  localparam shade_t WHITE_SHADE = 2'd0;

  // Row-major index of pixel (x, y) inside one bank.
  function automatic frame_index_t frame_index(input logic [7:0] x, input logic [7:0] y);
    return (frame_index_t'(y) * frame_index_t'(FRAME_WIDTH)) + frame_index_t'(x);
  endfunction

  // DMG shade to grey on 4:4:4 RGB: 0 white, 1 light grey, 2 dark grey,
  // 3 black. Every channel carries the same level.
  function automatic rgb_t shade_rgb(input shade_t shade);
    logic [3:0] level;
    case (shade)
      2'd0: level = 4'hF;
      2'd1: level = 4'hA;
      2'd2: level = 4'h5;
      default: level = 4'h0;
    endcase
    return '{red: level, green: level, blue: level};
  endfunction
endpackage
