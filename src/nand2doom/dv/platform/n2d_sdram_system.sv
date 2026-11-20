`ifndef SYNTHESIS
module n2d_sdram_system #(
    parameter bit FRAMEBUFFER_FULL_WIDTH = 1'b1,
    // Each board's D-cache follows its framebuffer geometry: four-way with
    // DE2-115 full width, two-way with DE10-Lite half width.
    parameter int unsigned DCACHE_WAYS = FRAMEBUFFER_FULL_WIDTH ? 4 : 2
) (
    input logic clk_i, input logic reset_i, input logic uart_rx_i,
    input logic sdram_force_error_i, input logic ps2_clk_i,
    input logic ps2_data_i, input logic preload_mode_i,
    input logic preload_valid_i, output logic preload_ready_o,
    input logic [25:0] preload_address_i,
    input logic [2047:0] preload_data_i,
    input logic [255:0] preload_byte_enable_i,
    output logic preload_response_valid_o, preload_response_error_o,
    output logic uart_tx_o,
    output logic halt_ack_o, output logic observation_valid_o,
    output logic [63:0] observation_order_o,
    output logic [31:0] observation_pc_o, observation_instruction_o,
    output logic [31:0] observation_next_pc_o,
    output logic [4:0] observation_rd_address_o,
    output logic observation_rd_write_o,
    output logic [31:0] observation_rd_write_data_o,
    output logic observation_memory_valid_o, observation_memory_write_o,
    output logic [31:0] observation_memory_address_o,
    output logic [31:0] observation_memory_read_data_o,
    output logic [31:0] observation_memory_write_data_o,
    output logic [3:0] observation_memory_read_mask_o,
    output logic [3:0] observation_memory_write_mask_o,
    output logic observation_trap_o, output logic [31:0] observation_cause_o,
    output logic [31:0] observation_trap_value_o,
    output logic framebuffer_write_valid_o,
    output logic [15:0] framebuffer_write_address_o,
    output logic [31:0] framebuffer_write_data_o,
    output logic [3:0] framebuffer_write_strobe_o,
    output logic framebuffer_front_bank_o,
    output logic framebuffer_swap_pending_o,
    output logic instruction_hit_event_o, instruction_miss_event_o,
    output logic data_hit_event_o, data_miss_event_o,
    output logic debug_memory_request_valid_o, ps2_event_consumed_o,
    output logic [31:0] system_completion_o, system_tick_o,
    output logic [9:0] pixel_x_o, pixel_y_o, output logic visible_o,
    output logic [3:0] vga_red_o, vga_green_o, vga_blue_o,
    output logic vga_hsync_o, vga_vsync_o, frame_start_o, frame_end_o,
    output logic [31:0] frame_sequence_o
);
    // Same public boundary as n2d_integrated_system, but line traffic goes
    // through the product n2d_memory_controller (arbiter plus SDRAM
    // controller) into a cycle-level SDRAM device. As on the board, the
    // system stays in reset until SDRAM initialization completes.
    // Instruction line traffic is read-only; its write payload is unused.
    /* verilator lint_off UNUSEDSIGNAL */
    logic [2:0] memory_request_prefetch;
    logic [2:0] memory_request_valid, memory_request_ready;
    logic [2:0] memory_request_write;
    logic [77:0] memory_request_address;
    logic [383:0] memory_request_write_data, memory_response_data;
    logic [47:0] memory_request_byte_enable;
    /* verilator lint_on UNUSEDSIGNAL */
    logic [2:0] memory_response_valid, memory_response_error;
    logic init_done, system_reset;
    logic [12:0] sdram_address;
    logic [1:0] sdram_bank, sdram_dqm;
    logic sdram_cke, sdram_cs_n, sdram_ras_n, sdram_cas_n, sdram_we_n;
    logic sdram_dq_oe;
    logic [15:0] sdram_dq_out, sdram_dq_in;

    assign system_reset = reset_i || !init_done;
    assign preload_ready_o = preload_mode_i;
    assign preload_response_valid_o = preload_mode_i && preload_valid_i;
    assign preload_response_error_o = 1'b0;

    n2d_system #(.FRAMEBUFFER_FULL_WIDTH(FRAMEBUFFER_FULL_WIDTH),
                 .DCACHE_WAYS(DCACHE_WAYS)) u_system (
        .scan_tck_i(1'b0), .scan_tdi_i(1'b0), .scan_capture_i(1'b0),
        .scan_shift_i(1'b0), .scan_update_i(1'b0), .scan_update_ir_i(1'b0),
        /* verilator lint_off PINCONNECTEMPTY */
        .scan_ir_i(2'b00), .scan_tdo_o(),.*,
        /* verilator lint_on PINCONNECTEMPTY */
        .reset_i(system_reset),
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

    n2d_memory_controller u_memory (
        .clk_i, .reset_i,
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
        .init_done_o(init_done), .sdram_address_o(sdram_address),
        .sdram_bank_o(sdram_bank), .sdram_cke_o(sdram_cke),
        .sdram_cs_n_o(sdram_cs_n), .sdram_ras_n_o(sdram_ras_n),
        .sdram_cas_n_o(sdram_cas_n), .sdram_we_n_o(sdram_we_n),
        .sdram_dqm_o(sdram_dqm), .sdram_dq_o(sdram_dq_out),
        .sdram_dq_oe_o(sdram_dq_oe), .sdram_dq_i(sdram_dq_in));

    n2d_sdram_device_model u_device (
        .clk_i, .address_i(sdram_address), .bank_i(sdram_bank),
        .ras_n_i(sdram_ras_n), .cas_n_i(sdram_cas_n), .we_n_i(sdram_we_n),
        .dqm_i(sdram_dqm), .dq_i(sdram_dq_out), .dq_oe_i(sdram_dq_oe),
        .dq_o(sdram_dq_in),
        .preload_valid_i(preload_mode_i && preload_valid_i),
        .preload_address_i, .preload_data_i, .preload_byte_enable_i);

    // CKE/CS are tied active by the controller; fault injection is a
    // fast-model feature with no meaning on the real controller path.
    /* verilator lint_off UNUSEDSIGNAL */
    logic unused_inputs;
    assign unused_inputs = sdram_cke ^ sdram_cs_n ^ sdram_force_error_i;
    /* verilator lint_on UNUSEDSIGNAL */

    always_ff @(posedge clk_i)
        if (preload_mode_i)
            assert (reset_i) else $fatal(1, "nand2doom preload requires asserted reset");
endmodule
`endif
