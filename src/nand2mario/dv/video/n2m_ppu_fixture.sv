`default_nettype none

// Simulation-only composition for the focused PPU targets: the real timebase
// supplies the dot enable, two project dual-port RAMs play the VRAM and OAM
// stores (port A is the test's load port, port B the PPU read port) and
// every PPU port is exposed. The register port follows the memory peripheral
// contract.
module n2m_ppu_fixture (
  input  logic                                clk_i,
  input  logic                                reset_i,
  // VRAM load port (store port A).
  input  logic                                vram_write_i,
  input  logic [12:0]                         vram_address_i,
  input  logic [7:0]                          vram_data_i,
  // OAM load port (store port A).
  input  logic                                oam_write_i,
  input  logic [7:0]                          oam_address_i,
  input  logic [7:0]                          oam_data_i,
  // LCD register port.
  input  n2m_interfaces_pkg::bus_request_t    io_request_i,
  output n2m_interfaces_pkg::bus_response_t   io_response_o,
  output logic                                io_selected_o,
  // Timebase and PPU observation.
  output logic                                dot_enable_o,
  output logic                                vram_blocked_o,
  output logic                                oam_blocked_o,
  output logic                                vblank_irq_o,
  output logic                                stat_irq_o,
  output logic                                pixel_valid_o,
  output logic [1:0]                          pixel_shade_o,
  output logic [7:0]                          pixel_x_o,
  output logic [7:0]                          pixel_y_o,
  output logic                                line_start_o,
  output logic                                frame_start_o,
  output logic                                vram_read_o,
  output logic                                oam_read_o,
  output logic                                lcd_enable_o,
  output n2m_ppu_pkg::dot_t                   dot_o,
  output n2m_ppu_pkg::line_t                  ly_o,
  output n2m_ppu_pkg::mode_t                  mode_o
);
  logic        dot_enable, machine_cycle_enable;
  logic        vram_read, vram_valid;
  logic [12:0] vram_address;
  logic [7:0]  vram_data;
  logic        oam_read, oam_valid;
  logic [7:0]  oam_address, oam_data;
  logic        load_read;

  n2m_timebase u_timebase (
    .clk_i                  (clk_i),
    .reset_i                (reset_i),
    .dot_enable_o           (dot_enable),
    .machine_cycle_enable_o (machine_cycle_enable)
  );

  assign load_read = 1'b0;
  /* verilator lint_off PINCONNECTEMPTY */
  n2m_dual_port_ram #(
    .DATA_WIDTH (8),
    .DEPTH      (8192),
    .DUAL_CLOCK (1'b0)
  ) u_vram (
    .clk_a_i         (clk_i),
    .reset_a_i       (reset_i),
    .read_a_i        (load_read),
    .write_a_i       (vram_write_i),
    .address_a_i     (vram_address_i),
    .write_data_a_i  (vram_data_i),
    .byte_enable_a_i (1'b1),
    .read_data_a_o   (),
    .read_valid_a_o  (),
    .clk_b_i         (clk_i),
    .reset_b_i       (reset_i),
    .read_b_i        (vram_read),
    .address_b_i     (vram_address),
    .read_data_b_o   (vram_data),
    .read_valid_b_o  (vram_valid)
  );

  n2m_dual_port_ram #(
    .DATA_WIDTH (8),
    .DEPTH      (256),
    .DUAL_CLOCK (1'b0)
  ) u_oam (
    .clk_a_i         (clk_i),
    .reset_a_i       (reset_i),
    .read_a_i        (load_read),
    .write_a_i       (oam_write_i),
    .address_a_i     (oam_address_i),
    .write_data_a_i  (oam_data_i),
    .byte_enable_a_i (1'b1),
    .read_data_a_o   (),
    .read_valid_a_o  (),
    .clk_b_i         (clk_i),
    .reset_b_i       (reset_i),
    .read_b_i        (oam_read),
    .address_b_i     (oam_address),
    .read_data_b_o   (oam_data),
    .read_valid_b_o  (oam_valid)
  );
  /* verilator lint_on PINCONNECTEMPTY */

  n2m_ppu u_ppu (
    .clk_i          (clk_i),
    .reset_i        (reset_i),
    .dot_enable_i   (dot_enable),
    .io_request_i   (io_request_i),
    .io_response_o  (io_response_o),
    .io_selected_o  (io_selected_o),
    .vram_read_o    (vram_read),
    .vram_address_o (vram_address),
    .vram_valid_i   (vram_valid),
    .vram_data_i    (vram_data),
    .oam_read_o     (oam_read),
    .oam_address_o  (oam_address),
    .oam_valid_i    (oam_valid),
    .oam_data_i     (oam_data),
    .vram_blocked_o (vram_blocked_o),
    .oam_blocked_o  (oam_blocked_o),
    .vblank_irq_o   (vblank_irq_o),
    .stat_irq_o     (stat_irq_o),
    .pixel_valid_o  (pixel_valid_o),
    .pixel_shade_o  (pixel_shade_o),
    .pixel_x_o      (pixel_x_o),
    .pixel_y_o      (pixel_y_o),
    .line_start_o   (line_start_o),
    .frame_start_o  (frame_start_o),
    .lcd_enable_o   (lcd_enable_o),
    .dot_o          (dot_o),
    .ly_o           (ly_o),
    .mode_o         (mode_o)
  );

  assign dot_enable_o = dot_enable;
  assign vram_read_o = vram_read;
  assign oam_read_o = oam_read;
  // Why: the machine enable is not needed here; the PPU consumes dots only.
  logic unused_machine_cycle_enable;
  assign unused_machine_cycle_enable = machine_cycle_enable;
endmodule
