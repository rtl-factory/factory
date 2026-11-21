`include "src/common/rtl/pkg/macros.vh"

module n2a_rv32_regfile (
    input  logic        clk_i,
    input  logic        rst_i,
    input  logic [4:0]  read_a_index_i,
    output logic [31:0] read_a_data_o,
    input  logic [4:0]  read_b_index_i,
    output logic [31:0] read_b_data_o,
    input  logic        write_enable_i,
    input  logic [4:0]  write_index_i,
    input  logic [31:0] write_data_i
);
    logic [31:0] registers_q [1:31];
    logic write_x1_to_x31;

    always_comb begin
        read_a_data_o = (read_a_index_i == 5'd0)
            ? 32'h0000_0000
            : registers_q[read_a_index_i];
        read_b_data_o = (read_b_index_i == 5'd0)
            ? 32'h0000_0000
            : registers_q[read_b_index_i];
    end

    assign write_x1_to_x31 = write_enable_i && (write_index_i != 5'd0);

    `N2A_MEM_WRITE_RST(registers_q, clk_i, write_x1_to_x31, write_index_i,
        write_data_i, rst_i)

    `N2A_ASSERT_KNOWN(read_a_index_known, clk_i, rst_i, read_a_index_i)
    `N2A_ASSERT_KNOWN(read_b_index_known, clk_i, rst_i, read_b_index_i)
    `N2A_ASSERT_KNOWN(write_enable_known, clk_i, rst_i, write_enable_i)
    `N2A_ASSERT(
        enabled_write_known,
        clk_i,
        rst_i,
        !write_enable_i || !$isunknown({write_index_i, write_data_i})
    )
    `N2A_ASSERT_NO_RESET(
        read_a_zero_is_zero,
        clk_i,
        (read_a_index_i != 5'd0) || (read_a_data_o == 32'h0000_0000)
    )
    `N2A_ASSERT_NO_RESET(
        read_b_zero_is_zero,
        clk_i,
        (read_b_index_i != 5'd0) || (read_b_data_o == 32'h0000_0000)
    )
endmodule
