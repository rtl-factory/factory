// Public-pin composition of the common endpoint with a test-driven byte sink:
// the test owns inject_ready_i, so it can accept, refuse or stall each offer.
module n2a_jtag_keyboard_fixture (
  input logic clk_i, reset_i, tck_i,
  input logic [1:0] ir_i,
  input logic capture_i, shift_i, update_i, update_ir_i, tdi_i,
  output logic tdo_o,
  output logic inject_valid_o,
  output logic [7:0] inject_byte_o,
  input logic inject_ready_i
);
  n2a_jtag_keyboard u_endpoint (
    .clk_i(clk_i), .reset_i(reset_i), .tck_i(tck_i), .ir_i(ir_i),
    .capture_i(capture_i), .shift_i(shift_i), .update_i(update_i),
    .update_ir_i(update_ir_i), .tdi_i(tdi_i), .tdo_o(tdo_o),
    .inject_valid_o(inject_valid_o), .inject_byte_o(inject_byte_o), .inject_ready_i(inject_ready_i));
endmodule
