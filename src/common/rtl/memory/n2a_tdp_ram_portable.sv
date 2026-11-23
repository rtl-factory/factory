// Portable backend of the common same-clock true-dual-port RAM contract.
//
// This file and n2a_tdp_ram_intel.sv define the same module name. A build
// selects exactly one of them: Verilator targets list this file; a Quartus
// project lists the Intel file. Product RTL instantiates `n2a_tdp_ram` and
// never selects a backend itself. The owning contract is
// wiki/src/common/memory/HAS.md.
// The selection boundary needs one module name in two differently named
// files, so the filename lint is waived for this file only.
/* verilator lint_off DECLFILENAME */
`include "src/common/rtl/pkg/macros.vh"

module n2a_tdp_ram #(
    parameter int unsigned DATA_WIDTH = 32,
    parameter int unsigned DEPTH = 256
) (
    input  logic                                             clk_i,
    input  logic                                             a_write_enable_i,
    input  logic [((DEPTH < 2) ? 1 : $clog2(DEPTH))-1:0]    a_address_i,
    input  logic [((DATA_WIDTH < 1) ? 1 : DATA_WIDTH)-1:0]  a_write_data_i,
    output logic [((DATA_WIDTH < 1) ? 1 : DATA_WIDTH)-1:0]  a_read_data_o,
    input  logic                                             b_write_enable_i,
    input  logic [((DEPTH < 2) ? 1 : $clog2(DEPTH))-1:0]    b_address_i,
    input  logic [((DATA_WIDTH < 1) ? 1 : DATA_WIDTH)-1:0]  b_write_data_i,
    output logic [((DATA_WIDTH < 1) ? 1 : DATA_WIDTH)-1:0]  b_read_data_o
);
    localparam int unsigned IMPLEMENTATION_DATA_WIDTH =
        (DATA_WIDTH < 1) ? 1 : DATA_WIDTH;
    localparam int unsigned IMPLEMENTATION_DEPTH = (DEPTH < 2) ? 2 : DEPTH;
    localparam bit DEPTH_IS_POWER_OF_TWO =
        (DEPTH >= 2) && ((DEPTH & (DEPTH - 1)) == 0);

    logic [IMPLEMENTATION_DATA_WIDTH-1:0] memory [0:IMPLEMENTATION_DEPTH-1];

    `ASSERT_PARAM(g_invalid_data_width, DATA_WIDTH >= 1,
        "n2a_tdp_ram DATA_WIDTH must be at least 1")
    `ASSERT_PARAM(g_invalid_depth, DEPTH_IS_POWER_OF_TWO,
        "n2a_tdp_ram DEPTH must be a power of two and at least 2")

    // Why: this is the conventional inferred true-dual-port template, one read
    // and one write process per port. Nonblocking semantics make every
    // same-edge collision read-first: each port's output captures the pre-edge
    // word (same-port and mixed-port old data) and the enabled writes land
    // afterwards. Two enabled writes to one address in one edge are forbidden
    // by the contract; the process order below is not a promise of a winner.
    // There is no reset, enable, initialization, or bypass path.
    `MEM_READ(a_read_data_o, memory, clk_i, a_address_i)
    `MEM_WRITE(memory, clk_i, a_write_enable_i, a_address_i, a_write_data_i)
    `MEM_READ(b_read_data_o, memory, clk_i, b_address_i)
    `MEM_WRITE(memory, clk_i, b_write_enable_i, b_address_i, b_write_data_i)
endmodule
