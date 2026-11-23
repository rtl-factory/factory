// Board-level composition for the n2m_de10_lite board smoke: the real board
// top with its pins, the owned SDRAM device model on the SDRAM pins and the
// resolved DRAM_DQ bus between them (wiki/src/nand2mario/rtl/has.md#verification-strategy). The
// model is clocked by the inverse of the DRAM_CLK pin, i.e. the board's
// system clock as the device's own clock relation defines it, so a wrong pin
// phase reaches the model as a wrong capture edge and returns its poison word.
`default_nettype none

module n2m_de10_lite_fixture #(
    parameter int unsigned UART_CLKS_PER_BIT = 8,
    parameter int unsigned UART_FRAC_NUM = 0,
    parameter int unsigned UART_FRAC_DEN = 1,
    parameter int unsigned HEARTBEAT_BIT = 20,
    parameter int unsigned FILL_SEED = 32'h0DE10117
) (
    input  logic       MAX10_CLK1_50,
    input  logic [1:0] KEY,
    input  logic [7:0] SW,
    input  logic       UART_RXD,
    output logic       UART_TXD,
    output logic [9:0] LEDR,
    output logic [7:0] HEX0,
    output logic [7:0] HEX1,
    output logic [7:0] HEX2,
    output logic [7:0] HEX3,
    output logic [7:0] HEX4,
    output logic [7:0] HEX5,
    output logic [3:0] VGA_R,
    output logic [3:0] VGA_G,
    output logic [3:0] VGA_B,
    output logic       VGA_HS,
    output logic       VGA_VS,
    output logic       DRAM_CLK,
    output logic       DRAM_CKE,
    output logic       DRAM_CS_N
);
    logic [12:0] dram_addr;
    logic [1:0]  dram_ba;
    logic        dram_cas_n, dram_ras_n, dram_we_n, dram_ldqm, dram_udqm;
    logic [15:0] model_dq;
    logic        model_drive;
    tri   [15:0] DRAM_DQ;

    n2m_de10_lite #(
        .UART_CLKS_PER_BIT (UART_CLKS_PER_BIT),
        .UART_FRAC_NUM     (UART_FRAC_NUM),
        .UART_FRAC_DEN     (UART_FRAC_DEN),
        .HEARTBEAT_BIT     (HEARTBEAT_BIT)
    ) u_board (
        .MAX10_CLK1_50 (MAX10_CLK1_50),
        .KEY           (KEY),
        .SW            (SW),
        .UART_RXD      (UART_RXD),
        .UART_TXD      (UART_TXD),
        .LEDR          (LEDR),
        .HEX0          (HEX0),
        .HEX1          (HEX1),
        .HEX2          (HEX2),
        .HEX3          (HEX3),
        .HEX4          (HEX4),
        .HEX5          (HEX5),
        .VGA_R         (VGA_R),
        .VGA_G         (VGA_G),
        .VGA_B         (VGA_B),
        .VGA_HS        (VGA_HS),
        .VGA_VS        (VGA_VS),
        .DRAM_ADDR     (dram_addr),
        .DRAM_BA       (dram_ba),
        .DRAM_CAS_N    (dram_cas_n),
        .DRAM_CKE      (DRAM_CKE),
        .DRAM_CLK      (DRAM_CLK),
        .DRAM_CS_N     (DRAM_CS_N),
        .DRAM_LDQM     (dram_ldqm),
        .DRAM_RAS_N    (dram_ras_n),
        .DRAM_UDQM     (dram_udqm),
        .DRAM_WE_N     (dram_we_n),
        .DRAM_DQ       (DRAM_DQ)
    );

    // Why: the device drives the bus only in its CL2 read-output window (the
    // model's read_out_q); the board drives it only with its output enable.
    // Both at once is bus contention.
    logic device_clk;
    assign device_clk = ~DRAM_CLK;
    logic board_drive;
    logic [15:0] board_dq;
    assign board_dq = DRAM_DQ;
    assign board_drive = u_board.dram_dq_oe;
    assign model_drive = u_sdram.read_out_q;
    assign DRAM_DQ = model_drive ? model_dq : 16'hzzzz;
    always @(posedge device_clk) begin
        assert (!(board_drive && model_drive)) else $fatal(1, "n2m_de10_lite_fixture: DRAM_DQ contention");
    end

    n2a_sdram_model #(
        .INIT_REFRESHES (8),
        .FILL_SEED      (FILL_SEED)
    ) u_sdram (
        .clk_i   (device_clk),
        .cke_i   (DRAM_CKE),
        .cs_n_i  (DRAM_CS_N),
        .ras_n_i (dram_ras_n),
        .cas_n_i (dram_cas_n),
        .we_n_i  (dram_we_n),
        .ba_i    (dram_ba),
        .addr_i  (dram_addr),
        .dqm_i   ({dram_udqm, dram_ldqm}),
        .dq_i    (board_dq),
        .dq_oe_i (board_drive),
        .dq_o    (model_dq),
        .preload_valid_i       (1'b0),
        .preload_address_i     ('0),
        .preload_data_i        ('0),
        .preload_byte_enable_i ('0)
    );
endmodule
