// Formal harness for n2a_ps2_receiver: a reference frame receiver written
// from the spec, properties and covers. Every item cites its spec line in
// wiki/src/common/fv/ps2/fv-plan.md, which also records the results.
// Spec: wiki/src/common/rtl/ps2/mas.md (Receiver).
module n2a_ps2_receiver_fv (
    input logic clk_i,
    input logic reset_i,
    input logic ps2_clk_i,
    input logic ps2_data_i,
    input logic enable_i,
    input logic abort_i
);
    localparam int unsigned TIMEOUT = 4;

    logic                        byte_valid_o;
    logic [7:0]                  byte_o;
    n2a_ps2_pkg::t_ps2_rx_errors errors_o;

    // Why: the frame timeout is shortened from 5,000 cycles so the counter and
    // the covers stay small; the rule it checks does not depend on the count.
    n2a_ps2_receiver #(.FRAME_TIMEOUT_CYCLES(TIMEOUT)) dut (.*);

    // Why: every proof starts from the spec's reset state (wiki/bkm/fv.md).
    logic init_q = 1'b1;
    always_ff @(posedge clk_i) init_q <= 1'b0;
    always_comb if (init_q) a_start_in_reset: assume (reset_i);

    // Ref: MAS Receiver: each pin passes a two-stage synchronizer reset high;
    // data is sampled on registered falling clock edges.
    logic [1:0] clk_sync_q, data_sync_q;
    logic       clk_previous_q;
    logic       edge_seen, data_bit, receiving;
    assign edge_seen = clk_previous_q && !clk_sync_q[1];
    assign data_bit = data_sync_q[1];
    assign receiving = enable_i && !abort_i;

    // Ref: MAS Receiver: low start, eight data bits LSB first, odd parity,
    // high stop; FRAME_TIMEOUT_CYCLES clocks without an edge discard a frame.
    logic       active_q;
    logic [3:0] bits_q;
    logic [8:0] shift_q;
    logic [2:0] idle_q;
    logic       stop_edge, good, parity_ok, expect_timeout, expect_bad_start;
    assign stop_edge = receiving && active_q && edge_seen && bits_q == 4'd9;
    assign parity_ok = ^shift_q;
    assign good = stop_edge && parity_ok && data_bit;
    assign expect_timeout = receiving && active_q && !edge_seen && idle_q == 3'(TIMEOUT - 1);
    assign expect_bad_start = receiving && !active_q && edge_seen && data_bit;

    always_ff @(posedge clk_i) begin
        if (reset_i) begin
            clk_sync_q <= 2'b11;
            data_sync_q <= 2'b11;
            clk_previous_q <= 1'b1;
            active_q <= 1'b0;
            bits_q <= '0;
            idle_q <= '0;
        end else begin
            clk_sync_q <= {clk_sync_q[0], ps2_clk_i};
            data_sync_q <= {data_sync_q[0], ps2_data_i};
            clk_previous_q <= clk_sync_q[1];
            if (abort_i) begin
                active_q <= 1'b0;
                bits_q <= '0;
                idle_q <= '0;
            end else if (receiving) begin
                if (active_q && edge_seen) begin
                    idle_q <= '0;
                    if (bits_q == 4'd9) begin
                        active_q <= 1'b0;
                        bits_q <= '0;
                    end else begin
                        shift_q[bits_q] <= data_bit;
                        bits_q <= bits_q + 4'd1;
                    end
                end else if (active_q) begin
                    if (expect_timeout) begin
                        active_q <= 1'b0;
                        bits_q <= '0;
                        idle_q <= '0;
                    end else idle_q <= idle_q + 3'd1;
                end else if (edge_seen && !data_bit) begin
                    active_q <= 1'b1;
                    bits_q <= '0;
                    idle_q <= '0;
                end
            end
        end
    end

    always_ff @(posedge clk_i) if (!reset_i) begin
        p_byte_only_for_good_frame: assert (byte_valid_o == good);
        if (good) p_byte_is_frame_data: assert (byte_o == shift_q[7:0]);
        p_parity_error_on_bad_parity: assert (errors_o.parity == (stop_edge && !parity_ok));
        p_frame_error_on_bad_stop_or_start: assert (errors_o.frame == ((stop_edge && !data_bit) || expect_bad_start));
        p_timeout_after_silence: assert (errors_o.timeout == expect_timeout);
        if (!receiving) p_silent_unless_receiving: assert (!byte_valid_o && errors_o == '0);

        // Why: helper invariants, proven like any property, only narrow the reachable state for the proof.
        h_active_matches: assert (dut.regs_q.active == active_q);
        h_index_matches: assert (dut.regs_q.index == bits_q);
        h_timer_matches: assert (dut.frame_timeout_q == idle_q[1:0]);
        h_timer_in_range: assert (idle_q < 3'(TIMEOUT));
        h_previous_matches: assert (dut.regs_q.previous == clk_previous_q);
        h_clock_sync_matches: assert (dut.u_clock_sync.stages_q == clk_sync_q);
        h_data_sync_matches: assert (dut.u_data_sync.stages_q == data_sync_q);
        h_parity_matches: assert (!(active_q && bits_q == 4'd9) || dut.regs_q.parity == shift_q[8]);
    end
    for (genvar j = 0; j < 8; j++) begin : g_bit
        always_ff @(posedge clk_i)
            if (!reset_i && active_q && bits_q > 4'(j)) h_data_matches: assert (dut.regs_q.data[j] == shift_q[j]);
    end

    always_ff @(posedge clk_i) if (!reset_i) begin
        c_good_byte: cover (byte_valid_o);
        c_good_extended_prefix: cover (byte_valid_o && byte_o == 8'hE0);
        c_parity_error: cover (errors_o.parity && !errors_o.frame);
        c_stop_error: cover (errors_o.frame && stop_edge);
        c_parity_and_stop_error: cover (errors_o.parity && errors_o.frame);
        c_bad_start: cover (expect_bad_start);
        c_timeout: cover (errors_o.timeout);
        c_abort_on_stop_edge: cover (abort_i && enable_i && active_q && edge_seen && bits_q == 4'd9);
        c_disabled_mid_frame: cover (!enable_i && active_q && edge_seen);
    end
endmodule
