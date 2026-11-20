module n2d_c_runtime_system (
    input logic clk_i, input logic reset_i, input logic uart_rx_i,
    input logic sdram_force_error_i, input logic ps2_clk_i,
    input logic ps2_data_i, output logic uart_tx_o,
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
    logic [2:0] memory_request_prefetch;
    logic [2:0] memory_request_valid, memory_request_ready;
    logic [2:0] memory_request_write;
    logic [77:0] memory_request_address;
    logic [383:0] memory_request_write_data, memory_response_data;
    logic [47:0] memory_request_byte_enable;
    logic [2:0] memory_response_valid, memory_response_error;

    n2d_system u_system (
        .scan_tck_i(1'b0), .scan_tdi_i(1'b0), .scan_capture_i(1'b0),
        .scan_shift_i(1'b0), .scan_update_i(1'b0), .scan_update_ir_i(1'b0),
        /* verilator lint_off PINCONNECTEMPTY */
        .scan_ir_i(2'b00), .scan_tdo_o(),.*,
        /* verilator lint_on PINCONNECTEMPTY */
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

    n2d_memory_fast_subsystem u_memory (
        .clk_i, .reset_i,
        .master_request_valid_i(memory_request_valid),
        .master_request_prefetch_i(memory_request_prefetch),
        .master_request_ready_o(memory_request_ready),
        .master_request_write_i(memory_request_write),
        .master_request_address_i(memory_request_address),
        .master_request_write_data_i(memory_request_write_data),
        .master_request_byte_enable_i(memory_request_byte_enable),
        .force_error_i(sdram_force_error_i),
        .master_response_valid_o(memory_response_valid),
        .master_response_data_o(memory_response_data),
        .master_response_error_o(memory_response_error));
endmodule
