module n2d_halt_sdram #(
    // The product default (DE2-115) D-cache; the test reads it for its
    // dirty-line count and Halt budget.
    parameter int unsigned DCACHE_WAYS = 4
) (
    input logic stall_i,
    input logic reject_write_i,
    input logic [15:0] sdram_dq_i,
    output logic [15:0] sdram_dq_o,
    output logic sdram_dq_oe_o, init_done_o,
    output logic [12:0] sdram_address_o,
    output logic [1:0] sdram_bank_o, sdram_dqm_o,
    output logic sdram_cke_o, sdram_cs_n_o,
    output logic sdram_ras_n_o, sdram_cas_n_o, sdram_we_n_o,
    output logic line_valid_o, line_ready_o, line_write_o, line_response_o, line_error_o,
    output logic [25:0] line_address_o,
    input  logic        clk_i,
    input  logic        reset_i,
    input  logic        uart_rx_i,
    input  logic        ps2_clk_i,
    input  logic        ps2_data_i,
    output logic        uart_tx_o,

    output logic        halt_ack_o,
    output logic        observation_valid_o,
    output logic [63:0] observation_order_o,
    output logic [31:0] observation_pc_o,
    output logic [31:0] observation_instruction_o,
    output logic [31:0] observation_next_pc_o,
    output logic [4:0]  observation_rd_address_o,
    output logic        observation_rd_write_o,
    output logic [31:0] observation_rd_write_data_o,
    output logic        observation_memory_valid_o,
    output logic        observation_memory_write_o,
    output logic [31:0] observation_memory_address_o,
    output logic [31:0] observation_memory_read_data_o,
    output logic [31:0] observation_memory_write_data_o,
    output logic [3:0]  observation_memory_read_mask_o,
    output logic [3:0]  observation_memory_write_mask_o,
    output logic        observation_trap_o,
    output logic [31:0] observation_cause_o,
    output logic [31:0] observation_trap_value_o,

    output logic        framebuffer_write_valid_o,
    output logic [15:0] framebuffer_write_address_o,
    output logic [31:0] framebuffer_write_data_o,
    output logic [3:0]  framebuffer_write_strobe_o,
    output logic framebuffer_front_bank_o,
    output logic framebuffer_swap_pending_o,
    output logic        instruction_hit_event_o,
    output logic        instruction_miss_event_o,
    output logic        data_hit_event_o,
    output logic        data_miss_event_o,
    output logic        debug_memory_request_valid_o,
    output logic        ps2_event_consumed_o,
    output logic [31:0] system_completion_o,
    output logic [31:0] system_tick_o,
    output logic [9:0]  pixel_x_o,
    output logic [9:0]  pixel_y_o,
    output logic        visible_o,
    output logic [3:0]  vga_red_o,
    output logic [3:0]  vga_green_o,
    output logic [3:0]  vga_blue_o,
    output logic        vga_hsync_o,
    output logic        vga_vsync_o,
    output logic        frame_start_o,
    output logic        frame_end_o,
    output logic [31:0] frame_sequence_o,

    output logic [2:0]   memory_request_valid_o,
    output logic [2:0]   memory_request_prefetch_o,
    output logic [2:0]   memory_request_ready_i,
    output logic [2:0]   memory_request_write_o,
    output logic [77:0]  memory_request_address_o,
    output logic [383:0] memory_request_write_data_o,
    output logic [47:0]  memory_request_byte_enable_o,
    output logic [2:0]   memory_response_valid_i,
    output logic [383:0] memory_response_data_i,
    output logic [2:0]   memory_response_error_i
);
    logic [127:0] line_write_data, line_read_data;
    logic [15:0] line_enables;
    logic controller_ready, controller_response, controller_error, reject_q;
    logic reject;
    assign reject = reject_write_i && line_write_o;
    // Public system observation ports and explicit memory interfaces only.
    n2d_system #(.DCACHE_WAYS(DCACHE_WAYS)) u_system (
        .scan_tck_i(1'b0), .scan_tdi_i(1'b0), .scan_capture_i(1'b0),
        .scan_shift_i(1'b0), .scan_update_i(1'b0), .scan_update_ir_i(1'b0),
        /* verilator lint_off PINCONNECTEMPTY */
        .scan_ir_i(2'b00), .scan_tdo_o(),.*,
        /* verilator lint_on PINCONNECTEMPTY */
        .reset_i(reset_i || !init_done_o));
    n2d_memory_arbiter u_arbiter (
        .clk_i, .reset_i,
        .master_request_valid_i(memory_request_valid_o),
        .master_request_prefetch_i(memory_request_prefetch_o),
        .master_request_ready_o(memory_request_ready_i),
        .master_request_write_i(memory_request_write_o),
        .master_request_address_i(memory_request_address_o),
        .master_request_write_data_i(memory_request_write_data_o),
        .master_request_byte_enable_i(memory_request_byte_enable_o),
        .master_response_valid_o(memory_response_valid_i),
        .master_response_data_o(memory_response_data_i),
        .master_response_error_o(memory_response_error_i),
        .memory_request_valid_o(line_valid_o),
        .memory_request_ready_i(line_ready_o),
        .memory_request_write_o(line_write_o),
        .memory_request_address_o(line_address_o),
        .memory_request_write_data_o(line_write_data),
        .memory_request_byte_enable_o(line_enables),
        .memory_response_valid_i(line_response_o),
        .memory_response_data_i(line_read_data),
        .memory_response_error_i(line_error_o));
    assign line_ready_o = controller_ready && !stall_i;
    // DV-only error before controller acceptance: no failed-write side effect.
    always_ff @(posedge clk_i) begin
        if (reset_i) reject_q <= 1'b0;
        else reject_q <= line_valid_o && line_ready_o && reject;
    end
    assign line_response_o = controller_response || reject_q;
    assign line_error_o = controller_error || reject_q;
    n2d_sdram_controller u_controller (
        .clk_i, .reset_i,
        .request_valid_i(line_valid_o && !stall_i && !reject),
        .request_ready_o(controller_ready),
        .request_write_i(line_write_o),
        .request_address_i(line_address_o),
        .request_write_data_i(line_write_data),
        .request_byte_enable_i(line_enables),
        .response_valid_o(controller_response), .response_data_o(line_read_data),
        .response_error_o(controller_error), .init_done_o,
        .sdram_address_o, .sdram_bank_o, .sdram_cke_o, .sdram_cs_n_o,
        .sdram_ras_n_o, .sdram_cas_n_o, .sdram_we_n_o, .sdram_dqm_o,
        .sdram_dq_o, .sdram_dq_oe_o, .sdram_dq_i);
endmodule
