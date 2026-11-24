// Portable backend of the common write-through single-port RAM contract
// owned by wiki/src/common/rtl/memory/mas.md: one clock, one-edge registered read
// on every edge, optional full-word write, and a same-edge same-address
// collision returns the NEW word (write-through). No reset or initialization.
//
// This file and n2a_sync_ram_wt_ihp.sv define the same module name. Verilator
// targets list this file; only an IHP ASIC source list selects the IHP file.
// Product RTL instantiates `n2a_sync_ram_wt` and never selects a backend.
// The selection boundary needs one module name in differently named files,
// so the filename lint is waived for this file only.
/* verilator lint_off DECLFILENAME */
`include "src/common/rtl/pkg/macros.vh"

module n2a_sync_ram_wt #(
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
    logic [IMPLEMENTATION_DATA_WIDTH-1:0] stored_q;
    logic [IMPLEMENTATION_DATA_WIDTH-1:0] written_q;
    logic                                 wrote_q;

    `ASSERT_PARAM(g_invalid_data_width, DATA_WIDTH >= 1,
        "n2a_sync_ram_wt DATA_WIDTH must be at least 1")
    `ASSERT_PARAM(g_invalid_depth, DEPTH_IS_POWER_OF_TWO,
        "n2a_sync_ram_wt DEPTH must be a power of two and at least 2")

    // Why: the array keeps the conventional read-first port pair so it still
    // infers as a plain RAM; the written word
    // and a one-bit "this edge wrote" flag are registered beside it and select
    // the new word on a write edge. The write always targets the read address
    // (single port), so this is exactly write-through.
    `MEM_READ(stored_q, memory, clk_i, address_i)
    `MEM_WRITE(memory, clk_i, write_enable_i, address_i,
        write_data_i)
    `DFF(written_q, write_data_i, clk_i)
    `DFF(wrote_q, write_enable_i, clk_i)

    assign read_data_o = wrote_q ? written_q : stored_q;
endmodule
