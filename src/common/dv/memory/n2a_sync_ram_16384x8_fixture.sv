// Exact nand2doom coincident-address geometry (16384 x 8) on the public
// ports of the common single-port contract; the cocotb test derives widths
// from these ports.
module n2a_sync_ram_16384x8_fixture (
    input  logic          clk_i,
    input  logic          write_enable_i,
    input  logic [13:0]   address_i,
    input  logic [7:0] write_data_i,
    output logic [7:0] read_data_o
);
    n2a_sync_ram #(
        .DATA_WIDTH(8),
        .DEPTH(16384)
    ) u_ram (
        .clk_i(clk_i),
        .write_enable_i(write_enable_i),
        .address_i(address_i),
        .write_data_i(write_data_i),
        .read_data_o(read_data_o)
    );
endmodule
