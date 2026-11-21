// Fetch stage (Q100H) and two-entry instruction queue (Q101H)
//----------------------------------------------------------
// 1. Own the fetch PC and issue one word-aligned instruction request.
// 2. Track the single owed fetch response; a flush (redirect, predicted
//    redirect, trap, halt or resume) while it is owed marks it stale, so it
//    is dropped however many flushes happen before it arrives.
// 3. Queue responses in a two-entry skid buffer, because responses have no
//    ready. An empty queue bypasses a current response straight to
//    the Q101H slot so a one-edge hit reaches Q102H with no extra stage.
//    The Q101H slot never depends on this cycle's flush; n2d_ctrl gates
//    the take instead.
// 4. Issue only when every response already owed, plus the new one, has a
//    queue entry after this edge even if Q102H takes nothing more.
// 5. Predict at fetch with a direct-mapped branch target buffer in one
//    simple-dual-port RAM, read with the next fetch PC so its entry for the
//    current fetch PC is ready in the clock that PC is requested. An entry
//    predicted taken (a two-bit counter at least two, or a return entry
//    while the return-address stack holds one) makes the fetch after the
//    transfer its target, so a correct prediction costs no refill clock.
//    The prediction, the entry's counter and its hit travel with the
//    fetched word through the queue. A reset sweep writes every entry
//    invalid; no prediction is made until it completes.
// 6. Check the Q101H slot. A fetch prediction is accepted when the word is
//    a transfer whose target it matches: the JAL or branch target, the
//    return-address-stack entry of a return while the stack holds one, or
//    any target of another JALR. Otherwise (a stale or aliased entry, or no
//    fetch prediction) the slot is predicted statically: a JAL or backward
//    branch with a word-aligned target, or a return while the stack holds
//    an entry. A static prediction, or a rejected fetch prediction, asks
//    n2d_ctrl to redirect fetch on the edge the slot enters Q102H, to the
//    static target or, for a rejected one predicted not taken, to pc + 4:
//    one refill clock. A fetch error is never predicted and never
//    redirected (Q102H traps).
// 7. Keep the return-address stack. Taking a JAL or JALR with rd = ra into
//    Q102H pushes pc + 4; taking a return (exactly JALR x0, 0(ra)) while the
//    stack is not empty pops. The stack is a hint: Q102H checks every
//    predicted target, so stale or overwritten entries cost a mispredict,
//    never correctness. It wraps on overflow and a trap clears it
//    (ras_clear_i). RAS_DEPTH = 0 removes it.
// 8. Train the BTB from Q102H (n2d_ctrl): a committed, non-trapping branch,
//    JAL or JALR writes its entry. Taken: the resolved target, the return
//    flag, and the counter incremented from the one seen at fetch (two on
//    a new entry). Not taken after a hit: the counter decremented, the
//    target kept (a taken counter's target is the accepted fetch target the
//    slot carries). A not-taken miss writes nothing, and a fetch prediction
//    Q101H rejected trains as a miss, so an alias never keeps a taken
//    counter with another site's target. BTB_ENTRIES = 0 removes
//    the buffer and leaves static and return prediction.
//----------------------------------------------------------
// The request may be withdrawn or its address changed only while it has not
// been accepted and only because of a flush (redirect, trap, halt or
// resume) or a fetch stop; a new request may be accepted on the edge that
// samples the previous response.
//----------------------------------------------------------

`include "src/common/rtl/pkg/macros.vh"

module n2d_if #(
    // Return-address stack entries: 0 (disabled) or a power of two >= 2
    parameter int RAS_DEPTH = n2d_core_pkg::RAS_DEPTH_DEFAULT,
    // Fetch branch target buffer entries: 0 (disabled) or a power of two
    // from 2 to 2048 (36-bit entries; 256 per M9K)
    parameter int BTB_ENTRIES = n2d_core_pkg::BTB_ENTRIES_DEFAULT
) (
    input  logic                      clk_i,
    input  logic                      rst_i,
    input  logic                      enable_i,
    // RUN state without a halt request: new fetches may issue
    input  logic                      fetch_allow_i,
    // Drop the queue and every owed response (mark it stale)
    input  logic                      flush_i,
    // Load the fetch PC (a flush always accompanies a redirect)
    input  logic                      redirect_valid_i,
    input  logic [31:0]               redirect_pc_i,
    // Q102H takes the Q101H slot on this edge
    input  logic                      take_i,
    // Empty the return-address stack (a trap discarded or is the Q102H slot)
    input  logic                      ras_clear_i,
    // A branch, JAL or JALR commits in Q102H without a trap: train the BTB
    // from its slot (PC, instruction, fetch-time entry) and its resolution
    input  logic                      btb_update_i,
    input  n2d_core_pkg::t_fetch_slot btb_update_slot_i,
    input  logic                      btb_update_taken_i,
    input  logic [31:0]               btb_update_target_i,

    output logic                      instruction_request_valid_o,
    input  logic                      instruction_request_ready_i,
    output logic [31:0]               instruction_request_address_o,
    input  logic                      instruction_response_valid_i,
    input  logic [31:0]               instruction_response_data_i,
    input  logic                      instruction_response_error_i,

    output n2d_core_pkg::t_fetch_slot slot_Q101H_o,
    // The Q101H slot needs fetch redirected to predict_target_Q101H_o if it
    // is taken: a static prediction or a rejected fetch prediction
    output logic                      predict_redirect_Q101H_o,
    output logic [31:0]               predict_target_Q101H_o,
    // An accepted fetch whose response has not arrived (halt must wait)
    output logic                      fetch_owed_o
);
    import n2d_core_pkg::*;

    localparam t_if_regs IF_RESET = '{
        pc: 32'h0000_0000,
        outstanding: 1'b0,
        outstanding_stale: 1'b0,
        outstanding_pc: 32'h0000_0000,
        outstanding_fetch_taken: 1'b0,
        outstanding_btb_hit: 1'b0,
        outstanding_btb_counter: 2'b00,
        outstanding_target: 32'h0000_0000,
        entry0: '0,
        entry1: '0};

    t_if_regs if_q, if_d;

    logic                      response_now;
    logic                      response_current;
    n2d_core_pkg::t_fetch_slot response_slot;
    logic                      pop;
    logic                      bypass;
    logic                      push;
    logic [1:0]                queued_count;
    logic [1:0]                queued_after;
    logic                      request_valid;
    logic                      request_accept;
    n2d_core_pkg::t_fetch_slot entry0_after_pop;
    n2d_core_pkg::t_fetch_slot entry1_after_pop;
    n2d_core_pkg::t_fetch_slot head_slot;
    logic                      head_usable;
    logic                      head_jal;
    logic                      head_branch;
    logic                      head_jalr;
    logic [31:0]               direct_target;
    logic                      static_aligned;
    logic                      static_taken;
    logic [31:0]               static_target;
    logic                      fetch_accept;
    logic                      head_call;
    logic                      head_return;
    logic                      ras_push;
    logic                      ras_pop;
    logic                      ras_valid;
    logic [31:0]               ras_top_target;
    logic                      btb_hit;
    logic                      btb_taken;
    logic [1:0]                btb_counter;
    logic [31:0]               btb_target;

    always_comb begin
        response_now = instruction_response_valid_i && if_q.outstanding;
        response_current = response_now && !if_q.outstanding_stale;

        response_slot = '0;
        response_slot.valid = response_current;
        response_slot.pc = if_q.outstanding_pc;
        response_slot.instruction = instruction_response_data_i;
        response_slot.fetch_error = instruction_response_error_i;
        response_slot.fetch_taken = if_q.outstanding_fetch_taken;
        response_slot.btb_hit = if_q.outstanding_btb_hit;
        response_slot.btb_counter = if_q.outstanding_btb_counter;
        response_slot.predicted_target = if_q.outstanding_target;

        head_slot = if_q.entry0.valid ? if_q.entry0 : response_slot;
    end

    // Why: checking or forming the head's prediction is a separate process from
    // the head itself because the return-address stack lookup feeds it.
    always_comb begin
        head_usable = head_slot.valid && !head_slot.fetch_error;
        head_jal = head_slot.instruction[6:0] == OPCODE_JAL;
        head_branch = head_slot.instruction[6:0] == OPCODE_BRANCH;
        head_jalr = head_slot.instruction[6:0] == OPCODE_JALR;
        direct_target = head_slot.pc +
            (head_jal ? imm_j(head_slot.instruction) : imm_b(head_slot.instruction));
        // Why: only ra links and only the canonical return pops, so other JALR uses
        // never disturb the stack. A return with an empty stack has no stacked
        // target, so it is not predicted statically.
        head_call = head_usable && (head_jal || head_jalr) &&
            (head_slot.instruction[11:7] == REG_RA);
        head_return = head_usable && (head_slot.instruction == INSN_RET) && ras_valid;

        // Why: a backward conditional branch is usually a loop back-edge, so it and
        // JAL are predicted taken, as is a return from the stack. The target must be
        // word aligned (fetch PCs are, so bit 1 of the immediate decides) because a
        // misaligned target traps in Q102H instead.
        static_aligned = head_jal ? !head_slot.instruction[21] : !head_slot.instruction[8];
        static_taken = head_return ||
            (head_usable && (head_jal || (head_branch && head_slot.instruction[31])) &&
             static_aligned);
        static_target = head_return ? ras_top_target : direct_target;

        // Why: Q101H can confirm only the direct target, the stacked return target,
        // or (for any other JALR, or a return with an empty stack) the buffered one,
        // so a fetch prediction stands only when its target is that one.
        fetch_accept = head_usable && head_slot.fetch_taken && (
            (head_jal || head_branch) ? (head_slot.predicted_target == direct_target) :
            head_jalr ? (!head_return || (head_slot.predicted_target == ras_top_target)) :
            1'b0);

        slot_Q101H_o = head_slot;
        if (fetch_accept) begin
            slot_Q101H_o.predicted_taken = 1'b1;
            predict_redirect_Q101H_o = 1'b0;
            predict_target_Q101H_o = head_slot.predicted_target;
        end else begin
            slot_Q101H_o.predicted_taken = static_taken;
            slot_Q101H_o.predicted_target = static_taken ? static_target : 32'b0;
            // Why: clearing btb_hit makes a rejected entry (an alias or stale target)
            // retrain as a miss instead of strengthening its counter.
            if (head_slot.fetch_taken) slot_Q101H_o.btb_hit = 1'b0;
            predict_redirect_Q101H_o = static_taken || (head_usable && head_slot.fetch_taken);
            predict_target_Q101H_o = static_taken ? static_target : head_slot.pc + 32'd4;
        end
    end

    // Why: queue movement and issue depend on take_i, which n2d_ctrl derives
    // from the Q101H slot above, so they need their own process.
    always_comb begin

        pop = take_i && if_q.entry0.valid;
        ras_push = take_i && head_call;
        ras_pop = take_i && head_return;
        bypass = take_i && !if_q.entry0.valid;
        push = response_current && !flush_i && !bypass;

        queued_count = {1'b0, if_q.entry0.valid} + {1'b0, if_q.entry1.valid};
        queued_after = queued_count + {1'b0, push} - {1'b0, pop};

        // Why: one outstanding fetch, accepted only when none is owed or the owed one
        // responds on this edge, and only when its response will find a free queue
        // entry without relying on a later take, so a response always has an entry
        // to land in.
        request_valid = !rst_i && enable_i && fetch_allow_i && !flush_i &&
            (!if_q.outstanding || response_now) && (queued_after <= 2'd1);
        request_accept = request_valid && instruction_request_ready_i;

        instruction_request_valid_o = request_valid;
        instruction_request_address_o = if_q.pc;
        fetch_owed_o = if_q.outstanding;

        if_d = if_q;

        entry0_after_pop = pop ? if_q.entry1 : if_q.entry0;
        entry1_after_pop = pop ? '0 : if_q.entry1;
        if (flush_i) begin
            if_d.entry0 = '0;
            if_d.entry1 = '0;
        end else begin
            if_d.entry0 = entry0_after_pop;
            if_d.entry1 = entry1_after_pop;
            if (push) begin
                if (!entry0_after_pop.valid) if_d.entry0 = response_slot;
                else if_d.entry1 = response_slot;
            end
        end

        if (request_accept) begin
            if_d.outstanding = 1'b1;
            if_d.outstanding_stale = 1'b0;
            if_d.outstanding_pc = if_q.pc;
            if_d.outstanding_fetch_taken = btb_taken;
            if_d.outstanding_btb_hit = btb_hit;
            if_d.outstanding_btb_counter = btb_counter;
            if_d.outstanding_target = btb_target;
        end else if (response_now) begin
            if_d.outstanding = 1'b0;
            if_d.outstanding_stale = 1'b0;
        end else if (flush_i && if_q.outstanding) begin
            // Why: sticky, because a predicted redirect and the Q102H mispredict of the
            // same branch can both flush before one slow response arrives.
            if_d.outstanding_stale = 1'b1;
        end

        if (redirect_valid_i) if_d.pc = redirect_pc_i;
        else if (request_accept) if_d.pc = btb_taken ? btb_target : if_q.pc + 32'd4;

    end

    `N2A_DFF_RST_VAL(if_q, if_d, clk_i, rst_i, IF_RESET)

    // Why: the return-address stack is circular so a push when full overwrites
    // the oldest entry, losing only the outermost return of a deep call chain;
    // count says how many entries are live.
    generate
    if (RAS_DEPTH > 0) begin : g_ras
        localparam int INDEX_BITS = $clog2(RAS_DEPTH);

        // Why: plain sized signals, no typedef: Quartus Standard sizes a packed
        // struct declared in a generate scope without the local INDEX_BITS.
        logic [INDEX_BITS-1:0] ras_top_q, ras_top_d;
        logic [INDEX_BITS:0]   ras_count_q, ras_count_d;  // Why: 0..RAS_DEPTH needs one more bit than the index
        logic [31:0] entry_q [RAS_DEPTH];
        logic [31:0] entry_d [RAS_DEPTH];
        logic [INDEX_BITS-1:0] push_index;

        // Why: lookup and update are separate processes: the update depends on the
        // take, which depends on the lookup through the prediction.
        always_comb begin
            ras_valid = ras_count_q != '0;
            ras_top_target = entry_q[ras_top_q];
        end

        always_comb begin
            push_index = ras_top_q + INDEX_BITS'(1);
            ras_top_d = ras_top_q;
            ras_count_d = ras_count_q;
            entry_d = entry_q;
            if (ras_clear_i) begin
                ras_count_d = '0;
            end else if (ras_push) begin
                entry_d[push_index] = head_slot.pc + 32'd4;
                ras_top_d = push_index;
                if (ras_count_q != (INDEX_BITS+1)'(RAS_DEPTH))
                    ras_count_d = ras_count_q + (INDEX_BITS+1)'(1);
            end else if (ras_pop) begin
                ras_top_d = ras_top_q - INDEX_BITS'(1);
                ras_count_d = ras_count_q - (INDEX_BITS+1)'(1);
            end
        end

        `N2A_DFF_RST(ras_top_q, ras_top_d, clk_i, rst_i)
        `N2A_DFF_RST(ras_count_q, ras_count_d, clk_i, rst_i)
        `N2A_DFF_ARRAY_EN(entry_q, entry_d, clk_i, ras_push)

        `N2A_ASSERT(if_ras_count_bounded, clk_i, rst_i,
            ras_count_q <= (INDEX_BITS+1)'(RAS_DEPTH))
    end else begin : g_no_ras
        assign ras_valid = 1'b0;
        assign ras_top_target = 32'b0;
        logic unused_ras;
        assign unused_ras = ^{ras_clear_i, ras_push, ras_pop};
    end
    endgenerate

    // Why: one t_btb_entry per index PC[INDEX+1:2], tagged by the next
    // BTB_TAG_BITS PC bits, in a simple-dual-port RAM whose registered read
    // address is the next fetch PC, so the entry is ready in the clock that PC
    // is fetched.
    generate
    if (BTB_ENTRIES > 0) begin : g_btb
        localparam int INDEX_BITS = $clog2(BTB_ENTRIES);
        localparam int TAG_LOW = INDEX_BITS + 2;

        // Why: plain sized signals, no generate-scope typedef (see g_ras).
        logic [INDEX_BITS-1:0] sweep_index_q, sweep_index_d;
        logic                  sweep_done_q, sweep_done_d;
        logic [INDEX_BITS-1:0] read_index;
        logic [INDEX_BITS-1:0] write_index;
        logic                  write;
        t_btb_entry            read_entry;
        t_btb_entry            write_entry;
        logic                  update_return;

        // Why: the RAM was addressed with if_d.pc on the edge that loaded if_q.pc,
        // so the entry read is the one for the PC requested in this clock.
        always_comb begin
            read_index = if_d.pc[TAG_LOW-1:2];
            btb_hit = sweep_done_q && read_entry.valid &&
                (read_entry.tag == if_q.pc[TAG_LOW +: BTB_TAG_BITS]);
            btb_taken = btb_hit && (read_entry.is_return ? ras_valid : read_entry.counter[1]);
            btb_counter = read_entry.counter;
            btb_target = (read_entry.is_return && btb_hit) ? ras_top_target :
                {{(30 - BTB_TARGET_BITS){1'b0}}, read_entry.target, 2'b00};
        end

        always_comb begin
            update_return = btb_update_slot_i.instruction == INSN_RET;
            write_entry = '0;
            write_entry.valid = 1'b1;
            write_entry.tag = btb_update_slot_i.pc[TAG_LOW +: BTB_TAG_BITS];
            if (btb_update_taken_i) begin
                write_entry.is_return = update_return;
                write_entry.target = btb_update_target_i[BTB_TARGET_BITS+1:2];
                if (!btb_update_slot_i.btb_hit) write_entry.counter = 2'b10;
                else if (btb_update_slot_i.btb_counter != 2'b11)
                    write_entry.counter = btb_update_slot_i.btb_counter + 2'b01;
                else write_entry.counter = 2'b11;
            end else begin
                // Why: a not-taken update keeps the fetch target the slot carries, so a
                // counter that still predicts taken keeps pointing where it did.
                write_entry.target = btb_update_slot_i.predicted_target[BTB_TARGET_BITS+1:2];
                write_entry.counter = (btb_update_slot_i.btb_counter == 2'b00) ? 2'b00 :
                    btb_update_slot_i.btb_counter - 2'b01;
            end
            write = btb_update_i && (btb_update_taken_i || btb_update_slot_i.btb_hit);
            write_index = btb_update_slot_i.pc[TAG_LOW-1:2];
            sweep_index_d = sweep_index_q;
            sweep_done_d = sweep_done_q;
            if (!sweep_done_q) begin
                write = 1'b1;
                write_index = sweep_index_q;
                write_entry = '0;
                sweep_index_d = sweep_index_q + INDEX_BITS'(1);
                sweep_done_d = sweep_index_q == INDEX_BITS'(BTB_ENTRIES - 1);
            end
        end

        `N2A_DFF_RST(sweep_index_q, sweep_index_d, clk_i, rst_i)
        `N2A_DFF_RST(sweep_done_q, sweep_done_d, clk_i, rst_i)

        n2a_sdp_ram #(.DATA_WIDTH($bits(t_btb_entry)), .DEPTH(BTB_ENTRIES)) u_table (
            .clk_i(clk_i),
            .write_enable_i(write),
            .write_address_i(write_index),
            .write_data_i(write_entry),
            .read_address_i(read_index),
            .read_data_o(read_entry)
        );

        // Why: PC bits outside the index, tag and word target are not stored; a
        // slot that never trains does not read its other fields.
        `N2A_UNUSED_BITS(unused_update_bits, {btb_update_slot_i.pc[31:TAG_LOW+BTB_TAG_BITS],
                                      btb_update_slot_i.pc[1:0],
                                      btb_update_target_i[31:BTB_TARGET_BITS+2],
                                      btb_update_target_i[1:0],
                                      btb_update_slot_i.predicted_target[31:BTB_TARGET_BITS+2],
                                      btb_update_slot_i.predicted_target[1:0],
                                      btb_update_slot_i.valid, btb_update_slot_i.fetch_error,
                                      btb_update_slot_i.fetch_taken,
                                      btb_update_slot_i.predicted_taken,
                                      if_q.pc[31:TAG_LOW+BTB_TAG_BITS], if_d.pc[31:TAG_LOW],
                                      if_d.pc[1:0]})

        // Why: a misaligned target traps in Q102H and never trains the buffer.
        `N2A_ASSERT(if_btb_target_aligned, clk_i, rst_i,
            !(btb_update_i && btb_update_taken_i) || (btb_update_target_i[1:0] == 2'b00))
    end else begin : g_no_btb
        assign btb_hit = 1'b0;
        assign btb_taken = 1'b0;
        assign btb_counter = 2'b00;
        assign btb_target = 32'b0;
        logic unused_btb;
        assign unused_btb = ^{btb_update_i, btb_update_slot_i, btb_update_taken_i,
                              btb_update_target_i, if_d.pc};
    end
    endgenerate

    `N2A_ASSERT(if_queue_no_overflow, clk_i, rst_i,
        !(push && entry0_after_pop.valid && entry1_after_pop.valid && !flush_i))
    `N2A_ASSERT(if_take_needs_slot, clk_i, rst_i, !take_i || slot_Q101H_o.valid)
    `N2A_ASSERT(if_redirect_flushes, clk_i, rst_i, !redirect_valid_i || flush_i)
    `N2A_ASSERT(if_fetch_pc_aligned, clk_i, rst_i, if_q.pc[1:0] == 2'b00)
    `N2A_ASSERT(if_predicted_target_aligned, clk_i, rst_i,
        !slot_Q101H_o.predicted_taken || (slot_Q101H_o.predicted_target[1:0] == 2'b00))
    `N2A_ASSERT(if_redirect_target_aligned, clk_i, rst_i,
        !(slot_Q101H_o.valid && predict_redirect_Q101H_o) || (predict_target_Q101H_o[1:0] == 2'b00))
    `N2A_ASSERT_PARAM(g_invalid_ras_depth, (RAS_DEPTH == 0) || ((RAS_DEPTH >= 2) &&
        ((RAS_DEPTH & (RAS_DEPTH - 1)) == 0)), "RAS_DEPTH must be 0 or a power of two >= 2")
    `N2A_ASSERT_PARAM(g_invalid_btb_entries, (BTB_ENTRIES == 0) || ((BTB_ENTRIES >= 2) &&
        (BTB_ENTRIES <= 2048) && ((BTB_ENTRIES & (BTB_ENTRIES - 1)) == 0)),
        "BTB_ENTRIES must be 0 or a power of two from 2 to 2048")
endmodule
