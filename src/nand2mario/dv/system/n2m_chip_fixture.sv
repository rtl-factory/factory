// Simulation-only composition for the n2m_chip target: the ASIC chip top
// (n2m_chip, behavioral pads from src/common/dv/pads/) with an external
// cartridge device on its cartridge pins, run in lockstep with a reference
// n2m_system in the existing on-chip-backing configuration. The device model
// is n2m_cartridge_backing itself: its backing port is always ready and
// answers on the next edge, so the chip's mapper sees the reference's
// timing. The reference gets the chip core's reset and button synchronizers
// (the same common modules), so both consoles leave reset on the same edge.
//
// The bench preloads both cartridge stores through one direct preload port
// while reset is held and drives one serial line and one button bus into
// both. On every edge after the synchronized reset releases, the chip's
// output pins must equal the reference's corresponding outputs (pixel
// stream, line/frame markers and the loader's serial line): the fixture
// fails at once otherwise. The reference's raster coordinates and status
// pass through for the bench, and the chip's cartridge pins are counted.
`default_nettype none
`include "src/common/rtl/pkg/macros.vh"

module n2m_chip_fixture #(
  parameter int unsigned CART_ROM_BYTES = 32768,
  parameter int unsigned CART_RAM_BYTES = 8192,
  parameter int unsigned UART_CLKS_PER_BIT = 8
) (
  input  logic                               clk_i,
  input  logic                               reset_i,
  input  logic                               uart_rx_i,
  input  logic [7:0]                         buttons_i,
  // Direct preload of both cartridge stores (reset held).
  input  logic                               rom_load_write_i,
  input  n2m_cartridge_pkg::rom_address_t    rom_load_address_i,
  input  logic [7:0]                         rom_load_data_i,
  // The chip's output pins.
  output logic                               uart_tx_o,
  output logic                               pixel_valid_o,
  output logic [1:0]                         pixel_shade_o,
  output logic                               line_start_o,
  output logic                               frame_start_o,
  // Reference observation.
  output logic [7:0]                         pixel_x_o,
  output logic [7:0]                         pixel_y_o,
  output logic                               ref_reset_o,
  output logic                               ref_halted_o,
  output logic                               ref_fault_o,
  output logic                               ref_locked_o,
  output logic                               ref_uart_tx_o,
  // Cartridge pin activity: requests accepted on the chip's bus.
  output logic [31:0]                        cart_reads_o,
  output logic [31:0]                        cart_writes_o
);
  // Chip pins.
  logic        clk_pad, reset_pad, uart_rx_pad, uart_tx_pad;
  logic [7:0]  buttons_pad;
  logic        pixel_valid_pad, line_start_pad, frame_start_pad;
  logic [1:0]  pixel_shade_pad;
  logic        cart_valid_pad, cart_write_pad, cart_ram_pad;
  logic [20:0] cart_address_pad;
  logic [7:0]  cart_write_data_pad, cart_read_data_pad;
  logic        cart_ready_pad, cart_response_valid_pad;

  assign clk_pad = clk_i;
  assign reset_pad = reset_i;
  assign uart_rx_pad = uart_rx_i;
  assign buttons_pad = buttons_i;

  n2m_chip #(
    .UART_CLKS_PER_BIT (UART_CLKS_PER_BIT)
  ) u_chip (
    .clk_pad                 (clk_pad),
    .reset_pad               (reset_pad),
    .uart_rx_pad             (uart_rx_pad),
    .uart_tx_pad             (uart_tx_pad),
    .buttons_pad             (buttons_pad),
    .pixel_valid_pad         (pixel_valid_pad),
    .pixel_shade_pad         (pixel_shade_pad),
    .line_start_pad          (line_start_pad),
    .frame_start_pad         (frame_start_pad),
    .cart_valid_pad          (cart_valid_pad),
    .cart_write_pad          (cart_write_pad),
    .cart_ram_pad            (cart_ram_pad),
    .cart_address_pad        (cart_address_pad),
    .cart_write_data_pad     (cart_write_data_pad),
    .cart_ready_pad          (cart_ready_pad),
    .cart_response_valid_pad (cart_response_valid_pad),
    .cart_read_data_pad      (cart_read_data_pad)
  );

  // External cartridge device: the on-chip backing model on the pins. Its
  // load port is the bench's preload path only; its responses drop while
  // the chip's synchronized system reset is held, as on chip.
  logic                                 sync_reset;
  n2m_cartridge_pkg::backing_request_t  device_request, device_load;
  n2m_cartridge_pkg::backing_response_t device_response;
  logic                                 device_ready;
  assign device_request.valid = cart_valid_pad;
  assign device_request.write = cart_write_pad;
  assign device_request.ram = cart_ram_pad;
  assign device_request.address = cart_address_pad;
  assign device_request.write_data = cart_write_data_pad;
  assign device_load.valid = rom_load_write_i;
  assign device_load.write = 1'b1;
  assign device_load.ram = 1'b0;
  assign device_load.address = rom_load_address_i;
  assign device_load.write_data = rom_load_data_i;
  assign cart_ready_pad = device_ready;
  assign cart_response_valid_pad = device_response.valid;
  assign cart_read_data_pad = device_response.read_data;

  /* verilator lint_off PINCONNECTEMPTY */
  n2m_cartridge_backing #(
    .ROM_BYTES (CART_ROM_BYTES),
    .RAM_BYTES (CART_RAM_BYTES)
  ) u_device (
    .clk_i           (clk_i),
    .reset_i         (sync_reset),
    .request_i       (device_request),
    .ready_o         (device_ready),
    .response_o      (device_response),
    .load_request_i  (device_load),
    .load_ready_o    (),
    .load_response_o (),
    .initialized_o   ()
  );

  // Reference: the same synchronizers as n2m_chip_core, then n2m_system in
  // its on-chip-backing, display-tier configuration.
  logic [7:0] ref_buttons;
  n2a_reset_sync u_ref_reset (
    .clk_i         (clk_i),
    .async_reset_i (reset_i),
    .reset_o       (sync_reset)
  );
  genvar button;
  generate
    for (button = 0; button < 8; button++) begin : g_ref_buttons
      n2a_cdc_bit_sync u_sync (
        .clk_i   (clk_i),
        .reset_i (sync_reset),
        .async_i (buttons_i[button]),
        .sync_o  (ref_buttons[button])
      );
    end
  endgenerate

  logic       ref_pixel_valid, ref_line_start, ref_frame_start;
  logic [1:0] ref_pixel_shade;
  n2m_system #(
    .CART_ROM_BYTES    (CART_ROM_BYTES),
    .CART_RAM_BYTES    (CART_RAM_BYTES),
    .UART_CLKS_PER_BIT (UART_CLKS_PER_BIT)
  ) u_ref (
    .clk_i               (clk_i),
    .reset_i             (sync_reset),
    .rom_load_write_i    (rom_load_write_i),
    .rom_load_address_i  (rom_load_address_i),
    .rom_load_data_i     (rom_load_data_i),
    .rom_load_ready_o    (),
    .uart_rx_i           (uart_rx_i),
    .uart_tx_o           (ref_uart_tx_o),
    .core_reset_o        (ref_reset_o),
    .buttons_i           (ref_buttons),
    .serial_tx_valid_o   (),
    .serial_tx_data_o    (),
    .retirement_o        (),
    .halted_o            (ref_halted_o),
    .stopped_o           (),
    .locked_o            (ref_locked_o),
    .fault_o             (ref_fault_o),
    .pixel_valid_o       (ref_pixel_valid),
    .pixel_shade_o       (ref_pixel_shade),
    .pixel_x_o           (pixel_x_o),
    .pixel_y_o           (pixel_y_o),
    .line_start_o        (ref_line_start),
    .frame_start_o       (ref_frame_start),
    .vga_red_o           (),
    .vga_green_o         (),
    .vga_blue_o          (),
    .vga_hsync_o         (),
    .vga_vsync_o         (),
    .pixel_start_o       (),
    .frame_swapped_o     (),
    .frame_dropped_o     (),
    .frame_repeated_o    (),
    .display_valid_o     (),
    .dram_addr_o         (),
    .dram_ba_o           (),
    .dram_cas_n_o        (),
    .dram_cke_o          (),
    .dram_clk_o          (),
    .dram_cs_n_o         (),
    .dram_dqml_o         (),
    .dram_dqmh_o         (),
    .dram_ras_n_o        (),
    .dram_we_n_o         (),
    .dram_dq_o           (),
    .dram_dq_oe_o        (),
    .dram_dq_i           (16'h0000),
    .cart_bus_request_o  (),
    .cart_bus_ready_i    (1'b0),
    .cart_bus_response_i ('0)
  );
  /* verilator lint_on PINCONNECTEMPTY */

  assign uart_tx_o = uart_tx_pad;
  assign pixel_valid_o = pixel_valid_pad;
  assign pixel_shade_o = pixel_shade_pad;
  assign line_start_o = line_start_pad;
  assign frame_start_o = frame_start_pad;

  // Lockstep: every chip output pin equals the reference output.
  `N2A_ASSERT(chip_matches_reference, clk_i, sync_reset,
      {uart_tx_pad, pixel_valid_pad, pixel_shade_pad, line_start_pad, frame_start_pad} ==
      {ref_uart_tx_o, ref_pixel_valid, ref_pixel_shade, ref_line_start, ref_frame_start})

  // Cartridge pin activity (accepted bus requests by direction).
  logic        cart_accept;
  logic [31:0] cart_reads_d, cart_writes_d;
  assign cart_accept = cart_valid_pad && cart_ready_pad;
  assign cart_reads_d = cart_reads_o + 32'((cart_accept && !cart_write_pad) ? 1 : 0);
  assign cart_writes_d = cart_writes_o + 32'((cart_accept && cart_write_pad) ? 1 : 0);
  `N2A_DFF_RST(cart_reads_o, cart_reads_d, clk_i, sync_reset)
  `N2A_DFF_RST(cart_writes_o, cart_writes_d, clk_i, sync_reset)
endmodule
