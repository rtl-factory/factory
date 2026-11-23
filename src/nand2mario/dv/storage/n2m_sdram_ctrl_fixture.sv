// Simulation-only composition of the common SDRAM controller in the
// nand2mario configuration and the common device model for the n2m_sdram_ctrl
// target: the bench drives the request interface and observes the pins the
// model decodes. The refresh parameters are raised only by the negative target
// that proves the model's refresh deadline check.
`default_nettype none

module n2m_sdram_ctrl_fixture #(
    parameter int unsigned REFRESH_INTERVAL = n2m_sdram_backing_pkg::SDRAM_REFRESH_DUE,
    parameter int unsigned REFRESH_DEADLINE_CLOCKS = n2m_sdram_backing_pkg::SDRAM_REFRESH_DEADLINE,
    parameter int unsigned FILL_SEED = 32'h2545F491
) (
    input  logic        clk_i,
    input  logic        reset_i,
    input  logic        request_valid_i,
    input  logic        request_write_i,
    input  logic [25:0] request_address_i,
    input  logic [15:0] request_write_data_i,
    input  logic [1:0]  request_byte_enable_i,
    output logic        request_ready_o,
    output logic        response_valid_o,
    output logic [15:0] response_data_o,
    output logic        idle_o,
    output logic        initialized_o,
    output logic [12:0] dram_addr_o,
    output logic [1:0]  dram_ba_o,
    output logic        dram_cas_n_o,
    output logic        dram_cke_o,
    output logic        dram_cs_n_o,
    output logic        dram_dqml_o,
    output logic        dram_dqmh_o,
    output logic        dram_ras_n_o,
    output logic        dram_we_n_o,
    output logic [15:0] dram_dq_o,
    output logic        dram_dq_oe_o,
    output logic [15:0] dram_dq_i
);
    logic response_error;

    import n2m_sdram_backing_pkg::*;

    n2a_sdram_ctrl #(
        .BURST_BEATS           (SDRAM_BURST_BEATS),
        .OPEN_ROW              (SDRAM_OPEN_ROW),
        .ROW_XOR_BANK          (SDRAM_ROW_XOR_BANK),
        .POWERUP_CLOCKS        (SDRAM_POWERUP_CLOCKS),
        .INIT_REFRESHES        (SDRAM_INIT_REFRESHES),
        .INIT_WAIT_MIN         (SDRAM_INIT_WAIT_MIN),
        .WAIT_RCD              (SDRAM_WAIT_RCD),
        .WAIT_RP               (SDRAM_WAIT_RP),
        .WAIT_RFC              (SDRAM_WAIT_RFC),
        .WAIT_WR               (SDRAM_WAIT_WR),
        .WAIT_MRD              (SDRAM_WAIT_MRD),
        .REFRESH_DUE           (REFRESH_INTERVAL),
        .REFRESH_DEADLINE      (REFRESH_DEADLINE_CLOCKS),
        .REFRESH_AGE_FROM_MODE (SDRAM_REFRESH_AGE_FROM_MODE)
    ) u_ctrl (
        .clk_i, .reset_i, .request_valid_i, .request_ready_o, .request_write_i,
        .request_address_i, .request_write_data_i, .request_byte_enable_i,
        .response_valid_o, .response_data_o, .response_error_o(response_error), .idle_o,
        .init_done_o(initialized_o),
        .sdram_address_o(dram_addr_o), .sdram_bank_o(dram_ba_o), .sdram_cke_o(dram_cke_o),
        .sdram_cs_n_o(dram_cs_n_o), .sdram_ras_n_o(dram_ras_n_o), .sdram_cas_n_o(dram_cas_n_o),
        .sdram_we_n_o(dram_we_n_o), .sdram_dqm_o({dram_dqmh_o, dram_dqml_o}),
        .sdram_dq_o(dram_dq_o), .sdram_dq_oe_o(dram_dq_oe_o), .sdram_dq_i(dram_dq_i));

    // Why: nand2mario only issues aligned word requests, so error never rises;
    // the bench has no port for it.
    /* verilator lint_off UNUSEDSIGNAL */
    logic response_error_unused;
    assign response_error_unused = response_error;
    /* verilator lint_on UNUSEDSIGNAL */

    n2a_sdram_model #(
        .INIT_REFRESHES (8),
        .FILL_SEED      (FILL_SEED)
    ) u_sdram (
        .clk_i   (clk_i),
        .cke_i   (dram_cke_o),
        .cs_n_i  (dram_cs_n_o),
        .ras_n_i (dram_ras_n_o),
        .cas_n_i (dram_cas_n_o),
        .we_n_i  (dram_we_n_o),
        .ba_i    (dram_ba_o),
        .addr_i  (dram_addr_o),
        .dqm_i   ({dram_dqmh_o, dram_dqml_o}),
        .dq_i    (dram_dq_o),
        .dq_oe_i (dram_dq_oe_o),
        .dq_o    (dram_dq_i),
        .preload_valid_i       (1'b0),
        .preload_address_i     ('0),
        .preload_data_i        ('0),
        .preload_byte_enable_i ('0)
    );
endmodule
