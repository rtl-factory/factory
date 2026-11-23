`default_nettype none
// Owner-authored loader ABI adapted from nand2mario 510be34b. The source
// record and native byte-port timing are in wiki/src/nand2mario/rtl/has.md#top-level-behaviour.
package n2m_library_pkg;
    /* verilator lint_off UNUSEDPARAM */
    localparam logic [7:0] PROFILE_DIRECT = 8'd1;
    localparam logic [7:0] PROFILE_LOADER = 8'd2;
    localparam logic [7:0] PROFILE_MBC1 = 8'd3;
    localparam logic [7:0] MENU_INDEX = 8'd16;
    localparam logic [20:0] CATALOGUE_ADDRESS = 21'h088000;
    localparam logic [7:0] RESULT_NONE = 8'd0;
    localparam logic [7:0] RESULT_OK = 8'd1;
    localparam logic [7:0] RESULT_INVALID_SLOT = 8'd2;
    localparam logic [7:0] RESULT_CRC_MISMATCH = 8'd3;
    localparam logic [7:0] RESULT_NOT_READY = 8'd4;
    localparam int unsigned ACTIVE_ROM_BYTES = 65536;
    localparam int unsigned LIBRARY_ROM_BYTES = 1048576;
    // Long enough to visit every VRAM byte once after a successful image swap.
    localparam logic [13:0] RESET_CLOCKS = 14'd8192;
    /* verilator lint_on UNUSEDPARAM */

    typedef enum logic [3:0] {
        LIB_BOOT, LIB_IDLE, LIB_ENTRY_REQUEST, LIB_ENTRY_RESPONSE,
        LIB_CHECK, LIB_COPY_REQUEST, LIB_COPY_RESPONSE, LIB_RESET, LIB_FAILED, LIB_DRAIN
    } t_phase;

    typedef struct packed {
        t_phase phase;
        logic image_valid;
        logic window_copy;
        logic window_ready;
        logic [5:0] bank;
        logic [7:0] profile;
        logic [7:0] index;
        logic [7:0] result;
        logic [7:0] target;
        logic [7:0] entry_valid;
        logic [7:0] entry_profile;
        logic [23:0] entry_length;
        logic [31:0] entry_crc;
        logic [31:0] crc;
        logic [16:0] offset;
        logic [20:0] source;
        logic [13:0] reset_count;
    } t_state;

    localparam t_state RESET_STATE = '{
        phase: LIB_BOOT, index: 8'hff, crc: 32'hffffffff, default: '0
    };

    function automatic logic [31:0] crc32_byte(
        input logic [31:0] crc, input logic [7:0] data);
        logic [31:0] value;
        value = crc ^ {24'd0, data};
        for (int unsigned bit_index = 0; bit_index < 8; bit_index++)
            value = value[0] ? (value >> 1) ^ 32'hedb88320 : value >> 1;
        return value;
    endfunction
endpackage
