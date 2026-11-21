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

  // The frame timeout is shortened from 5,000 cycles: it bounds how long a
  // frame may take, not how the FIFO behaves, and the shorter count keeps the
  // receiver's state small.
  n2d_ps2_keyboard #(.FRAME_TIMEOUT_CYCLES(4)) dut (.*);

  // Every proof starts from the spec's reset state.
  logic init_q = 1'b1;
  always_ff @(posedge clk_i) init_q <= 1'b0;
  always_comb if (init_q) assume (reset_i);

  // ---- Interface events, decoded from the spec ----------------------------
  logic request_accept, base_ready, control_write, clear_fifo, clear_diagnostics;
  logic data_read, status_read, mmio_inject, uart_accept, scan_offer_prefix, scan_accept;
  assign request_accept = mmio_request_valid_i && mmio_request_ready_o;
  // CONTROL acts on any lane-0 write the response channel can take.
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

  // A completed physical event (white-box: the receiver's framing is outside
  // this FIFO's scope) and its spec encoding {extended, break, code}.
  logic physical_event;
  logic [9:0] physical_data;
  assign physical_event = dut.physical_event_push;
  assign physical_data = dut.physical_event_data;

  // ---- Reference queue -----------------------------------------------------
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
      // Scan-inject qualifiers: E0/F0 set them, only the completing byte clears them.
      if (scan_offer_prefix && scan_inject_byte_i == 8'he0) scan_extended_q <= 1'b1;
      if (scan_offer_prefix && scan_inject_byte_i == 8'hf0) scan_break_q <= 1'b1;
      if (scan_accept) begin
        scan_extended_q <= 1'b0;
        scan_break_q <= 1'b0;
      end
    end
  end

  // Expected response of the request accepted on the last edge.
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
      // STATUS: only the FIFO fields (bits 6:0) are checked here.
      expect_data_q <= data_read ? (ref_count_q != 0 ? {1'b1, 21'b0, ref_mem[ref_head_q]} : 32'b0)
                                 : {25'b0, expected_status[6:0]};
    end
  end
  logic was_status_q;
  always_ff @(posedge clk_i) was_status_q <= status_read;

  // Overflow bookkeeping.
  logic physical_dropped_q, overflow_previous_q, clear_diagnostics_q;
  always_ff @(posedge clk_i) begin
    physical_dropped_q <= !reset_i && physical_event && !space;
    overflow_previous_q <= dut.fifo_q.overflow_error;
    clear_diagnostics_q <= !reset_i && clear_diagnostics;
  end

  // ---- Properties -------------------------------------------------------
  always_ff @(posedge clk_i) if (!reset_i) begin
    // P_one_push_per_edge, P_physical_has_priority: a physical event
    // suppresses every other source on its edge, even when it is dropped.
    assert ($onehot0({physical_accept, uart_accept, mmio_inject, scan_accept}));
    if (physical_event) assert (!uart_accept && !mmio_inject && !scan_accept);
    // P_no_accept_without_slot: a held source is accepted only into a free slot.
    if (!space) assert (!uart_accept && !mmio_inject && !scan_accept);
    // P_control_suppresses_pushes
    if (control_write) assert (!push);
    // P_scan_prefix_always_ready
    if (scan_offer_prefix) assert (scan_inject_ready_o);
    // P_events_read_in_order: DATA returns the oldest event; STATUS reports occupancy.
    if (expect_q) assert (mmio_response_valid_o);
    if (expect_check_q && !was_status_q) begin
      assert (mmio_response_read_data_o == expect_data_q);
      assert (!mmio_response_error_o);
    end
    if (expect_check_q && was_status_q) begin
      assert (mmio_response_read_data_o[6:0] == expect_data_q[6:0]);
      assert (!mmio_response_error_o);
    end
    // P_drop_sets_overflow / P_only_drop_sets_overflow
    if (physical_dropped_q && !clear_diagnostics_q) assert (dut.fifo_q.overflow_error);
    if (!overflow_previous_q && dut.fifo_q.overflow_error) assert (physical_dropped_q);

    // Helper invariants (proven, they only constrain the reachable state).
    assert (dut.fifo_q.fifo_count == ref_count_q);
    assert (dut.fifo_q.fifo_read_pointer == ref_head_q);
    assert (dut.scan_inject_q.extended_prefix == scan_extended_q);
    assert (dut.scan_inject_q.break_prefix == scan_break_q);
    assert (ref_count_q <= 4'd8);
  end
  for (genvar j = 0; j < 8; j++) begin : g_slot
    always_ff @(posedge clk_i)
      if (!reset_i && 3'(j - ref_head_q) < ref_count_q) assert (dut.fifo_memory[j] == ref_mem[j]);
  end

  // ---- Covers -----------------------------------------------------------
  always_ff @(posedge clk_i) if (!reset_i) begin
    cover (ref_count_q == 4'd8);
    cover (ref_count_q == 4'd8 && pop && push);
    cover (physical_event && !space);
    cover (physical_accept);
    cover (uart_accept);
    cover (mmio_inject);
    cover (scan_accept && scan_extended_q && scan_break_q);
    cover (scan_inject_valid_i && !scan_offer_prefix && !scan_inject_ready_o);
    cover (expect_check_q && !was_status_q && mmio_response_read_data_o[31] && mmio_response_read_data_o[9]);
    cover (clear_fifo && ref_count_q != 0);
    cover (mmio_response_valid_o && !mmio_response_ready_i && ref_count_q == 4'd8);
  end
endmodule
