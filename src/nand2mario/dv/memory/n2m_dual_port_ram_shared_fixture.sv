// Simulation-only top for the ASIC backend's shared single-port path of
// n2m_dual_port_ram (n2m_dual_port_ram_asic.sv): the 8 KiB byte store of
// VRAM and WRAM on one clock, over the portable n2a_sync_ram store.
`default_nettype none

module n2m_dual_port_ram_shared_fixture (
  input  logic        clk_i,
  input  logic        reset_a_i,
  input  logic        reset_b_i,
  input  logic        read_a_i,
  input  logic        write_a_i,
  input  logic [12:0] address_a_i,
  input  logic [7:0]  write_data_a_i,
  output logic [7:0]  read_data_a_o,
  output logic        read_valid_a_o,
  input  logic        read_b_i,
  input  logic [12:0] address_b_i,
  output logic [7:0]  read_data_b_o,
  output logic        read_valid_b_o
);
  n2m_dual_port_ram #(
    .DATA_WIDTH (8),
    .DEPTH      (8192),
    .DUAL_CLOCK (1'b0)
  ) dut (
    .clk_a_i         (clk_i),
    .reset_a_i       (reset_a_i),
    .read_a_i        (read_a_i),
    .write_a_i       (write_a_i),
    .address_a_i     (address_a_i),
    .write_data_a_i  (write_data_a_i),
    .byte_enable_a_i (1'b1),
    .read_data_a_o   (read_data_a_o),
    .read_valid_a_o  (read_valid_a_o),
    .clk_b_i         (clk_i),
    .reset_b_i       (reset_b_i),
    .read_b_i        (read_b_i),
    .address_b_i     (address_b_i),
    .read_data_b_o   (read_data_b_o),
    .read_valid_b_o  (read_valid_b_o)
  );
endmodule
