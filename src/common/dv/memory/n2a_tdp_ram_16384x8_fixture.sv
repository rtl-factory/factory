// Exact nand2linux shared host window byte-plane geometry (16,384 x 8, one
// of four planes of the 64 KiB window) on the public ports of the common
// true-dual-port contract. The cocotb test derives widths from these ports,
// so the same testcase proves this geometry.
module n2a_tdp_ram_16384x8_fixture (
  input  logic        clk_i,
  input  logic        a_write_enable_i,
  input  logic [13:0] a_address_i,
  input  logic [7:0]  a_write_data_i,
  output logic [7:0]  a_read_data_o,
  input  logic        b_write_enable_i,
  input  logic [13:0] b_address_i,
  input  logic [7:0]  b_write_data_i,
  output logic [7:0]  b_read_data_o
);
  n2a_tdp_ram #(
    .DATA_WIDTH(8),
    .DEPTH(16384)
  ) u_ram (
    .clk_i(clk_i),
    .a_write_enable_i(a_write_enable_i),
    .a_address_i(a_address_i),
    .a_write_data_i(a_write_data_i),
    .a_read_data_o(a_read_data_o),
    .b_write_enable_i(b_write_enable_i),
    .b_address_i(b_address_i),
    .b_write_data_i(b_write_data_i),
    .b_read_data_o(b_read_data_o)
  );
endmodule
