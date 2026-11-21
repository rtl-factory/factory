// Board-level composition for the n2m_de2_115 board smoke: the real board
// top with its pins, the owned SDRAM device model on the SDRAM pins and the
// resolved DRAM_DQ bus between them (wiki/src/nand2mario/fpga/DV.md). The
// model is clocked by the inverse of the DRAM_CLK pin, i.e. the board's
// system clock as the device's own clock relation defines it, so a wrong pin
// phase reaches the model as a wrong capture edge and returns its poison word.
`default_nettype none

module n2m_de2_115_fixture #(
    parameter int unsigned UART_CLKS_PER_BIT = 8,
    parameter int unsigned HEARTBEAT_BIT = 20,
    parameter int unsigned FILL_SEED = 32'h0DE10117
) (
    input  logic       CLOCK_50,
    input  logic [1:0] KEY,
    input  logic [7:0] SW,
    input  logic       UART_RXD,
    output logic       UART_TXD,
    output logic [9:0] LEDR,
    output logic [6:0] HEX0,
    output logic [6:0] HEX1,
    output logic [6:0] HEX2,
    output logic [6:0] HEX3,
    output logic [6:0] HEX4,
    output logic [6:0] HEX5,
    output logic [7:0] VGA_R,
    output logic [7:0] VGA_G,
    output logic [7:0] VGA_B,
    output logic       VGA_CLK,
    output logic       VGA_BLANK_N,
    output logic       VGA_SYNC_N,
    output logic [3:0] DRAM_DQM,
    output logic       VGA_HS,
    output logic       VGA_VS,
    output logic       DRAM_CLK,
    output logic       DRAM_CKE,
    output logic       DRAM_CS_N
);
    logic [12:0] dram_addr;
    logic [1:0]  dram_ba;
    logic        dram_cas_n, dram_ras_n, dram_we_n;
    logic [15:0] model_dq;
    logic        model_drive;
    tri   [15:0] DRAM_DQ;

    n2m_de2_115 #(
        .UART_CLKS_PER_BIT (UART_CLKS_PER_BIT),
        .HEARTBEAT_BIT     (HEARTBEAT_BIT)
    ) u_board (
        .CLOCK_50 (CLOCK_50),
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
        .VGA_CLK       (VGA_CLK),
        .VGA_BLANK_N   (VGA_BLANK_N),
        .VGA_SYNC_N    (VGA_SYNC_N),
        .VGA_HS        (VGA_HS),
        .VGA_VS        (VGA_VS),
        .DRAM_ADDR     (dram_addr),
        .DRAM_BA       (dram_ba),
        .DRAM_CAS_N    (dram_cas_n),
        .DRAM_CKE      (DRAM_CKE),
        .DRAM_CLK      (DRAM_CLK),
        .DRAM_CS_N     (DRAM_CS_N),
        .DRAM_DQM      (DRAM_DQM),
        .DRAM_RAS_N    (dram_ras_n),
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
        assert (DRAM_DQM[3:2] == 2'b11) else $fatal(1, "upper SDRAM lanes must stay masked");
        assert (!(board_drive && model_drive)) else $fatal(1, "n2m_de2_115_fixture: DRAM_DQ contention");
    end

    n2m_sdram_model #(
        .FILL_SEED (FILL_SEED)
    ) u_sdram (
        .clk_i   (device_clk),
        .cke_i   (DRAM_CKE),
        .cs_n_i  (DRAM_CS_N),
        .ras_n_i (dram_ras_n),
        .cas_n_i (dram_cas_n),
        .we_n_i  (dram_we_n),
        .ba_i    (dram_ba),
        .addr_i  (dram_addr),
        .dqm_i   (DRAM_DQM[1:0]),
        .dq_i    (board_dq),
        .dq_oe_i (board_drive),
        .dq_o    (model_dq)
    );
endmodule
