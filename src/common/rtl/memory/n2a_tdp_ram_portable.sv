// Portable backend of the common same-clock true-dual-port RAM contract.
//
// This file and n2a_tdp_ram_intel.sv define the same module name. A build
// selects exactly one of them: Verilator targets list this file; a Quartus
// project lists the Intel file. Product RTL instantiates `n2a_tdp_ram` and
// never selects a backend itself. The owning contract is
// wiki/src/common/rtl/memory/mas.md.
// The selection boundary needs one module name in two differently named
// files, so the filename lint is waived for this file only.
/* verilator lint_off DECLFILENAME */
`include "src/common/rtl/pkg/macros.vh"

module n2a_tdp_ram #(
    parameter int unsigned DATA_WIDTH = 32,
    parameter int unsigned DEPTH = 256,
    parameter int unsigned ADDR_WIDTH = $clog2(DEPTH)
) (
    input  logic                                             clk_i,
    input  logic                                             a_write_enable_i,
    input  logic [((ADDR_WIDTH < 1) ? 1 : ADDR_WIDTH)-1:0]  a_address_i,
    input  logic [((DATA_WIDTH < 1) ? 1 : DATA_WIDTH)-1:0]  a_write_data_i,
    output logic [((DATA_WIDTH < 1) ? 1 : DATA_WIDTH)-1:0]  a_read_data_o,
    input  logic                                             b_write_enable_i,
    input  logic [((ADDR_WIDTH < 1) ? 1 : ADDR_WIDTH)-1:0]  b_address_i,
    input  logic [((DATA_WIDTH < 1) ? 1 : DATA_WIDTH)-1:0]  b_write_data_i,
    output logic [((DATA_WIDTH < 1) ? 1 : DATA_WIDTH)-1:0]  b_read_data_o
);
    localparam int unsigned IMPLEMENTATION_DATA_WIDTH =
        (DATA_WIDTH < 1) ? 1 : DATA_WIDTH;
    localparam int unsigned IMPLEMENTATION_DEPTH = (DEPTH < 2) ? 2 : DEPTH;
    localparam int unsigned REQUIRED_ADDR_WIDTH =
        (DEPTH < 2) ? 1 : $clog2(DEPTH);
    localparam bit DEPTH_IS_POWER_OF_TWO =
        (DEPTH >= 2) && ((DEPTH & (DEPTH - 1)) == 0);

    logic [IMPLEMENTATION_DATA_WIDTH-1:0] memory [0:IMPLEMENTATION_DEPTH-1];
    logic [REQUIRED_ADDR_WIDTH-1:0] a_address;
    logic [REQUIRED_ADDR_WIDTH-1:0] b_address;

    // Why: explicit sizes keep an invalid ADDR_WIDTH probe lint-clean until its
    // elaboration diagnostic fires. Both are no-ops for supported values.
    assign a_address = REQUIRED_ADDR_WIDTH'(a_address_i);
    assign b_address = REQUIRED_ADDR_WIDTH'(b_address_i);

    `ASSERT_PARAM(g_invalid_data_width, DATA_WIDTH >= 1,
        "n2a_tdp_ram DATA_WIDTH must be at least 1")
    `ASSERT_PARAM(g_invalid_depth, DEPTH_IS_POWER_OF_TWO,
        "n2a_tdp_ram DEPTH must be a power of two and at least 2")
    `ASSERT_PARAM(g_invalid_addr_width, ADDR_WIDTH == REQUIRED_ADDR_WIDTH,
        "n2a_tdp_ram ADDR_WIDTH must equal $clog2(DEPTH)")

    // Why: this is the conventional inferred true-dual-port template, one read
    // and one write process per port. Nonblocking semantics make every
    // same-edge collision read-first: each port's output captures the pre-edge
    // word (same-port and mixed-port old data) and the enabled writes land
    // afterwards. Two enabled writes to one address in one edge are forbidden
    // by the contract; the process order below is not a promise of a winner.
    // There is no reset, enable, initialization, or bypass path.
    `MEM_READ(a_read_data_o, memory, clk_i, a_address)
    `MEM_WRITE(memory, clk_i, a_write_enable_i, a_address, a_write_data_i)
    `MEM_READ(b_read_data_o, memory, clk_i, b_address)
    `MEM_WRITE(memory, clk_i, b_write_enable_i, b_address, b_write_data_i)
endmodule
