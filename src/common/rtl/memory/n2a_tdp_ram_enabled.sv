// Common same-clock true-dual-port RAM with read requests: the n2a_tdp_ram
// store plus, per port, a read enable, a registered read-valid, a held read
// word and a reset that suppresses that port's requests. The owning contract
// is wiki/src/common/rtl/memory/mas.md. It is backend-neutral: the build's
// source list picks the n2a_tdp_ram backend it instantiates.
`include "src/common/rtl/pkg/macros.vh"

module n2a_tdp_ram_enabled #(
    parameter int unsigned DATA_WIDTH = 32,
    parameter int unsigned DEPTH = 256
) (
    input  logic                                             clk_i,
    input  logic                                             a_reset_i,
    input  logic                                             a_read_enable_i,
    input  logic                                             a_write_enable_i,
    input  logic [((DEPTH < 2) ? 1 : $clog2(DEPTH))-1:0]    a_address_i,
    input  logic [((DATA_WIDTH < 1) ? 1 : DATA_WIDTH)-1:0]  a_write_data_i,
    output logic [((DATA_WIDTH < 1) ? 1 : DATA_WIDTH)-1:0]  a_read_data_o,
    output logic                                             a_read_valid_o,
    input  logic                                             b_reset_i,
    input  logic                                             b_read_enable_i,
    input  logic                                             b_write_enable_i,
    input  logic [((DEPTH < 2) ? 1 : $clog2(DEPTH))-1:0]    b_address_i,
    input  logic [((DATA_WIDTH < 1) ? 1 : DATA_WIDTH)-1:0]  b_write_data_i,
    output logic [((DATA_WIDTH < 1) ? 1 : DATA_WIDTH)-1:0]  b_read_data_o,
    output logic                                             b_read_valid_o
);
    logic                           a_write_enable, b_write_enable;
    logic [$bits(a_read_data_o)-1:0] a_store_data, b_store_data, a_held_q, b_held_q;

    // Why: a request under a port's reset must not change the store or that
    // port's outputs, so the reset drops the write and clears that port's
    // read-valid register (its word holds). The resets are per port so one
    // owner (a loader) can keep its port through the other owner's reset.
    assign a_write_enable = a_write_enable_i && !a_reset_i;
    assign b_write_enable = b_write_enable_i && !b_reset_i;

    n2a_tdp_ram #(
        .DATA_WIDTH (DATA_WIDTH),
        .DEPTH      (DEPTH)
    ) u_ram (
        .clk_i            (clk_i),
        .a_write_enable_i (a_write_enable),
        .a_address_i      (a_address_i),
        .a_write_data_i   (a_write_data_i),
        .a_read_data_o    (a_store_data),
        .b_write_enable_i (b_write_enable),
        .b_address_i      (b_address_i),
        .b_write_data_i   (b_write_data_i),
        .b_read_data_o    (b_store_data)
    );

    // Why: an M9K in true-dual-port mode has no per-port read enable, so the
    // store reads both addresses on every edge and each port's enable, valid
    // and hold live in these registers: the word of an enabled read is
    // presented with valid, and it stays on the output until the port's next
    // enabled read.
    `DFF_RST(a_read_valid_o, a_read_enable_i, clk_i, a_reset_i)
    assign a_read_data_o = a_read_valid_o ? a_store_data : a_held_q;
    `DFF(a_held_q, a_read_data_o, clk_i)
    `DFF_RST(b_read_valid_o, b_read_enable_i, clk_i, b_reset_i)
    assign b_read_data_o = b_read_valid_o ? b_store_data : b_held_q;
    `DFF(b_held_q, b_read_data_o, clk_i)

    `ASSERT_KNOWN(a_control_known, clk_i, a_reset_i, {a_read_enable_i, a_write_enable_i})
    `ASSERT(a_address_known, clk_i, a_reset_i,
        !(a_read_enable_i || a_write_enable_i) || !$isunknown(a_address_i))
    `ASSERT(a_write_data_known, clk_i, a_reset_i,
        !a_write_enable_i || !$isunknown(a_write_data_i))
    `ASSERT_KNOWN(b_control_known, clk_i, b_reset_i, {b_read_enable_i, b_write_enable_i})
    `ASSERT(b_address_known, clk_i, b_reset_i,
        !(b_read_enable_i || b_write_enable_i) || !$isunknown(b_address_i))
    `ASSERT(b_write_data_known, clk_i, b_reset_i,
        !b_write_enable_i || !$isunknown(b_write_data_i))
    // Why: the store promises no winner for two writes to one word in one edge.
    `ASSERT_NEVER(write_collision, clk_i, a_reset_i || b_reset_i,
        a_write_enable_i && b_write_enable_i && (a_address_i == b_address_i))
endmodule
