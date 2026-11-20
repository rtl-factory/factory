module n2d_memory_fast_subsystem (
    input  logic         clk_i,
    input  logic         reset_i,
    input  logic [2:0]   master_request_valid_i,
    input  logic [2:0]   master_request_prefetch_i,
    output logic [2:0]   master_request_ready_o,
    input  logic [2:0]   master_request_write_i,
    input  logic [77:0]  master_request_address_i,
    input  logic [383:0] master_request_write_data_i,
    input  logic [47:0]  master_request_byte_enable_i,
    input  logic         force_error_i,
    output logic [2:0]   master_response_valid_o,
    output logic [383:0] master_response_data_o,
    output logic [2:0]   master_response_error_o
);
    logic req_valid, req_ready, req_write;
    logic [25:0] req_address;
    logic [127:0] req_write_data;
    logic [15:0] req_byte_enable;
    logic rsp_valid, rsp_error;
    logic [127:0] rsp_data;

    n2d_memory_arbiter u_arbiter (.*,
        .memory_request_valid_o(req_valid), .memory_request_ready_i(req_ready),
        .memory_request_write_o(req_write), .memory_request_address_o(req_address),
        .memory_request_write_data_o(req_write_data), .memory_request_byte_enable_o(req_byte_enable),
        .memory_response_valid_i(rsp_valid), .memory_response_data_i(rsp_data),
        .memory_response_error_i(rsp_error));
    n2d_sdram_fast_model #(.LATENCY_CYCLES(3)) u_model (
        .clk_i, .reset_i, .request_valid_i(req_valid), .request_ready_o(req_ready),
        .request_write_i(req_write), .request_address_i(req_address),
        .request_write_data_i(req_write_data), .request_byte_enable_i(req_byte_enable),
        .preload_valid_i(1'b0), .preload_address_i('0),
        .preload_data_i('0), .preload_byte_enable_i('0),
        .force_error_i,
        .response_valid_o(rsp_valid), .response_data_o(rsp_data), .response_error_o(rsp_error));
endmodule
