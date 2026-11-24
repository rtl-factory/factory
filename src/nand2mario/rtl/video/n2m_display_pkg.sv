`default_nettype none

// nand2mario display facts owned by the display tier: the DMG frame geometry
// the frame store holds, the three-bank frame store size, the 3x scaled and
// centered image window inside the 640x480 VGA raster, the DMG shade palette
// on 4:4:4 RGB, the border and blanking colours, the bridge, scanout and
// snapshot-reader register structs and the pure frame-index and palette
// helpers. The snapshot record and its port types are n2m_snapshot_pkg.
package n2m_display_pkg;
    // Source frame: one DMG LCD image of two-bit shades, 23040 pixels.
    localparam int unsigned FRAME_WIDTH  = 160;
    localparam int unsigned FRAME_HEIGHT = 144;
    localparam int unsigned FRAME_PIXELS = FRAME_WIDTH * FRAME_HEIGHT;
    localparam int unsigned FRAME_INDEX_WIDTH = $clog2(FRAME_PIXELS);

    // Frame store: writing, ready and displayed banks, each one frame padded
    // to the power-of-two depth the common RAM contract requires.
    localparam int unsigned BANK_COUNT = 3;
    localparam int unsigned BANK_DEPTH = 2 ** FRAME_INDEX_WIDTH;

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

    typedef logic [FRAME_INDEX_WIDTH-1:0] t_frame_index;
    typedef logic [1:0] t_bank;
    typedef logic [1:0] t_shade;

    typedef struct packed {
        logic [3:0] red;
        logic [3:0] green;
        logic [3:0] blue;
    } t_rgb;

    // Frame bridge bank ownership: the banks being written, holding the newest
    // complete frame and being displayed, and the ready/display validity.
    typedef struct packed {
        t_bank     write_bank;
        t_bank     ready_bank;
        t_bank     display_bank;
        logic      ready_fresh;     // the ready bank holds a complete frame not yet displayed
        logic      display_valid;   // the displayed bank holds a complete frame
        logic      pinned;          // a snapshot holds the displayed bank
        n2m_snapshot_pkg::t_frame_id frame_count;   // ID of the newest completion (completions since reset)
        n2m_snapshot_pkg::t_frame_id display_id;    // ID of the frame in the displayed bank
    } t_frame_bridge_state;

    // Snapshot reader: one request in progress, its four frame-store reads
    // and the response register.
    typedef struct packed {
        logic               busy;       // a pixel byte is being assembled
        logic [2:0]         issued;     // frame-store reads presented (0..4)
        logic [2:0]         captured;   // shades captured (0..4)
        logic               capture;    // the store's output this cycle is ours
        t_frame_index       index;      // first pixel of the byte's quad
        logic [7:0]         data;       // shades assembled so far
        n2m_snapshot_pkg::t_snapshot_response response;
    } t_frame_snapshot_state;

    // Scanout source address generation.
    typedef struct packed {
        logic [7:0]   column;     // source x of the current image clock
        logic [1:0]   repeat_x;   // image clocks already spent on this column
        t_frame_index line_base;  // index of source pixel (0, source y)
        logic [1:0]   repeat_y;   // image lines already spent on this source row
    } t_scanout_address;

    // Scanout two-stage output pipeline: the frame-store read, then the palette.
    typedef struct packed {
        logic in_image_q, visible_q, hsync_q, vsync_q, frame_start_q;
        t_rgb rgb_qq;
        logic hsync_qq, vsync_qq, frame_start_qq;
    } t_scanout_pipe;

    // Outside the image window and during blanking the output is black.
    localparam t_rgb BORDER_RGB = '{red: 4'h0, green: 4'h0, blue: 4'h0};
    localparam t_rgb BLANK_RGB  = '{red: 4'h0, green: 4'h0, blue: 4'h0};
    // The image window shows shade 0 (white) until a complete frame exists.
    localparam t_shade WHITE_SHADE = 2'd0;

    // Row-major index of pixel (x, y) inside one bank.
    function automatic t_frame_index frame_index(input logic [7:0] x, input logic [7:0] y);
        return (t_frame_index'(y) * t_frame_index'(FRAME_WIDTH)) + t_frame_index'(x);
    endfunction

    // DMG shade to grey on 4:4:4 RGB: 0 white, 1 light grey, 2 dark grey,
    // 3 black. Every channel carries the same level.
    function automatic t_rgb shade_rgb(input t_shade shade);
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
