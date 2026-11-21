`include "src/common/rtl/pkg/macros.vh"

module n2d_memory_arbiter (
    input  logic         clk_i,
    input  logic         reset_i,

    input  logic [2:0]   master_request_valid_i,
    // A valid request marked prefetch competes only when no other master
    // presents a demand request (D-cache next-line prefetch, wiki/src/nand2doom/rtl/memory/mas.md).
    input  logic [2:0]   master_request_prefetch_i,
    output logic [2:0]   master_request_ready_o,
    input  logic [2:0]   master_request_write_i,
    input  logic [77:0]  master_request_address_i,
    input  logic [383:0] master_request_write_data_i,
    input  logic [47:0]  master_request_byte_enable_i,
    output logic [2:0]   master_response_valid_o,
    output logic [383:0] master_response_data_o,
    output logic [2:0]   master_response_error_o,

    output logic         memory_request_valid_o,
    input  logic         memory_request_ready_i,
    output logic         memory_request_write_o,
    output logic [25:0]  memory_request_address_o,
    output logic [127:0] memory_request_write_data_o,
    output logic [15:0]  memory_request_byte_enable_o,
    input  logic         memory_response_valid_i,
    input  logic [127:0] memory_response_data_i,
    input  logic         memory_response_error_i
);
    logic [1:0] next_owner_q;
    logic [1:0] selected_owner;
    logic       selected_valid;
    logic [1:0] owner_q;
    logic       busy_q;
    logic [1:0] next_owner_d;
    logic [1:0] owner_d;
    logic       busy_d;
    // Why: a presented request that the controller stalls keeps its selection,
    // so a higher-priority master arriving later cannot change the stalled
    // downstream payload (masters hold their requests until accepted).
    logic       stalled_q;
    logic       stalled_d;
    logic [1:0] stalled_owner_q;
    logic [1:0] stalled_owner_d;
    // Ref: wiki/src/nand2doom/rtl/memory/mas.md "Requirements" MEM-R6: prefetches
    // have lowest priority, so new-selection candidates are demand requests
    // first and prefetches only when no demand is valid. A stalled selection is
    // kept either way.
    logic [2:0] demand_valid;
    logic [2:0] candidate_valid;
    assign demand_valid = master_request_valid_i & ~master_request_prefetch_i;
    assign candidate_valid = (|demand_valid) ? demand_valid : master_request_valid_i;

    always_comb begin
        selected_valid = 1'b0;
        selected_owner = next_owner_q;
        if (stalled_q) begin
            selected_valid = master_request_valid_i[stalled_owner_q];
            selected_owner = stalled_owner_q;
        end else case (next_owner_q)
            2'd0: begin
                if (candidate_valid[0]) begin selected_valid = 1'b1; selected_owner = 2'd0; end
                else if (candidate_valid[1]) begin selected_valid = 1'b1; selected_owner = 2'd1; end
                else if (candidate_valid[2]) begin selected_valid = 1'b1; selected_owner = 2'd2; end
            end
            2'd1: begin
                if (candidate_valid[1]) begin selected_valid = 1'b1; selected_owner = 2'd1; end
                else if (candidate_valid[2]) begin selected_valid = 1'b1; selected_owner = 2'd2; end
                else if (candidate_valid[0]) begin selected_valid = 1'b1; selected_owner = 2'd0; end
            end
            default: begin
                if (candidate_valid[2]) begin selected_valid = 1'b1; selected_owner = 2'd2; end
                else if (candidate_valid[0]) begin selected_valid = 1'b1; selected_owner = 2'd0; end
                else if (candidate_valid[1]) begin selected_valid = 1'b1; selected_owner = 2'd1; end
            end
        endcase
    end

    always_comb begin
        master_request_ready_o = 3'b000;
        master_response_valid_o = 3'b000;
        master_response_data_o = '0;
        master_response_error_o = 3'b000;
        memory_request_valid_o = !busy_q && selected_valid;
        memory_request_write_o = master_request_write_i[selected_owner];
        memory_request_address_o = master_request_address_i[selected_owner*26 +: 26];
        memory_request_write_data_o = master_request_write_data_i[selected_owner*128 +: 128];
        memory_request_byte_enable_o = master_request_byte_enable_i[selected_owner*16 +: 16];
        if (!busy_q && selected_valid) begin
            master_request_ready_o[selected_owner] = memory_request_ready_i;
        end
        if (busy_q && memory_response_valid_i) begin
            master_response_valid_o[owner_q] = 1'b1;
            master_response_data_o[owner_q*128 +: 128] = memory_response_data_i;
            master_response_error_o[owner_q] = memory_response_error_i;
        end
    end

    // Ref: wiki/src/nand2doom/rtl/memory/mas.md "Requirements" MEM-R6: one grant
    // per outstanding line transaction; the rotating priority pointer advances
    // past the owner when its response returns, so each master is accepted at
    // least once per three completions.
    always_comb begin
        next_owner_d = next_owner_q;
        owner_d = owner_q;
        busy_d = busy_q;
        stalled_d = !busy_q && memory_request_valid_o && !memory_request_ready_i;
        stalled_owner_d = selected_owner;
        if (!busy_q && memory_request_valid_o && memory_request_ready_i) begin
            owner_d = selected_owner;
            busy_d = 1'b1;
        end
        if (busy_q && memory_response_valid_i) begin
            busy_d = 1'b0;
            next_owner_d = (owner_q == 2'd2) ? 2'd0 : owner_q + 2'd1;
        end
    end

    `N2A_DFF_RST(next_owner_q, next_owner_d, clk_i, reset_i)
    `N2A_DFF_RST(owner_q, owner_d, clk_i, reset_i)
    `N2A_DFF_RST(busy_q, busy_d, clk_i, reset_i)
    `N2A_DFF_RST(stalled_q, stalled_d, clk_i, reset_i)
    `N2A_DFF_RST(stalled_owner_q, stalled_owner_d, clk_i, reset_i)

    `N2A_ASSERT_CLOCKED(clk_i, reset_i, $onehot0(master_request_ready_o))
    `N2A_ASSERT_CLOCKED(clk_i, reset_i, $onehot0(master_response_valid_o))
    `N2A_ASSERT_CLOCKED_WHEN(clk_i, reset_i, busy_q, !memory_request_valid_o)
    `N2A_ASSERT_CLOCKED_WHEN(clk_i, reset_i, stalled_q, master_request_valid_i[stalled_owner_q])
endmodule
