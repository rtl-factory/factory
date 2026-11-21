// Simulation-only composition: the platform PMON event derivation observing
// the master side of the real n2d_memory_arbiter, so a test can compare
// MEM_BACKPRESSURE with the arbiter's actual downstream request handshake.
module n2d_pmon_events_fixture (
    input  logic         clk_i,
    input  logic         reset_i,
    input  logic [2:0]   master_request_valid_i,
    input  logic [2:0]   master_request_write_i,
    input  logic         data_request_prefetch_i,
    input  logic         memory_request_ready_i,
    input  logic         memory_response_valid_i,
    input  logic         memory_response_error_i,
    output logic [2:0]   master_request_ready_o,
    output logic [2:0]   master_response_valid_o,
    output logic         memory_request_valid_o,
    output logic [127:0] events_o
);
    logic [2:0] master_response_error;
    /* verilator lint_off UNUSEDSIGNAL */
    logic [383:0] master_response_data;
    logic memory_request_write;
    logic [25:0] memory_request_address;
    logic [127:0] memory_request_write_data;
    logic [15:0] memory_request_byte_enable;
    /* verilator lint_on UNUSEDSIGNAL */

    n2d_memory_arbiter u_arbiter (
        .master_request_prefetch_i({1'b0, data_request_prefetch_i, 1'b0}),
        .clk_i, .reset_i,
        .master_request_valid_i,
        .master_request_ready_o,
        .master_request_write_i,
        .master_request_address_i(78'b0),
        .master_request_write_data_i(384'b0),
        .master_request_byte_enable_i(48'b0),
        .master_response_valid_o,
        .master_response_data_o(master_response_data),
        .master_response_error_o(master_response_error),
        .memory_request_valid_o,
        .memory_request_ready_i,
        .memory_request_write_o(memory_request_write),
        .memory_request_address_o(memory_request_address),
        .memory_request_write_data_o(memory_request_write_data),
        .memory_request_byte_enable_o(memory_request_byte_enable),
        .memory_response_valid_i,
        .memory_response_data_i(128'b0),
        .memory_response_error_i
    );

    n2d_pmon_platform_events u_events (
        .clk_i, .reset_i,
        .instruction_hit_event_i(1'b0),
        .instruction_miss_event_i(1'b0),
        .data_hit_event_i(1'b0),
        .data_prefetch_issue_event_i(1'b0),
        .data_prefetch_useful_event_i(1'b0),
        .data_prefetch_drop_event_i(1'b0),
        .data_prefetch_deferred_event_i(1'b0),
        .data_prefetch_cancel_event_i(1'b0),
        .data_write_buffer_pending_i(1'b0),
        .data_write_buffer_wait_i(1'b0),
        .data_miss_event_i(1'b0),
        .line_request_valid_i(master_request_valid_i),
        .line_request_ready_i(master_request_ready_o),
        .line_request_prefetch_i(data_request_prefetch_i),
        .line_request_write_i(master_request_write_i),
        .line_response_valid_i(master_response_valid_o),
        .line_response_error_i(master_response_error),
        .data_accept_i(1'b0),
        .data_write_i(1'b0),
        .data_sdram_i(1'b0),
        .data_framebuffer_i(1'b0),
        .uncached_wait_i(1'b0),
        .events_o
    );
endmodule
