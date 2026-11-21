`default_nettype none
`include "src/common/rtl/pkg/macros.vh"

// DE2-115 electrical adapter; wiki/src/nand2mario/fpga/de2_115/board-spec.md (DE2-115 profile).
// The low x16 SDRAM has the existing controller geometry. Upper DQ pins
// are unused inputs under the QSF policy; upper DQM stays masked.
module n2m_de2_115 #(
    // Loader UART bit period in system clocks: 12 1/2 is the 2 Mbaud board
    // rate at 25 MHz, through the PHY's fractional accumulator.
    parameter int unsigned UART_CLKS_PER_BIT = 12,
    parameter int unsigned UART_FRAC_NUM = 1,
    parameter int unsigned UART_FRAC_DEN = 2,
    // Default cartridge behavior is unchanged; the maintained library profile enables this.
    parameter int unsigned CART_LIBRARY = 0,
    // LEDR7 shows this bit of the free-running system-clock counter: 24 is a
    // 0.67 s toggle on the board; the board smoke shortens it to observe it.
    parameter int unsigned HEARTBEAT_BIT = 24
) (
    input  logic       CLOCK_50,
    input  logic [1:0] KEY,
    input  logic [7:0] SW,
    input  logic       UART_RXD,
    output logic       UART_TXD,
    output logic [9:0] LEDR,
    output logic [6:0] HEX0,
    output logic [6:0] HEX1,
    output logic [6:0] HEX2,
    output logic [6:0] HEX3,
    output logic [6:0] HEX4,
    output logic [6:0] HEX5,
    output logic [7:0] VGA_R,
    output logic [7:0] VGA_G,
    output logic [7:0] VGA_B,
    output logic       VGA_CLK,
    output logic       VGA_BLANK_N,
    output logic       VGA_SYNC_N,
    output logic       VGA_HS,
    output logic       VGA_VS,
    output logic [12:0] DRAM_ADDR,
    output logic [1:0] DRAM_BA,
    output logic       DRAM_CAS_N,
    output logic       DRAM_CKE,
    output logic       DRAM_CLK,
    output logic       DRAM_CS_N,
    output logic [3:0] DRAM_DQM,
    output logic       DRAM_RAS_N,
    output logic       DRAM_WE_N,
    inout  tri  [15:0] DRAM_DQ
);
    localparam int unsigned SERIAL_STRETCH_BIT = 22;  // Why: a one-clock serial strobe is invisible on an LED; 2^22 clocks stretch it to 168 ms.
    `ASSERT_PARAM(g_invalid_heartbeat, (HEARTBEAT_BIT >= 1) && (HEARTBEAT_BIT <= 30),
        "n2m_de2_115 HEARTBEAT_BIT must be between 1 and 30")

    logic clk_25, locked, reset, core_reset;
    logic [3:0] red, green, blue;
    // Ref: ADV7123 captures mid-pixel, so VGA_CLK is the inverted clock and the
    // connector syncs lead by half a pixel. Why: VGA_BLANK_N is tied high because
    // the display already emits black outside active video.
    assign VGA_R = {red, red};
    assign VGA_G = {green, green};
    assign VGA_B = {blue, blue};
    assign VGA_CLK = ~clk_25;
    assign VGA_BLANK_N = 1'b1;
    assign VGA_SYNC_N = 1'b0;
    assign DRAM_DQM[3:2] = 2'b11;
    /* verilator lint_off UNUSEDSIGNAL */
    logic [7:0] hex0_segments;
    /* verilator lint_on UNUSEDSIGNAL */
    assign HEX0 = hex0_segments[6:0];
    /* verilator lint_off UNUSEDSIGNAL */
    logic [7:0] hex1_segments;
    /* verilator lint_on UNUSEDSIGNAL */
    assign HEX1 = hex1_segments[6:0];
    /* verilator lint_off UNUSEDSIGNAL */
    logic [7:0] hex2_segments;
    /* verilator lint_on UNUSEDSIGNAL */
    assign HEX2 = hex2_segments[6:0];
    /* verilator lint_off UNUSEDSIGNAL */
    logic [7:0] hex3_segments;
    /* verilator lint_on UNUSEDSIGNAL */
    assign HEX3 = hex3_segments[6:0];
    /* verilator lint_off UNUSEDSIGNAL */
    logic [7:0] hex4_segments;
    /* verilator lint_on UNUSEDSIGNAL */
    assign HEX4 = hex4_segments[6:0];
    /* verilator lint_off UNUSEDSIGNAL */
    logic [7:0] hex5_segments;
    /* verilator lint_on UNUSEDSIGNAL */
    assign HEX5 = hex5_segments[6:0];

    logic [15:0] dram_dq_out, dram_dq_in;
    logic dram_dq_oe;
    logic halted, stopped, cpu_locked, fault, display_valid;
    logic serial_tx_valid;
    logic [7:0] serial_tx_data;
    // Why: only the retired PC reaches a pin, so the other observation fields
    // are unused by design.
    /* verilator lint_off UNUSEDSIGNAL */
    n2m_interfaces_pkg::t_retirement retirement;
    /* verilator lint_on UNUSEDSIGNAL */
    // JTAG scan ports and the keyboard endpoint's byte offer.
    logic jtag_tck, jtag_tdi, jtag_capture, jtag_shift, jtag_update, jtag_update_ir, jtag_tdo;
    logic [1:0] jtag_ir;
    logic inject_valid;
    logic [7:0] inject_byte;
    logic [7:0] jtag_buttons, board_levels, fallback_buttons, buttons;
    logic [8:0] board_raw, board_level;
    logic [15:0] pc_q, pc_d;
    logic [7:0] serial_q;
    logic [HEARTBEAT_BIT:0] heartbeat_q, heartbeat_d;
    logic [SERIAL_STRETCH_BIT:0] stretch_q, stretch_d;

    n2m_de2_115_clock u_clock (
        .reference_clk_i (CLOCK_50),
        .reset_i         (!KEY[0]),
        .clk_25_o        (clk_25),
        .locked_o        (locked)
    );
    n2a_reset_sync u_reset (
        .clk_i         (clk_25),
        .async_reset_i (!KEY[0] || !locked),
        .reset_o       (reset)
    );

    // Why: the keyboard decoder takes every Set-2 byte the endpoint offers,
    // so inject_ready is tied high. Ref: n2m_de2_115_quartus.json names this
    // Virtual JTAG hierarchy.
    n2a_jtag_virtual u_jtag_virtual (
        .tck_o       (jtag_tck),
        .tdi_o       (jtag_tdi),
        .capture_o   (jtag_capture),
        .shift_o     (jtag_shift),
        .update_o    (jtag_update),
        .update_ir_o (jtag_update_ir),
        .ir_o        (jtag_ir),
        .tdo_i       (jtag_tdo)
    );
    n2a_jtag_keyboard u_jtag_keyboard (
        .clk_i          (clk_25),
        .reset_i        (reset),
        .tck_i          (jtag_tck),
        .ir_i           (jtag_ir),
        .capture_i      (jtag_capture),
        .shift_i        (jtag_shift),
        .update_i       (jtag_update),
        .update_ir_i    (jtag_update_ir),
        .tdi_i          (jtag_tdi),
        .tdo_o          (jtag_tdo),
        .inject_valid_o (inject_valid),
        .inject_byte_o  (inject_byte),
        .inject_ready_i (1'b1)
    );
    n2m_scancode_buttons u_scancode (
        .clk_i        (clk_25),
        .reset_i      (reset),
        .byte_valid_i (inject_valid),
        .byte_i       (inject_byte),
        .buttons_o    (jtag_buttons)
    );

    // Why: the switches and KEY1 give a joypad without the host keyboard:
    // SW[7:0] hold button levels in joypad order (Right, Left, Up, Down, A, B,
    // Select, Start) and KEY1 pressed is Start.
    assign board_raw = {!KEY[1], SW};
    n2m_board_inputs #(
        .WIDTH (9)
    ) u_inputs (
        .clk_i   (clk_25),
        .reset_i (reset),
        .raw_i   (board_raw),
        .level_o (board_level)
    );
    assign board_levels = board_level[7:0];
    assign fallback_buttons = board_levels | {board_level[8], 7'b0};
    assign buttons = jtag_buttons | fallback_buttons;

    // Why: these observation ports have no board pin, so PINCONNECTEMPTY is
    // waived for this instance only.
    /* verilator lint_off PINCONNECTEMPTY */
    // The console core leaves reset only once the SDRAM is initialized and
    // the loader does not hold it (system HAS reset domains).
    n2m_system #(
        .CART_SDRAM        (1),
        .CART_LIBRARY      (CART_LIBRARY),
        .UART_CLKS_PER_BIT (UART_CLKS_PER_BIT),
        .UART_FRAC_NUM     (UART_FRAC_NUM),
        .UART_FRAC_DEN     (UART_FRAC_DEN)
    ) u_system (
        .clk_i              (clk_25),
        .reset_i            (reset),
        .rom_load_write_i   (1'b0),
        .rom_load_address_i ('0),
        .rom_load_data_i    (8'h00),
        .rom_load_ready_o   (),
        .uart_rx_i          (UART_RXD),
        .uart_tx_o          (UART_TXD),
        .core_reset_o       (core_reset),
        .buttons_i          (buttons),
        .serial_tx_valid_o  (serial_tx_valid),
        .serial_tx_data_o   (serial_tx_data),
        .retirement_o       (retirement),
        .halted_o           (halted),
        .stopped_o          (stopped),
        .locked_o           (cpu_locked),
        .fault_o            (fault),
        .pixel_valid_o      (),
        .pixel_shade_o      (),
        .pixel_x_o          (),
        .pixel_y_o          (),
        .line_start_o       (),
        .frame_start_o      (),
        .vga_red_o          (red),
        .vga_green_o        (green),
        .vga_blue_o         (blue),
        .vga_hsync_o        (VGA_HS),
        .vga_vsync_o        (VGA_VS),
        .pixel_start_o      (),
        .frame_swapped_o    (),
        .frame_dropped_o    (),
        .frame_repeated_o   (),
        .display_valid_o    (display_valid),
        .dram_addr_o        (DRAM_ADDR),
        .dram_ba_o          (DRAM_BA),
        .dram_cas_n_o       (DRAM_CAS_N),
        .dram_cke_o         (DRAM_CKE),
        .dram_clk_o         (DRAM_CLK),
        .dram_cs_n_o        (DRAM_CS_N),
        .dram_dqml_o        (DRAM_DQM[0]),
        .dram_dqmh_o        (DRAM_DQM[1]),
        .dram_ras_n_o       (DRAM_RAS_N),
        .dram_we_n_o        (DRAM_WE_N),
        .dram_dq_o          (dram_dq_out),
        .dram_dq_oe_o       (dram_dq_oe),
        .dram_dq_i          (dram_dq_in),
        // Why: with CART_SDRAM the cartridge lives in SDRAM, so the ASIC chip's
        // off-chip cartridge bus is never ready and never answers.
        .cart_bus_request_o  (),
        .cart_bus_ready_i    (1'b0),
        .cart_bus_response_i ('0)
    );
    /* verilator lint_on PINCONNECTEMPTY */

    // Why: the system keeps DQ split into out, enable and in; the board top is
    // the one place the inout pin is resolved and tri-stated.
    assign DRAM_DQ = dram_dq_oe ? dram_dq_out : 16'hzzzz;
    assign dram_dq_in = DRAM_DQ;

    logic retirement_valid;
    assign retirement_valid = retirement.valid;
    assign pc_d = retirement.pc_after;
    `DFF_RST_EN(pc_q, pc_d, clk_25, retirement_valid, core_reset, 16'h0000)
    `DFF_RST_EN(serial_q, serial_tx_data, clk_25, serial_tx_valid, core_reset, 8'h00)
    assign heartbeat_d = heartbeat_q + 1'b1;
    `DFF_RST(heartbeat_q, heartbeat_d, clk_25, reset)
    assign stretch_d = serial_tx_valid ? {1'b1, {SERIAL_STRETCH_BIT{1'b0}}} :
                       (stretch_q[SERIAL_STRETCH_BIT] ? stretch_q + 1'b1 : stretch_q);
    `DFF_RST(stretch_q, stretch_d, clk_25, core_reset)

    assign LEDR = {stretch_q[SERIAL_STRETCH_BIT], |buttons, heartbeat_q[HEARTBEAT_BIT],
                   display_valid, fault, cpu_locked, stopped, halted, core_reset, locked};
    n2m_seven_segment u_hex0 (.nibble_i(pc_q[3:0]),      .segments_o(hex0_segments));
    n2m_seven_segment u_hex1 (.nibble_i(pc_q[7:4]),      .segments_o(hex1_segments));
    n2m_seven_segment u_hex2 (.nibble_i(pc_q[11:8]),     .segments_o(hex2_segments));
    n2m_seven_segment u_hex3 (.nibble_i(pc_q[15:12]),    .segments_o(hex3_segments));
    n2m_seven_segment u_hex4 (.nibble_i(serial_q[3:0]),  .segments_o(hex4_segments));
    n2m_seven_segment u_hex5 (.nibble_i(serial_q[7:4]),  .segments_o(hex5_segments));
endmodule
