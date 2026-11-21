// Verification-only public-port stimulus for the Python retirement observer.
module n2d_retirement_fixture (
    input logic clk_i, clock_reset_i,
    input logic [63:0] order_i,
    input logic [31:0] tick_i, address_i, data_i,
    input logic [3:0] mask_i,
    input logic valid_i, memory_valid_i, write_i, trap_i,
    input logic [31:0] pc_i, instruction_i, next_pc_i, read_data_i, rd_data_i,
    input logic [4:0] rd_i,
    input logic [3:0] read_mask_i,
    input logic rd_write_i,
    input logic uart_rx_i,
    output logic uart_rx_o,
    output logic [31:0] clock_tick_o,
    output logic [63:0] observation_order_o,
    output logic [31:0] system_tick_o, observation_memory_address_o,
    output logic [31:0] observation_memory_write_data_o,
    output logic [3:0] observation_memory_write_mask_o,
    output logic observation_valid_o, observation_memory_valid_o,
    output logic observation_memory_write_o, observation_trap_o,
    output logic [31:0] observation_pc_o, observation_instruction_o,
    output logic [31:0] observation_next_pc_o, observation_memory_read_data_o,
    output logic [31:0] observation_rd_write_data_o,
    output logic [4:0] observation_rd_address_o,
    output logic [3:0] observation_memory_read_mask_o,
    output logic observation_rd_write_o
);
    // Why: an independent physical-clock witness; tick_i stays an
    // architectural vector field and is never used to manufacture edge
    // ordering, so the observer cannot pass on a fabricated order.
    assign uart_rx_o = uart_rx_i;
    always_ff @(posedge clk_i) begin
        if (clock_reset_i)
            clock_tick_o <= 32'd0;
        else
            clock_tick_o <= clock_tick_o + 32'd1;
        observation_order_o <= order_i;
        system_tick_o <= tick_i;
        observation_memory_address_o <= address_i;
        observation_memory_write_data_o <= data_i;
        observation_memory_write_mask_o <= mask_i;
        observation_valid_o <= valid_i;
        observation_memory_valid_o <= memory_valid_i;
        observation_memory_write_o <= write_i;
        observation_trap_o <= trap_i;
        observation_pc_o <= pc_i;
        observation_instruction_o <= instruction_i;
        observation_next_pc_o <= next_pc_i;
        observation_memory_read_data_o <= read_data_i;
        observation_rd_write_data_o <= rd_data_i;
        observation_rd_address_o <= rd_i;
        observation_memory_read_mask_o <= read_mask_i;
        observation_rd_write_o <= rd_write_i;
    end
endmodule
