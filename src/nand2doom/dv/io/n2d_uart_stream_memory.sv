// Simulation-only composition for the streaming load session: the product
// serial bridge at the 3 Mbaud load rate (8 1/3 clocks per bit) feeding the
// actual debug memory adapter, with a test-driven halt status and scaled
// stream gap/idle bounds. It holds no state besides the adapter reset
// release register.
module n2d_uart_stream_memory
    import n2d_pkg::*;
(
    input  logic         clk_i,
    input  logic         reset_i,
    input  logic         uart_rx_i,
    output logic         uart_tx_o,
    input  logic         halt_ack_i,
    output logic         halt_req_o,
    output logic         arb_req_o,
    input  logic         arb_grant_i,
    output logic         transaction_commit_o,
    output logic         transaction_done_o,
    output logic         transaction_error_o,
    output logic         line_request_valid_o,
    input  logic         line_request_ready_i,
    output logic         line_request_write_o,
    output logic [25:0]  line_request_address_o,
    output logic [127:0] line_request_write_data_o,
    output logic [15:0]  line_request_byte_enable_o,
    input  logic         line_response_valid_i,
    input  logic [127:0] line_response_data_i,
    input  logic         line_response_error_i
);
    logic transaction_begin, transaction_abort;
    logic word_request_ready, word_response_valid, word_response_error;
    logic [31:0] word_response_data;
    logic adapter_reset_q;
    /* verilator lint_off UNUSEDSIGNAL */
    n2d_debug_control_t debug_control;
    n2d_input_event_t   input_event_unused;
    /* verilator lint_on UNUSEDSIGNAL */
    n2d_debug_txn_req_t    txn_req;
    n2d_debug_mem_req_t    word_request;
    n2d_debug_mem_rsp_t    word_response;
    n2d_debug_status_t     debug_status;
    n2d_debug_txn_status_t txn_status;

    // Same registered adapter reset release as n2d_uart_debug_memory.
    always_ff @(posedge clk_i or posedge reset_i) begin
        if (reset_i) adapter_reset_q <= 1'b1;
        else adapter_reset_q <= 1'b0;
    end

    assign debug_status = '{halt_ack: halt_ack_i, cache_maint_done: halt_ack_i};
    assign txn_status = '{arb_grant: arb_grant_i, txn_done: transaction_done_o,
                          txn_error: transaction_error_o};

    n2d_pkg::n2d_screen_req_t unused_screen_req;
    n2d_uart_debug #(
        .BAUD(N2D_UART_LOAD_BAUD),
        .STREAM_GAP_TIMEOUT_CYCLES(4000),
        .STREAM_IDLE_CYCLES(40000)
    ) u_uart (
        .clk_i, .reset_i, .uart_rx_i, .uart_tx_o,
        .debug_control_o(debug_control),
        .debug_status_i(debug_status),
        .input_event_o(input_event_unused),
        .input_event_ready_i(1'b1),
        .txn_req_o(txn_req),
        .txn_status_i(txn_status),
        .mem_req_o(word_request),
        .mem_rsp_i(word_response),
        // No screen in this fixture: endpoint screen reads time out.
        .screen_req_o(unused_screen_req), .screen_rsp_i('0));
    assign halt_req_o = debug_control.halt_req;
    assign arb_req_o = txn_req.arb_req;
    assign transaction_begin = txn_req.txn_begin;
    assign transaction_commit_o = txn_req.txn_commit;
    assign transaction_abort = txn_req.txn_abort;
    assign word_response = '{ready: word_request_ready, rsp_valid: word_response_valid,
                             rdata: word_response_data, rsp_error: word_response_error};

    n2d_debug_memory_adapter u_adapter (
        .clk_i, .reset_i(adapter_reset_q),
        .transaction_begin_i(transaction_begin),
        .transaction_commit_i(transaction_commit_o),
        .transaction_abort_i(transaction_abort),
        .transaction_done_o, .transaction_error_o,
        .word_request_valid_i(word_request.valid),
        .word_request_ready_o(word_request_ready),
        .word_request_write_i(word_request.write),
        .word_request_address_i(word_request.addr),
        .word_request_write_data_i(word_request.wdata),
        .word_request_byte_enable_i(word_request.wstrb),
        .word_response_valid_o(word_response_valid),
        .word_response_data_o(word_response_data),
        .word_response_error_o(word_response_error),
        .line_request_valid_o, .line_request_ready_i,
        .line_request_write_o, .line_request_address_o,
        .line_request_write_data_o, .line_request_byte_enable_o,
        .line_response_valid_i, .line_response_data_i, .line_response_error_i);
endmodule
