// Byte geometry (8,192 x 8, the nand2mario VRAM and WRAM store) on the
// public ports of the common write-through RAM contract; the IHP backend
// packs two bytes per macro row. The cocotb test derives widths from these
// ports, so one testcase covers every backend.
module n2a_sync_ram_wt_8192x8_fixture (
  input  logic        clk_i,
  input  logic        write_enable_i,
  input  logic [12:0] address_i,
  input  logic [7:0]  write_data_i,
  output logic [7:0]  read_data_o
);
  n2a_sync_ram_wt #(
    .DATA_WIDTH(8),
    .DEPTH(8192)
  ) u_ram (
    .clk_i(clk_i),
    .write_enable_i(write_enable_i),
    .address_i(address_i),
    .write_data_i(write_data_i),
    .read_data_o(read_data_o)
  );
endmodule
