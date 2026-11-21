// Formal harness for n2d_memory_arbiter: assumptions on the masters and the
// memory controller, properties and covers. Every item cites its spec line in
// wiki/src/nand2doom/fv/memory/fv-plan.md, which also records the results.
// Spec: wiki/src/nand2doom/rtl/memory/mas.md (MEM-R6, Line port,
// Arbitration and debug adaptation).
module n2d_memory_arbiter_fv (
    input logic         clk_i,
    input logic         reset_i,
    input logic [2:0]   master_request_valid_i,
    input logic [2:0]   master_request_prefetch_i,
    input logic [2:0]   master_request_write_i,
    input logic [77:0]  master_request_address_i,
    input logic [383:0] master_request_write_data_i,
    input logic [47:0]  master_request_byte_enable_i,
    input logic         memory_request_ready_i,
    input logic         memory_response_valid_i,
    input logic [127:0] memory_response_data_i,
    input logic         memory_response_error_i
);
    logic [2:0]   master_request_ready_o;
    logic [2:0]   master_response_valid_o;
    logic [383:0] master_response_data_o;
    logic [2:0]   master_response_error_o;
    logic         memory_request_valid_o;
    logic         memory_request_write_o;
    logic [25:0]  memory_request_address_o;
    logic [127:0] memory_request_write_data_o;
    logic [15:0]  memory_request_byte_enable_o;

    n2d_memory_arbiter dut (.*);

    // Why: every proof starts from the spec's reset state (wiki/bkm/fv/formal.md).
    logic init_q = 1'b1;
    always_ff @(posedge clk_i) init_q <= 1'b0;
    always_comb if (init_q) a_start_in_reset: assume (reset_i);

    logic [2:0] master_accept;
    logic       memory_accept;
    logic [1:0] winner;
    logic [2:0] demand;
    assign master_accept = master_request_valid_i & master_request_ready_o;
    assign memory_accept = memory_request_valid_o && memory_request_ready_i;
    assign winner = master_accept[1] ? 2'd1 : (master_accept[2] ? 2'd2 : 2'd0);
    assign demand = master_request_valid_i & ~master_request_prefetch_i;

    logic [170:0] memory_payload;
    assign memory_payload = {memory_request_write_o, memory_request_address_o,
                             memory_request_write_data_o, memory_request_byte_enable_o};

    logic [2:0]   master_waiting_q;
    logic [2:0]   prev_prefetch_q, prev_write_q;
    logic [77:0]  prev_address_q;
    logic [383:0] prev_write_data_q;
    logic [47:0]  prev_byte_enable_q;
    logic         memory_stalled_q;
    logic [170:0] prev_memory_payload_q;
    logic         stall_began_with_demand_q;
    always_ff @(posedge clk_i) begin
        if (reset_i) begin
            master_waiting_q <= '0;
            memory_stalled_q <= 1'b0;
            stall_began_with_demand_q <= 1'b0;
        end else begin
            master_waiting_q <= master_request_valid_i & ~master_request_ready_o;
            memory_stalled_q <= memory_request_valid_o && !memory_request_ready_i;
            if (memory_request_valid_o && !memory_stalled_q)
                stall_began_with_demand_q <= |demand;
        end
        prev_prefetch_q <= master_request_prefetch_i;
        prev_write_q <= master_request_write_i;
        prev_address_q <= master_request_address_i;
        prev_write_data_q <= master_request_write_data_i;
        prev_byte_enable_q <= master_request_byte_enable_i;
        prev_memory_payload_q <= memory_payload;
    end

    logic       outstanding_q;
    logic [1:0] outstanding_owner_q;
    always_ff @(posedge clk_i) begin
        if (reset_i) begin
            outstanding_q <= 1'b0;
            outstanding_owner_q <= 2'd0;
        end else begin
            if (memory_response_valid_i) outstanding_q <= 1'b0;
            if (memory_accept) begin
                outstanding_q <= 1'b1;
                outstanding_owner_q <= winner;
            end
        end
    end

    // Ref: memory MAS, Line port: "The requester holds the full payload until accepted".
    for (genvar i = 0; i < 3; i++) begin : g_master
        always_comb if (!reset_i && master_waiting_q[i]) begin
            a_master_holds_valid: assume (master_request_valid_i[i]);
            a_master_holds_prefetch: assume (master_request_prefetch_i[i] == prev_prefetch_q[i]);
            a_master_holds_write: assume (master_request_write_i[i] == prev_write_q[i]);
            a_master_holds_address: assume (master_request_address_i[i*26 +: 26] == prev_address_q[i*26 +: 26]);
            a_master_holds_data: assume (master_request_write_data_i[i*128 +: 128] == prev_write_data_q[i*128 +: 128]);
            a_master_holds_enables: assume (master_request_byte_enable_i[i*16 +: 16] == prev_byte_enable_q[i*16 +: 16]);
        end
    end
    // Ref: memory MAS, Line port: "exactly one later response pulse".
    always_comb if (!reset_i && memory_response_valid_i) a_one_response_per_request: assume (outstanding_q);

    logic [2:0][1:0] others_completed_while_waiting_q;
    for (genvar i = 0; i < 3; i++) begin : g_fair
        always_ff @(posedge clk_i) begin
            if (reset_i || !demand[i] || master_accept[i])
                others_completed_while_waiting_q[i] <= 2'd0;
            else if (memory_response_valid_i && outstanding_owner_q != 2'(i) &&
                     others_completed_while_waiting_q[i] != 2'd3)
                others_completed_while_waiting_q[i] <= others_completed_while_waiting_q[i] + 2'd1;
        end
        // Ref: memory MAS, MEM-R6: "each master is accepted at least once per three completions".
        always_ff @(posedge clk_i)
            if (!reset_i) p_round_robin_bound: assert (others_completed_while_waiting_q[i] <= 2'd2);
    end

    // Ref: memory MAS, MEM-R6, Line port and Arbitration (wiki/src/nand2doom/fv/memory/fv-plan.md maps each label).
    always_ff @(posedge clk_i) if (!reset_i) begin
        p_one_line_transaction_outstanding: assert (!(outstanding_q && memory_request_valid_o));
        p_one_grant_per_edge: assert ($onehot0(master_request_ready_o));
        p_no_lost_or_duplicated_request: assert ((|master_accept) == memory_accept);
        if (memory_accept) begin
            p_forwarded_write_is_the_winners: assert (memory_request_write_o == master_request_write_i[winner]);
            p_forwarded_address_is_the_winners: assert (memory_request_address_o == master_request_address_i[winner*26 +: 26]);
            p_forwarded_data_is_the_winners: assert (memory_request_write_data_o == master_request_write_data_i[winner*128 +: 128]);
            p_forwarded_enables_are_the_winners: assert (memory_request_byte_enable_o == master_request_byte_enable_i[winner*16 +: 16]);
        end
        if (memory_response_valid_i) begin
            p_response_to_owner_only: assert (master_response_valid_o == 3'(1 << outstanding_owner_q));
            p_response_data_to_owner: assert (master_response_data_o[outstanding_owner_q*128 +: 128] == memory_response_data_i);
            p_response_error_to_owner: assert (master_response_error_o[outstanding_owner_q] == memory_response_error_i);
        end else begin
            p_no_response_without_memory_response: assert (master_response_valid_o == 3'b000);
        end
        if (memory_stalled_q) begin
            p_stalled_request_held_valid: assert (memory_request_valid_o);
            p_stalled_request_held_payload: assert (memory_payload == prev_memory_payload_q);
        end
        if (memory_accept && master_request_prefetch_i[winner])
            p_prefetch_only_without_demand: assert (!(memory_stalled_q ? stall_began_with_demand_q : (|demand)));

        // Why: helper invariants, proven like any property, only narrow the reachable state for the proof.
        h_busy_matches_outstanding: assert (dut.busy_q == outstanding_q);
        if (outstanding_q) h_owner_matches: assert (dut.owner_q == outstanding_owner_q);
        h_stall_matches: assert (dut.stalled_q == memory_stalled_q);
        h_pointer_in_range: assert (dut.next_owner_q != 2'd3);
    end

    logic       completed_q;
    logic [1:0] completed_owner_q;
    always_ff @(posedge clk_i) begin
        completed_q <= !reset_i && memory_response_valid_i;
        completed_owner_q <= outstanding_owner_q;
    end
    always_ff @(posedge clk_i) if (!reset_i) begin
        c_master0_accepted: cover (master_accept[0]);
        c_master1_accepted: cover (master_accept[1]);
        c_master2_accepted: cover (master_accept[2]);
        c_stall_then_accept: cover (memory_stalled_q && memory_accept);
        c_prefetch_accepted: cover (memory_accept && master_request_prefetch_i[winner]);
        if (completed_q) c_next_master_wins_after_completion: cover (master_accept[completed_owner_q == 2'd2 ? 2'd0 : completed_owner_q + 2'd1]);
        c_master0_bound_tight: cover (others_completed_while_waiting_q[0] == 2'd2 && master_accept[0]);
        c_master1_bound_tight: cover (others_completed_while_waiting_q[1] == 2'd2 && master_accept[1]);
        c_master2_bound_tight: cover (others_completed_while_waiting_q[2] == 2'd2 && master_accept[2]);
    end
endmodule
