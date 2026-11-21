// Bench top for the common SDRAM controller: the controller drives the common
// device model; the cocotb test drives the request port
// (wiki/src/common/dv/sdram/dv-plan.md). The two configurations are the
// nand2doom one (burst eight, open row, row-XOR-bank map) and the nand2mario
// one (burst one, close page, its waits).
module n2a_sdram_ctrl_fixture #(
    parameter bit LINE = 1'b0
) (
    input  logic          clk_i,
    input  logic          reset_i,
    input  logic          request_valid_i,
    output logic          request_ready_o,
    input  logic          request_write_i,
    input  logic [25:0]   request_address_i,
    input  logic [127:0]  request_write_data_i,
    input  logic [15:0]   request_byte_enable_i,
    output logic          response_valid_o,
    output logic [127:0]  response_data_o,
    output logic          response_error_o,
    output logic          idle_o,
    output logic          init_done_o
);
    localparam int unsigned BEATS = LINE ? 8 : 1;

    logic [12:0] sdram_address;
    logic [1:0]  sdram_bank, sdram_dqm;
    logic        sdram_cke, sdram_cs_n, sdram_ras_n, sdram_cas_n, sdram_we_n, sdram_dq_oe;
    logic [15:0] sdram_dq_out, sdram_dq_in;
    logic [16*BEATS-1:0] response_data;

    n2a_sdram_ctrl #(
        .BURST_BEATS      (BEATS),
        .OPEN_ROW         (LINE),
        .ROW_XOR_BANK     (LINE),
        .POWERUP_CLOCKS   (LINE ? 5000 : 5001),
        .INIT_REFRESHES   (LINE ? 2 : 8),
        .INIT_WAIT_MIN    (LINE ? 2 : 0),
        .WAIT_RCD         (LINE ? 0 : 2),
        .WAIT_RP          (LINE ? 0 : 2),
        .WAIT_RFC         (LINE ? 0 : 3),
        .WAIT_WR          (LINE ? 0 : 2),
        .WAIT_MRD         (LINE ? 0 : 2),
        .REFRESH_DUE      (LINE ? 163 : 160),
        .REFRESH_DEADLINE (LINE ? 194 : 195)
    ) u_ctrl (
        .clk_i, .reset_i, .request_valid_i, .request_ready_o, .request_write_i,
        .request_address_i,
        .request_write_data_i(request_write_data_i[16*BEATS-1:0]),
        .request_byte_enable_i(request_byte_enable_i[2*BEATS-1:0]),
        .response_valid_o, .response_data_o(response_data), .response_error_o, .idle_o, .init_done_o,
        .sdram_address_o(sdram_address), .sdram_bank_o(sdram_bank), .sdram_cke_o(sdram_cke),
        .sdram_cs_n_o(sdram_cs_n), .sdram_ras_n_o(sdram_ras_n), .sdram_cas_n_o(sdram_cas_n),
        .sdram_we_n_o(sdram_we_n), .sdram_dqm_o(sdram_dqm), .sdram_dq_o(sdram_dq_out),
        .sdram_dq_oe_o(sdram_dq_oe), .sdram_dq_i(sdram_dq_in));
    assign response_data_o = 128'(response_data);

    n2a_sdram_model #(
        .BURST_BEATS    (BEATS),
        .ROW_XOR_BANK   (LINE),
        .INIT_REFRESHES (LINE ? 2 : 8)
    ) u_model (
        .clk_i, .cke_i(sdram_cke), .cs_n_i(sdram_cs_n), .ras_n_i(sdram_ras_n),
        .cas_n_i(sdram_cas_n), .we_n_i(sdram_we_n), .ba_i(sdram_bank), .addr_i(sdram_address),
        .dqm_i(sdram_dqm), .dq_i(sdram_dq_out), .dq_oe_i(sdram_dq_oe), .dq_o(sdram_dq_in),
        .preload_valid_i(1'b0), .preload_address_i('0), .preload_data_i('0),
        .preload_byte_enable_i('0));

    // Why: the bench drives full-width buses in both configurations; the burst-one
    // controller uses only the low word and lanes.
    /* verilator lint_off UNUSEDSIGNAL */
    logic unused_inputs;
    assign unused_inputs = ^{request_write_data_i, request_byte_enable_i};
    /* verilator lint_on UNUSEDSIGNAL */
endmodule
