// Common single-port RAM with a read request: the n2a_sync_ram store plus a
// read enable, a registered read-valid, a held read word and a reset that
// suppresses requests. The owning contract is
// wiki/src/common/rtl/memory/mas.md. It is backend-neutral: the build's
// source list picks the n2a_sync_ram backend it instantiates.
`include "src/common/rtl/pkg/macros.vh"

module n2a_sync_ram_enabled #(
    parameter int unsigned DATA_WIDTH = 32,
    parameter int unsigned DEPTH = 256
) (
    input  logic                                             clk_i,
    input  logic                                             reset_i,
    input  logic                                             read_enable_i,
    input  logic                                             write_enable_i,
    input  logic [((DEPTH < 2) ? 1 : $clog2(DEPTH))-1:0]    address_i,
    input  logic [((DATA_WIDTH < 1) ? 1 : DATA_WIDTH)-1:0]  write_data_i,
    output logic [((DATA_WIDTH < 1) ? 1 : DATA_WIDTH)-1:0]  read_data_o,
    output logic                                             read_valid_o
);
    logic                         write_enable;
    logic [$bits(read_data_o)-1:0] store_data, held_q;

    // Why: a request under reset must not change the store or the outputs, so
    // reset drops the write and clears the read-valid register (the word holds).
    assign write_enable = write_enable_i && !reset_i;

    n2a_sync_ram #(
        .DATA_WIDTH (DATA_WIDTH),
        .DEPTH      (DEPTH)
    ) u_ram (
        .clk_i          (clk_i),
        .write_enable_i (write_enable),
        .address_i      (address_i),
        .write_data_i   (write_data_i),
        .read_data_o    (store_data)
    );

    // Why: the store reads its address on every edge (an M9K in these modes has
    // no output hold), so the read enable, valid and hold live in these
    // registers: the word of an enabled read is presented with valid, and it
    // stays on the output until the next enabled read.
    `DFF_RST(read_valid_o, read_enable_i, clk_i, reset_i)
    assign read_data_o = read_valid_o ? store_data : held_q;
    `DFF(held_q, read_data_o, clk_i)

    `ASSERT_KNOWN(control_known, clk_i, reset_i, {read_enable_i, write_enable_i})
    `ASSERT(address_known, clk_i, reset_i,
        !(read_enable_i || write_enable_i) || !$isunknown(address_i))
    `ASSERT(write_data_known, clk_i, reset_i,
        !write_enable_i || !$isunknown(write_data_i))
endmodule
