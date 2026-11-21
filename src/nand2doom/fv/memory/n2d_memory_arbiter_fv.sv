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

    // Every proof starts from the spec's reset state.
    logic init_q = 1'b1;
    always_ff @(posedge clk_i) init_q <= 1'b0;
    always_comb if (init_q) assume (reset_i);

    // ---- Observation of the interfaces ------------------------------------
    logic [2:0] master_accept;
    logic       memory_accept;
    logic [1:0] winner;
    logic [2:0] demand;
    assign master_accept = master_request_valid_i & master_request_ready_o;
    assign memory_accept = memory_request_valid_o && memory_request_ready_i;
    assign winner = master_accept[1] ? 2'd1 : (master_accept[2] ? 2'd2 : 2'd0);
    assign demand = master_request_valid_i & ~master_request_prefetch_i;

    // Downstream payload as one word, for the stability checks.
    logic [170:0] memory_payload;
    assign memory_payload = {memory_request_write_o, memory_request_address_o,
                             memory_request_write_data_o, memory_request_byte_enable_o};

    // Previous-edge samples (pure observation, no reset needed for payloads).
    logic [2:0]   waiting_q;   // master valid and not accepted on the last edge
    logic [2:0]   prev_prefetch_q, prev_write_q;
    logic [77:0]  prev_address_q;
    logic [383:0] prev_write_data_q;
    logic [47:0]  prev_byte_enable_q;
    logic         stalled_q;   // downstream valid and not accepted on the last edge
    logic [170:0] prev_memory_payload_q;
    logic         stall_began_with_demand_q;
    always_ff @(posedge clk_i) begin
        if (reset_i) begin
            waiting_q <= '0;
            stalled_q <= 1'b0;
            stall_began_with_demand_q <= 1'b0;
        end else begin
            waiting_q <= master_request_valid_i & ~master_request_ready_o;
            stalled_q <= memory_request_valid_o && !memory_request_ready_i;
            if (memory_request_valid_o && !stalled_q)
                stall_began_with_demand_q <= |demand;
        end
        prev_prefetch_q <= master_request_prefetch_i;
        prev_write_q <= master_request_write_i;
        prev_address_q <= master_request_address_i;
        prev_write_data_q <= master_request_write_data_i;
        prev_byte_enable_q <= master_request_byte_enable_i;
        prev_memory_payload_q <= memory_payload;
    end

    // One line transaction in flight downstream, and the master it belongs to.
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

    // ---- Assumptions: what the neighbours guarantee -------------------------
    // A_master_holds_request: the requester holds the full payload until accepted.
    for (genvar i = 0; i < 3; i++) begin : g_master
        always_comb if (!reset_i && waiting_q[i]) begin
            assume (master_request_valid_i[i]);
            assume (master_request_prefetch_i[i] == prev_prefetch_q[i]);
            assume (master_request_write_i[i] == prev_write_q[i]);
            assume (master_request_address_i[i*26 +: 26] == prev_address_q[i*26 +: 26]);
            assume (master_request_write_data_i[i*128 +: 128] == prev_write_data_q[i*128 +: 128]);
            assume (master_request_byte_enable_i[i*16 +: 16] == prev_byte_enable_q[i*16 +: 16]);
        end
    end
    // A_one_response_per_request: exactly one later response per accepted request.
    always_comb if (!reset_i && memory_response_valid_i) assume (outstanding_q);

    // ---- Round-robin bound (MEM-R6) ----------------------------------------
    // Completions of other masters seen while master i holds a demand request.
    logic [2:0][1:0] others_completed_q;
    for (genvar i = 0; i < 3; i++) begin : g_fair
        always_ff @(posedge clk_i) begin
            if (reset_i || !demand[i] || master_accept[i])
                others_completed_q[i] <= 2'd0;
            else if (memory_response_valid_i && outstanding_owner_q != 2'(i) &&
                     others_completed_q[i] != 2'd3)
                others_completed_q[i] <= others_completed_q[i] + 2'd1;
        end
    end

    // ---- Properties -------------------------------------------------------
    always_ff @(posedge clk_i) if (!reset_i) begin
        // P_one_line_transaction_outstanding
        assert (!(outstanding_q && memory_request_valid_o));
        // P_one_grant_per_edge
        assert ($onehot0(master_request_ready_o));
        // P_no_lost_or_duplicated_request: a master handshake happens exactly
        // when the downstream handshake does.
        assert ((|master_accept) == memory_accept);
        // P_forwarded_payload_is_the_winners
        if (memory_accept) begin
            assert (memory_request_write_o == master_request_write_i[winner]);
            assert (memory_request_address_o == master_request_address_i[winner*26 +: 26]);
            assert (memory_request_write_data_o == master_request_write_data_i[winner*128 +: 128]);
            assert (memory_request_byte_enable_o == master_request_byte_enable_i[winner*16 +: 16]);
        end
        // P_response_to_owner: each response pulse goes to the captured owner only.
        if (memory_response_valid_i) begin
            assert (master_response_valid_o == 3'(1 << outstanding_owner_q));
            assert (master_response_data_o[outstanding_owner_q*128 +: 128] == memory_response_data_i);
            assert (master_response_error_o[outstanding_owner_q] == memory_response_error_i);
        end else begin
            assert (master_response_valid_o == 3'b000);
        end
        // P_stalled_request_held: a stalled request stays presented, unchanged.
        if (stalled_q) begin
            assert (memory_request_valid_o);
            assert (memory_payload == prev_memory_payload_q);
        end
        // P_prefetch_only_without_demand: a prefetch is chosen only when no
        // master presented demand at the time it was chosen.
        if (memory_accept && master_request_prefetch_i[winner])
            assert (!(stalled_q ? stall_began_with_demand_q : (|demand)));
        // P_round_robin_bound (MEM-R6): a waiting demand request is accepted
        // before a third completion of another master.
        for (int i = 0; i < 3; i++) assert (others_completed_q[i] <= 2'd2);

        // Helper invariants (proven, they only constrain the reachable state).
        assert (dut.busy_q == outstanding_q);
        if (outstanding_q) assert (dut.owner_q == outstanding_owner_q);
        assert (dut.stalled_q == stalled_q);
        assert (dut.next_owner_q != 2'd3);
        if (stalled_q) assert (memory_request_valid_o);
    end

    // ---- Covers -----------------------------------------------------------
    logic       completed_q;
    logic [1:0] completed_owner_q;
    always_ff @(posedge clk_i) begin
        completed_q <= !reset_i && memory_response_valid_i;
        completed_owner_q <= outstanding_owner_q;
    end
    always_ff @(posedge clk_i) if (!reset_i) begin
        cover (master_accept[0]);
        cover (master_accept[1]);
        cover (master_accept[2]);
        cover (stalled_q && memory_accept);
        cover (memory_accept && master_request_prefetch_i[winner]);
        // The pointer moves past the owner: the next master wins right after a completion.
        if (completed_q) cover (master_accept[completed_owner_q == 2'd2 ? 2'd0 : completed_owner_q + 2'd1]);
        cover (others_completed_q[0] == 2'd2 && master_accept[0]);
        cover (others_completed_q[1] == 2'd2 && master_accept[1]);
        cover (others_completed_q[2] == 2'd2 && master_accept[2]);
    end
endmodule
