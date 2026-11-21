`default_nettype none
`include "src/common/rtl/pkg/macros.vh"

// Three-bank frame store: one common same-clock simple-dual-port RAM per
// bank, each holding one 160x144 frame of two-bit shades at the power-of-two
// depth the common contract requires. The write port lands one shade per
// edge into the addressed bank; the read port returns the addressed bank's
// shade one edge later (the bank select travels with the read). Storage has
// no reset or initialization: the frame bridge keeps the display blank until
// a complete frame has been written, and bank ownership keeps a bank that is
// being written out of the read path.
module n2m_frame_ram (
  input  logic                           clk_i,
  input  logic                           reset_i,
  input  logic                           write_enable_i,
  input  n2m_display_pkg::bank_t         write_bank_i,
  input  n2m_display_pkg::frame_index_t  write_index_i,
  input  n2m_display_pkg::shade_t        write_shade_i,
  input  n2m_display_pkg::bank_t         read_bank_i,
  input  n2m_display_pkg::frame_index_t  read_index_i,
  output n2m_display_pkg::shade_t        read_shade_o
);
  import n2m_display_pkg::*;

  shade_t bank_shade [0:BANK_COUNT-1];
  bank_t  read_bank_q;

  // Why: explicit generate block and genvar; Quartus rejects the inline form.
  genvar bank;
  generate
  for (bank = 0; bank < BANK_COUNT; bank = bank + 1) begin : g_banks
    logic write_enable;
    assign write_enable = write_enable_i && (write_bank_i == bank_t'(bank));
    n2a_sdp_ram #(
      .DATA_WIDTH (2),
      .DEPTH      (BANK_DEPTH)
    ) u_ram (
      .clk_i           (clk_i),
      .write_enable_i  (write_enable),
      .write_address_i (write_index_i),
      .write_data_i    (write_shade_i),
      .read_address_i  (read_index_i),
      .read_data_o     (bank_shade[bank])
    );
  end
  endgenerate

  // Why: the selected bank is registered with the read so the output names the
  // word of the bank that was addressed on the read edge.
  `N2A_DFF_RST(read_bank_q, read_bank_i, clk_i, reset_i)
  assign read_shade_o = bank_shade[read_bank_q];

  `N2A_ASSERT_PARAM(g_bank_depth_holds_a_frame, BANK_DEPTH >= FRAME_PIXELS,
      "n2m_frame_ram BANK_DEPTH must hold one complete frame")
  `N2A_ASSERT(write_bank_exists, clk_i, reset_i,
      !write_enable_i || (write_bank_i < bank_t'(BANK_COUNT)))
  `N2A_ASSERT(read_bank_exists, clk_i, reset_i, read_bank_i < bank_t'(BANK_COUNT))
  `N2A_ASSERT(write_index_in_frame, clk_i, reset_i,
      !write_enable_i || (write_index_i < frame_index_t'(FRAME_PIXELS)))
endmodule
