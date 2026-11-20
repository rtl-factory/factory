module n2m_dual_port_ram_single_fixture (
  input  logic        clk_a_i,
  input  logic        clk_b_i,
  input  logic        reset_a_i,
  input  logic        reset_b_i,
  input  logic        read_a_i,
  input  logic        write_a_i,
  input  logic [3:0]  address_a_i,
  input  logic [31:0] write_data_a_i,
  input  logic [3:0]  byte_enable_a_i,
  output logic [31:0] read_data_a_o,
  output logic        read_valid_a_o,
  input  logic        read_b_i,
  input  logic [3:0]  address_b_i,
  output logic [31:0] read_data_b_o,
  output logic        read_valid_b_o
);
  n2m_dual_port_ram #(
    .DATA_WIDTH(32),
    .DEPTH(16),
    .DUAL_CLOCK(1'b0)
  ) dut (.*);
endmodule
