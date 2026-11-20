`default_nettype none

// nand2mario ASIC chip top (wiki/src/nand2mario/chip/HAS.md):
// the IHP SG13G2 sg13g2_io pad ring around n2m_chip_core and nothing else.
// Every signal pin has one pad: sg13g2_IOPadIn for an input,
// sg13g2_IOPadOut4mA for an output (no pin is bidirectional). Four core
// (vdd/vss) and four IO (iovdd/iovss) supply pads complete the ring; the
// ORFS pad.tcl footprint places the instances by the names below and adds
// corners, fillers and bond pads; it attaches bond pads to instances named
// sg13g2_IOPad*, hence the PDK-style instance names. The clock enters through its input pad
// and the clock tree starts at that pad's p2c pin.
//
// The pad cells' pad pins are inout in the PDK; every one here is used in
// one direction only, so the chip ports are plain inputs and outputs.
// Simulation uses directional behavioral pad models
// (src/common/dv/pads/); the ASIC flow uses the PDK
// cells as black boxes.
module n2m_chip #(
  // Loader UART bit period in clocks (217 is 115200 baud at 25 MHz).
  parameter int unsigned UART_CLKS_PER_BIT = 217
) (
  input  logic clk_pad,
  input  logic reset_pad,
  input  logic uart_rx_pad,
  output logic uart_tx_pad,
  input  logic [7:0] buttons_pad,
  output logic pixel_valid_pad,
  output logic [1:0] pixel_shade_pad,
  output logic line_start_pad,
  output logic frame_start_pad,
  output logic cart_valid_pad,
  output logic cart_write_pad,
  output logic cart_ram_pad,
  output logic [20:0] cart_address_pad,
  output logic [7:0] cart_write_data_pad,
  input  logic cart_ready_pad,
  input  logic cart_response_valid_pad,
  input  logic [7:0] cart_read_data_pad
);
  logic clk_p2c;
  logic reset_p2c;
  logic uart_rx_p2c;
  logic uart_tx_c2p;
  logic [7:0] buttons_p2c;
  logic pixel_valid_c2p;
  logic [1:0] pixel_shade_c2p;
  logic line_start_c2p;
  logic frame_start_c2p;
  logic cart_valid_c2p;
  logic cart_write_c2p;
  logic cart_ram_c2p;
  logic [20:0] cart_address_c2p;
  logic [7:0] cart_write_data_c2p;
  logic cart_ready_p2c;
  logic cart_response_valid_p2c;
  logic [7:0] cart_read_data_p2c;

  // Signal pads (instance names are the footprint's IO_*_PINS entries).
  sg13g2_IOPadIn sg13g2_IOPad_clk (.pad(clk_pad), .p2c(clk_p2c));
  sg13g2_IOPadIn sg13g2_IOPad_reset (.pad(reset_pad), .p2c(reset_p2c));
  sg13g2_IOPadIn sg13g2_IOPad_uart_rx (.pad(uart_rx_pad), .p2c(uart_rx_p2c));
  sg13g2_IOPadOut4mA sg13g2_IOPad_uart_tx (.pad(uart_tx_pad), .c2p(uart_tx_c2p));
  sg13g2_IOPadIn sg13g2_IOPad_buttons_0 (.pad(buttons_pad[0]), .p2c(buttons_p2c[0]));
  sg13g2_IOPadIn sg13g2_IOPad_buttons_1 (.pad(buttons_pad[1]), .p2c(buttons_p2c[1]));
  sg13g2_IOPadIn sg13g2_IOPad_buttons_2 (.pad(buttons_pad[2]), .p2c(buttons_p2c[2]));
  sg13g2_IOPadIn sg13g2_IOPad_buttons_3 (.pad(buttons_pad[3]), .p2c(buttons_p2c[3]));
  sg13g2_IOPadIn sg13g2_IOPad_buttons_4 (.pad(buttons_pad[4]), .p2c(buttons_p2c[4]));
  sg13g2_IOPadIn sg13g2_IOPad_buttons_5 (.pad(buttons_pad[5]), .p2c(buttons_p2c[5]));
  sg13g2_IOPadIn sg13g2_IOPad_buttons_6 (.pad(buttons_pad[6]), .p2c(buttons_p2c[6]));
  sg13g2_IOPadIn sg13g2_IOPad_buttons_7 (.pad(buttons_pad[7]), .p2c(buttons_p2c[7]));
  sg13g2_IOPadOut4mA sg13g2_IOPad_pixel_valid (.pad(pixel_valid_pad), .c2p(pixel_valid_c2p));
  sg13g2_IOPadOut4mA sg13g2_IOPad_pixel_shade_0 (.pad(pixel_shade_pad[0]), .c2p(pixel_shade_c2p[0]));
  sg13g2_IOPadOut4mA sg13g2_IOPad_pixel_shade_1 (.pad(pixel_shade_pad[1]), .c2p(pixel_shade_c2p[1]));
  sg13g2_IOPadOut4mA sg13g2_IOPad_line_start (.pad(line_start_pad), .c2p(line_start_c2p));
  sg13g2_IOPadOut4mA sg13g2_IOPad_frame_start (.pad(frame_start_pad), .c2p(frame_start_c2p));
  sg13g2_IOPadOut4mA sg13g2_IOPad_cart_valid (.pad(cart_valid_pad), .c2p(cart_valid_c2p));
  sg13g2_IOPadOut4mA sg13g2_IOPad_cart_write (.pad(cart_write_pad), .c2p(cart_write_c2p));
  sg13g2_IOPadOut4mA sg13g2_IOPad_cart_ram (.pad(cart_ram_pad), .c2p(cart_ram_c2p));
  sg13g2_IOPadOut4mA sg13g2_IOPad_cart_address_0 (.pad(cart_address_pad[0]), .c2p(cart_address_c2p[0]));
  sg13g2_IOPadOut4mA sg13g2_IOPad_cart_address_1 (.pad(cart_address_pad[1]), .c2p(cart_address_c2p[1]));
  sg13g2_IOPadOut4mA sg13g2_IOPad_cart_address_2 (.pad(cart_address_pad[2]), .c2p(cart_address_c2p[2]));
  sg13g2_IOPadOut4mA sg13g2_IOPad_cart_address_3 (.pad(cart_address_pad[3]), .c2p(cart_address_c2p[3]));
  sg13g2_IOPadOut4mA sg13g2_IOPad_cart_address_4 (.pad(cart_address_pad[4]), .c2p(cart_address_c2p[4]));
  sg13g2_IOPadOut4mA sg13g2_IOPad_cart_address_5 (.pad(cart_address_pad[5]), .c2p(cart_address_c2p[5]));
  sg13g2_IOPadOut4mA sg13g2_IOPad_cart_address_6 (.pad(cart_address_pad[6]), .c2p(cart_address_c2p[6]));
  sg13g2_IOPadOut4mA sg13g2_IOPad_cart_address_7 (.pad(cart_address_pad[7]), .c2p(cart_address_c2p[7]));
  sg13g2_IOPadOut4mA sg13g2_IOPad_cart_address_8 (.pad(cart_address_pad[8]), .c2p(cart_address_c2p[8]));
  sg13g2_IOPadOut4mA sg13g2_IOPad_cart_address_9 (.pad(cart_address_pad[9]), .c2p(cart_address_c2p[9]));
  sg13g2_IOPadOut4mA sg13g2_IOPad_cart_address_10 (.pad(cart_address_pad[10]), .c2p(cart_address_c2p[10]));
  sg13g2_IOPadOut4mA sg13g2_IOPad_cart_address_11 (.pad(cart_address_pad[11]), .c2p(cart_address_c2p[11]));
  sg13g2_IOPadOut4mA sg13g2_IOPad_cart_address_12 (.pad(cart_address_pad[12]), .c2p(cart_address_c2p[12]));
  sg13g2_IOPadOut4mA sg13g2_IOPad_cart_address_13 (.pad(cart_address_pad[13]), .c2p(cart_address_c2p[13]));
  sg13g2_IOPadOut4mA sg13g2_IOPad_cart_address_14 (.pad(cart_address_pad[14]), .c2p(cart_address_c2p[14]));
  sg13g2_IOPadOut4mA sg13g2_IOPad_cart_address_15 (.pad(cart_address_pad[15]), .c2p(cart_address_c2p[15]));
  sg13g2_IOPadOut4mA sg13g2_IOPad_cart_address_16 (.pad(cart_address_pad[16]), .c2p(cart_address_c2p[16]));
  sg13g2_IOPadOut4mA sg13g2_IOPad_cart_address_17 (.pad(cart_address_pad[17]), .c2p(cart_address_c2p[17]));
  sg13g2_IOPadOut4mA sg13g2_IOPad_cart_address_18 (.pad(cart_address_pad[18]), .c2p(cart_address_c2p[18]));
  sg13g2_IOPadOut4mA sg13g2_IOPad_cart_address_19 (.pad(cart_address_pad[19]), .c2p(cart_address_c2p[19]));
  sg13g2_IOPadOut4mA sg13g2_IOPad_cart_address_20 (.pad(cart_address_pad[20]), .c2p(cart_address_c2p[20]));
  sg13g2_IOPadOut4mA sg13g2_IOPad_cart_write_data_0 (.pad(cart_write_data_pad[0]), .c2p(cart_write_data_c2p[0]));
  sg13g2_IOPadOut4mA sg13g2_IOPad_cart_write_data_1 (.pad(cart_write_data_pad[1]), .c2p(cart_write_data_c2p[1]));
  sg13g2_IOPadOut4mA sg13g2_IOPad_cart_write_data_2 (.pad(cart_write_data_pad[2]), .c2p(cart_write_data_c2p[2]));
  sg13g2_IOPadOut4mA sg13g2_IOPad_cart_write_data_3 (.pad(cart_write_data_pad[3]), .c2p(cart_write_data_c2p[3]));
  sg13g2_IOPadOut4mA sg13g2_IOPad_cart_write_data_4 (.pad(cart_write_data_pad[4]), .c2p(cart_write_data_c2p[4]));
  sg13g2_IOPadOut4mA sg13g2_IOPad_cart_write_data_5 (.pad(cart_write_data_pad[5]), .c2p(cart_write_data_c2p[5]));
  sg13g2_IOPadOut4mA sg13g2_IOPad_cart_write_data_6 (.pad(cart_write_data_pad[6]), .c2p(cart_write_data_c2p[6]));
  sg13g2_IOPadOut4mA sg13g2_IOPad_cart_write_data_7 (.pad(cart_write_data_pad[7]), .c2p(cart_write_data_c2p[7]));
  sg13g2_IOPadIn sg13g2_IOPad_cart_ready (.pad(cart_ready_pad), .p2c(cart_ready_p2c));
  sg13g2_IOPadIn sg13g2_IOPad_cart_response_valid (.pad(cart_response_valid_pad), .p2c(cart_response_valid_p2c));
  sg13g2_IOPadIn sg13g2_IOPad_cart_read_data_0 (.pad(cart_read_data_pad[0]), .p2c(cart_read_data_p2c[0]));
  sg13g2_IOPadIn sg13g2_IOPad_cart_read_data_1 (.pad(cart_read_data_pad[1]), .p2c(cart_read_data_p2c[1]));
  sg13g2_IOPadIn sg13g2_IOPad_cart_read_data_2 (.pad(cart_read_data_pad[2]), .p2c(cart_read_data_p2c[2]));
  sg13g2_IOPadIn sg13g2_IOPad_cart_read_data_3 (.pad(cart_read_data_pad[3]), .p2c(cart_read_data_p2c[3]));
  sg13g2_IOPadIn sg13g2_IOPad_cart_read_data_4 (.pad(cart_read_data_pad[4]), .p2c(cart_read_data_p2c[4]));
  sg13g2_IOPadIn sg13g2_IOPad_cart_read_data_5 (.pad(cart_read_data_pad[5]), .p2c(cart_read_data_p2c[5]));
  sg13g2_IOPadIn sg13g2_IOPad_cart_read_data_6 (.pad(cart_read_data_pad[6]), .p2c(cart_read_data_p2c[6]));
  sg13g2_IOPadIn sg13g2_IOPad_cart_read_data_7 (.pad(cart_read_data_pad[7]), .p2c(cart_read_data_p2c[7]));

  // Supply pads: core vdd/vss and IO iovdd/iovss, kept with no ports.
  (* keep *) sg13g2_IOPadVdd sg13g2_IOPad_vdd_0 ();
  (* keep *) sg13g2_IOPadVdd sg13g2_IOPad_vdd_1 ();
  (* keep *) sg13g2_IOPadVss sg13g2_IOPad_vss_0 ();
  (* keep *) sg13g2_IOPadVss sg13g2_IOPad_vss_1 ();
  (* keep *) sg13g2_IOPadIOVdd sg13g2_IOPad_iovdd_0 ();
  (* keep *) sg13g2_IOPadIOVdd sg13g2_IOPad_iovdd_1 ();
  (* keep *) sg13g2_IOPadIOVss sg13g2_IOPad_iovss_0 ();
  (* keep *) sg13g2_IOPadIOVss sg13g2_IOPad_iovss_1 ();

  n2m_chip_core #(
    .UART_CLKS_PER_BIT (UART_CLKS_PER_BIT)
  ) u_core (
    .clk_i                 (clk_p2c),
    .reset_i               (reset_p2c),
    .uart_rx_i             (uart_rx_p2c),
    .uart_tx_o             (uart_tx_c2p),
    .buttons_i             (buttons_p2c),
    .pixel_valid_o         (pixel_valid_c2p),
    .pixel_shade_o         (pixel_shade_c2p),
    .line_start_o          (line_start_c2p),
    .frame_start_o         (frame_start_c2p),
    .cart_valid_o          (cart_valid_c2p),
    .cart_write_o          (cart_write_c2p),
    .cart_ram_o            (cart_ram_c2p),
    .cart_address_o        (cart_address_c2p),
    .cart_write_data_o     (cart_write_data_c2p),
    .cart_ready_i          (cart_ready_p2c),
    .cart_response_valid_i (cart_response_valid_p2c),
    .cart_read_data_i      (cart_read_data_p2c)
  );
endmodule
