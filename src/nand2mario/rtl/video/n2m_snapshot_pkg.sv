`default_nettype none

// nand2mario frame-snapshot facts shared by the display tier and the UART
// loader (display HAS, frame snapshot): the record layout, the frame ID and
// offset types, the snapshot byte-port records and the header helper. Facts
// and pure helpers only; n2m_frame_bridge, n2m_frame_snapshot and
// n2m_uart_loader own the behavior.
package n2m_snapshot_pkg;
    /* verilator lint_off UNUSEDPARAM */
    // Record: an eight-byte header (magic "NM", format version, on-screen flag
    // and pinned bank, 32-bit frame ID least significant byte first) followed
    // by the frame's 160x144 shades, four per byte, row-major, pixel 4m+j in
    // bits [2j+1:2j] of byte 8+m: 5760 pixel bytes, 5768 in all.
    localparam int unsigned SNAPSHOT_HEADER_BYTES = 8;
    localparam int unsigned SNAPSHOT_PIXEL_BYTES = 160 * 144 / 4;
    localparam int unsigned SNAPSHOT_RECORD_BYTES = SNAPSHOT_HEADER_BYTES + SNAPSHOT_PIXEL_BYTES;
    localparam int unsigned SNAPSHOT_OFFSET_WIDTH = $clog2(SNAPSHOT_RECORD_BYTES);
    localparam logic [7:0] SNAPSHOT_MAGIC0 = 8'h4E;   // 'N'
    localparam logic [7:0] SNAPSHOT_MAGIC1 = 8'h4D;   // 'M'
    localparam logic [7:0] SNAPSHOT_FORMAT_VERSION = 8'h01;
    localparam int unsigned FRAME_ID_WIDTH = 32;
    /* verilator lint_on UNUSEDPARAM */

    typedef logic [FRAME_ID_WIDTH-1:0] t_frame_id;
    typedef logic [SNAPSHOT_OFFSET_WIDTH-1:0] t_snapshot_offset;

    // Snapshot byte port between the UART loader and the display: one request
    // at a time, accepted with ready; one response per accepted request.
    typedef struct packed {
        logic             valid;
        t_snapshot_offset offset;   // byte offset inside the record
    } t_snapshot_request;

    typedef struct packed {
        logic       valid;
        logic [7:0] data;
    } t_snapshot_response;

    // Header byte `offset` (0..7) of the record. Byte 3 is {on screen, 5'b0,
    // bank}: on screen is the display-valid level when the byte is read (low
    // once an LCD-off origin blanked the pinned frame, which then stays off
    // screen until the pin is released).
    function automatic logic [7:0] snapshot_header_byte(input logic [2:0] offset, input logic [1:0] bank,
                                                        input logic on_screen, input t_frame_id frame_id);
        case (offset)
            3'd0: return SNAPSHOT_MAGIC0;
            3'd1: return SNAPSHOT_MAGIC1;
            3'd2: return SNAPSHOT_FORMAT_VERSION;
            3'd3: return {on_screen, 5'd0, bank};
            3'd4: return frame_id[7:0];
            3'd5: return frame_id[15:8];
            3'd6: return frame_id[23:16];
            default: return frame_id[31:24];
        endcase
    endfunction

    // The SNAP_READ range: the whole 24-bit address field is the record
    // offset, and offset + length must stay within the record.
    function automatic logic snapshot_range_valid(input logic [23:0] address, input logic [7:0] length_minus_one);
        logic [24:0] end_offset;
        end_offset = {1'b0, address} + 25'(length_minus_one) + 25'd1;
        return end_offset <= 25'(SNAPSHOT_RECORD_BYTES);
    endfunction
endpackage
