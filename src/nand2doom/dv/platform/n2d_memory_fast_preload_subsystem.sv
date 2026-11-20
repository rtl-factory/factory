`ifndef SYNTHESIS
module n2d_memory_fast_preload_subsystem (
    input logic clk_i, input logic reset_i,
    input logic [2:0] master_request_valid_i,
    input logic [2:0] master_request_prefetch_i,
    output logic [2:0] master_request_ready_o,
    input logic [2:0] master_request_write_i,
    input logic [77:0] master_request_address_i,
    input logic [383:0] master_request_write_data_i,
    input logic [47:0] master_request_byte_enable_i,
    input logic force_error_i,
    input logic preload_mode_i, input logic preload_valid_i,
    output logic preload_ready_o, input logic [25:0] preload_address_i,
    input logic [2047:0] preload_data_i,
    input logic [255:0] preload_byte_enable_i,
    output logic preload_response_valid_o,
    output logic preload_response_error_o,
    output logic [2:0] master_response_valid_o,
    output logic [383:0] master_response_data_o,
    output logic [2:0] master_response_error_o
);
    logic arb_valid, arb_ready, arb_write;
    logic [25:0] arb_address;
    logic [127:0] arb_write_data;
    logic [15:0] arb_byte_enable;
    logic model_valid, model_write, model_response_valid, model_response_error;
    logic [25:0] model_address;
    logic [127:0] model_write_data, model_response_data;
    logic [15:0] model_byte_enable;

    n2d_memory_arbiter u_arbiter (.*,
        .memory_request_valid_o(arb_valid),
        .memory_request_ready_i(arb_ready),
        .memory_request_write_o(arb_write),
        .memory_request_address_o(arb_address),
        .memory_request_write_data_o(arb_write_data),
        .memory_request_byte_enable_o(arb_byte_enable),
        .memory_response_valid_i(model_response_valid && !preload_mode_i),
        .memory_response_data_i(model_response_data),
        .memory_response_error_i(model_response_error));

    assign model_valid = preload_mode_i ? 1'b0 : arb_valid;
    assign model_write = arb_write;
    assign model_address = arb_address;
    assign model_write_data = arb_write_data;
    assign model_byte_enable = arb_byte_enable;
    assign arb_ready = !preload_mode_i && model_ready;
    assign preload_ready_o = preload_mode_i;
    assign preload_response_valid_o = preload_mode_i && preload_valid_i;
    assign preload_response_error_o = 1'b0;

    logic model_ready;
    n2d_sdram_fast_model #(.LATENCY_CYCLES(3)) u_model (
        .clk_i, .reset_i(reset_i && !preload_mode_i),
        .request_valid_i(model_valid), .request_ready_o(model_ready),
        .request_write_i(model_write), .request_address_i(model_address),
        .request_write_data_i(model_write_data),
        .request_byte_enable_i(model_byte_enable), .force_error_i,
        .preload_valid_i(preload_mode_i && preload_valid_i),
        .preload_address_i, .preload_data_i, .preload_byte_enable_i,
        .response_valid_o(model_response_valid),
        .response_data_o(model_response_data),
        .response_error_o(model_response_error));

    always_ff @(posedge clk_i) begin
        if (preload_mode_i) begin
            assert (reset_i)
                else $fatal(1, "nand2doom preload requires asserted reset");
            assert (master_request_valid_i == 3'b000);
        end
    end
endmodule
`endif
