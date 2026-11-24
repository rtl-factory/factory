`default_nettype none
`include "src/common/rtl/pkg/macros.vh"

// Set-2 keyboard byte stream to Game Boy button levels. Each byte arrives
// with a one-cycle valid and is always taken; the common n2a_ps2_decoder
// turns E0/F0 prefixes and codes into key events
// (wiki/src/common/rtl/ps2/mas.md#decoder). An event the map below names sets
// (make) or clears (break) its button; every other event is ignored. Buttons
// are active-high in the n2m_joypad buttons_i order:
//   Right/Left/Up/Down: the extended arrow keys; A: X; B: Z;
//   Select: Space; Start: Enter.
// Board mapping policy lives in wiki/src/nand2mario/rtl/has.md#boards.
module n2m_scancode_buttons (
    input  logic       clk_i,
    input  logic       reset_i,
    input  logic       byte_valid_i,
    input  logic [7:0] byte_i,
    output logic [7:0] buttons_o
);
    import n2a_ps2_pkg::*;

    localparam logic [7:0] CODE_RIGHT  = 8'h74;
    localparam logic [7:0] CODE_LEFT   = 8'h6B;
    localparam logic [7:0] CODE_UP     = 8'h75;
    localparam logic [7:0] CODE_DOWN   = 8'h72;
    localparam logic [7:0] CODE_A      = 8'h22;
    localparam logic [7:0] CODE_B      = 8'h1A;
    localparam logic [7:0] CODE_SELECT = 8'h29;
    localparam logic [7:0] CODE_START  = 8'h5A;

    logic       byte_ready, event_valid;
    t_ps2_event key_event;
    logic [7:0] buttons_q, buttons_d;
    logic [7:0] hit;

    n2a_ps2_decoder u_decoder (
        .clk_i         (clk_i),
        .reset_i       (reset_i),
        .byte_valid_i  (byte_valid_i),
        .byte_i        (byte_i),
        .byte_ready_o  (byte_ready),
        .clear_i       (1'b0),
        .event_valid_o (event_valid),
        .event_o       (key_event),
        .event_ready_i (1'b1)
    );
    // Why: event_ready_i is tied high, so every byte is ready.
    `UNUSED_BITS(byte_ready_unused, byte_ready)

    always_comb begin
        hit = 8'h00;
        if (key_event.extended) begin
            case (key_event.code)
                CODE_RIGHT: hit[0] = 1'b1;
                CODE_LEFT:  hit[1] = 1'b1;
                CODE_UP:    hit[2] = 1'b1;
                CODE_DOWN:  hit[3] = 1'b1;
                default:    hit = 8'h00;
            endcase
        end else begin
            case (key_event.code)
                CODE_A:      hit[4] = 1'b1;
                CODE_B:      hit[5] = 1'b1;
                CODE_SELECT: hit[6] = 1'b1;
                CODE_START:  hit[7] = 1'b1;
                default:     hit = 8'h00;
            endcase
        end
    end

    always_comb begin
        buttons_d = buttons_q;
        if (event_valid) buttons_d = key_event.key_break ? (buttons_q & ~hit) : (buttons_q | hit);
    end
    `DFF_RST(buttons_q, buttons_d, clk_i, reset_i)
    assign buttons_o = buttons_q;
endmodule
