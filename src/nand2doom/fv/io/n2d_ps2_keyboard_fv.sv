// Formal harness for the n2d_ps2_keyboard event FIFO: a reference queue built
// from the spec, properties and covers. Every item cites its spec line in
// wiki/src/nand2doom/fv/io/fv-plan.md, which also records the results.
// Spec: wiki/src/nand2doom/rtl/io/mas.md (IO-R9, PS/2 MMIO registers,
// JTAG keyboard, PS/2 keyboard receiver).
module n2d_ps2_keyboard_fv (
    input logic        clk_i,
    input logic        reset_i,
    input logic        ps2_clk_i,
    input logic        ps2_data_i,
    input logic        inject_valid_i,
    input logic [9:0]  inject_event_i,
    input logic        scan_inject_valid_i,
    input logic [7:0]  scan_inject_byte_i,
    input logic        mmio_request_valid_i,
    input logic        mmio_request_write_i,
    input logic [11:0] mmio_request_address_i,
    input logic [31:0] mmio_request_write_data_i,
    input logic [3:0]  mmio_request_write_strobe_i,
    input logic        mmio_response_ready_i
);
    logic        inject_ready_o;
    logic        scan_inject_ready_o;
    logic        mmio_request_ready_o;
    logic        mmio_response_valid_o;
    logic [31:0] mmio_response_read_data_o;
    logic        mmio_response_error_o;

    // Why: the frame timeout is shortened from 5,000 cycles. It bounds how long a
    // frame may take, not how the FIFO behaves, and the shorter count keeps the
    // receiver's state small.
    n2d_ps2_keyboard #(.FRAME_TIMEOUT_CYCLES(4)) dut (.*);

    // Why: every proof starts from the spec's reset state (wiki/bkm/fv.md).
    logic init_q = 1'b1;
    always_ff @(posedge clk_i) init_q <= 1'b0;
    always_comb if (init_q) a_start_in_reset: assume (reset_i);

    logic request_accept, base_ready, control_write, clear_fifo, clear_diagnostics;
    logic data_read, status_read, mmio_inject, uart_accept, scan_offer_prefix, scan_accept;
    assign request_accept = mmio_request_valid_i && mmio_request_ready_o;
    // Ref: I/O MAS, CONTROL row: "A lane-0 write" acts once the response channel can take it.
    assign base_ready = !mmio_response_valid_o || mmio_response_ready_i;
    assign control_write = base_ready && mmio_request_valid_i && mmio_request_write_i &&
                           mmio_request_address_i == 12'h008 &&
                           mmio_request_write_strobe_i == 4'b0001;
    assign clear_fifo = control_write && mmio_request_write_data_i[1];
    assign clear_diagnostics = control_write && mmio_request_write_data_i[2];
    assign data_read = request_accept && !mmio_request_write_i && mmio_request_address_i == 12'h000;
    assign status_read = request_accept && !mmio_request_write_i && mmio_request_address_i == 12'h004;
    assign mmio_inject = request_accept && mmio_request_write_i && mmio_request_address_i == 12'h00c &&
                         mmio_request_write_strobe_i == 4'hf && mmio_request_write_data_i[31:10] == '0;
    assign uart_accept = inject_valid_i && inject_ready_o;
    assign scan_offer_prefix = scan_inject_valid_i &&
                               (scan_inject_byte_i == 8'he0 || scan_inject_byte_i == 8'hf0);
    assign scan_accept = scan_inject_valid_i && scan_inject_ready_o && !scan_offer_prefix;

    // Why: white-box, because the receiver's framing is outside this FIFO's scope.
    logic physical_event;
    logic [9:0] physical_data;
    assign physical_event = dut.physical_event_push;
    assign physical_data = dut.physical_event_data;

    logic [9:0] ref_mem [8];
    logic [2:0] ref_head_q;
    logic [3:0] ref_count_q;
    logic       scan_extended_q, scan_break_q;
    logic       pop, space, physical_accept, push;
    logic [9:0] push_data;
    assign pop = data_read && ref_count_q != 4'd0;
    assign space = ref_count_q < 4'd8 || pop;
    assign physical_accept = physical_event && space;
    assign push = physical_accept || uart_accept || mmio_inject || scan_accept;
    assign push_data = physical_accept ? physical_data :
                       uart_accept ? inject_event_i :
                       mmio_inject ? mmio_request_write_data_i[9:0] :
                       {scan_extended_q, scan_break_q, scan_inject_byte_i};

    always_ff @(posedge clk_i) begin
        if (reset_i) begin
            ref_head_q <= '0;
            ref_count_q <= '0;
            scan_extended_q <= 1'b0;
            scan_break_q <= 1'b0;
        end else begin
            if (clear_fifo) begin
                ref_head_q <= '0;
                ref_count_q <= '0;
            end else begin
                if (push) ref_mem[3'(ref_head_q + ref_count_q[2:0])] <= push_data;
                if (pop) ref_head_q <= ref_head_q + 3'd1;
                ref_count_q <= ref_count_q + 4'(push) - 4'(pop);
            end
            // Ref: I/O MAS, JTAG keyboard: E0/F0 set the qualifiers; "Only reset and the completing byte clear" them.
            if (scan_offer_prefix && scan_inject_byte_i == 8'he0) scan_extended_q <= 1'b1;
            if (scan_offer_prefix && scan_inject_byte_i == 8'hf0) scan_break_q <= 1'b1;
            if (scan_accept) begin
                scan_extended_q <= 1'b0;
                scan_break_q <= 1'b0;
            end
        end
    end

    logic        expect_q;
    logic        expect_check_q;
    logic [31:0] expect_data_q;
    logic [31:0] expected_status;
    always_comb begin
        expected_status = '0;
        expected_status[0] = ref_count_q != 0;
        expected_status[1] = ref_count_q == 0;
        expected_status[2] = ref_count_q == 4'd8;
        expected_status[6:3] = ref_count_q;
    end
    always_ff @(posedge clk_i) begin
        if (reset_i) begin
            expect_q <= 1'b0;
            expect_check_q <= 1'b0;
        end else begin
            expect_q <= request_accept;
            expect_check_q <= data_read || status_read;
            // Why: STATUS is checked only in its FIFO fields, bits 6:0 (fv-plan.md, out of scope).
            expect_data_q <= data_read ? (ref_count_q != 0 ? {1'b1, 21'b0, ref_mem[ref_head_q]} : 32'b0)
                                       : {25'b0, expected_status[6:0]};
        end
    end
    logic was_status_q;
    always_ff @(posedge clk_i) was_status_q <= status_read;

    logic physical_dropped_q, overflow_previous_q, clear_diagnostics_q;
    always_ff @(posedge clk_i) begin
        physical_dropped_q <= !reset_i && physical_event && !space;
        overflow_previous_q <= dut.fifo_q.overflow_error;
        clear_diagnostics_q <= !reset_i && clear_diagnostics;
    end

    // Ref: I/O MAS, IO-R9, PS/2 MMIO registers, JTAG keyboard and receiver push rules
    // (wiki/src/nand2doom/fv/io/fv-plan.md maps each label to its spec line).
    always_ff @(posedge clk_i) if (!reset_i) begin
        p_one_push_per_edge: assert ($onehot0({physical_accept, uart_accept, mmio_inject, scan_accept}));
        if (physical_event) p_physical_has_priority: assert (!uart_accept && !mmio_inject && !scan_accept);
        if (mmio_inject) p_fixed_priority_uart_over_inject: assert (!inject_valid_i);
        if (scan_accept) p_fixed_priority_over_scan: assert (!inject_valid_i && !mmio_inject);
        if (inject_valid_i && space && !physical_event && !control_write)
            p_fixed_priority_uart_taken: assert (inject_ready_o);
        if (!space) p_no_accept_without_slot: assert (!uart_accept && !mmio_inject && !scan_accept);
        if (control_write) p_control_suppresses_pushes: assert (!push);
        if (scan_offer_prefix) p_scan_prefix_always_ready: assert (scan_inject_ready_o);
        if (expect_q) p_every_request_answered: assert (mmio_response_valid_o);
        if (expect_check_q && !was_status_q) begin
            p_events_read_in_order: assert (mmio_response_read_data_o == expect_data_q);
            p_data_read_no_error: assert (!mmio_response_error_o);
        end
        if (expect_check_q && was_status_q) begin
            p_status_reports_occupancy: assert (mmio_response_read_data_o[6:0] == expect_data_q[6:0]);
            p_status_read_no_error: assert (!mmio_response_error_o);
        end
        if (physical_dropped_q && !clear_diagnostics_q) p_drop_sets_overflow: assert (dut.fifo_q.overflow_error);
        if (!overflow_previous_q && dut.fifo_q.overflow_error) p_only_drop_sets_overflow: assert (physical_dropped_q);

        // Why: helper invariants, proven like any property, only narrow the reachable state for the proof.
        h_count_matches: assert (dut.fifo_q.fifo_count == ref_count_q);
        h_read_pointer_matches: assert (dut.fifo_q.fifo_read_pointer == ref_head_q);
        h_extended_matches: assert (dut.scan_inject_q.extended_prefix == scan_extended_q);
        h_break_matches: assert (dut.scan_inject_q.break_prefix == scan_break_q);
        h_count_in_range: assert (ref_count_q <= 4'd8);
    end
    for (genvar j = 0; j < 8; j++) begin : g_slot
        always_ff @(posedge clk_i)
            if (!reset_i && 3'(j - ref_head_q) < ref_count_q) h_slot_matches: assert (dut.fifo_memory[j] == ref_mem[j]);
    end

    always_ff @(posedge clk_i) if (!reset_i) begin
        c_full: cover (ref_count_q == 4'd8);
        c_full_push_and_pop: cover (ref_count_q == 4'd8 && pop && push);
        c_physical_dropped: cover (physical_event && !space);
        c_physical_accepted: cover (physical_accept);
        c_uart_accepted: cover (uart_accept);
        c_inject_accepted: cover (mmio_inject);
        c_scan_with_both_qualifiers: cover (scan_accept && scan_extended_q && scan_break_q);
        c_scan_refused: cover (scan_inject_valid_i && !scan_offer_prefix && !scan_inject_ready_o);
        c_extended_event_read: cover (expect_check_q && !was_status_q && mmio_response_read_data_o[31] && mmio_response_read_data_o[9]);
        c_clear_nonempty: cover (clear_fifo && ref_count_q != 0);
        c_stalled_response_when_full: cover (mmio_response_valid_o && !mmio_response_ready_i && ref_count_q == 4'd8);
    end
endmodule
