`default_nettype none

// Simulation-only composition for the focused DMA target: the real timebase
// supplies the machine-cycle enable, the DMA block sits between the test's
// CPU request port and the real memory map with the real cartridge mapper
// and a 32 KiB on-chip backing on its cartridge port (which the test can
// stall to model a slow backing), the memory's
// peripheral port fans out to the DMA register (every other address answers
// open bus), and the PPU-side ports of the memory (blocking levels, OAM read
// port) and the DMA observation (active level, OAM writes) are exposed.
module n2m_dma_fixture (
  input  logic                                clk_i,
  input  logic                                reset_i,
  // CPU bus as the test drives it.
  input  n2m_interfaces_pkg::t_bus_request    cpu_request_i,
  output n2m_interfaces_pkg::t_bus_response   cpu_response_o,
  // Cartridge image load port.
  input  logic                                rom_load_write_i,
  input  n2m_cartridge_pkg::t_rom_address     rom_load_address_i,
  input  logic [7:0]                          rom_load_data_i,
  // Holds the backing not ready (a slow backing such as SDRAM under load).
  input  logic                                backing_stall_i,
  // PPU side of the memory map.
  input  logic                                vram_blocked_i,
  input  logic                                oam_blocked_i,
  input  logic                                ppu_oam_read_i,
  input  logic [7:0]                          ppu_oam_address_i,
  output logic [7:0]                          ppu_oam_data_o,
  output logic                                ppu_oam_valid_o,
  // Observation.
  output logic                                machine_cycle_enable_o,
  output logic                                dma_active_o,
  output logic                                oam_write_o,
  output logic [7:0]                          oam_address_o,
  output logic [7:0]                          oam_data_o
);
  logic                                dot_enable, machine_cycle_enable;
  n2m_interfaces_pkg::t_bus_request    request, io_request;
  n2m_interfaces_pkg::t_bus_response   response, io_response, dma_response;
  logic                                dma_selected;
  logic [4:0]                          interrupt_enable;
  logic                                oam_write;
  logic [7:0]                          oam_address, oam_data;
  logic                                ppu_vram_read;
  logic [12:0]                         ppu_vram_address;
  n2m_interfaces_pkg::t_bus_request    cart_request;
  n2m_interfaces_pkg::t_bus_response   cart_response;
  logic                                cart_ready, backing_ready;
  n2m_cartridge_pkg::t_backing_request  backing_request;
  n2m_cartridge_pkg::t_backing_response backing_response;

  n2m_timebase u_timebase (
    .clk_i                  (clk_i),
    .reset_i                (reset_i),
    .dot_enable_o           (dot_enable),
    .machine_cycle_enable_o (machine_cycle_enable)
  );

  n2m_dma u_dma (
    .clk_i                  (clk_i),
    .reset_i                (reset_i),
    .machine_cycle_enable_i (machine_cycle_enable),
    .io_request_i           (io_request),
    .io_response_o          (dma_response),
    .io_selected_o          (dma_selected),
    .cpu_request_i          (cpu_request_i),
    .cpu_response_o         (cpu_response_o),
    .mem_request_o          (request),
    .mem_response_i         (response),
    .oam_write_o            (oam_write),
    .oam_address_o          (oam_address),
    .oam_data_o             (oam_data),
    .active_o               (dma_active_o)
  );

  assign ppu_vram_read = 1'b0;
  assign ppu_vram_address = '0;
  /* verilator lint_off PINCONNECTEMPTY */
  n2m_memory u_memory (
    .clk_i              (clk_i),
    .reset_i            (reset_i),
    .request_i          (request),
    .response_o         (response),
    .io_request_o       (io_request),
    .io_response_i      (io_response),
    .interrupt_enable_o (interrupt_enable),
    .ppu_vram_read_i    (ppu_vram_read),
    .ppu_vram_address_i (ppu_vram_address),
    .ppu_vram_data_o    (),
    .ppu_vram_valid_o   (),
    .ppu_oam_read_i     (ppu_oam_read_i),
    .ppu_oam_address_i  (ppu_oam_address_i),
    .ppu_oam_data_o     (ppu_oam_data_o),
    .ppu_oam_valid_o    (ppu_oam_valid_o),
    .vram_clear_i         (1'b0),
    .vram_clear_address_i (13'd0),
    .dma_oam_write_i    (oam_write),
    .dma_oam_address_i  (oam_address),
    .dma_oam_data_i     (oam_data),
    .vram_blocked_i     (vram_blocked_i),
    .oam_blocked_i      (oam_blocked_i),
    .cart_request_o     (cart_request),
    .cart_ready_i       (cart_ready),
    .cart_response_i    (cart_response)
  );
  /* verilator lint_on PINCONNECTEMPTY */

  n2m_cartridge u_cartridge (
    .clk_i              (clk_i),
    .reset_i            (reset_i),
    .request_i          (cart_request),
    .ready_o            (cart_ready),
    .response_o         (cart_response),
    .backing_request_o  (backing_request),
    .backing_ready_i    (backing_ready && !backing_stall_i),
    .backing_response_i (backing_response),
    .load_write_i       (rom_load_write_i),
    .load_address_i     (rom_load_address_i),
    .load_data_i        (rom_load_data_i)
  );

  // Why: the on-chip backing's load port is always ready, so the bench's one-byte-per-edge
  // preload can become plain ROM writes, and its responses and the constant
  // initialization status need no check here. Gating the request while stalled
  // keeps a stalled backing from seeing a cartridge request.
  n2m_cartridge_pkg::t_backing_request backing_gated;
  always_comb begin
    backing_gated = backing_request;
    backing_gated.valid = backing_request.valid && !backing_stall_i;
  end

  n2m_cartridge_pkg::t_backing_request rom_load_request;
  always_comb begin
    rom_load_request.valid = rom_load_write_i;
    rom_load_request.write = 1'b1;
    rom_load_request.ram = 1'b0;
    rom_load_request.address = rom_load_address_i;
    rom_load_request.write_data = rom_load_data_i;
  end
  /* verilator lint_off PINCONNECTEMPTY */
  n2m_cartridge_backing #(
    .ROM_BYTES (32768),
    .RAM_BYTES (8192)
  ) u_backing (
    .clk_i           (clk_i),
    .reset_i         (reset_i),
    .request_i       (backing_gated),
    .ready_o         (backing_ready),
    .response_o      (backing_response),
    .load_request_i  (rom_load_request),
    .load_ready_o    (),
    .load_response_o (),
    .initialized_o   ()
  );
  /* verilator lint_on PINCONNECTEMPTY */

  always_comb begin
    io_response.valid = io_request.valid;
    io_response.fault = 1'b0;
    io_response.read_data = n2m_memory_pkg::OPEN_BUS_DATA;
    if (dma_selected) io_response = dma_response;
  end

  assign machine_cycle_enable_o = machine_cycle_enable;
  assign oam_write_o = oam_write;
  assign oam_address_o = oam_address;
  assign oam_data_o = oam_data;

  logic unused_signals;
  assign unused_signals = dot_enable | (|interrupt_enable);
endmodule
