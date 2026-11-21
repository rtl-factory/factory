`default_nettype none
`include "src/common/rtl/pkg/macros.vh"

// Set-2 keyboard byte stream to Game Boy button levels. Each byte arrives
// with a one-cycle valid; E0 marks the next code as extended and F0 marks
// it as a release, exactly as a PS/2 keyboard sends them. A code the map
// below names sets (make) or clears (break) its button; every other code is
// consumed and ignored. The prefix state is cleared by the byte that
// completes an event and by reset. Buttons are active-high in the joypad
// order (wiki/src/nand2mario/rtl/joypad/mas.md#button-input):
//   Right/Left/Up/Down: the extended arrow keys; A: X; B: Z;
//   Select: Space; Start: Enter.
// Board mapping policy lives in wiki/src/nand2mario/fpga/<board>/board-spec.md.
module n2m_scancode_buttons (
  input  logic       clk_i,
  input  logic       reset_i,
  input  logic       byte_valid_i,
  input  logic [7:0] byte_i,
  output logic [7:0] buttons_o
);
  localparam logic [7:0] PREFIX_EXTENDED = 8'hE0;
  localparam logic [7:0] PREFIX_RELEASE  = 8'hF0;
  localparam logic [7:0] CODE_RIGHT  = 8'h74;  // extended
  localparam logic [7:0] CODE_LEFT   = 8'h6B;  // extended
  localparam logic [7:0] CODE_UP     = 8'h75;  // extended
  localparam logic [7:0] CODE_DOWN   = 8'h72;  // extended
  localparam logic [7:0] CODE_A      = 8'h22;  // X
  localparam logic [7:0] CODE_B      = 8'h1A;  // Z
  localparam logic [7:0] CODE_SELECT = 8'h29;  // Space
  localparam logic [7:0] CODE_START  = 8'h5A;  // Enter

  logic       extended_q, extended_d, release_q, release_d;
  logic [7:0] buttons_q, buttons_d;
  logic [7:0] hit;

  always_comb begin
    hit = 8'h00;
    if (extended_q) begin
      case (byte_i)
        CODE_RIGHT: hit[0] = 1'b1;
        CODE_LEFT:  hit[1] = 1'b1;
        CODE_UP:    hit[2] = 1'b1;
        CODE_DOWN:  hit[3] = 1'b1;
        default:    hit = 8'h00;
      endcase
    end else begin
      case (byte_i)
        CODE_A:      hit[4] = 1'b1;
        CODE_B:      hit[5] = 1'b1;
        CODE_SELECT: hit[6] = 1'b1;
        CODE_START:  hit[7] = 1'b1;
        default:     hit = 8'h00;
      endcase
    end
  end

  always_comb begin
    extended_d = extended_q;
    release_d = release_q;
    buttons_d = buttons_q;
    if (byte_valid_i) begin
      if (byte_i == PREFIX_EXTENDED) extended_d = 1'b1;
      else if (byte_i == PREFIX_RELEASE) release_d = 1'b1;
      else begin
        extended_d = 1'b0;
        release_d = 1'b0;
        buttons_d = release_q ? (buttons_q & ~hit) : (buttons_q | hit);
      end
    end
  end
  `DFF_RST(extended_q, extended_d, clk_i, reset_i)
  `DFF_RST(release_q, release_d, clk_i, reset_i)
  `DFF_RST(buttons_q, buttons_d, clk_i, reset_i)
  assign buttons_o = buttons_q;

  `ASSERT(byte_known, clk_i, reset_i, !byte_valid_i || !$isunknown(byte_i))
  `ASSERT(event_clears_prefixes, clk_i, reset_i,
      !(byte_valid_i && (byte_i != PREFIX_EXTENDED) && (byte_i != PREFIX_RELEASE)) ||
      (!extended_d && !release_d))
endmodule
