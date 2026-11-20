// Two-bank IHP geometry (8,192 x 16, the Hack screen CPU copy) on the public
// ports of the common write-through RAM contract. The cocotb test derives
// widths from these ports, so one testcase covers every backend.
module n2a_sync_ram_wt_8192x16_fixture (
  input  logic        clk_i,
  input  logic        write_enable_i,
  input  logic [12:0] address_i,
  input  logic [15:0] write_data_i,
  output logic [15:0] read_data_o
);
  n2a_sync_ram_wt #(
    .DATA_WIDTH(16),
    .DEPTH(8192)
  ) u_ram (
    .clk_i(clk_i),
    .write_enable_i(write_enable_i),
    .address_i(address_i),
    .write_data_i(write_data_i),
    .read_data_o(read_data_o)
  );
endmodule
