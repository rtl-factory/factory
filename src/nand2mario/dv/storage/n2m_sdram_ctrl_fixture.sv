// Simulation-only composition of the adapted SDRAM controller and the owned
// device model for the n2m_sdram_ctrl target: the bench drives the request
// interface and observes the pins the model decodes. The refresh parameters
// are lowered or raised only by the negative target that proves the model's
// refresh deadline check.
`default_nettype none

module n2m_sdram_ctrl_fixture #(
  parameter int REFRESH_INTERVAL = n2m_sdram_pkg::SDRAM_REFRESH_INTERVAL,
  parameter int REFRESH_DEADLINE_CLOCKS = n2m_sdram_pkg::SDRAM_REFRESH_DEADLINE_CLOCKS,
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
  // Pin observation for the bench's command monitor.
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
  logic dram_clk;

  n2m_sdram_ctrl #(
    .REFRESH_INTERVAL        (REFRESH_INTERVAL),
    .REFRESH_DEADLINE_CLOCKS (REFRESH_DEADLINE_CLOCKS)
  ) u_ctrl (
    .clk_i                 (clk_i),
    .reset_i               (reset_i),
    .request_valid_i       (request_valid_i),
    .request_write_i       (request_write_i),
    .request_address_i     (request_address_i),
    .request_write_data_i  (request_write_data_i),
    .request_byte_enable_i (request_byte_enable_i),
    .request_ready_o       (request_ready_o),
    .response_valid_o      (response_valid_o),
    .response_data_o       (response_data_o),
    .idle_o                (idle_o),
    .initialized_o         (initialized_o),
    .dram_addr_o           (dram_addr_o),
    .dram_ba_o             (dram_ba_o),
    .dram_cas_n_o          (dram_cas_n_o),
    .dram_cke_o            (dram_cke_o),
    .dram_clk_o            (dram_clk),
    .dram_cs_n_o           (dram_cs_n_o),
    .dram_dqml_o           (dram_dqml_o),
    .dram_dqmh_o           (dram_dqmh_o),
    .dram_ras_n_o          (dram_ras_n_o),
    .dram_we_n_o           (dram_we_n_o),
    .dram_dq_o             (dram_dq_o),
    .dram_dq_oe_o          (dram_dq_oe_o),
    .dram_dq_i             (dram_dq_i)
  );

  // The model follows the controller-edge abstraction of the inverted pin
  // clock (storage HAS), so it is clocked by the controller clock; dram_clk
  // is the board pin and carries no simulation meaning here.
  /* verilator lint_off UNUSEDSIGNAL */
  logic dram_clk_unused;
  assign dram_clk_unused = dram_clk;
  /* verilator lint_on UNUSEDSIGNAL */

  n2m_sdram_model #(
    .FILL_SEED (FILL_SEED)
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
    .dq_o    (dram_dq_i)
  );
endmodule
