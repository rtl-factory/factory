// Simulation-only public-pin fixture: commands launch on clk_i rising,
// the device samples on the inverted clock, and DQ resolves at the pins.
module n2d_sdram_board_timing (
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
    input  logic [15:0]  device_data_i,
    input  logic         device_drive_i,
    output logic [15:0]  device_data_o,
    output logic         device_clk_o
);
    tri [15:0] dq;
    logic [15:0] sdram_dq_i;
    assign device_clk_o = ~clk_i;
    assign dq = sdram_dq_oe_o ? sdram_dq_o : 16'hzzzz;
    assign dq = device_drive_i ? device_data_i : 16'hzzzz;
    assign sdram_dq_i = dq;
    assign device_data_o = dq;
    logic idle_o;
    n2d_sdram_ctrl_fixture u_controller (.*);
endmodule
