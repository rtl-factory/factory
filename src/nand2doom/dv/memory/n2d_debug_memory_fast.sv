module n2d_debug_memory_fast (
    input  logic        clk_i,
    input  logic        reset_i,
    input  logic        transaction_begin_i,
    input  logic        transaction_commit_i,
    input  logic        transaction_abort_i,
    output logic        transaction_done_o,
    output logic        transaction_error_o,
    input  logic        word_request_valid_i,
    output logic        word_request_ready_o,
    input  logic        word_request_write_i,
    input  logic [31:0] word_request_address_i,
    input  logic [31:0] word_request_write_data_i,
    input  logic [3:0]  word_request_byte_enable_i,
    output logic        word_response_valid_o,
    output logic [31:0] word_response_data_o,
    output logic        word_response_error_o
);
    logic line_request_valid, line_request_ready, line_request_write;
    logic [25:0] line_request_address;
    logic [127:0] line_request_write_data;
    logic [15:0] line_request_byte_enable;
    logic line_response_valid, line_response_error;
    logic [127:0] line_response_data;
    n2d_debug_memory_adapter u_adapter (.*,
        .line_request_valid_o(line_request_valid), .line_request_ready_i(line_request_ready),
        .line_request_write_o(line_request_write), .line_request_address_o(line_request_address),
        .line_request_write_data_o(line_request_write_data),
        .line_request_byte_enable_o(line_request_byte_enable),
        .line_response_valid_i(line_response_valid), .line_response_data_i(line_response_data),
        .line_response_error_i(line_response_error));
    n2d_sdram_fast_model #(.LATENCY_CYCLES(2)) u_model (
        .clk_i, .reset_i, .request_valid_i(line_request_valid),
        .request_ready_o(line_request_ready), .request_write_i(line_request_write),
        .request_address_i(line_request_address), .request_write_data_i(line_request_write_data),
        .request_byte_enable_i(line_request_byte_enable), .force_error_i(1'b0),
        .preload_valid_i(1'b0), .preload_address_i('0),
        .preload_data_i('0), .preload_byte_enable_i('0),
        .response_valid_o(line_response_valid),
        .response_data_o(line_response_data), .response_error_o(line_response_error));
endmodule
