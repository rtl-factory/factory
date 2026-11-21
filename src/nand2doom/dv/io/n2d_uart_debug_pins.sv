// Simulation-only flat-pin view of n2d_uart_debug for the cocotb tests: the
// public scalar pin names of the flat debug port list are kept exactly while
// the product module carries n2d_pkg port groups. Pure wiring, no state.
module n2d_uart_debug_pins
    import n2d_pkg::*;
#(
    parameter int unsigned CLKS_PER_BIT = 25,
    parameter int unsigned INTERBYTE_TIMEOUT_CYCLES = 512,
    parameter int unsigned OP_TIMEOUT_CYCLES = 1024,
    parameter int unsigned HALT_TIMEOUT_CYCLES = OP_TIMEOUT_CYCLES,
    parameter int unsigned MAX_WORDS = 8
) (
    input  logic        clk_i,
    input  logic        reset_i,
    input  logic        uart_rx_i,
    output logic        uart_tx_o,

    output logic        halt_req_o,
    input  logic        halt_ack_i,
    input  logic        cache_maint_done_i,
    output logic        cache_invalidate_o,
    output logic        reset_pc_valid_o,
    output logic [31:0] reset_pc_o,

    output logic        input_event_valid_o,
    input  logic        input_event_ready_i,
    output logic [9:0]  input_event_o,

    output logic        arb_req_o,
    input  logic        arb_grant_i,
    output logic        mem_txn_begin_o,
    output logic        mem_txn_commit_o,
    output logic        mem_txn_abort_o,
    input  logic        mem_txn_done_i,
    input  logic        mem_txn_error_i,
    output logic        mem_req_valid_o,
    input  logic        mem_req_ready_i,
    output logic        mem_req_write_o,
    output logic [31:0] mem_req_addr_o,
    output logic [31:0] mem_req_wdata_o,
    output logic [3:0]  mem_req_wstrb_o,
    input  logic        mem_rsp_valid_i,
    input  logic [31:0] mem_rsp_rdata_i,
    input  logic        mem_rsp_error_i
);
    t_n2d_debug_control    debug_control;
    t_n2d_debug_status     debug_status;
    t_n2d_input_event      input_event;
    t_n2d_debug_txn_req    txn_req;
    t_n2d_debug_txn_status txn_status;
    t_n2d_debug_mem_req    mem_req;
    t_n2d_debug_mem_rsp    mem_rsp;

    assign debug_status = '{halt_ack: halt_ack_i, cache_maint_done: cache_maint_done_i};
    assign txn_status = '{arb_grant: arb_grant_i, txn_done: mem_txn_done_i,
                          txn_error: mem_txn_error_i};
    assign mem_rsp = '{ready: mem_req_ready_i, rsp_valid: mem_rsp_valid_i,
                       rdata: mem_rsp_rdata_i, rsp_error: mem_rsp_error_i};

    assign halt_req_o = debug_control.halt_req;
    assign cache_invalidate_o = debug_control.cache_invalidate;
    assign reset_pc_valid_o = debug_control.reset_pc_valid;
    assign reset_pc_o = debug_control.reset_pc;
    assign input_event_valid_o = input_event.valid;
    assign input_event_o = input_event.code;
    assign arb_req_o = txn_req.arb_req;
    assign mem_txn_begin_o = txn_req.txn_begin;
    assign mem_txn_commit_o = txn_req.txn_commit;
    assign mem_txn_abort_o = txn_req.txn_abort;
    assign mem_req_valid_o = mem_req.valid;
    assign mem_req_write_o = mem_req.write;
    assign mem_req_addr_o = mem_req.addr;
    assign mem_req_wdata_o = mem_req.wdata;
    assign mem_req_wstrb_o = mem_req.wstrb;

    n2d_pkg::t_n2d_screen_req unused_screen_req;
    n2d_uart_debug #(
        .CLKS_PER_BIT(CLKS_PER_BIT),
        .INTERBYTE_TIMEOUT_CYCLES(INTERBYTE_TIMEOUT_CYCLES),
        .OP_TIMEOUT_CYCLES(OP_TIMEOUT_CYCLES),
        .HALT_TIMEOUT_CYCLES(HALT_TIMEOUT_CYCLES), .MAX_WORDS(MAX_WORDS)
    ) u_debug (
        .clk_i, .reset_i, .uart_rx_i, .uart_tx_o,
        .debug_control_o(debug_control), .debug_status_i(debug_status),
        .input_event_o(input_event), .input_event_ready_i(input_event_ready_i),
        .txn_req_o(txn_req), .txn_status_i(txn_status),
        .mem_req_o(mem_req), .mem_rsp_i(mem_rsp),
        // Why: no screen in this fixture, so endpoint screen reads time out
        // instead of hanging.
        .screen_req_o(unused_screen_req), .screen_rsp_i('0));
endmodule
