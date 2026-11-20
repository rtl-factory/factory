// DE10-Lite electrical and clock policy; no simulation memory in this top.
module n2d_de10_lite (
    input logic MAX10_CLK1_50, input logic RESET_N,
    input logic UART_RXD, output logic UART_TXD,
    input logic PS2_CLK, input logic PS2_DAT,
    output logic [3:0] VGA_R, VGA_G, VGA_B,
    output logic VGA_HS, VGA_VS,
    output logic [3:0] LEDR,
    output logic [12:0] DRAM_ADDR,
    output logic [1:0] DRAM_BA,
    output logic DRAM_CAS_N, DRAM_CKE, DRAM_CLK, DRAM_CS_N,
    inout tri [15:0] DRAM_DQ,
    output logic DRAM_LDQM, DRAM_RAS_N, DRAM_UDQM, DRAM_WE_N
);
    logic clk_25, locked, reset, system_reset;
    logic init_done, halt_ack;
    // Instruction line traffic is read-only; its write payload is unused.
    /* verilator lint_off UNUSEDSIGNAL */
    logic [2:0] memory_request_prefetch;
    logic [2:0] memory_request_valid, memory_request_ready, memory_request_write;
    logic [77:0] memory_request_address;
    logic [383:0] memory_request_write_data, memory_response_data;
    logic [47:0] memory_request_byte_enable;
    /* verilator lint_on UNUSEDSIGNAL */
    logic [2:0] memory_response_valid, memory_response_error;
    logic [12:0] address;
    logic [1:0] bank, dqm;
    logic cke, cs_n, ras_n, cas_n, we_n, dq_oe;
    logic [15:0] dq_out, dq_in;
    logic jtag_tck, jtag_tdi, jtag_tdo, jtag_capture, jtag_shift;
    logic jtag_update, jtag_update_ir;
    logic [1:0] jtag_ir;

    n2d_de10_lite_clock u_clock (.reference_clk_i(MAX10_CLK1_50),
        .reset_i(!RESET_N), .clk_25_o(clk_25), .locked_o(locked));
    n2a_reset_sync u_reset (.clk_i(clk_25),
        .async_reset_i(!RESET_N || !locked), .reset_o(reset));
    // The SDRAM initializes first; UART and CPU start together afterward.
    assign system_reset = reset || !init_done;
    assign LEDR = {system_reset, halt_ack, init_done, locked};

    // Host keyboard: the common Virtual JTAG wrapper presents the USB-Blaster
    // hub's scan ports to the keyboard endpoint inside n2d_system
    // (io/HAS.md#jtag-keyboard-composition).
    n2a_jtag_virtual u_jtag_virtual (
        .tck_o(jtag_tck), .tdi_o(jtag_tdi), .tdo_i(jtag_tdo), .ir_o(jtag_ir),
        .capture_o(jtag_capture), .shift_o(jtag_shift), .update_o(jtag_update),
        .update_ir_o(jtag_update_ir));

    // Architectural observation ports are simulation/debug monitors, not pins.
    /* verilator lint_off PINCONNECTEMPTY */
    // Half-width framebuffer banks keep both banks within the MAX 10 M9K
    // budget of one full-width bank (video/HAS.md); the D-cache stays at
    // 16 KiB two-way (memory/HAS.md).
    n2d_system #(.FRAMEBUFFER_FULL_WIDTH(1'b0), .DCACHE_WAYS(2),
                 .DCACHE_PREFETCH_LINES(2)) u_system (
        .scan_tck_i(jtag_tck), .scan_tdi_i(jtag_tdi),
        .scan_capture_i(jtag_capture), .scan_shift_i(jtag_shift),
        .scan_update_i(jtag_update), .scan_update_ir_i(jtag_update_ir),
        .scan_ir_i(jtag_ir), .scan_tdo_o(jtag_tdo),
        .clk_i(clk_25),
        .reset_i(system_reset),
        .uart_rx_i(UART_RXD),
        .ps2_clk_i(PS2_CLK),
        .ps2_data_i(PS2_DAT),
        .uart_tx_o(UART_TXD),
        .halt_ack_o(halt_ack),
        .observation_valid_o(),
        .observation_order_o(),
        .observation_pc_o(),
        .observation_instruction_o(),
        .observation_next_pc_o(),
        .observation_rd_address_o(),
        .observation_rd_write_o(),
        .observation_rd_write_data_o(),
        .observation_memory_valid_o(),
        .observation_memory_write_o(),
        .observation_memory_address_o(),
        .observation_memory_read_data_o(),
        .observation_memory_write_data_o(),
        .observation_memory_read_mask_o(),
        .observation_memory_write_mask_o(),
        .observation_trap_o(),
        .observation_cause_o(),
        .observation_trap_value_o(),
        .framebuffer_write_valid_o(),
        .framebuffer_write_address_o(),
        .framebuffer_write_data_o(),
        .framebuffer_write_strobe_o(),
        .framebuffer_front_bank_o(),
        .framebuffer_swap_pending_o(),
        .instruction_hit_event_o(),
        .instruction_miss_event_o(),
        .data_hit_event_o(),
        .data_miss_event_o(),
        .debug_memory_request_valid_o(),
        .ps2_event_consumed_o(),
        .system_completion_o(),
        .system_tick_o(),
        .pixel_x_o(),
        .pixel_y_o(),
        .visible_o(),
        .vga_red_o(VGA_R),
        .vga_green_o(VGA_G),
        .vga_blue_o(VGA_B),
        .vga_hsync_o(VGA_HS),
        .vga_vsync_o(VGA_VS),
        .frame_start_o(),
        .frame_end_o(),
        .frame_sequence_o(),
        .memory_request_valid_o(memory_request_valid),
        .memory_request_prefetch_o(memory_request_prefetch),
        .memory_request_ready_i(memory_request_ready),
        .memory_request_write_o(memory_request_write),
        .memory_request_address_o(memory_request_address),
        .memory_request_write_data_o(memory_request_write_data),
        .memory_request_byte_enable_o(memory_request_byte_enable),
        .memory_response_valid_i(memory_response_valid),
        .memory_response_data_i(memory_response_data),
        .memory_response_error_i(memory_response_error));
    /* verilator lint_on PINCONNECTEMPTY */

    n2d_memory_controller u_memory (
        .clk_i(clk_25), .reset_i(reset),
        .instruction_request_valid_i(memory_request_valid[0]),
        .data_request_prefetch_i(memory_request_prefetch[1]),
        .instruction_request_ready_o(memory_request_ready[0]),
        .instruction_request_address_i(memory_request_address[0 +: 26]),
        .instruction_response_valid_o(memory_response_valid[0]),
        .instruction_response_data_o(memory_response_data[0 +: 128]),
        .instruction_response_error_o(memory_response_error[0]),
        .data_request_valid_i(memory_request_valid[1]),
        .data_request_ready_o(memory_request_ready[1]),
        .data_request_address_i(memory_request_address[26 +: 26]),
        .data_response_valid_o(memory_response_valid[1]),
        .data_response_data_o(memory_response_data[128 +: 128]),
        .data_response_error_o(memory_response_error[1]),
        .data_request_write_i(memory_request_write[1]),
        .data_request_write_data_i(memory_request_write_data[128 +: 128]),
        .data_request_byte_enable_i(memory_request_byte_enable[16 +: 16]),
        .debug_request_valid_i(memory_request_valid[2]),
        .debug_request_ready_o(memory_request_ready[2]),
        .debug_request_address_i(memory_request_address[52 +: 26]),
        .debug_response_valid_o(memory_response_valid[2]),
        .debug_response_data_o(memory_response_data[256 +: 128]),
        .debug_response_error_o(memory_response_error[2]),
        .debug_request_write_i(memory_request_write[2]),
        .debug_request_write_data_i(memory_request_write_data[256 +: 128]),
        .debug_request_byte_enable_i(memory_request_byte_enable[32 +: 16]),
        .init_done_o(init_done), .sdram_address_o(address), .sdram_bank_o(bank),
        .sdram_cke_o(cke), .sdram_cs_n_o(cs_n), .sdram_ras_n_o(ras_n),
        .sdram_cas_n_o(cas_n), .sdram_we_n_o(we_n), .sdram_dqm_o(dqm),
        .sdram_dq_o(dq_out), .sdram_dq_oe_o(dq_oe), .sdram_dq_i(dq_in));

    // Board pin binding. Falling-edge external clock gives commands/data half
    // a cycle to settle; DQ is the only resolved bidirectional bus.
    assign DRAM_CLK = ~clk_25;
    assign DRAM_ADDR = address;
    assign DRAM_BA = bank;
    assign DRAM_CKE = cke;
    assign DRAM_CS_N = cs_n;
    assign DRAM_RAS_N = ras_n;
    assign DRAM_CAS_N = cas_n;
    assign DRAM_WE_N = we_n;
    assign DRAM_LDQM = dqm[0];
    assign DRAM_UDQM = dqm[1];
    assign DRAM_DQ = dq_oe ? dq_out : 16'hzzzz;
    assign dq_in = DRAM_DQ;
endmodule
