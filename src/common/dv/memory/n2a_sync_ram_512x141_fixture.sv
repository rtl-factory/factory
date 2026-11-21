// Exact nand2doom coincident-address geometry (512 x 141) on the public
// ports of the common single-port contract; the cocotb test derives widths
// from these ports.
module n2a_sync_ram_512x141_fixture (
    input  logic          clk_i,
    input  logic          write_enable_i,
    input  logic [8:0]   address_i,
    input  logic [140:0] write_data_i,
    output logic [140:0] read_data_o
);
    n2a_sync_ram #(
        .DATA_WIDTH(141),
        .DEPTH(512)
    ) u_ram (
        .clk_i(clk_i),
        .write_enable_i(write_enable_i),
        .address_i(address_i),
        .write_data_i(write_data_i),
        .read_data_o(read_data_o)
    );
endmodule
