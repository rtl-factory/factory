// Simulation-only lockstep of the common SDRAM controller against the project
// controller it replaces (wiki/src/common/dv/sdram/dv-plan.md): both take the
// same request stream and the same DQ input, and the bench compares their pins
// and responses every clock. LINE 1 pairs n2d_sdram_controller with the
// nand2doom configuration, LINE 0 pairs n2m_sdram_ctrl with the nand2mario one.
`default_nettype none

module n2a_sdram_ctrl_lockstep_fixture #(
    parameter bit LINE = 1'b1
) (
    input  logic          clk_i,
    input  logic          reset_i,
    input  logic          request_valid_i,
    input  logic          request_write_i,
    input  logic [25:0]   request_address_i,
    input  logic [127:0]  request_write_data_i,
    input  logic [15:0]   request_byte_enable_i,
    input  logic [15:0]   sdram_dq_i,
    // {cke, cs_n, ras_n, cas_n, we_n, bank, address, dqm, dq_oe, dq}
    output logic [38:0]   ref_pins_o,
    output logic [38:0]   dut_pins_o,
    output logic          ref_ready_o,
    output logic          dut_ready_o,
    output logic          ref_response_valid_o,
    output logic          dut_response_valid_o,
    output logic          ref_response_error_o,
    output logic          dut_response_error_o,
    output logic [127:0]  ref_response_data_o,
    output logic [127:0]  dut_response_data_o,
    output logic          ref_init_done_o,
    output logic          dut_init_done_o,
    output logic          ref_idle_o,
    output logic          dut_idle_o
);
    localparam int unsigned BEATS = LINE ? 8 : 1;

    logic [12:0] d_address;
    logic [1:0]  d_bank, d_dqm;
    logic        d_cke, d_cs_n, d_ras_n, d_cas_n, d_we_n, d_dq_oe;
    logic [15:0] d_dq;
    logic [16*BEATS-1:0] d_data;

    n2a_sdram_ctrl #(
        .BURST_BEATS           (BEATS),
        .OPEN_ROW              (LINE),
        .ROW_XOR_BANK          (LINE),
        .POWERUP_CLOCKS        (LINE ? 5000 : 5001),
        .INIT_REFRESHES        (LINE ? 2 : 8),
        .INIT_WAIT_MIN         (LINE ? 2 : 0),
        .WAIT_RCD              (LINE ? 0 : 2),
        .WAIT_RP               (LINE ? 0 : 2),
        .WAIT_RFC              (LINE ? 0 : 3),
        .WAIT_WR               (LINE ? 0 : 2),
        .WAIT_MRD              (LINE ? 0 : 2),
        .REFRESH_DUE           (LINE ? 163 : 160),
        .REFRESH_DEADLINE      (LINE ? 194 : 195),
        .REFRESH_AGE_FROM_MODE (!LINE)
    ) u_dut (
        .clk_i, .reset_i, .request_valid_i, .request_ready_o(dut_ready_o), .request_write_i,
        .request_address_i,
        .request_write_data_i(request_write_data_i[16*BEATS-1:0]),
        .request_byte_enable_i(request_byte_enable_i[2*BEATS-1:0]),
        .response_valid_o(dut_response_valid_o), .response_data_o(d_data),
        .response_error_o(dut_response_error_o), .idle_o(dut_idle_o), .init_done_o(dut_init_done_o),
        .sdram_address_o(d_address), .sdram_bank_o(d_bank), .sdram_cke_o(d_cke), .sdram_cs_n_o(d_cs_n),
        .sdram_ras_n_o(d_ras_n), .sdram_cas_n_o(d_cas_n), .sdram_we_n_o(d_we_n), .sdram_dqm_o(d_dqm),
        .sdram_dq_o(d_dq), .sdram_dq_oe_o(d_dq_oe), .sdram_dq_i);
    assign dut_response_data_o = 128'(d_data);
    assign dut_pins_o = {d_cke, d_cs_n, d_ras_n, d_cas_n, d_we_n, d_bank, d_address, d_dqm, d_dq_oe, d_dq};

    if (LINE) begin : g_n2d
        logic [12:0] address;
        logic [1:0]  bank, dqm;
        logic        cke, cs_n, ras_n, cas_n, we_n, dq_oe;
        logic [15:0] dq;
        n2d_sdram_controller u_ref (
            .clk_i, .reset_i, .request_valid_i, .request_ready_o(ref_ready_o), .request_write_i,
            .request_address_i, .request_write_data_i, .request_byte_enable_i,
            .response_valid_o(ref_response_valid_o), .response_data_o(ref_response_data_o),
            .response_error_o(ref_response_error_o), .init_done_o(ref_init_done_o),
            .sdram_address_o(address), .sdram_bank_o(bank), .sdram_cke_o(cke), .sdram_cs_n_o(cs_n),
            .sdram_ras_n_o(ras_n), .sdram_cas_n_o(cas_n), .sdram_we_n_o(we_n), .sdram_dqm_o(dqm),
            .sdram_dq_o(dq), .sdram_dq_oe_o(dq_oe), .sdram_dq_i);
        // Why: the nand2doom controller has no idle output; the test skips idle.
        assign ref_idle_o = 1'b0;
        assign ref_pins_o = {cke, cs_n, ras_n, cas_n, we_n, bank, address, dqm, dq_oe, dq};
    end else begin : g_n2m
        logic [12:0] address;
        logic [1:0]  bank;
        logic        cke, cs_n, ras_n, cas_n, we_n, dqml, dqmh, dq_oe, dram_clk;
        logic [15:0] dq, data;
        n2m_sdram_ctrl u_ref (
            .clk_i, .reset_i, .request_valid_i, .request_write_i, .request_address_i,
            .request_write_data_i(request_write_data_i[15:0]),
            .request_byte_enable_i(request_byte_enable_i[1:0]),
            .request_ready_o(ref_ready_o), .response_valid_o(ref_response_valid_o),
            .response_data_o(data), .idle_o(ref_idle_o), .initialized_o(ref_init_done_o),
            .dram_addr_o(address), .dram_ba_o(bank), .dram_cas_n_o(cas_n), .dram_cke_o(cke),
            .dram_clk_o(dram_clk), .dram_cs_n_o(cs_n), .dram_dqml_o(dqml), .dram_dqmh_o(dqmh),
            .dram_ras_n_o(ras_n), .dram_we_n_o(we_n), .dram_dq_o(dq), .dram_dq_oe_o(dq_oe),
            .dram_dq_i(sdram_dq_i));
        assign ref_response_data_o = 128'(data);
        // Why: nand2mario's controller has no error output; it never sees a misaligned request.
        assign ref_response_error_o = 1'b0;
        assign ref_pins_o = {cke, cs_n, ras_n, cas_n, we_n, bank, address, dqmh, dqml, dq_oe, dq};
        /* verilator lint_off UNUSEDSIGNAL */
        logic dram_clk_unused;
        assign dram_clk_unused = dram_clk;
        /* verilator lint_on UNUSEDSIGNAL */
    end
endmodule
