module n2d_cached_rv32im #(
    parameter int unsigned DCACHE_WAYS = 4
) (
    input  logic         clk_i,
    input  logic         reset_i,
    input  logic         enable_i,

    output logic         instruction_line_request_valid_o,
    input  logic         instruction_line_request_ready_i,
    output logic         instruction_line_request_write_o,
    output logic [25:0]  instruction_line_request_address_o,
    output logic [127:0] instruction_line_request_write_data_o,
    output logic [15:0]  instruction_line_request_byte_enable_o,
    input  logic         instruction_line_response_valid_i,
    input  logic [127:0] instruction_line_response_data_i,
    input  logic         instruction_line_response_error_i,

    output logic         data_line_request_valid_o,
    input  logic         data_line_request_ready_i,
    output logic         data_line_request_write_o,
    output logic [25:0]  data_line_request_address_o,
    output logic [127:0] data_line_request_write_data_o,
    output logic [15:0]  data_line_request_byte_enable_o,
    input  logic         data_line_response_valid_i,
    input  logic [127:0] data_line_response_data_i,
    input  logic         data_line_response_error_i,

    input  logic         instruction_maintenance_valid_i,
    output logic         instruction_maintenance_ready_o,
    output logic         instruction_maintenance_done_o,
    input  logic         data_maintenance_valid_i,
    output logic         data_maintenance_ready_o,
    output logic         data_maintenance_done_o,
    output logic         data_maintenance_error_o,

    output logic         instruction_hit_event_o,
    output logic         instruction_miss_event_o,
    output logic         data_hit_event_o,
    output logic         data_miss_event_o,

    output logic         observation_valid_o,
    output logic [63:0]  observation_order_o,
    output logic [31:0]  observation_pc_o,
    output logic [31:0]  observation_instruction_o,
    output logic [31:0]  observation_next_pc_o,
    output logic [4:0]   observation_rd_address_o,
    output logic         observation_rd_write_o,
    output logic [31:0]  observation_rd_write_data_o,
    output logic         observation_memory_valid_o,
    output logic         observation_memory_write_o,
    output logic [31:0]  observation_memory_address_o,
    output logic [31:0]  observation_memory_read_data_o,
    output logic [31:0]  observation_memory_write_data_o,
    output logic [3:0]   observation_memory_read_mask_o,
    output logic [3:0]   observation_memory_write_mask_o,
    output logic         observation_trap_o,
    output logic [31:0]  observation_cause_o,
    output logic [31:0]  observation_trap_value_o,
    output logic         halt_ack_o
);
    logic instruction_request_valid;
    logic instruction_request_ready;
    logic [31:0] instruction_request_address;
    logic instruction_response_valid;
    logic [31:0] instruction_response_data;
    logic instruction_response_error;
    logic data_request_valid;
    logic data_request_ready;
    logic data_request_write;
    logic [31:0] data_request_address;
    logic [31:0] data_request_write_data;
    logic [3:0] data_request_byte_enable;
    logic data_response_valid;
    logic [31:0] data_response_data;
    logic data_response_error;
    // Why: pmon_events is unused here because this composition supplies no
    // platform events and exposes no PMON port.
    /* verilator lint_off UNUSEDSIGNAL */
    logic [127:0] pmon_events;
    // Why: this composition keeps the plain D-cache (PREFETCH_LINES 0), so its
    // prefetch marker and events stay low and are not exposed.
    logic data_line_request_prefetch, data_line_request_background;
    logic [4:0] data_prefetch_events;
    logic [1:0] data_write_buffer_events;
    /* verilator lint_on UNUSEDSIGNAL */

    n2d_rv32im_core u_core (
        .clk_i(clk_i), .reset_i(reset_i), .enable_i(enable_i),
        .halt_request_i(1'b0), .reset_pc_request_i(1'b0), .resume_i(1'b0),
        .instruction_request_valid_o(instruction_request_valid),
        .instruction_request_ready_i(instruction_request_ready),
        .instruction_request_address_o(instruction_request_address),
        .instruction_response_valid_i(instruction_response_valid),
        .instruction_response_data_i(instruction_response_data),
        .instruction_response_error_i(instruction_response_error),
        .data_request_valid_o(data_request_valid),
        .data_request_ready_i(data_request_ready),
        .data_request_write_o(data_request_write),
        .data_request_address_o(data_request_address),
        .data_request_write_data_o(data_request_write_data),
        .data_request_byte_enable_o(data_request_byte_enable),
        .data_response_valid_i(data_response_valid),
        .data_response_read_data_i(data_response_data),
        .data_response_error_i(data_response_error),
        .observation_valid_o(observation_valid_o),
        .observation_order_o(observation_order_o),
        .observation_pc_o(observation_pc_o),
        .observation_instruction_o(observation_instruction_o),
        .observation_next_pc_o(observation_next_pc_o),
        .observation_rd_address_o(observation_rd_address_o),
        .observation_rd_write_o(observation_rd_write_o),
        .observation_rd_write_data_o(observation_rd_write_data_o),
        .observation_memory_valid_o(observation_memory_valid_o),
        .observation_memory_write_o(observation_memory_write_o),
        .observation_memory_address_o(observation_memory_address_o),
        .observation_memory_read_data_o(observation_memory_read_data_o),
        .observation_memory_write_data_o(observation_memory_write_data_o),
        .observation_memory_read_mask_o(observation_memory_read_mask_o),
        .observation_memory_write_mask_o(observation_memory_write_mask_o),
        .observation_trap_o(observation_trap_o),
        .observation_cause_o(observation_cause_o),
        .observation_trap_value_o(observation_trap_value_o),
        .halt_ack_o(halt_ack_o),
        .pmon_platform_events_i(128'b0),
        .pmon_events_o(pmon_events)
    );

    n2d_icache u_icache (
        .clk_i(clk_i), .reset_i(reset_i),
        .cpu_request_valid_i(instruction_request_valid),
        .cpu_request_ready_o(instruction_request_ready),
        .cpu_request_address_i(instruction_request_address),
        .cpu_response_valid_o(instruction_response_valid),
        .cpu_response_data_o(instruction_response_data),
        .cpu_response_error_o(instruction_response_error),
        .line_request_valid_o(instruction_line_request_valid_o),
        .line_request_ready_i(instruction_line_request_ready_i),
        .line_request_write_o(instruction_line_request_write_o),
        .line_request_address_o(instruction_line_request_address_o),
        .line_request_write_data_o(instruction_line_request_write_data_o),
        .line_request_byte_enable_o(instruction_line_request_byte_enable_o),
        .line_response_valid_i(instruction_line_response_valid_i),
        .line_response_data_i(instruction_line_response_data_i),
        .line_response_error_i(instruction_line_response_error_i),
        .maintenance_valid_i(instruction_maintenance_valid_i),
        .maintenance_ready_o(instruction_maintenance_ready_o),
        .maintenance_done_o(instruction_maintenance_done_o),
        .hit_event_o(instruction_hit_event_o),
        .miss_event_o(instruction_miss_event_o)
    );

    n2d_dcache #(.WAYS(DCACHE_WAYS)) u_dcache (
        .clk_i(clk_i), .reset_i(reset_i),
        .cpu_request_valid_i(data_request_valid),
        .cpu_request_ready_o(data_request_ready),
        .cpu_request_write_i(data_request_write),
        .cpu_request_address_i(data_request_address),
        .cpu_request_write_data_i(data_request_write_data),
        .cpu_request_byte_enable_i(data_request_byte_enable),
        .cpu_response_valid_o(data_response_valid),
        .cpu_response_data_o(data_response_data),
        .cpu_response_error_o(data_response_error),
        .line_request_valid_o(data_line_request_valid_o),
        .line_request_ready_i(data_line_request_ready_i),
        .line_request_write_o(data_line_request_write_o),
        .line_request_address_o(data_line_request_address_o),
        .line_request_write_data_o(data_line_request_write_data_o),
        .line_request_byte_enable_o(data_line_request_byte_enable_o),
        .line_request_prefetch_o(data_line_request_prefetch),
        .line_request_background_o(data_line_request_background),
        .line_response_valid_i(data_line_response_valid_i),
        .line_response_data_i(data_line_response_data_i),
        .line_response_error_i(data_line_response_error_i),
        .maintenance_valid_i(data_maintenance_valid_i),
        .maintenance_ready_o(data_maintenance_ready_o),
        .maintenance_done_o(data_maintenance_done_o),
        .maintenance_error_o(data_maintenance_error_o),
        .hit_event_o(data_hit_event_o),
        .miss_event_o(data_miss_event_o),
        .prefetch_issue_event_o(data_prefetch_events[0]),
        .prefetch_useful_event_o(data_prefetch_events[1]),
        .prefetch_drop_event_o(data_prefetch_events[2]),
        .prefetch_deferred_event_o(data_prefetch_events[3]),
        .prefetch_cancel_event_o(data_prefetch_events[4]),
        .write_buffer_pending_o(data_write_buffer_events[0]),
        .write_buffer_wait_o(data_write_buffer_events[1])
    );
endmodule
