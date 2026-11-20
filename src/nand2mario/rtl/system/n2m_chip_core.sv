`default_nettype none
`include "src/common/rtl/pkg/macros.vh"

// Pad-less core of the nand2mario ASIC chip (wiki/src/nand2mario/chip/HAS.md):
// n2m_system with the off-chip cartridge bus (CART_EXTERNAL)
// and without the FPGA display tier (DISPLAY = 0), wrapped with the chip's
// reset and button synchronizers. Its ports are exactly the chip's signal
// pins; n2m_chip adds only the IHP pad ring around it, and the core-only
// ASIC estimates elaborate this module. The simulation preload port, the
// CPU observation outputs, the serial link observation, the SDRAM and VGA
// pins are left unconnected, so synthesis removes what only they consume.
//
// reset_i is asynchronous at the pin: n2a_reset_sync asserts the system
// reset at once and releases it on a clock edge. buttons_i are asynchronous
// levels, each through a two-flop synchronizer. The cartridge bus inputs
// are synchronous to clk_i by the bus contract (the device shares the
// clock); uart_rx_i is synchronized inside the loader's receiver.
/* verilator lint_off SYNCASYNCNET */
module n2m_chip_core #(
  // Loader UART bit period in clocks (217 is 115200 baud at 25 MHz).
  parameter int unsigned UART_CLKS_PER_BIT = 217
) (
  input  logic        clk_i,
  input  logic        reset_i,
  // Cartridge loader and debug serial link (8-N-1).
  input  logic        uart_rx_i,
  output logic        uart_tx_o,
  // Active-high buttons: Right, Left, Up, Down, A, B, Select, Start.
  input  logic [7:0]  buttons_i,
  // PPU pixel stream: one pulse per pixel in raster order with its 2-bit
  // shade, and the line/frame markers that accompany pixels x = 0 / (0, 0).
  output logic        pixel_valid_o,
  output logic [1:0]  pixel_shade_o,
  output logic        line_start_o,
  output logic        frame_start_o,
  // Off-chip cartridge bus (n2m_system g_external): request held until
  // sampled with cart_ready_i, one response per request on a later edge.
  output logic        cart_valid_o,
  output logic        cart_write_o,
  output logic        cart_ram_o,
  output logic [20:0] cart_address_o,
  output logic [7:0]  cart_write_data_o,
  input  logic        cart_ready_i,
  input  logic        cart_response_valid_i,
  input  logic [7:0]  cart_read_data_i
);
  logic                                 reset;
  logic [7:0]                           buttons;
  n2m_cartridge_pkg::backing_request_t  cart_request;
  n2m_cartridge_pkg::backing_response_t cart_response;

  n2a_reset_sync u_reset (
    .clk_i         (clk_i),
    .async_reset_i (reset_i),
    .reset_o       (reset)
  );

  genvar button;
  generate
    for (button = 0; button < 8; button++) begin : g_buttons
      n2a_cdc_bit_sync u_sync (
        .clk_i   (clk_i),
        .reset_i (reset),
        .async_i (buttons_i[button]),
        .sync_o  (buttons[button])
      );
    end
  endgenerate

  assign cart_valid_o = cart_request.valid;
  assign cart_write_o = cart_request.write;
  assign cart_ram_o = cart_request.ram;
  assign cart_address_o = cart_request.address;
  assign cart_write_data_o = cart_request.write_data;
  assign cart_response.valid = cart_response_valid_i;
  assign cart_response.read_data = cart_read_data_i;

  // Observation, preload, SDRAM and VGA ports are not chip pins.
  /* verilator lint_off PINCONNECTEMPTY */
  n2m_system #(
    .CART_EXTERNAL     (1),
    .DISPLAY           (0),
    .UART_CLKS_PER_BIT (UART_CLKS_PER_BIT)
  ) u_system (
    .clk_i               (clk_i),
    .reset_i             (reset),
    .rom_load_write_i    (1'b0),
    .rom_load_address_i  ('0),
    .rom_load_data_i     (8'h00),
    .rom_load_ready_o    (),
    .uart_rx_i           (uart_rx_i),
    .uart_tx_o           (uart_tx_o),
    .core_reset_o        (),
    .buttons_i           (buttons),
    .serial_tx_valid_o   (),
    .serial_tx_data_o    (),
    .retirement_o        (),
    .halted_o            (),
    .stopped_o           (),
    .locked_o            (),
    .fault_o             (),
    .pixel_valid_o       (pixel_valid_o),
    .pixel_shade_o       (pixel_shade_o),
    .pixel_x_o           (),
    .pixel_y_o           (),
    .line_start_o        (line_start_o),
    .frame_start_o       (frame_start_o),
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
    .cart_bus_request_o  (cart_request),
    .cart_bus_ready_i    (cart_ready_i),
    .cart_bus_response_i (cart_response)
  );
  /* verilator lint_on PINCONNECTEMPTY */
endmodule
/* verilator lint_on SYNCASYNCNET */
