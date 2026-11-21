`default_nettype none
`include "src/common/rtl/pkg/macros.vh"

// Objects (sprites) of one visible line. OAM scan: at the dot-enable edge
// that ends dot 2n (n = 0..39) of a visible line the four bytes of OAM
// entry n are read through the OAM port in the following four cycles; at
// the fifth cycle the entry is kept when LY + 16 - Y is below the object
// height (8, or 16 with LCDC.2) and fewer than ten entries are kept, in OAM
// order and regardless of X. Row fetch: at the edge that ends dot 80 the
// two plane bytes of every kept object's row (vertical flip applied) are
// read from VRAM, two cycles per object, finishing before the first tile
// fetch at the end of dot 91. Per pixel: the object with the smallest X
// (lowest OAM index on a tie) that covers the pixel with a nonzero color is
// the candidate; the mixer applies LCDC.1, the palettes and the priority
// attribute. The line's kept objects are discarded at the end of the line.
module n2m_ppu_objects (
    input  logic                   clk_i,
    input  logic                   reset_i,
    input  logic                   dot_enable_i,
    input  logic                   lcd_enable_i,
    input  n2m_ppu_pkg::t_dot      dot_i,
    input  n2m_ppu_pkg::t_line     ly_i,
    input  logic [7:0]             lcdc_i,
    // OAM read port: one registered read per cycle, data valid the next cycle.
    output logic                   oam_read_o,
    output logic [7:0]             oam_address_o,
    input  logic                   oam_valid_i,
    input  logic [7:0]             oam_data_i,
    // VRAM read port, owned while fetch_busy_o is high.
    output logic                   vram_read_o,
    output logic [12:0]            vram_address_o,
    input  logic                   vram_valid_i,
    input  logic [7:0]             vram_data_i,
    output logic                   fetch_busy_o,
    // Candidate object for the pixel x about to be emitted.
    input  logic [7:0]             pixel_x_i,
    output logic                   candidate_valid_o,
    output logic [1:0]             candidate_color_o,
    output logic [7:0]             candidate_attributes_o
);
    import n2m_ppu_pkg::*;

    t_object_scan_state  scan_q, scan_d;
    logic         visible_line, scan_launch, line_end, scan_busy;
    logic [5:0]   entry_q, entry_d;
    logic [1:0]   byte_select;
    logic [7:0]   y_q, x_q, tile_q;
    logic         y_capture, x_capture, tile_capture, attr_valid;
    logic [7:0]   ly_offset, distance, height;
    logic         tall, on_line, keep;
    logic [3:0]   count_q, count_d;
    t_object      objects_q [0:OBJECTS_PER_LINE-1];
    t_object      objects_d [0:OBJECTS_PER_LINE-1];
    t_object      scanned;
    logic         objects_update;

    logic         fetch_start, fetch_busy_q, fetch_busy_d;
    logic [4:0]   step_q, step_d, read_steps;
    logic [3:0]   read_entry, capture_entry;
    logic         read_plane, capture_plane, read_now, capture_now;
    logic         read_tall;
    logic [7:0]   read_tile;
    logic [3:0]   read_row;

    logic [7:0]   covered_x;
    logic [7:0]   dx [0:OBJECTS_PER_LINE-1];
    logic [1:0]   colors [0:OBJECTS_PER_LINE-1];
    logic         candidates [0:OBJECTS_PER_LINE-1];
    logic         best_valid;
    logic [7:0]   best_x;
    logic [3:0]   best_index;

    assign visible_line = ly_i < t_line'(VISIBLE_LINES);
    assign line_end = dot_enable_i && lcd_enable_i && (dot_i == t_dot'(DOTS_PER_LINE - 1));

    assign scan_launch = dot_enable_i && lcd_enable_i && visible_line &&
                         (dot_i < t_dot'(MODE2_DOTS)) && !dot_i[0];
    always_comb begin
        scan_d = scan_q;
        case (scan_q)
            SCAN_IDLE:   if (scan_launch) scan_d = SCAN_Y;
            SCAN_Y:      scan_d = SCAN_X;
            SCAN_X:      scan_d = SCAN_TILE;
            SCAN_TILE:   scan_d = SCAN_ATTR;
            SCAN_ATTR:   scan_d = SCAN_DECIDE;
            default:     scan_d = SCAN_IDLE;
        endcase
        if (!lcd_enable_i) scan_d = SCAN_IDLE;
    end
    `DFF_RST_VAL(scan_q, scan_d, clk_i, reset_i, SCAN_IDLE)
    assign scan_busy = scan_q != SCAN_IDLE;

    assign entry_d = dot_i[6:1];
    `DFF_RST_EN(entry_q, entry_d, clk_i, scan_launch, reset_i, 6'd0)

    always_comb begin
        byte_select = 2'd0;
        case (scan_q)
            SCAN_Y:    byte_select = 2'd0;
            SCAN_X:    byte_select = 2'd1;
            SCAN_TILE: byte_select = 2'd2;
            default:   byte_select = 2'd3;
        endcase
    end
    assign oam_read_o = lcd_enable_i && ((scan_q == SCAN_Y) || (scan_q == SCAN_X) ||
                                         (scan_q == SCAN_TILE) || (scan_q == SCAN_ATTR));
    assign oam_address_o = {entry_q, byte_select};

    assign y_capture = scan_q == SCAN_X;
    assign x_capture = scan_q == SCAN_TILE;
    assign tile_capture = scan_q == SCAN_ATTR;
    assign attr_valid = scan_q == SCAN_DECIDE;
    `DFF_RST_EN(y_q, oam_data_i, clk_i, y_capture, reset_i, 8'h00)
    `DFF_RST_EN(x_q, oam_data_i, clk_i, x_capture, reset_i, 8'h00)
    `DFF_RST_EN(tile_q, oam_data_i, clk_i, tile_capture, reset_i, 8'h00)

    // Ref: Pan Docs "OAM": the object covers the line when LY + 16 - Y, in eight
    // bits, is below its height; the line's size select is read here.
    assign tall = lcdc_i[LCDC_OBJ_SIZE_BIT];
    assign height = tall ? 8'd16 : 8'd8;
    assign ly_offset = ly_i + 8'(OBJECT_Y_OFFSET);
    assign distance = ly_offset - y_q;
    assign on_line = distance < height;
    assign keep = attr_valid && lcd_enable_i && on_line && (count_q < 4'(OBJECTS_PER_LINE));

    always_comb begin
        scanned = '0;
        scanned.x = x_q;
        scanned.row = object_row(tall, oam_data_i[ATTR_Y_FLIP_BIT], distance[3:0]);
        scanned.tall = tall;
        scanned.tile = tile_q;
        scanned.attributes = oam_data_i;
    end

    always_comb begin
        count_d = count_q;
        if (keep) count_d = count_q + 4'd1;
        if (line_end || !lcd_enable_i) count_d = 4'd0;
    end
    `DFF_RST(count_q, count_d, clk_i, reset_i)

    // Why: two reads per kept object, one per plane, pipelined so step s captures
    // the data of step s - 1 (step s < 2 * count reads plane s[0] of object
    // s[4:1]); done after 2 * count + 1 steps, none when no object was kept, so
    // the fetch ends before the first background fetch at dot 91.
    assign fetch_start = dot_enable_i && lcd_enable_i && visible_line &&
                         (dot_i == t_dot'(MODE2_DOTS)) && (count_q != 4'd0);
    assign read_steps = {count_q, 1'b0};
    always_comb begin
        step_d = step_q;
        fetch_busy_d = fetch_busy_q;
        if (fetch_start) begin
            step_d = 5'd0;
            fetch_busy_d = 1'b1;
        end else if (fetch_busy_q) begin
            step_d = step_q + 5'd1;
            if (step_q == read_steps) fetch_busy_d = 1'b0;
        end
        if (!lcd_enable_i) begin
            fetch_busy_d = 1'b0;
            step_d = 5'd0;
        end
    end
    `DFF_RST(step_q, step_d, clk_i, reset_i)
    `DFF_RST(fetch_busy_q, fetch_busy_d, clk_i, reset_i)

    assign read_now = fetch_busy_q && (step_q < read_steps);
    assign read_entry = step_q[4:1];
    assign read_plane = step_q[0];
    assign read_tall = objects_q[read_entry].tall;
    assign read_tile = objects_q[read_entry].tile;
    assign read_row = objects_q[read_entry].row;
    assign vram_read_o = read_now;
    assign vram_address_o = object_offset(read_tall, read_tile, read_row, read_plane);
    assign fetch_busy_o = fetch_busy_q;

    assign capture_now = fetch_busy_q && (step_q != 5'd0);
    assign capture_entry = 4'((step_q - 5'd1) >> 1);
    assign capture_plane = step_q[0] == 1'b0;  // Why: step s captures step s - 1's data, and odd s - 1 read the high plane.

    always_comb begin
        objects_d = objects_q;
        if (keep) objects_d[count_q] = scanned;
        if (capture_now) begin
            if (capture_plane) objects_d[capture_entry].high = vram_data_i;
            else objects_d[capture_entry].low = vram_data_i;
        end
    end
    assign objects_update = keep || capture_now;
    `DFF_ARRAY_RST_EN(objects_q, objects_d, clk_i, objects_update, reset_i)

    // Ref: Pan Docs "Drawing priority": object i covers pixel x when
    // 0 <= x + 8 - X < 8 (eight-bit wrap puts every other distance at 8 or above);
    // its color is the tile column x + 8 - X, mirrored when attribute bit 5 is
    // set. The smallest X wins, the lowest index on a tie.
    assign covered_x = pixel_x_i + 8'(OBJECT_X_OFFSET);
    always_comb begin
        best_valid = 1'b0;
        best_x = 8'd0;
        best_index = 4'd0;
        for (int i = 0; i < OBJECTS_PER_LINE; i++) begin
            dx[i] = covered_x - objects_q[i].x;
            colors[i] = row_color(objects_q[i].low, objects_q[i].high, dx[i][2:0],
                                  objects_q[i].attributes[ATTR_X_FLIP_BIT]);
            candidates[i] = (4'(i) < count_q) && (dx[i] < 8'd8) && (colors[i] != 2'd0);
            if (candidates[i] && (!best_valid || (objects_q[i].x < best_x))) begin
                best_valid = 1'b1;
                best_x = objects_q[i].x;
                best_index = 4'(i);
            end
        end
    end
    assign candidate_valid_o = best_valid;
    assign candidate_color_o = colors[best_index];
    assign candidate_attributes_o = objects_q[best_index].attributes;

    `ASSERT(scan_fits_in_two_dots, clk_i, reset_i, !(scan_launch && scan_busy))
    `ASSERT(fetch_before_tiles, clk_i, reset_i,
        !(dot_enable_i && fetch_busy_q && (dot_i >= t_dot'(FETCH_DOT))))
    `ASSERT(oam_answers_reads, clk_i, reset_i,
        !(y_capture || x_capture || tile_capture || attr_valid) || oam_valid_i)
    `ASSERT(oam_data_known, clk_i, reset_i,
        !(y_capture || x_capture || tile_capture || attr_valid) || !$isunknown(oam_data_i))
    `ASSERT(object_vram_answers_reads, clk_i, reset_i, !capture_now || vram_valid_i)
    `ASSERT(object_vram_data_known, clk_i, reset_i, !capture_now || !$isunknown(vram_data_i))
    `ASSERT(count_in_range, clk_i, reset_i, count_q <= 4'(OBJECTS_PER_LINE))
    `ASSERT(entry_in_range, clk_i, reset_i, !scan_busy || (entry_q < 6'(OAM_ENTRIES)))
    `ASSERT_KNOWN(object_controls_known, clk_i, reset_i,
        {dot_enable_i, lcd_enable_i, lcdc_i, pixel_x_i})
endmodule
