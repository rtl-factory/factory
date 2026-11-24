`include "src/common/rtl/pkg/macros.vh"

// Set-2 event decoder: a byte stream to key events
// (wiki/src/common/rtl/ps2/mas.md#decoder). E0 and F0 set the pending
// extended and break qualifiers and are always ready; any other byte is
// offered at once as {extended, key_break, code} on event_o and is ready when
// event_ready_i is. An accepted event, clear_i or reset clears both
// qualifiers; a refused byte keeps them.
module n2a_ps2_decoder (
    input  logic                    clk_i,
    input  logic                    reset_i,
    input  logic                    byte_valid_i,
    input  logic [7:0]              byte_i,
    output logic                    byte_ready_o,
    // Drops the pending qualifiers at the end of this clock; the event
    // offered on this clock still carries them.
    input  logic                    clear_i,
    output logic                    event_valid_o,
    output n2a_ps2_pkg::t_ps2_event event_o,
    input  logic                    event_ready_i
);
    import n2a_ps2_pkg::*;

    typedef struct packed {
        logic extended;
        logic key_break;
    } t_pending;
    localparam t_pending PENDING_RESET = '{extended: 1'b0, key_break: 1'b0};

    t_pending pending_q, pending_d;
    logic prefix;

    assign prefix = ps2_is_prefix(byte_i);
    assign byte_ready_o = prefix || event_ready_i;
    assign event_valid_o = byte_valid_i && !prefix;
    assign event_o = '{extended: pending_q.extended, key_break: pending_q.key_break,
                       code: byte_i};

    always_comb begin
        pending_d = pending_q;
        if (byte_valid_i && (byte_i == PS2_PREFIX_EXTENDED)) pending_d.extended = 1'b1;
        else if (byte_valid_i && (byte_i == PS2_PREFIX_BREAK)) pending_d.key_break = 1'b1;
        else if (event_valid_o && event_ready_i) pending_d = PENDING_RESET;
        if (clear_i) pending_d = PENDING_RESET;
    end

    `DFF_RST_VAL(pending_q, pending_d, clk_i, reset_i, PENDING_RESET)
endmodule
