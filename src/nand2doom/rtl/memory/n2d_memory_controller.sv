module n2d_memory_controller (
    input  logic         clk_i,
    input  logic         reset_i,
    input  logic         instruction_request_valid_i,
    output logic         instruction_request_ready_o,
    input  logic [25:0]  instruction_request_address_i,
    output logic         instruction_response_valid_o,
    output logic [127:0] instruction_response_data_o,
    output logic         instruction_response_error_o,
    input  logic         data_request_valid_i,
    // The data request is a next-line prefetch (lowest arbitration priority).
    input  logic         data_request_prefetch_i,
    output logic         data_request_ready_o,
    input  logic         data_request_write_i,
    input  logic [25:0]  data_request_address_i,
    input  logic [127:0] data_request_write_data_i,
    input  logic [15:0]  data_request_byte_enable_i,
    output logic         data_response_valid_o,
    output logic [127:0] data_response_data_o,
    output logic         data_response_error_o,
    input  logic         debug_request_valid_i,
    output logic         debug_request_ready_o,
    input  logic         debug_request_write_i,
    input  logic [25:0]  debug_request_address_i,
    input  logic [127:0] debug_request_write_data_i,
    input  logic [15:0]  debug_request_byte_enable_i,
    output logic         debug_response_valid_o,
    output logic [127:0] debug_response_data_o,
    output logic         debug_response_error_o,
    output logic         init_done_o,
    output logic [12:0]  sdram_address_o,
    output logic [1:0]   sdram_bank_o,
    output logic         sdram_cke_o,
    output logic         sdram_cs_n_o,
    output logic         sdram_ras_n_o,
    output logic         sdram_cas_n_o,
    output logic         sdram_we_n_o,
    output logic [1:0]   sdram_dqm_o,
    output logic [15:0]  sdram_dq_o,
    output logic         sdram_dq_oe_o,
    input  logic [15:0]  sdram_dq_i
);
    logic [2:0] master_request_valid, master_request_ready, master_request_write;
    logic [77:0] master_request_address;
    logic [383:0] master_request_write_data;
    logic [47:0] master_request_byte_enable;
    logic [2:0] master_response_valid, master_response_error;
    logic [383:0] master_response_data;
    logic memory_request_valid, memory_request_ready, memory_request_write;
    logic [25:0] memory_request_address;
    logic [127:0] memory_request_write_data;
    logic [15:0] memory_request_byte_enable;
    logic memory_response_valid, memory_response_error;
    logic [127:0] memory_response_data;

    assign master_request_valid = {debug_request_valid_i, data_request_valid_i,
                                   instruction_request_valid_i};
    assign master_request_write = {debug_request_write_i, data_request_write_i, 1'b0};
    assign master_request_address = {debug_request_address_i, data_request_address_i,
                                     instruction_request_address_i};
    assign master_request_write_data = {debug_request_write_data_i, data_request_write_data_i,
                                        128'b0};
    assign master_request_byte_enable = {debug_request_byte_enable_i,
                                         data_request_byte_enable_i, 16'b0};
    assign instruction_request_ready_o = master_request_ready[0];
    assign data_request_ready_o = master_request_ready[1];
    assign debug_request_ready_o = master_request_ready[2];
    assign instruction_response_valid_o = master_response_valid[0];
    assign data_response_valid_o = master_response_valid[1];
    assign debug_response_valid_o = master_response_valid[2];
    assign instruction_response_data_o = master_response_data[127:0];
    assign data_response_data_o = master_response_data[255:128];
    assign debug_response_data_o = master_response_data[383:256];
    assign instruction_response_error_o = master_response_error[0];
    assign data_response_error_o = master_response_error[1];
    assign debug_response_error_o = master_response_error[2];

    n2d_memory_arbiter u_arbiter (
        .clk_i, .reset_i,
        .master_request_valid_i(master_request_valid),
        .master_request_prefetch_i({1'b0, data_request_prefetch_i, 1'b0}),
        .master_request_ready_o(master_request_ready),
        .master_request_write_i(master_request_write),
        .master_request_address_i(master_request_address),
        .master_request_write_data_i(master_request_write_data),
        .master_request_byte_enable_i(master_request_byte_enable),
        .master_response_valid_o(master_response_valid),
        .master_response_data_o(master_response_data),
        .master_response_error_o(master_response_error),
        .memory_request_valid_o(memory_request_valid),
        .memory_request_ready_i(memory_request_ready),
        .memory_request_write_o(memory_request_write),
        .memory_request_address_o(memory_request_address),
        .memory_request_write_data_o(memory_request_write_data),
        .memory_request_byte_enable_o(memory_request_byte_enable),
        .memory_response_valid_i(memory_response_valid),
        .memory_response_data_i(memory_response_data),
        .memory_response_error_i(memory_response_error));

    n2d_sdram_controller u_sdram (
        .clk_i, .reset_i,
        .request_valid_i(memory_request_valid), .request_ready_o(memory_request_ready),
        .request_write_i(memory_request_write), .request_address_i(memory_request_address),
        .request_write_data_i(memory_request_write_data),
        .request_byte_enable_i(memory_request_byte_enable),
        .response_valid_o(memory_response_valid), .response_data_o(memory_response_data),
        .response_error_o(memory_response_error), .init_done_o,
        .sdram_address_o, .sdram_bank_o, .sdram_cke_o, .sdram_cs_n_o,
        .sdram_ras_n_o, .sdram_cas_n_o, .sdram_we_n_o, .sdram_dqm_o,
        .sdram_dq_o, .sdram_dq_oe_o, .sdram_dq_i);
endmodule
