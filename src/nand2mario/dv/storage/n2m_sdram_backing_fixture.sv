// Simulation-only composition of the SDRAM cartridge backing (adapter plus
// controller) and the owned device model for the n2m_sdram_backing target:
// the bench plays the mapper on the backing port and the loader on the load
// port and observes the pins and the initialization status.
`default_nettype none

module n2m_sdram_backing_fixture #(
  parameter int unsigned FILL_SEED = 32'h2545F491
) (
  input  logic                                   clk_i,
  input  logic                                   reset_i,
  input  n2m_cartridge_pkg::backing_request_t    request_i,
  output logic                                   ready_o,
  output n2m_cartridge_pkg::backing_response_t   response_o,
  input  n2m_cartridge_pkg::backing_request_t    load_request_i,
  output logic                                   load_ready_o,
  output n2m_cartridge_pkg::backing_response_t   load_response_o,
  output logic                                   initialized_o,
  output logic                                   dram_cs_n_o,
  output logic                                   dram_ras_n_o,
  output logic                                   dram_cas_n_o,
  output logic                                   dram_we_n_o
);
  logic [12:0] dram_addr;
  logic [1:0]  dram_ba;
  logic        dram_cke, dram_clk, dram_dqml, dram_dqmh, dram_dq_oe;
  logic [15:0] dram_dq_o, dram_dq_i;

  n2m_sdram_backing u_backing (
    .clk_i           (clk_i),
    .reset_i         (reset_i),
    .request_i       (request_i),
    .ready_o         (ready_o),
    .response_o      (response_o),
    .load_request_i  (load_request_i),
    .load_ready_o    (load_ready_o),
    .load_response_o (load_response_o),
    .initialized_o   (initialized_o),
    .dram_addr_o     (dram_addr),
    .dram_ba_o       (dram_ba),
    .dram_cas_n_o    (dram_cas_n_o),
    .dram_cke_o      (dram_cke),
    .dram_clk_o      (dram_clk),
    .dram_cs_n_o     (dram_cs_n_o),
    .dram_dqml_o     (dram_dqml),
    .dram_dqmh_o     (dram_dqmh),
    .dram_ras_n_o    (dram_ras_n_o),
    .dram_we_n_o     (dram_we_n_o),
    .dram_dq_o       (dram_dq_o),
    .dram_dq_oe_o    (dram_dq_oe),
    .dram_dq_i       (dram_dq_i)
  );

  /* verilator lint_off UNUSEDSIGNAL */
  logic dram_clk_unused;
  assign dram_clk_unused = dram_clk;
  /* verilator lint_on UNUSEDSIGNAL */

  n2m_sdram_model #(
    .FILL_SEED (FILL_SEED)
  ) u_sdram (
    .clk_i   (clk_i),
    .cke_i   (dram_cke),
    .cs_n_i  (dram_cs_n_o),
    .ras_n_i (dram_ras_n_o),
    .cas_n_i (dram_cas_n_o),
    .we_n_i  (dram_we_n_o),
    .ba_i    (dram_ba),
    .addr_i  (dram_addr),
    .dqm_i   ({dram_dqmh, dram_dqml}),
    .dq_i    (dram_dq_o),
    .dq_oe_i (dram_dq_oe),
    .dq_o    (dram_dq_i)
  );
endmodule
