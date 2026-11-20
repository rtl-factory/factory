`default_nettype none

// One hexadecimal digit on a DE10-Lite seven-segment display: segments are
// active-low in the board's a..g order on bits 0..6, the decimal point on
// bit 7 (held off). Purely combinational.
module n2m_seven_segment (
  input  logic [3:0] nibble_i,
  output logic [7:0] segments_o
);
  logic [6:0] lit;  // active-high g f e d c b a
  always_comb begin
    case (nibble_i)
      4'h0: lit = 7'b0111111;
      4'h1: lit = 7'b0000110;
      4'h2: lit = 7'b1011011;
      4'h3: lit = 7'b1001111;
      4'h4: lit = 7'b1100110;
      4'h5: lit = 7'b1101101;
      4'h6: lit = 7'b1111101;
      4'h7: lit = 7'b0000111;
      4'h8: lit = 7'b1111111;
      4'h9: lit = 7'b1101111;
      4'hA: lit = 7'b1110111;
      4'hB: lit = 7'b1111100;
      4'hC: lit = 7'b0111001;
      4'hD: lit = 7'b1011110;
      4'hE: lit = 7'b1111001;
      default: lit = 7'b1110001;
    endcase
  end
  assign segments_o = {1'b1, ~lit};
endmodule
