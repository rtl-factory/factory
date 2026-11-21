// Bench top for the common SDRAM device model: the cocotb test drives the
// device pins directly (wiki/src/common/dv/sdram/dv-plan.md).
module n2a_sdram_model_fixture #(
    parameter int unsigned BURST_BEATS = 1,
    parameter bit ROW_XOR_BANK = 1'b0
) (
    input  logic          clk_i,
    input  logic          cke_i,
    input  logic          cs_n_i,
    input  logic          ras_n_i,
    input  logic          cas_n_i,
    input  logic          we_n_i,
    input  logic [1:0]    ba_i,
    input  logic [12:0]   addr_i,
    input  logic [1:0]    dqm_i,
    input  logic [15:0]   dq_i,
    input  logic          dq_oe_i,
    output logic [15:0]   dq_o,
    input  logic          preload_valid_i,
    input  logic [25:0]   preload_address_i,
    input  logic [2047:0] preload_data_i,
    input  logic [255:0]  preload_byte_enable_i
);
    n2a_sdram_model #(
        .BURST_BEATS    (BURST_BEATS),
        .ROW_XOR_BANK   (ROW_XOR_BANK),
        .INIT_REFRESHES (2),
        .POWERUP_CLOCKS (8),
        .FILL           (1'b1)
    ) u_model (.*);
endmodule
