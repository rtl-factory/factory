// Portable backend of the common same-clock simple-dual-port RAM contract.
//
// This file and n2a_sdp_ram_intel.sv define the same module name. A build
// selects exactly one of them: Verilator targets list this file; a Quartus
// project lists the Intel file. Product RTL instantiates `n2a_sdp_ram` and
// never selects a backend itself. The owning contract is
// wiki/src/common/rtl/memory/mas.md.
// The selection boundary needs one module name in two differently named
// files, so the filename lint is waived for this file only.
/* verilator lint_off DECLFILENAME */
`include "src/common/rtl/pkg/macros.vh"

module n2a_sdp_ram_enabled #(
    parameter int unsigned DATA_WIDTH = 32,
    parameter int unsigned DEPTH = 256
) (
    input  logic                                             clk_i,
    input  logic                                             read_enable_i,
    input  logic                                             write_enable_i,
    input  logic [((DEPTH < 2) ? 1 : $clog2(DEPTH))-1:0]    write_address_i,
    input  logic [((DATA_WIDTH < 1) ? 1 : DATA_WIDTH)-1:0]  write_data_i,
    input  logic [((DEPTH < 2) ? 1 : $clog2(DEPTH))-1:0]    read_address_i,
    output logic [((DATA_WIDTH < 1) ? 1 : DATA_WIDTH)-1:0]  read_data_o
);
    localparam int unsigned IMPLEMENTATION_DATA_WIDTH =
        (DATA_WIDTH < 1) ? 1 : DATA_WIDTH;
    localparam int unsigned IMPLEMENTATION_DEPTH = (DEPTH < 2) ? 2 : DEPTH;
    localparam bit DEPTH_IS_POWER_OF_TWO =
        (DEPTH >= 2) && ((DEPTH & (DEPTH - 1)) == 0);

    logic [IMPLEMENTATION_DATA_WIDTH-1:0] memory [0:IMPLEMENTATION_DEPTH-1];

    `ASSERT_PARAM(g_invalid_data_width, DATA_WIDTH >= 1,
        "n2a_sdp_ram DATA_WIDTH must be at least 1")
    `ASSERT_PARAM(g_invalid_depth, DEPTH_IS_POWER_OF_TWO,
        "n2a_sdp_ram DEPTH must be a power of two and at least 2")

    // Why: this is the conventional inferred simple-dual-port template, so it
    // has no reset, initialization or bypass. Nonblocking semantics make a
    // same-edge same-address collision read-first: the output captures the
    // pre-edge word and the write lands afterwards.
    `MEM_READ_EN(read_data_o, memory, clk_i, read_enable_i, read_address_i)
    `MEM_WRITE(memory, clk_i, write_enable_i, write_address_i, write_data_i)
endmodule

// Existing consumers retain the always-active one-edge read contract.
module n2a_sdp_ram #(
    parameter int unsigned DATA_WIDTH = 32,
    parameter int unsigned DEPTH = 256
) (
    input logic clk_i,
    input logic write_enable_i,
    input logic [((DEPTH < 2) ? 1 : $clog2(DEPTH))-1:0] write_address_i,
    input logic [((DATA_WIDTH < 1) ? 1 : DATA_WIDTH)-1:0] write_data_i,
    input logic [((DEPTH < 2) ? 1 : $clog2(DEPTH))-1:0] read_address_i,
    output logic [((DATA_WIDTH < 1) ? 1 : DATA_WIDTH)-1:0] read_data_o
);
    n2a_sdp_ram_enabled #(
        .DATA_WIDTH(DATA_WIDTH), .DEPTH(DEPTH)
    ) u_enabled (
        .clk_i(clk_i), .read_enable_i(1'b1),
        .write_enable_i(write_enable_i), .write_address_i(write_address_i),
        .write_data_i(write_data_i), .read_address_i(read_address_i),
        .read_data_o(read_data_o)
    );
endmodule
