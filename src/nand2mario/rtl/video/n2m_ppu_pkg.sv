`default_nettype none

// DMG picture-processor facts owned by the nand2mario video subsystem: the
// LCD register addresses the PPU claims inside the peripheral window, raster
// geometry in dots and lines, the STAT mode encoding, LCDC/STAT bit
// positions, the object geometry and the pure addressing/palette helpers
// shared by the renderers.
package n2m_ppu_pkg;
  // LCD registers served by the PPU; every other $FF00-$FF7F address keeps
  // the system's default responder (the DMA register belongs to the DMA block).
  localparam logic [15:0] LCDC_ADDRESS = 16'hFF40;
  localparam logic [15:0] STAT_ADDRESS = 16'hFF41;
  localparam logic [15:0] SCY_ADDRESS  = 16'hFF42;
  localparam logic [15:0] SCX_ADDRESS  = 16'hFF43;
  localparam logic [15:0] LY_ADDRESS   = 16'hFF44;
  localparam logic [15:0] LYC_ADDRESS  = 16'hFF45;
  localparam logic [15:0] BGP_ADDRESS  = 16'hFF47;
  localparam logic [15:0] OBP0_ADDRESS = 16'hFF48;
  localparam logic [15:0] OBP1_ADDRESS = 16'hFF49;
  localparam logic [15:0] WY_ADDRESS   = 16'hFF4A;
  localparam logic [15:0] WX_ADDRESS   = 16'hFF4B;

  // Raster geometry. A dot is one 4,194,304 Hz interval; t_dot holds 0..455 and
  // t_line 0..153. Mode 2 is dots 0..79 of a visible line and mode 3 lasts
  // MODE3_BASE_DOTS before the SCX[2:0] penalty. The end of FETCH_DOT launches
  // tile fetch 0, the end of dot PIXEL_DOT + SCX[2:0] emits pixel 0 and
  // LAST_FETCH_DOT is tile fetch 20 (only when SCX[2:0] != 0). LY reads the next
  // line from LY_READ_DOT; WX above WINDOW_LAST_WX shows no window pixel.
  localparam int unsigned DOTS_PER_LINE   = 456;
  localparam int unsigned LINES_PER_FRAME = 154;
  localparam int unsigned VISIBLE_LINES   = 144;
  localparam int unsigned VISIBLE_PIXELS  = 160;
  localparam int unsigned MODE2_DOTS      = 80;
  localparam int unsigned MODE3_BASE_DOTS = 172;
  localparam int unsigned FETCH_DOT       = 91;
  localparam int unsigned PIXEL_DOT       = 92;
  localparam int unsigned LAST_FETCH_DOT  = 251;
  localparam int unsigned LY_READ_DOT     = 452;
  localparam int unsigned WINDOW_LAST_WX  = 166;

  // Objects: 40 OAM entries of four bytes, at most ten per line, drawn at
  // (X - 8, Y - 16); the fetch of their tile rows at the start of mode 3
  // must finish before the first background tile fetch at dot 91.
  localparam int unsigned OAM_ENTRIES      = 40;
  localparam int unsigned OBJECTS_PER_LINE = 10;
  localparam int unsigned OBJECT_X_OFFSET  = 8;
  localparam int unsigned OBJECT_Y_OFFSET  = 16;

  typedef logic [8:0] t_dot;
  typedef logic [7:0] t_line;

  // STAT[1:0] mode encoding.
  typedef enum logic [1:0] {
    MODE_HBLANK   = 2'd0,
    MODE_VBLANK   = 2'd1,
    MODE_OAM_SCAN = 2'd2,
    MODE_TRANSFER = 2'd3
  } t_mode;

  // Ref: Pan Docs "LCDC": bit positions. Window map 0: $9800, 1: $9C00; tile
  // data 1: $8000 unsigned ids, 0: $8800 signed ids; BG map 0: $9800, 1: $9C00;
  // object size 0: 8x8, 1: 8x16.
  localparam int unsigned LCDC_ENABLE_BIT     = 7;
  localparam int unsigned LCDC_WINDOW_MAP_BIT = 6;
  localparam int unsigned LCDC_WINDOW_BIT     = 5;
  localparam int unsigned LCDC_TILE_DATA_BIT  = 4;
  localparam int unsigned LCDC_BG_MAP_BIT     = 3;
  localparam int unsigned LCDC_OBJ_SIZE_BIT   = 2;
  localparam int unsigned LCDC_OBJ_ENABLE_BIT = 1;
  localparam int unsigned LCDC_BG_ENABLE_BIT  = 0;

  // Ref: Pan Docs "OAM": attribute byte bit positions. Priority 1: background
  // colors 1-3 cover the object; palette 0: OBP0, 1: OBP1.
  localparam int unsigned ATTR_PRIORITY_BIT = 7;
  localparam int unsigned ATTR_Y_FLIP_BIT   = 6;
  localparam int unsigned ATTR_X_FLIP_BIT   = 5;
  localparam int unsigned ATTR_PALETTE_BIT  = 4;

  // One selected object of the current line: X, the row inside the object,
  // the tile id and attributes as scanned, and the two plane bytes of that
  // row once fetched.
  typedef struct packed {
    logic [7:0] x;
    logic [3:0] row;
    logic       tall;
    logic [7:0] tile;
    logic [7:0] attributes;
    logic [7:0] low;
    logic [7:0] high;
  } t_object;

  // Background/window tile fetch sequence of n2m_ppu_background.
  typedef enum logic [2:0] {
    FETCH_IDLE = 3'd0,
    FETCH_MAP  = 3'd1,  // map read presented
    FETCH_LOW  = 3'd2,  // map byte valid, low-plane read presented
    FETCH_HIGH = 3'd3,  // low byte valid, high-plane read presented
    FETCH_LAST = 3'd4   // high byte valid
  } t_background_fetch_state;

  // OAM scan sequence of n2m_ppu_objects.
  typedef enum logic [2:0] {
    SCAN_IDLE   = 3'd0,
    SCAN_Y      = 3'd1,  // Y read presented
    SCAN_X      = 3'd2,  // Y valid, X read presented
    SCAN_TILE   = 3'd3,  // X valid, tile read presented
    SCAN_ATTR   = 3'd4,  // tile valid, attributes read presented
    SCAN_DECIDE = 3'd5   // attributes valid: keep or drop the entry
  } t_object_scan_state;

  // STAT interrupt enables as stored (STAT[6:3]) and indexed in the enables
  // vector: bit 3 LYC, bit 2 mode 2, bit 1 mode 1, bit 0 mode 0.
  localparam int unsigned STAT_LYC_ENABLE   = 3;
  localparam int unsigned STAT_MODE2_ENABLE = 2;
  localparam int unsigned STAT_MODE1_ENABLE = 1;
  localparam int unsigned STAT_MODE0_ENABLE = 0;

  // Shade of one 2-bit color through a DMG palette byte.
  function automatic logic [1:0] palette_shade(input logic [7:0] palette,
                                               input logic [1:0] color);
    logic [1:0] shade;
    case (color)
      2'd0: shade = palette[1:0];
      2'd1: shade = palette[3:2];
      2'd2: shade = palette[5:4];
      default: shade = palette[7:6];
    endcase
    return shade;
  endfunction

  // VRAM offset ($8000-relative) of one tile-map entry: $1800 or $1C00 plus
  // row * 32 + column.
  function automatic logic [12:0] map_offset(input logic map_select,
                                             input logic [4:0] row,
                                             input logic [4:0] column);
    return {2'b11, map_select, row, column};
  endfunction

  // VRAM offset of one tile-row plane byte. Unsigned addressing places tile
  // id at $8000 + id * 16; signed addressing places ids 0..127 at $9000 and
  // 128..255 at $8800. The low plane is byte 0 of the row, the high plane 1.
  function automatic logic [12:0] tile_offset(input logic unsigned_ids,
                                              input logic [7:0] tile,
                                              input logic [2:0] row,
                                              input logic high_plane);
    logic high_half;
    high_half = unsigned_ids ? 1'b0 : !tile[7];
    return {high_half, tile[7], tile[6:0], row, high_plane};
  endfunction

  // VRAM offset of one object row plane byte: objects use $8000 unsigned
  // ids; an 8x16 object uses tile id & $FE for rows 0-7 and | $01 for rows
  // 8-15, after the vertical flip has been applied to the row.
  function automatic logic [12:0] object_offset(input logic tall,
                                                input logic [7:0] tile,
                                                input logic [3:0] row,
                                                input logic high_plane);
    logic [7:0] id;
    id = tall ? {tile[7:1], row[3]} : tile;
    return tile_offset(1'b1, id, row[2:0], high_plane);
  endfunction

  // Row of the object shown on the line: the distance from the object's top
  // (Y - 16) to LY, flipped over the object's height when attribute bit 6
  // is set. The caller has checked that the distance is below the height.
  function automatic logic [3:0] object_row(input logic tall,
                                            input logic flip,
                                            input logic [3:0] distance);
    logic [3:0] last;
    last = tall ? 4'd15 : 4'd7;
    return flip ? (last - distance) : distance;
  endfunction

  // Two-bit color of one pixel column of a tile row from its plane bytes:
  // column 0 is bit 7 unless the row is mirrored.
  function automatic logic [1:0] row_color(input logic [7:0] low,
                                           input logic [7:0] high,
                                           input logic [2:0] column,
                                           input logic mirror);
    logic [2:0] bit_index;
    bit_index = mirror ? column : (3'd7 - column);
    return {high[bit_index], low[bit_index]};
  endfunction
endpackage
