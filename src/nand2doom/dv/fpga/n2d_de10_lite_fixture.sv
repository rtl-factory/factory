// Expose the external SDRAM driver's side of the resolved board data bus.
module n2d_de10_lite_fixture (
    input logic MAX10_CLK1_50, RESET_N, UART_RXD, PS2_CLK, PS2_DAT,
    output logic UART_TXD,
    output logic [3:0] VGA_R, VGA_G, VGA_B, LEDR,
    output logic VGA_HS, VGA_VS,
    output logic [12:0] DRAM_ADDR,
    output logic [1:0] DRAM_BA,
    output logic DRAM_CAS_N, DRAM_CKE, DRAM_CLK, DRAM_CS_N,
    output logic DRAM_LDQM, DRAM_RAS_N, DRAM_UDQM, DRAM_WE_N,
    output logic [15:0] dram_data_o,
    input logic [15:0] dram_data_i,
    input logic dram_drive_i
);
    tri [15:0] DRAM_DQ;
    assign DRAM_DQ = dram_drive_i ? dram_data_i : 16'hzzzz;
    assign dram_data_o = DRAM_DQ;
    n2d_de10_lite u_board (.*);
endmodule
