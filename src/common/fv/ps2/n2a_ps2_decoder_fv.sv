// Formal harness for n2a_ps2_decoder: reference qualifiers written from the
// spec, properties and covers. Every item cites its spec line in
// wiki/src/common/fv/ps2/fv-plan.md, which also records the results.
// Spec: wiki/src/common/rtl/ps2/mas.md (Decoder).
module n2a_ps2_decoder_fv (
    input logic       clk_i,
    input logic       reset_i,
    input logic       byte_valid_i,
    input logic [7:0] byte_i,
    input logic       clear_i,
    input logic       event_ready_i
);
    logic                    byte_ready_o;
    logic                    event_valid_o;
    n2a_ps2_pkg::t_ps2_event event_o;

    n2a_ps2_decoder dut (.*);

    // Why: every proof starts from the spec's reset state (wiki/bkm/fv.md).
    logic init_q = 1'b1;
    always_ff @(posedge clk_i) init_q <= 1'b0;
    always_comb if (init_q) a_start_in_reset: assume (reset_i);

    // Ref: MAS Decoder: E0 sets extended, F0 sets break; an accepted event,
    // clear_i or reset clears both; a refused byte keeps them.
    logic extended_q, break_q, is_prefix, accepted;
    assign is_prefix = byte_i == 8'hE0 || byte_i == 8'hF0;
    assign accepted = byte_valid_i && !is_prefix && event_ready_i;
    always_ff @(posedge clk_i) begin
        if (reset_i || clear_i || accepted) begin
            extended_q <= 1'b0;
            break_q <= 1'b0;
        end else if (byte_valid_i && byte_i == 8'hE0) extended_q <= 1'b1;
        else if (byte_valid_i && byte_i == 8'hF0) break_q <= 1'b1;
    end

    always_ff @(posedge clk_i) if (!reset_i) begin
        p_event_for_every_code: assert (event_valid_o == (byte_valid_i && !is_prefix));
        if (event_valid_o) p_event_carries_qualifiers: assert (event_o == {extended_q, break_q, byte_i});
        p_prefix_always_ready: assert (byte_ready_o == (is_prefix || event_ready_i));

        // Why: helper invariants, proven like any property, only narrow the reachable state for the proof.
        h_extended_matches: assert (dut.pending_q.extended == extended_q);
        h_break_matches: assert (dut.pending_q.key_break == break_q);
    end

    always_ff @(posedge clk_i) if (!reset_i) begin
        c_extended_break_event: cover (accepted && extended_q && break_q);
        c_extended_make_event: cover (accepted && extended_q && !break_q);
        c_refused_with_qualifiers: cover (event_valid_o && !event_ready_i && extended_q && break_q);
        c_clear_drops_qualifiers: cover (clear_i && extended_q && !event_valid_o);
        c_repeated_prefix: cover (byte_valid_i && byte_i == 8'hE0 && extended_q);
    end
endmodule
