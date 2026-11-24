`include "src/common/rtl/pkg/macros.vh"

module n2a_sync_ram #(
    parameter int unsigned DATA_WIDTH = 32,
    parameter int unsigned DEPTH = 256
) (
    input  logic                                             clk_i,
    input  logic                                             write_enable_i,
    input  logic [((DEPTH < 2) ? 1 : $clog2(DEPTH))-1:0]    address_i,
    input  logic [((DATA_WIDTH < 1) ? 1 : DATA_WIDTH)-1:0]  write_data_i,
    output logic [((DATA_WIDTH < 1) ? 1 : DATA_WIDTH)-1:0]  read_data_o
);
    localparam int unsigned IMPLEMENTATION_DATA_WIDTH =
        (DATA_WIDTH < 1) ? 1 : DATA_WIDTH;
    localparam int unsigned IMPLEMENTATION_DEPTH = (DEPTH < 2) ? 2 : DEPTH;
    localparam bit DEPTH_IS_POWER_OF_TWO =
        (DEPTH >= 2) && ((DEPTH & (DEPTH - 1)) == 0);

    logic [IMPLEMENTATION_DATA_WIDTH-1:0] memory [0:IMPLEMENTATION_DEPTH-1];

    `ASSERT_PARAM(g_invalid_data_width, DATA_WIDTH >= 1,
        "n2a_sync_ram DATA_WIDTH must be at least 1")
    `ASSERT_PARAM(g_invalid_depth, DEPTH_IS_POWER_OF_TWO,
        "n2a_sync_ram DEPTH must be a power of two and at least 2")

    // Why: separate read and write processes on one array make a same-edge
    // collision read-first (old data) by nonblocking semantics, with no bypass.
    `MEM_READ(read_data_o, memory, clk_i, address_i)
    `MEM_WRITE(memory, clk_i, write_enable_i, address_i,
        write_data_i)
endmodule
