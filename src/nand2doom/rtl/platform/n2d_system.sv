`include "src/common/rtl/pkg/macros.vh"

/* The UART/PS2 PHYs require asynchronous assertion while product datapaths use
 * synchronous reset. The platform deliberately shares the externally
 * asserted level and synchronizes board release outside this boundary. */
/* verilator lint_off SYNCASYNCNET */
module n2d_system
    import n2d_pkg::*;
#(
    // Debug/load UART rate; a board top selects N2D_UART_LOAD_BAUD for fast
    // streaming loads (io/HAS.md "Link rate").
    parameter int unsigned UART_BAUD = N2D_UART_BAUD,
    // Framebuffer bank geometry: 320x200 banks, or 160x200 half-width banks
    // (video/HAS.md). Board tops select it.
    parameter bit FRAMEBUFFER_FULL_WIDTH = 1'b1,
    // D-cache ways over 512 sets of 16-byte lines: 4 (32 KiB) on the DE2-115,
    // 2 (16 KiB) on the DE10-Lite (memory/HAS.md). Board tops select it.
    parameter int unsigned DCACHE_WAYS = 4,
    // D-cache next-line prefetch buffer lines (memory/HAS.md); 0 disables
    // the prefetch. Board tops select it.
    parameter int unsigned DCACHE_PREFETCH_LINES = 2
) (
    input  logic        clk_i,
    input  logic        reset_i,
    input  logic        uart_rx_i,
    input  logic        ps2_clk_i,
    input  logic        ps2_data_i,
    output logic        uart_tx_o,
    input  logic        scan_tck_i, scan_tdi_i, scan_capture_i, scan_shift_i,
    input  logic        scan_update_i, scan_update_ir_i,
    input  logic [1:0]  scan_ir_i,
    output logic        scan_tdo_o,

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
    output logic        framebuffer_front_bank_o,
    output logic        framebuffer_swap_pending_o,
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
    // Per-master low-priority prefetch marker (only the D-cache sets it).
    output logic [2:0]   memory_request_prefetch_o,
    input  logic [2:0]   memory_request_ready_i,
    output logic [2:0]   memory_request_write_o,
    output logic [77:0]  memory_request_address_o,
    output logic [383:0] memory_request_write_data_o,
    output logic [47:0]  memory_request_byte_enable_o,
    input  logic [2:0]   memory_response_valid_i,
    input  logic [383:0] memory_response_data_i,
    input  logic [2:0]   memory_response_error_i
);
    import n2d_types_pkg::*;

    logic halt_request;
    /* verilator lint_off UNUSEDSIGNAL */
    logic icache_maintenance_valid, dcache_maintenance_valid;
    logic cache_maintenance_done;
    localparam system_maint_t MAINT_RESET = '{halt_request_prev: 1'b1, required: 1'b0,
                                              icache_complete: 1'b0, dcache_complete: 1'b0};
    system_maint_t maint_q, maint_d;
    logic reset_pc_valid;
    n2d_debug_control_t    debug_control;
    n2d_debug_status_t     debug_status;
    n2d_input_event_t      uart_input;
    n2d_debug_txn_req_t    debug_txn_req;
    n2d_debug_txn_status_t debug_txn_status;
    n2d_debug_mem_req_t    debug_word_request;
    n2d_debug_mem_rsp_t    debug_word_response;
    /* verilator lint_on UNUSEDSIGNAL */
    logic debug_line_valid, debug_line_ready, debug_line_write;
    logic [25:0] debug_line_address;
    logic [127:0] debug_line_write_data, debug_line_response_data;
    logic [15:0] debug_line_byte_enable;
    logic debug_line_response_valid, debug_line_response_error;
    logic debug_transaction_begin, debug_transaction_commit;
    logic debug_transaction_abort, debug_transaction_done;
    logic debug_transaction_error;
    logic debug_word_valid, debug_word_ready, debug_word_write;
    logic [31:0] debug_word_address, debug_word_write_data;
    logic [3:0] debug_word_byte_enable;
    logic debug_word_response_valid, debug_word_response_error;
    logic [31:0] debug_word_response_data;
    system_debug_owner_t debug_owner_q, debug_owner_d;
    logic debug_select_sdram, debug_select_device;
    logic debug_sdram_begin, debug_device_begin;
    logic debug_sdram_commit, debug_device_commit;
    logic debug_sdram_abort, debug_device_abort;
    logic debug_sdram_done, debug_sdram_error;
    logic debug_device_done, debug_device_error;
    logic debug_sdram_word_valid, debug_sdram_word_ready;
    logic debug_sdram_word_response_valid, debug_sdram_word_response_error;
    logic [31:0] debug_sdram_word_response_data;
    logic debug_device_word_valid, debug_device_word_ready;
    logic debug_device_word_response_valid, debug_device_word_response_error;
    logic [31:0] debug_device_word_response_data;
    logic [31:0] debug_device_word_address;
    logic device_line_valid, device_line_ready, device_line_write;
    logic [25:0] device_line_address;
    logic [127:0] device_line_write_data, device_line_response_data;
    logic [15:0] device_line_byte_enable;
    logic device_line_response_valid, device_line_response_error;

    logic instruction_request_valid, instruction_request_ready;
    logic [31:0] instruction_request_address;
    logic instruction_response_valid, instruction_response_error;
    logic [31:0] instruction_response_data;
    logic data_request_valid, data_request_ready, data_request_write;
    logic [31:0] data_request_address, data_request_write_data;
    logic [3:0] data_request_byte_enable;
    logic data_response_valid, data_response_error;
    logic [31:0] data_response_data;

    logic icache_line_valid, icache_line_ready, icache_line_write;
    logic [25:0] icache_line_address;
    logic [127:0] icache_line_write_data, icache_line_response_data;
    logic [15:0] icache_line_byte_enable;
    logic icache_line_response_valid, icache_line_response_error;
    /* verilator lint_off UNUSEDSIGNAL */
    logic icache_maintenance_ready;
    /* verilator lint_on UNUSEDSIGNAL */
    logic icache_maintenance_done;

    logic dcache_cpu_valid, dcache_cpu_ready;
    logic dcache_response_valid, dcache_response_error;
    logic [31:0] dcache_response_data;
    logic dcache_line_valid, dcache_line_ready, dcache_line_write;
    logic [25:0] dcache_line_address;
    logic [127:0] dcache_line_write_data, dcache_line_response_data;
    logic [15:0] dcache_line_byte_enable;
    logic dcache_line_response_valid, dcache_line_response_error;
    logic dcache_line_prefetch, dcache_line_background;
    logic dcache_prefetch_issue_event, dcache_prefetch_useful_event;
    logic dcache_prefetch_drop_event;
    logic dcache_prefetch_deferred_event, dcache_prefetch_cancel_event;
    logic dcache_write_buffer_pending, dcache_write_buffer_wait;
    /* verilator lint_off UNUSEDSIGNAL */
    logic dcache_maintenance_ready;
    /* verilator lint_on UNUSEDSIGNAL */
    logic dcache_maintenance_done;
    /* verilator lint_off UNUSEDSIGNAL */
    logic dcache_maintenance_error;
    /* verilator lint_on UNUSEDSIGNAL */

    logic framebuffer_request_valid, framebuffer_request_ready;
    logic framebuffer_response_valid;
    logic [31:0] framebuffer_response_data;
    logic palette_request_valid, palette_request_ready;
    logic palette_response_valid;
    logic [31:0] palette_response_data;
    logic palette_commit;
    logic palette_commit_pending, active_palette_bank;
    logic ps2_request_valid, ps2_request_ready;
    logic ps2_response_valid, ps2_response_error;
    logic [31:0] ps2_response_data;
    logic uart_input_valid, uart_input_ready;
    logic [9:0] uart_input_event;
    logic jtag_tck, jtag_tdi, jtag_tdo, jtag_capture, jtag_shift;
    logic jtag_update, jtag_update_ir;
    logic [1:0] jtag_ir;
    logic scan_inject_valid, scan_inject_ready;
    logic [7:0] scan_inject_byte;
    logic system_request_valid;
    localparam system_regs_t SYS_RESET = '{response_valid: 1'b0, response_data: '0,
                                           response_error: 1'b0, completion: '0, tick: '0};
    system_regs_t sys_q, sys_d;
    logic debug_framebuffer_request_valid, debug_framebuffer_request_ready;
    logic debug_framebuffer_request_write;
    logic [15:0] debug_framebuffer_request_address;
    logic [31:0] debug_framebuffer_request_write_data;
    logic [3:0] debug_framebuffer_request_write_strobe;
    logic debug_palette_request_valid, debug_palette_request_ready;
    logic debug_palette_request_write;
    logic [7:0] debug_palette_request_index;
    logic [31:0] debug_palette_request_write_data;
    logic [3:0] debug_palette_request_write_strobe;
    logic cpu_framebuffer_request_valid, cpu_palette_request_valid;
    // Screen reads of the UART memory endpoint (io/HAS.md "UART memory
    // endpoint"): the front bank and the scanned palette, while the CPU runs.
    n2d_screen_req_t screen_request;
    n2d_screen_rsp_t screen_response;
    logic screen_framebuffer_valid, screen_palette_valid;
    logic screen_framebuffer_pending_q, screen_framebuffer_pending_d;
    logic screen_palette_pending_q, screen_palette_pending_d;
    logic framebuffer_request_write_mux, palette_request_write_mux;
    logic [15:0] framebuffer_request_address_mux;
    logic [31:0] framebuffer_request_write_data_mux;
    logic [3:0] framebuffer_request_write_strobe_mux;
    logic [7:0] palette_request_index_mux;
    logic [31:0] palette_request_write_data_mux;
    logic [3:0] palette_request_write_strobe_mux;
    logic framebuffer_address_selected, palette_address_selected;
    logic framebuffer_back_address_selected, framebuffer_front_address_selected;
    logic framebuffer_swap_request;
    logic system_address_selected, video_control_address_selected;
    logic ps2_address_selected, sdram_address_selected;
    logic invalid_response_q, invalid_response_d;
    system_data_owner_t data_owner_q, data_owner_d;

    assign sdram_address_selected = data_request_address < 32'h0400_0000;
    assign framebuffer_back_address_selected =
        (data_request_address >= 32'h1000_0000) &&
        (data_request_address < 32'h1001_0000);
    // The front-bank view is read-only; a write there is unmapped.
    assign framebuffer_front_address_selected =
        (data_request_address >= 32'h1002_0000) &&
        (data_request_address < 32'h1003_0000) && !data_request_write;
    assign framebuffer_address_selected = framebuffer_back_address_selected ||
        framebuffer_front_address_selected;
    assign palette_address_selected =
        (data_request_address >= 32'h1001_0000) &&
        (data_request_address < 32'h1001_1000);
    assign system_address_selected =
        (data_request_address >= 32'h2000_0000) &&
        (data_request_address < 32'h2000_1000);
    assign video_control_address_selected =
        (data_request_address >= 32'h2000_1000) &&
        (data_request_address < 32'h2000_2000);
    assign ps2_address_selected =
        (data_request_address >= 32'h2000_2000) &&
        (data_request_address < 32'h2000_3000);

    assign dcache_cpu_valid = data_request_valid && sdram_address_selected;
    assign cpu_framebuffer_request_valid =
        data_request_valid && framebuffer_address_selected;
    assign cpu_palette_request_valid = data_request_valid && palette_address_selected;
    // Priority: halted debug bridge, then endpoint screen read, then CPU. A
    // screen read takes one port cycle; the endpoint paces them while the
    // CPU runs, so the CPU loses at most that cycle per pacing gap.
    assign screen_framebuffer_valid = screen_request.valid && !screen_request.palette &&
        !debug_framebuffer_request_valid;
    assign screen_palette_valid = screen_request.valid && screen_request.palette &&
        !debug_palette_request_valid;
    assign framebuffer_request_valid = cpu_framebuffer_request_valid ||
        debug_framebuffer_request_valid || screen_framebuffer_valid;
    assign palette_request_valid = cpu_palette_request_valid ||
        debug_palette_request_valid || screen_palette_valid;
    assign framebuffer_request_write_mux = debug_framebuffer_request_valid ?
        debug_framebuffer_request_write : (!screen_framebuffer_valid && data_request_write);
    assign framebuffer_request_address_mux = debug_framebuffer_request_valid ?
        debug_framebuffer_request_address : (screen_framebuffer_valid ?
        {screen_request.offset[15:2], 2'b00} : {data_request_address[15:2], 2'b00});
    assign framebuffer_request_write_data_mux = debug_framebuffer_request_valid ?
        debug_framebuffer_request_write_data : data_request_write_data;
    assign framebuffer_request_write_strobe_mux = debug_framebuffer_request_valid ?
        debug_framebuffer_request_write_strobe : data_request_byte_enable;
    assign palette_request_write_mux = debug_palette_request_valid ?
        debug_palette_request_write : (!screen_palette_valid && data_request_write);
    assign palette_request_index_mux = debug_palette_request_valid ?
        debug_palette_request_index : (screen_palette_valid ?
        screen_request.offset[9:2] : data_request_address[9:2]);
    assign palette_request_write_data_mux = debug_palette_request_valid ?
        debug_palette_request_write_data : data_request_write_data;
    assign palette_request_write_strobe_mux = debug_palette_request_valid ?
        debug_palette_request_write_strobe : data_request_byte_enable;
    assign debug_framebuffer_request_ready = framebuffer_request_ready;
    assign debug_palette_request_ready = palette_request_ready;
    // Both ports answer on the next clock.
    assign screen_framebuffer_pending_d = screen_framebuffer_valid && framebuffer_request_ready;
    assign screen_palette_pending_d = screen_palette_valid && palette_request_ready;
    `N2A_DFF_RST(screen_framebuffer_pending_q, screen_framebuffer_pending_d, clk_i, reset_i)
    `N2A_DFF_RST(screen_palette_pending_q, screen_palette_pending_d, clk_i, reset_i)
    assign screen_response = '{
        ready: screen_request.palette ? (palette_request_ready && !debug_palette_request_valid) :
            (framebuffer_request_ready && !debug_framebuffer_request_valid),
        rsp_valid: (screen_framebuffer_pending_q && framebuffer_response_valid) ||
            (screen_palette_pending_q && palette_response_valid),
        rdata: screen_palette_pending_q ? palette_response_data : framebuffer_response_data};
    assign ps2_request_valid = data_request_valid && ps2_address_selected;
    assign system_request_valid = data_request_valid &&
        (system_address_selected || video_control_address_selected);
    always_comb begin
        if (sdram_address_selected) begin
            data_request_ready = dcache_cpu_ready;
        end else if (framebuffer_address_selected) begin
            data_request_ready = framebuffer_request_ready &&
                !debug_framebuffer_request_valid && !screen_framebuffer_valid;
        end else if (palette_address_selected) begin
            data_request_ready = palette_request_ready &&
                !debug_palette_request_valid && !screen_palette_valid;
        end else if (ps2_address_selected) begin
            data_request_ready = ps2_request_ready;
        end else if (system_address_selected || video_control_address_selected) begin
            data_request_ready = !sys_q.response_valid;
        end else begin
            data_request_ready = !invalid_response_q;
        end
    end

    always_comb begin
        data_response_valid = 1'b0;
        data_response_data = '0;
        data_response_error = 1'b0;
        case (data_owner_q)
            DATA_SDRAM: begin
                data_response_valid = dcache_response_valid;
                data_response_data = dcache_response_data;
                data_response_error = dcache_response_error;
            end
            DATA_FRAMEBUFFER: begin
                data_response_valid = framebuffer_response_valid;
                data_response_data = framebuffer_response_data;
            end
            DATA_PALETTE: begin
                data_response_valid = palette_response_valid;
                data_response_data = palette_response_data;
            end
            DATA_SYSTEM: begin
                data_response_valid = sys_q.response_valid;
                data_response_data = sys_q.response_data;
                data_response_error = sys_q.response_error;
            end
            DATA_PS2: begin
                data_response_valid = ps2_response_valid;
                data_response_data = ps2_response_data;
                data_response_error = ps2_response_error;
            end
            DATA_ERROR: begin
                data_response_valid = invalid_response_q;
                data_response_error = invalid_response_q;
            end
            default: begin end
        endcase
    end

    // One outstanding data transaction owner; an unmapped address returns a
    // one-cycle error response. A request accepted on the edge that samples
    // the current owner's response takes ownership, so the response clear
    // comes first and a same-cycle acceptance overrides it.
    always_comb begin
        data_owner_d = data_owner_q;
        invalid_response_d = 1'b0;
        if (data_response_valid) begin
            data_owner_d = DATA_NONE;
        end
        if (data_request_valid && data_request_ready) begin
            if (sdram_address_selected) begin
                data_owner_d = DATA_SDRAM;
            end else if (framebuffer_address_selected) begin
                data_owner_d = DATA_FRAMEBUFFER;
            end else if (palette_address_selected) begin
                data_owner_d = DATA_PALETTE;
            end else if (system_address_selected ||
                         video_control_address_selected) begin
                data_owner_d = DATA_SYSTEM;
            end else if (ps2_address_selected) begin
                data_owner_d = DATA_PS2;
            end else begin
                data_owner_d = DATA_ERROR;
                invalid_response_d = 1'b1;
            end
        end
    end

    `N2A_DFF_RST_VAL(data_owner_q, data_owner_d, clk_i, reset_i, DATA_NONE)
    `N2A_DFF_RST(invalid_response_q, invalid_response_d, clk_i, reset_i)

    // Platform PMON event sources (core/HAS.md "Performance monitor"),
    // derived from existing handshakes. pmon_events is the core's registered
    // vector of all events, observed hierarchically by simulation harnesses.
    logic [127:0] pmon_platform_events;
    /* verilator lint_off UNUSEDSIGNAL */
    logic [127:0] pmon_events;
    /* verilator lint_on UNUSEDSIGNAL */

    n2d_pmon_platform_events u_pmon_events (
        .clk_i, .rst_i(reset_i),
        .instruction_hit_event_i(instruction_hit_event_o),
        .instruction_miss_event_i(instruction_miss_event_o),
        .data_hit_event_i(data_hit_event_o),
        .data_prefetch_issue_event_i(dcache_prefetch_issue_event),
        .data_prefetch_useful_event_i(dcache_prefetch_useful_event),
        .data_prefetch_drop_event_i(dcache_prefetch_drop_event),
        .data_prefetch_deferred_event_i(dcache_prefetch_deferred_event),
        .data_prefetch_cancel_event_i(dcache_prefetch_cancel_event),
        .data_write_buffer_pending_i(dcache_write_buffer_pending),
        .data_write_buffer_wait_i(dcache_write_buffer_wait),
        .data_miss_event_i(data_miss_event_o),
        .line_request_valid_i(memory_request_valid_o),
        .line_request_ready_i(memory_request_ready_i),
        .line_request_prefetch_i(dcache_line_prefetch),
        .line_request_write_i(memory_request_write_o),
        .line_response_valid_i(memory_response_valid_i),
        .line_response_error_i(memory_response_error_i),
        .data_accept_i(data_request_valid && data_request_ready),
        .data_write_i(data_request_write),
        .data_sdram_i(sdram_address_selected),
        .data_framebuffer_i(framebuffer_address_selected),
        .uncached_wait_i((data_owner_q != DATA_NONE) &&
            (data_owner_q != DATA_SDRAM) && !data_response_valid),
        .events_o(pmon_platform_events)
    );

    n2d_rv32im_core u_core (
        .clk_i, .rst_i(reset_i), .enable_i(1'b1),
        .halt_request_i(halt_request),
        .reset_pc_request_i(reset_pc_valid), .resume_i(!halt_request),
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
        .observation_valid_o, .observation_order_o, .observation_pc_o,
        .observation_instruction_o, .observation_next_pc_o,
        .observation_rd_address_o, .observation_rd_write_o,
        .observation_rd_write_data_o, .observation_memory_valid_o,
        .observation_memory_write_o, .observation_memory_address_o,
        .observation_memory_read_data_o, .observation_memory_write_data_o,
        .observation_memory_read_mask_o, .observation_memory_write_mask_o,
        .observation_trap_o, .observation_cause_o, .observation_trap_value_o,
        .halt_ack_o,
        .pmon_platform_events_i(pmon_platform_events),
        .pmon_events_o(pmon_events)
    );

    n2d_icache u_icache (
        .clk_i, .rst_i(reset_i),
        .cpu_request_valid_i(instruction_request_valid),
        .cpu_request_ready_o(instruction_request_ready),
        .cpu_request_address_i(instruction_request_address),
        .cpu_response_valid_o(instruction_response_valid),
        .cpu_response_data_o(instruction_response_data),
        .cpu_response_error_o(instruction_response_error),
        .line_request_valid_o(icache_line_valid),
        .line_request_ready_i(icache_line_ready),
        .line_request_write_o(icache_line_write),
        .line_request_address_o(icache_line_address),
        .line_request_write_data_o(icache_line_write_data),
        .line_request_byte_enable_o(icache_line_byte_enable),
        .line_response_valid_i(icache_line_response_valid),
        .line_response_data_i(icache_line_response_data),
        .line_response_error_i(icache_line_response_error),
        .maintenance_valid_i(icache_maintenance_valid),
        .maintenance_ready_o(icache_maintenance_ready),
        .maintenance_done_o(icache_maintenance_done),
        .hit_event_o(instruction_hit_event_o),
        .miss_event_o(instruction_miss_event_o)
    );

    n2d_dcache #(.WAYS(DCACHE_WAYS), .PREFETCH_LINES(DCACHE_PREFETCH_LINES)) u_dcache (
        .clk_i, .rst_i(reset_i),
        .cpu_request_valid_i(dcache_cpu_valid),
        .cpu_request_ready_o(dcache_cpu_ready),
        .cpu_request_write_i(data_request_write),
        .cpu_request_address_i(data_request_address),
        .cpu_request_write_data_i(data_request_write_data),
        .cpu_request_byte_enable_i(data_request_byte_enable),
        .cpu_response_valid_o(dcache_response_valid),
        .cpu_response_data_o(dcache_response_data),
        .cpu_response_error_o(dcache_response_error),
        .line_request_valid_o(dcache_line_valid),
        .line_request_ready_i(dcache_line_ready),
        .line_request_write_o(dcache_line_write),
        .line_request_address_o(dcache_line_address),
        .line_request_write_data_o(dcache_line_write_data),
        .line_request_byte_enable_o(dcache_line_byte_enable),
        .line_request_prefetch_o(dcache_line_prefetch),
        .line_request_background_o(dcache_line_background),
        .line_response_valid_i(dcache_line_response_valid),
        .line_response_data_i(dcache_line_response_data),
        .line_response_error_i(dcache_line_response_error),
        .maintenance_valid_i(dcache_maintenance_valid),
        .maintenance_ready_o(dcache_maintenance_ready),
        .maintenance_done_o(dcache_maintenance_done),
        .maintenance_error_o(dcache_maintenance_error),
        .hit_event_o(data_hit_event_o), .miss_event_o(data_miss_event_o),
        .prefetch_issue_event_o(dcache_prefetch_issue_event),
        .prefetch_useful_event_o(dcache_prefetch_useful_event),
        .prefetch_drop_event_o(dcache_prefetch_drop_event),
        .prefetch_deferred_event_o(dcache_prefetch_deferred_event),
        .prefetch_cancel_event_o(dcache_prefetch_cancel_event),
        .write_buffer_pending_o(dcache_write_buffer_pending),
        .write_buffer_wait_o(dcache_write_buffer_wait)
    );

    assign framebuffer_write_valid_o = framebuffer_request_valid &&
        framebuffer_request_ready && framebuffer_request_write_mux;
    assign framebuffer_write_address_o = framebuffer_request_address_mux;
    assign framebuffer_write_data_o = framebuffer_request_write_data_mux;
    assign framebuffer_write_strobe_o = framebuffer_request_write_strobe_mux;

    n2d_framebuffer_vga #(.FULL_WIDTH(FRAMEBUFFER_FULL_WIDTH)) u_framebuffer (
        .clk_i, .reset_i,
        .framebuffer_request_valid_i(framebuffer_request_valid),
        .framebuffer_request_ready_o(framebuffer_request_ready),
        .framebuffer_request_write_i(framebuffer_request_write_mux),
        .framebuffer_request_front_i(!debug_framebuffer_request_valid &&
            (screen_framebuffer_valid || framebuffer_front_address_selected)),
        .swap_request_i(framebuffer_swap_request),
        .swap_pending_o(framebuffer_swap_pending_o),
        .front_bank_o(framebuffer_front_bank_o),
        .framebuffer_request_address_i(framebuffer_request_address_mux),
        .framebuffer_request_write_data_i(framebuffer_request_write_data_mux),
        .framebuffer_request_write_strobe_i(framebuffer_request_write_strobe_mux),
        .framebuffer_response_valid_o(framebuffer_response_valid),
        .framebuffer_response_read_data_o(framebuffer_response_data),
        .palette_request_valid_i(palette_request_valid),
        .palette_request_active_i(screen_palette_valid),
        .palette_request_write_i(palette_request_write_mux),
        .palette_request_index_i(palette_request_index_mux),
        .palette_request_write_data_i(palette_request_write_data_mux),
        .palette_request_write_strobe_i(palette_request_write_strobe_mux),
        .palette_commit_i(palette_commit),
        .palette_request_ready_o(palette_request_ready),
        .palette_response_valid_o(palette_response_valid),
        .palette_response_read_data_o(palette_response_data),
        .palette_commit_pending_o(palette_commit_pending),
        .active_palette_bank_o(active_palette_bank),
        .pixel_x_o, .pixel_y_o, .visible_o, .vga_red_o, .vga_green_o,
        .vga_blue_o, .vga_hsync_o, .vga_vsync_o, .frame_start_o,
        .frame_end_o, .frame_sequence_o
    );

    n2d_ps2_keyboard u_keyboard (
        .clk_i, .reset_i, .ps2_clk_i, .ps2_data_i,
        .inject_valid_i(uart_input_valid),
        .inject_ready_o(uart_input_ready),
        .inject_event_i(uart_input_event),
        .scan_inject_valid_i(scan_inject_valid),
        .scan_inject_ready_o(scan_inject_ready),
        .scan_inject_byte_i(scan_inject_byte),
        .mmio_request_valid_i(ps2_request_valid),
        .mmio_request_ready_o(ps2_request_ready),
        .mmio_request_write_i(data_request_write),
        .mmio_request_address_i(data_request_address[11:0]),
        .mmio_request_write_data_i(data_request_write_data),
        .mmio_request_write_strobe_i(data_request_byte_enable),
        .mmio_response_valid_o(ps2_response_valid),
        .mmio_response_ready_i(1'b1),
        .mmio_response_read_data_o(ps2_response_data),
        .mmio_response_error_o(ps2_response_error)
    );

    // Host keyboard over Virtual JTAG (io/HAS.md): the common mailbox endpoint
    // offers raw Set-2 bytes as the input FIFO's lowest-priority producer.
    // The enclosing top supplies the neutral scan ports: board tops bind the
    // common n2a_jtag_virtual, the ASIC its own pins, DV fixtures tie them idle.
    assign jtag_tck = scan_tck_i;
    assign jtag_tdi = scan_tdi_i;
    assign jtag_capture = scan_capture_i;
    assign jtag_shift = scan_shift_i;
    assign jtag_update = scan_update_i;
    assign jtag_update_ir = scan_update_ir_i;
    assign jtag_ir = scan_ir_i;
    assign scan_tdo_o = jtag_tdo;
    n2a_jtag_keyboard u_jtag_keyboard (
        .clk_i, .reset_i, .tck_i(jtag_tck), .ir_i(jtag_ir),
        .capture_i(jtag_capture), .shift_i(jtag_shift), .update_i(jtag_update),
        .update_ir_i(jtag_update_ir), .tdi_i(jtag_tdi), .tdo_o(jtag_tdo),
        .inject_valid_o(scan_inject_valid), .inject_byte_o(scan_inject_byte),
        .inject_ready_i(scan_inject_ready)
    );

    // Small system and video-control registers stay uncached. The completion
    // marker is an observable software handshake; the durable result signature
    // remains in SDRAM and is checked after a precise UART halt.
    // Offset 0x000 bit 0 commits the palette alone. Offset 0x008 bit 0
    // requests a framebuffer swap and bit 1 commits the palette with it: one
    // write arms both on the same clock, so they flip at the same vertical
    // blank (video/HAS.md).
    logic video_control_write_lane0;
    assign video_control_write_lane0 = system_request_valid && data_request_ready &&
        data_request_write && video_control_address_selected &&
        data_request_byte_enable[0];
    assign palette_commit = video_control_write_lane0 &&
        (((data_request_address[11:0] == 12'h000) && data_request_write_data[0]) ||
         ((data_request_address[11:0] == 12'h008) && data_request_write_data[1]));
    assign framebuffer_swap_request = video_control_write_lane0 &&
        (data_request_address[11:0] == 12'h008) && data_request_write_data[0];
    assign system_completion_o = sys_q.completion;
    assign system_tick_o = sys_q.tick;
    assign ps2_event_consumed_o = ps2_response_valid &&
        !ps2_response_error && ps2_response_data[31];

    always_comb begin
        sys_d = sys_q;
        sys_d.tick = sys_q.tick + 1'b1;
        sys_d.response_valid = 1'b0;
        sys_d.response_error = 1'b0;
        if (system_request_valid && data_request_ready) begin
            sys_d.response_valid = 1'b1;
            sys_d.response_data = '0;
            if (system_address_selected) begin
                unique case (data_request_address[11:0])
                    12'h000: begin
                        sys_d.response_data = 32'h3144_324e;
                        sys_d.response_error = data_request_write;
                    end
                    12'h004: begin
                        sys_d.response_data = sys_q.completion;
                        if (data_request_write) begin
                            for (int lane = 0; lane < 4; lane++) begin
                                if (data_request_byte_enable[lane]) begin
                                    sys_d.completion[lane*8 +: 8] = data_request_write_data[lane*8 +: 8];
                                end
                            end
                        end
                    end
                    12'h008: begin
                        sys_d.response_data = sys_q.tick;
                        sys_d.response_error = data_request_write;
                    end
                    12'h00c: begin
                        sys_d.response_data = {30'b0,
                            cache_maintenance_done, halt_ack_o};
                        sys_d.response_error = data_request_write;
                    end
                    default: sys_d.response_error = 1'b1;
                endcase
            end else begin
                unique case (data_request_address[11:0])
                    12'h000: begin
                        sys_d.response_data = 32'b0;
                        sys_d.response_error = !data_request_write ||
                            !data_request_byte_enable[0];
                    end
                    12'h004: begin
                        sys_d.response_data = {frame_sequence_o[29:0],
                            palette_commit_pending, active_palette_bank};
                        sys_d.response_error = data_request_write;
                    end
                    12'h008: begin
                        sys_d.response_data = {30'b0, framebuffer_swap_pending_o,
                            framebuffer_front_bank_o};
                        sys_d.response_error = data_request_write &&
                            !data_request_byte_enable[0];
                    end
                    default: sys_d.response_error = 1'b1;
                endcase
            end
        end
    end

    `N2A_DFF_RST_VAL(sys_q, sys_d, clk_i, reset_i, SYS_RESET)

    // Halt-entry cache maintenance tracking: a rising halt request arms one
    // I-cache invalidate and one D-cache writeback; both must complete.
    always_comb begin
        maint_d = maint_q;
        maint_d.halt_request_prev = halt_request;
        if (!halt_request) begin
            maint_d.required = 1'b0;
            maint_d.icache_complete = 1'b0;
            maint_d.dcache_complete = 1'b0;
        end else if (!maint_q.halt_request_prev && halt_request) begin
            maint_d.required = 1'b1;
            maint_d.icache_complete = 1'b0;
            maint_d.dcache_complete = 1'b0;
        end else begin
            if (icache_maintenance_done) begin
                maint_d.icache_complete = 1'b1;
            end
            if (dcache_maintenance_done && !dcache_maintenance_error) begin
                maint_d.dcache_complete = 1'b1;
            end
        end
        if (maint_q.required &&
            (maint_q.icache_complete || icache_maintenance_done) &&
            (maint_q.dcache_complete ||
             (dcache_maintenance_done && !dcache_maintenance_error))) begin
            maint_d.required = 1'b0;
        end
    end

    `N2A_DFF_RST_VAL(maint_q, maint_d, clk_i, reset_i, MAINT_RESET)
    assign icache_maintenance_valid = maint_q.required && halt_ack_o &&
        !maint_q.icache_complete;
    assign dcache_maintenance_valid = maint_q.required && halt_ack_o &&
        !maint_q.dcache_complete;
    assign cache_maintenance_done = halt_request && !maint_q.required;
    assign debug_memory_request_valid_o = debug_line_valid;

    // Full-cache writeback uses a separately derived bound; short memory
    // transactions retain their original watchdog (memory/HAS.md).
    n2d_uart_debug #(.BAUD(UART_BAUD), .OP_TIMEOUT_CYCLES(4096),
                     .HALT_TIMEOUT_CYCLES(65536)) u_debug (
        .clk_i, .reset_i, .uart_rx_i, .uart_tx_o,
        .debug_control_o(debug_control),
        .debug_status_i(debug_status),
        .input_event_o(uart_input),
        .input_event_ready_i(uart_input_ready),
        .txn_req_o(debug_txn_req),
        .txn_status_i(debug_txn_status),
        .mem_req_o(debug_word_request),
        .mem_rsp_i(debug_word_response),
        .screen_req_o(screen_request),
        .screen_rsp_i(screen_response)
    );
    // Debug port groups unpacked onto the platform's existing nets.
    assign halt_request = debug_control.halt_req;
    assign reset_pc_valid = debug_control.reset_pc_valid;
    assign debug_status = '{halt_ack: halt_ack_o, cache_maint_done: cache_maintenance_done};
    assign uart_input_valid = uart_input.valid;
    assign uart_input_event = uart_input.code;
    assign debug_transaction_begin = debug_txn_req.txn_begin;
    assign debug_transaction_commit = debug_txn_req.txn_commit;
    assign debug_transaction_abort = debug_txn_req.txn_abort;
    assign debug_txn_status = '{arb_grant: 1'b1, txn_done: debug_transaction_done,
                                txn_error: debug_transaction_error};
    assign debug_word_valid = debug_word_request.valid;
    assign debug_word_write = debug_word_request.write;
    assign debug_word_address = debug_word_request.addr;
    assign debug_word_write_data = debug_word_request.wdata;
    assign debug_word_byte_enable = debug_word_request.wstrb;
    assign debug_word_response = '{ready: debug_word_ready, rsp_valid: debug_word_response_valid,
                                   rdata: debug_word_response_data, rsp_error: debug_word_response_error};

    assign debug_select_sdram = (debug_owner_q == DEBUG_SDRAM) ||
        ((debug_owner_q == DEBUG_NONE) &&
         (debug_word_address < 32'h0400_0000));
    assign debug_select_device = (debug_owner_q == DEBUG_DEVICE) ||
        ((debug_owner_q == DEBUG_NONE) &&
         (debug_word_address >= 32'h1000_0000));
    assign debug_sdram_begin = debug_transaction_begin && debug_select_sdram;
    assign debug_device_begin = debug_transaction_begin && debug_select_device;
    assign debug_sdram_commit = debug_transaction_commit &&
        (debug_owner_q == DEBUG_SDRAM);
    assign debug_device_commit = debug_transaction_commit &&
        (debug_owner_q == DEBUG_DEVICE);
    assign debug_sdram_abort = debug_transaction_abort &&
        (debug_owner_q == DEBUG_SDRAM);
    assign debug_device_abort = debug_transaction_abort &&
        (debug_owner_q == DEBUG_DEVICE);
    assign debug_sdram_word_valid = debug_word_valid && debug_select_sdram;
    assign debug_device_word_valid = debug_word_valid && debug_select_device;
    assign debug_word_ready = debug_select_sdram ? debug_sdram_word_ready :
        debug_device_word_ready;
    assign debug_word_response_valid = (debug_owner_q == DEBUG_SDRAM) ?
        debug_sdram_word_response_valid : debug_device_word_response_valid;
    assign debug_word_response_data = (debug_owner_q == DEBUG_SDRAM) ?
        debug_sdram_word_response_data : debug_device_word_response_data;
    assign debug_word_response_error = (debug_owner_q == DEBUG_SDRAM) ?
        debug_sdram_word_response_error : debug_device_word_response_error;
    assign debug_transaction_done = (debug_owner_q == DEBUG_SDRAM) ?
        debug_sdram_done : debug_device_done;
    assign debug_transaction_error = (debug_owner_q == DEBUG_SDRAM) ?
        debug_sdram_error : debug_device_error;
    assign debug_device_word_address =
        (debug_word_address >= 32'h1001_0000) ?
            (32'h0001_0000 + (debug_word_address - 32'h1001_0000)) :
            (debug_word_address - 32'h1000_0000);

    always_comb begin
        debug_owner_d = debug_owner_q;
        if (debug_transaction_begin) begin
            debug_owner_d = debug_select_sdram ? DEBUG_SDRAM : DEBUG_DEVICE;
        end else if (debug_transaction_done) begin
            debug_owner_d = DEBUG_NONE;
        end
    end

    `N2A_DFF_RST_VAL(debug_owner_q, debug_owner_d, clk_i, reset_i, DEBUG_NONE)

    n2d_debug_memory_adapter u_debug_adapter (
        .clk_i, .reset_i,
        .transaction_begin_i(debug_sdram_begin),
        .transaction_commit_i(debug_sdram_commit),
        .transaction_abort_i(debug_sdram_abort),
        .transaction_done_o(debug_sdram_done),
        .transaction_error_o(debug_sdram_error),
        .word_request_valid_i(debug_sdram_word_valid),
        .word_request_ready_o(debug_sdram_word_ready),
        .word_request_write_i(debug_word_write),
        .word_request_address_i(debug_word_address),
        .word_request_write_data_i(debug_word_write_data),
        .word_request_byte_enable_i(debug_word_byte_enable),
        .word_response_valid_o(debug_sdram_word_response_valid),
        .word_response_data_o(debug_sdram_word_response_data),
        .word_response_error_o(debug_sdram_word_response_error),
        .line_request_valid_o(debug_line_valid),
        .line_request_ready_i(debug_line_ready),
        .line_request_write_o(debug_line_write),
        .line_request_address_o(debug_line_address),
        .line_request_write_data_o(debug_line_write_data),
        .line_request_byte_enable_o(debug_line_byte_enable),
        .line_response_valid_i(debug_line_response_valid),
        .line_response_data_i(debug_line_response_data),
        .line_response_error_i(debug_line_response_error)
    );

    n2d_debug_memory_adapter u_debug_device_adapter (
        .clk_i, .reset_i,
        .transaction_begin_i(debug_device_begin),
        .transaction_commit_i(debug_device_commit),
        .transaction_abort_i(debug_device_abort),
        .transaction_done_o(debug_device_done),
        .transaction_error_o(debug_device_error),
        .word_request_valid_i(debug_device_word_valid),
        .word_request_ready_o(debug_device_word_ready),
        .word_request_write_i(debug_word_write),
        .word_request_address_i(debug_device_word_address),
        .word_request_write_data_i(debug_word_write_data),
        .word_request_byte_enable_i(debug_word_byte_enable),
        .word_response_valid_o(debug_device_word_response_valid),
        .word_response_data_o(debug_device_word_response_data),
        .word_response_error_o(debug_device_word_response_error),
        .line_request_valid_o(device_line_valid),
        .line_request_ready_i(device_line_ready),
        .line_request_write_o(device_line_write),
        .line_request_address_o(device_line_address),
        .line_request_write_data_o(device_line_write_data),
        .line_request_byte_enable_o(device_line_byte_enable),
        .line_response_valid_i(device_line_response_valid),
        .line_response_data_i(device_line_response_data),
        .line_response_error_i(device_line_response_error)
    );

    n2d_debug_device_line_bridge u_debug_device_bridge (
        .clk_i, .reset_i,
        .line_request_valid_i(device_line_valid),
        .line_request_ready_o(device_line_ready),
        .line_request_write_i(device_line_write),
        .line_request_address_i(device_line_address),
        .line_request_write_data_i(device_line_write_data),
        .line_request_byte_enable_i(device_line_byte_enable),
        .line_response_valid_o(device_line_response_valid),
        .line_response_data_o(device_line_response_data),
        .line_response_error_o(device_line_response_error),
        .framebuffer_request_valid_o(debug_framebuffer_request_valid),
        .framebuffer_request_ready_i(debug_framebuffer_request_ready),
        .framebuffer_request_write_o(debug_framebuffer_request_write),
        .framebuffer_request_address_o(debug_framebuffer_request_address),
        .framebuffer_request_write_data_o(debug_framebuffer_request_write_data),
        .framebuffer_request_write_strobe_o(debug_framebuffer_request_write_strobe),
        .framebuffer_response_valid_i(framebuffer_response_valid),
        .framebuffer_response_read_data_i(framebuffer_response_data),
        .palette_request_valid_o(debug_palette_request_valid),
        .palette_request_ready_i(debug_palette_request_ready),
        .palette_request_write_o(debug_palette_request_write),
        .palette_request_index_o(debug_palette_request_index),
        .palette_request_write_data_o(debug_palette_request_write_data),
        .palette_request_write_strobe_o(debug_palette_request_write_strobe),
        .palette_response_valid_i(palette_response_valid),
        .palette_response_read_data_i(palette_response_data)
    );

    assign memory_request_valid_o = {debug_line_valid, dcache_line_valid,
                                   icache_line_valid};
    assign memory_request_prefetch_o = {1'b0, dcache_line_prefetch || dcache_line_background, 1'b0};
    assign memory_request_write_o = {debug_line_write, dcache_line_write,
                                   icache_line_write};
    assign memory_request_address_o = {debug_line_address, dcache_line_address,
                                     icache_line_address};
    assign memory_request_write_data_o = {debug_line_write_data,
                                        dcache_line_write_data,
                                        icache_line_write_data};
    assign memory_request_byte_enable_o = {debug_line_byte_enable,
                                         dcache_line_byte_enable,
                                         icache_line_byte_enable};
    assign icache_line_ready = memory_request_ready_i[0];
    assign dcache_line_ready = memory_request_ready_i[1];
    assign debug_line_ready = memory_request_ready_i[2];
    assign icache_line_response_valid = memory_response_valid_i[0];
    assign dcache_line_response_valid = memory_response_valid_i[1];
    assign debug_line_response_valid = memory_response_valid_i[2];
    assign icache_line_response_data = memory_response_data_i[127:0];
    assign dcache_line_response_data = memory_response_data_i[255:128];
    assign debug_line_response_data = memory_response_data_i[383:256];
    assign icache_line_response_error = memory_response_error_i[0];
    assign dcache_line_response_error = memory_response_error_i[1];
    assign debug_line_response_error = memory_response_error_i[2];

    `N2A_ASSERT_CLOCKED(clk_i, reset_i, !((cpu_framebuffer_request_valid ||
        debug_framebuffer_request_valid) && dcache_cpu_valid))
    `N2A_ASSERT_CLOCKED(clk_i, reset_i, ($onehot0({sdram_address_selected,
                              framebuffer_back_address_selected,
                              framebuffer_front_address_selected,
                              palette_address_selected,
                              system_address_selected,
                              video_control_address_selected,
                              ps2_address_selected})))
    `N2A_ASSERT_CLOCKED_WHEN(clk_i, reset_i, framebuffer_write_valid_o,
        framebuffer_write_address_o < 16'd64000)
    `N2A_ASSERT_CLOCKED_FATAL_WHEN(clk_i, reset_i,
        dcache_maintenance_done && dcache_maintenance_error,
        !cache_maintenance_done, "failed D-cache maintenance exposed debug")
endmodule
/* verilator lint_on SYNCASYNCNET */
