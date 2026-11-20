// Expose the external SDRAM driver's side of the resolved board data bus.
module n2d_de2_115_fixture (
    input logic CLOCK_50, UART_RX, PS2_CLK, PS2_DAT,
    input logic [0:0] KEY,
    output logic UART_TX,
    output logic [7:0] VGA_R, VGA_G, VGA_B,
    output logic VGA_HS, VGA_VS, VGA_CLK, VGA_BLANK_N, VGA_SYNC_N,
    output logic [17:0] LEDR,
    output logic [8:0] LEDG,
    output logic [6:0] HEX0, HEX1, HEX2, HEX3, HEX4, HEX5, HEX6, HEX7,
    output logic [12:0] DRAM_ADDR,
    output logic [1:0] DRAM_BA,
    output logic DRAM_CAS_N, DRAM_CKE, DRAM_CLK, DRAM_CS_N,
    output logic [3:0] DRAM_DQM,
    output logic DRAM_RAS_N, DRAM_WE_N,
    output logic [15:0] dram_data_o,
    input logic [15:0] dram_data_i,
    input logic dram_drive_i
);
    tri [15:0] DRAM_DQ;
    assign DRAM_DQ = dram_drive_i ? dram_data_i : 16'hzzzz;
    assign dram_data_o = DRAM_DQ;
    n2d_de2_115 u_board (.*);
endmodule
