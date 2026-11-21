// Simulation-only composition for the n2m_system_sdram target: n2m_system
// with the SDRAM cartridge backing, the owned SDRAM device model on its pins
// and a host-side pair of common UART PHYs so the bench exchanges loader
// frames byte by byte. The CPU observation ports pass through; the pixel
// stream is exposed for the optional library scenario; the VGA output is unused.
// The direct preload port
// passes through for the direct-preload target.
`default_nettype none

module n2m_system_sdram_fixture #(
    parameter int unsigned UART_CLKS_PER_BIT = 8,
    parameter int unsigned CART_LIBRARY = 0,
    parameter int unsigned FILL_SEED = 32'h12345678
) (
    input  logic                               clk_i,
    input  logic                               reset_i,
    input  logic [7:0]                         buttons_i,
    // Direct preload port of n2m_system, passed through.
    input  logic                               rom_load_write_i,
    input  n2m_cartridge_pkg::t_rom_address    rom_load_address_i,
    input  logic [7:0]                         rom_load_data_i,
    output logic                               rom_load_ready_o,
    input  logic                               host_tx_valid_i,
    input  logic [7:0]                         host_tx_data_i,
    output logic                               host_tx_ready_o,
    output logic                               host_rx_valid_o,
    output logic [7:0]                         host_rx_data_o,
    output logic                               core_reset_o,
    output logic                               serial_tx_valid_o,
    output logic [7:0]                         serial_tx_data_o,
    output n2m_interfaces_pkg::t_retirement    retirement_o,
    output logic                               halted_o,
    output logic                               stopped_o,
    output logic                               locked_o,
    output logic                               fault_o,
    output logic                               pixel_valid_o,
    output logic [1:0]                         pixel_shade_o,
    output logic [7:0]                         pixel_x_o,
    output logic [7:0]                         pixel_y_o,
    output logic                               frame_start_o
);
    logic        host_line, system_line;
    logic [12:0] dram_addr;
    logic [1:0]  dram_ba;
    logic        dram_cas_n, dram_cke, dram_clk, dram_cs_n, dram_dqml, dram_dqmh, dram_ras_n, dram_we_n;
    logic [15:0] dram_dq_o, dram_dq_i;
    logic        dram_dq_oe;

    /* verilator lint_off PINCONNECTEMPTY */
    n2a_uart_tx #(.CLKS_PER_BIT (UART_CLKS_PER_BIT)) u_host_tx (
        .clk_i        (clk_i),
        .reset_i      (reset_i),
        .byte_valid_i (host_tx_valid_i),
        .byte_ready_o (host_tx_ready_o),
        .byte_i       (host_tx_data_i),
        .busy_o       (),
        .tx_o         (host_line)
    );

    n2a_uart_rx #(.CLKS_PER_BIT (UART_CLKS_PER_BIT)) u_host_rx (
        .clk_i           (clk_i),
        .reset_i         (reset_i),
        .rx_i            (system_line),
        .byte_o          (host_rx_data_o),
        .byte_valid_o    (host_rx_valid_o),
        .busy_o          (),
        .framing_error_o ()
    );

    n2m_system #(
        .CART_SDRAM        (1),
        .CART_LIBRARY      (CART_LIBRARY),
        .UART_CLKS_PER_BIT (UART_CLKS_PER_BIT)
    ) u_system (
        .clk_i              (clk_i),
        .reset_i            (reset_i),
        .rom_load_write_i   (rom_load_write_i),
        .rom_load_address_i (rom_load_address_i),
        .rom_load_data_i    (rom_load_data_i),
        .rom_load_ready_o   (rom_load_ready_o),
        .uart_rx_i          (host_line),
        .uart_tx_o          (system_line),
        .core_reset_o       (core_reset_o),
        .buttons_i          (buttons_i),
        .serial_tx_valid_o  (serial_tx_valid_o),
        .serial_tx_data_o   (serial_tx_data_o),
        .retirement_o       (retirement_o),
        .halted_o           (halted_o),
        .stopped_o          (stopped_o),
        .locked_o           (locked_o),
        .fault_o            (fault_o),
        .pixel_valid_o      (pixel_valid_o),
        .pixel_shade_o      (pixel_shade_o),
        .pixel_x_o          (pixel_x_o),
        .pixel_y_o          (pixel_y_o),
        .line_start_o       (),
        .frame_start_o      (frame_start_o),
        .vga_red_o          (),
        .vga_green_o        (),
        .vga_blue_o         (),
        .vga_hsync_o        (),
        .vga_vsync_o        (),
        .pixel_start_o      (),
        .frame_swapped_o    (),
        .frame_dropped_o    (),
        .frame_repeated_o   (),
        .display_valid_o    (),
        .dram_addr_o        (dram_addr),
        .dram_ba_o          (dram_ba),
        .dram_cas_n_o       (dram_cas_n),
        .dram_cke_o         (dram_cke),
        .dram_clk_o         (dram_clk),
        .dram_cs_n_o        (dram_cs_n),
        .dram_dqml_o        (dram_dqml),
        .dram_dqmh_o        (dram_dqmh),
        .dram_ras_n_o       (dram_ras_n),
        .dram_we_n_o        (dram_we_n),
        .dram_dq_o          (dram_dq_o),
        .dram_dq_oe_o       (dram_dq_oe),
        .dram_dq_i          (dram_dq_i),
        // Why: this composition serves the cartridge from the SDRAM backing, so the ASIC chip's off-chip cartridge bus stays idle.
        .cart_bus_request_o  (),
        .cart_bus_ready_i    (1'b0),
        .cart_bus_response_i ('0)
    );
    /* verilator lint_on PINCONNECTEMPTY */

    /* verilator lint_off UNUSEDSIGNAL */
    logic dram_clk_unused;
    assign dram_clk_unused = dram_clk;
    /* verilator lint_on UNUSEDSIGNAL */

    n2a_sdram_model #(
        .INIT_REFRESHES (8),
        .FILL_SEED      (FILL_SEED)
    ) u_sdram (
        .clk_i   (clk_i),
        .cke_i   (dram_cke),
        .cs_n_i  (dram_cs_n),
        .ras_n_i (dram_ras_n),
        .cas_n_i (dram_cas_n),
        .we_n_i  (dram_we_n),
        .ba_i    (dram_ba),
        .addr_i  (dram_addr),
        .dqm_i   ({dram_dqmh, dram_dqml}),
        .dq_i    (dram_dq_o),
        .dq_oe_i (dram_dq_oe),
        .dq_o    (dram_dq_i),
        .preload_valid_i       (1'b0),
        .preload_address_i     ('0),
        .preload_data_i        ('0),
        .preload_byte_enable_i ('0)
    );
endmodule
