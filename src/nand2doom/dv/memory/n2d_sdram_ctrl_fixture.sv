// Simulation-only composition of the common SDRAM controller in the nand2doom
// configuration (n2d_memory_controller_pkg, the set n2d_memory_controller passes):
// the controller's own ports, unchanged. The n2d_sdram_controller target and
// the platform and board-timing fixtures instantiate it, so every bench tests
// the product's parameter values.
`default_nettype none

module n2d_sdram_ctrl_fixture (
    input  logic         clk_i,
    input  logic         reset_i,
    input  logic         request_valid_i,
    output logic         request_ready_o,
    input  logic         request_write_i,
    input  logic [25:0]  request_address_i,
    input  logic [127:0] request_write_data_i,
    input  logic [15:0]  request_byte_enable_i,
    output logic         response_valid_o,
    output logic [127:0] response_data_o,
    output logic         response_error_o,
    output logic         idle_o,
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
    import n2d_memory_controller_pkg::*;

    n2a_sdram_ctrl #(
        .BURST_BEATS           (N2D_SDRAM_BURST_BEATS),
        .OPEN_ROW              (N2D_SDRAM_OPEN_ROW),
        .ROW_XOR_BANK          (N2D_SDRAM_ROW_XOR_BANK),
        .POWERUP_CLOCKS        (N2D_SDRAM_POWERUP_CLOCKS),
        .INIT_REFRESHES        (N2D_SDRAM_INIT_REFRESHES),
        .INIT_WAIT_MIN         (N2D_SDRAM_INIT_WAIT_MIN),
        .WAIT_RCD              (N2D_SDRAM_WAIT_RCD),
        .WAIT_RP               (N2D_SDRAM_WAIT_RP),
        .WAIT_RFC              (N2D_SDRAM_WAIT_RFC),
        .WAIT_WR               (N2D_SDRAM_WAIT_WR),
        .WAIT_MRD              (N2D_SDRAM_WAIT_MRD),
        .REFRESH_DUE           (N2D_SDRAM_REFRESH_DUE),
        .REFRESH_DEADLINE      (N2D_SDRAM_REFRESH_DEADLINE),
        .REFRESH_AGE_FROM_MODE (N2D_SDRAM_REFRESH_AGE_FROM_MODE)
    ) u_ctrl (.*);
endmodule
