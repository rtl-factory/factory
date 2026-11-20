// Pipeline control (Q101H..Q105H steering and debug drain)
//----------------------------------------------------------
// 1. Detect the Q102H hazards: a Q103H load or multiply producing a source
//    (one bubble), a divide still iterating, D admission (request valid
//    but not ready), and serialization of SYSTEM-class work.
// 2. Serialize CSR access, ECALL/EBREAK/MRET and every Q102H exception
//    (illegal, fetch-faulted, misaligned access or target): they commit in
//    Q102H only once Q103H..Q105H are empty, so traps are precise and CSR and
//    counter reads are exact without counter hazard logic.
// 3. Issue a data request from Q102H only when Q103H owes no response or
//    its response arrives on this edge without error; never speculatively.
// 4. Own flushes and redirects: a Q103H data-access trap, then a Q102H
//    redirect (a control transfer fetch did not predict, a predicted-taken
//    branch that falls through, MRET or trap), then halt entry, and finally
//    the early redirect n2d_if asks for a Q101H slot (a static JAL,
//    backward-branch or return prediction, or a rejected fetch-BTB
//    prediction) on the edge it enters Q102H; a slot whose fetch-BTB
//    prediction n2d_if accepted needs none. A trap also clears the
//    return-address stack in n2d_if, and every branch, JAL or JALR that
//    commits without a trap trains the fetch BTB.
// 5. Run the debug drain: a halt request stops fetch and discards Q101H;
//    Q102H..Q105H and any owed fetch response finish before halt_ack_o.
//    Resume restarts fetch at the architectural next PC (or zero after a
//    Reset-PC request while halted).
//----------------------------------------------------------
// Control owns whether a slot is valid, stalled, flushed, or committed.
// Datapath modules own the values themselves.
//----------------------------------------------------------

`include "src/common/rtl/pkg/macros.vh"

module n2d_ctrl (
    input  logic                      clk_i,
    input  logic                      rst_i,
    input  logic                      enable_i,
    input  logic                      halt_request_i,
    input  logic                      reset_pc_request_i,
    input  logic                      resume_i,

    // Shared stage records: control reads only valid bits and hazard fields.
    /* verilator lint_off UNUSEDSIGNAL */
    input  n2d_core_pkg::t_fetch_slot slot_Q101H_i,
    input  logic                      fetch_owed_i,
    // A fetch request is presented and not accepted (PMON attribution only)
    input  logic                      fetch_request_stalled_i,
    input  n2d_core_pkg::t_fetch_slot slot_Q102H_i,
    input  n2d_core_pkg::t_dec        dec_Q102H_i,
    input  logic                      exception_Q102H_i,
    // Resolved control transfer, and whether fetch must leave its path
    input  logic                      taken_Q102H_i,
    input  logic                      redirect_Q102H_i,
    input  logic [31:0]               next_pc_Q102H_i,
    input  n2d_core_pkg::t_q103       q103_i,
    input  logic                      hold_Q103H_i,
    input  logic                      trap_Q103H_i,
    input  logic                      q104_valid_i,
    input  n2d_core_pkg::t_retire     q105_i,
    /* verilator lint_on UNUSEDSIGNAL */
    input  logic                      div_busy_i,
    input  logic                      div_result_valid_i,
    input  logic [31:0]               mtvec_i,
    input  logic                      predict_redirect_Q101H_i,
    input  logic [31:0]               predict_target_Q101H_i,
    input  logic                      data_request_ready_i,

    // Fetch steering
    output logic                      fetch_allow_o,
    output logic                      if_flush_o,
    output logic                      if_redirect_valid_o,
    output logic [31:0]               if_redirect_pc_o,
    output logic                      take_Q101H_o,
    output logic                      ras_clear_o,
    output logic                      btb_update_o,

    // Q102H steering
    output logic                      advance_Q102H_o,
    output logic                      data_request_valid_o,
    output logic                      trap_commit_Q102H_o,
    output logic                      csr_commit_Q102H_o,
    output logic                      mret_commit_Q102H_o,
    output logic                      mul_issue_o,
    output logic                      div_issue_o,
    output logic                      div_consume_o,
    output logic                      div_kill_o,

    // Core-owned performance-monitor events by ID (INSTRET is formed by the
    // top level from the retirement and commit terms)
    output logic [127:0]              pmon_events_o,

    output logic                      halt_ack_o
);
    import n2d_core_pkg::*;

    localparam t_ctrl_regs CTRL_RESET = '{state: RUN_STATE_RUN, arch_pc: 32'h0000_0000};

    t_ctrl_regs ctrl_q, ctrl_d;

    logic slot_valid;
    logic memory_op;
    logic divide_op;
    logic serialize_Q102H;
    logic younger_empty;
    logic source_hazard_Q102H;
    logic redirect_commit_Q102H;
    logic late_redirect;
    logic late_flush;
    logic predict_redirect_Q101H;
    logic halt_enter;
    logic resume_now;
    logic resume_reset_pc;
    logic drain_abandon;
    logic drained;
    // PMON-only fetch-refill attribution (no functional consumer)
    logic ifetch_wait;
    logic mispredict_commit_Q102H;
    t_fetch_refill refill_q, refill_d;

    // Hazards and the data request never depend on data ready, so they live
    // in their own process: a device whose ready depends on valid forms no
    // false combinational loop through the advance logic below.
    always_comb begin
        slot_valid = slot_Q102H_i.valid;
        memory_op = (dec_Q102H_i.is_load || dec_Q102H_i.is_store) && !exception_Q102H_i;
        divide_op = dec_Q102H_i.is_div && !exception_Q102H_i;
        serialize_Q102H = exception_Q102H_i || dec_Q102H_i.is_csr || dec_Q102H_i.is_mret;
        younger_empty = !q103_i.valid && !q104_valid_i && !q105_i.valid;

        // Load-use and MUL-use: the Q103H result forms only on the next edge.
        source_hazard_Q102H = slot_valid && q103_i.valid && q103_i.rd_write &&
            (q103_i.is_load || q103_i.is_mul) &&
            ((dec_Q102H_i.uses_rs1 && (dec_Q102H_i.rs1 == q103_i.rd)) ||
             (dec_Q102H_i.uses_rs2 && (dec_Q102H_i.rs2 == q103_i.rd)));

        data_request_valid_o = !rst_i && enable_i && slot_valid && memory_op &&
            !source_hazard_Q102H && !hold_Q103H_i && !trap_Q103H_i;
    end

    always_comb begin
        div_issue_o = slot_valid && divide_op && !source_hazard_Q102H && !trap_Q103H_i &&
            !div_busy_i && !div_result_valid_i;
        div_kill_o = trap_Q103H_i;

        advance_Q102H_o = 1'b0;
        if (slot_valid && !hold_Q103H_i && !trap_Q103H_i && !source_hazard_Q102H) begin
            if (serialize_Q102H) advance_Q102H_o = younger_empty;
            else if (memory_op) advance_Q102H_o = data_request_ready_i && data_request_valid_o;
            else if (divide_op) advance_Q102H_o = div_result_valid_i;
            else advance_Q102H_o = 1'b1;
        end

        trap_commit_Q102H_o = advance_Q102H_o && exception_Q102H_i;
        csr_commit_Q102H_o = advance_Q102H_o && dec_Q102H_i.is_csr && !exception_Q102H_i;
        mret_commit_Q102H_o = advance_Q102H_o && dec_Q102H_i.is_mret && !exception_Q102H_i;
        mul_issue_o = advance_Q102H_o && dec_Q102H_i.is_mul && !exception_Q102H_i;
        div_consume_o = advance_Q102H_o && divide_op;
        redirect_commit_Q102H = advance_Q102H_o && (exception_Q102H_i || redirect_Q102H_i);
    end

    always_comb begin
        halt_enter = (ctrl_q.state == RUN_STATE_RUN) && halt_request_i;
        drained = !slot_valid && younger_empty && !fetch_owed_i;
        // A drain whose request was withdrawn restarts like a resume.
        drain_abandon = (ctrl_q.state == RUN_STATE_DRAIN) && drained && !halt_request_i;
        resume_now = ((ctrl_q.state == RUN_STATE_HALTED) && resume_i) || drain_abandon;
        resume_reset_pc = (ctrl_q.state == RUN_STATE_HALTED) && reset_pc_request_i;

        // Redirect priority: resume, Q103H data trap, Q102H redirect. Each
        // of these (and halt entry) also blocks the Q101H take, so the early
        // predicted redirect below is exclusive with them by construction
        // and never depends on itself through the take.
        late_redirect = resume_now || trap_Q103H_i || redirect_commit_Q102H;
        late_flush = late_redirect || halt_enter;

        fetch_allow_o = (ctrl_q.state == RUN_STATE_RUN) && !halt_request_i;
        take_Q101H_o = fetch_allow_o && slot_Q101H_i.valid && !late_flush &&
            (!slot_valid || advance_Q102H_o);
        // The slot entering Q102H needs fetch moved to its Q101H target now.
        predict_redirect_Q101H = take_Q101H_o && predict_redirect_Q101H_i;

        if_redirect_valid_o = 1'b1;
        if (resume_now) if_redirect_pc_o = resume_reset_pc ? 32'h0000_0000 : ctrl_q.arch_pc;
        else if (trap_Q103H_i) if_redirect_pc_o = mtvec_i;
        else if (redirect_commit_Q102H)
            if_redirect_pc_o = exception_Q102H_i ? mtvec_i : next_pc_Q102H_i;
        else if (predict_redirect_Q101H) if_redirect_pc_o = predict_target_Q101H_i;
        else begin
            if_redirect_valid_o = 1'b0;
            if_redirect_pc_o = 32'h0000_0000;
        end
        if_flush_o = late_flush || predict_redirect_Q101H;
        // The Q102H slot is discarded by a Q103H trap or traps itself; either
        // may have updated the return-address stack as it was taken.
        ras_clear_o = trap_Q103H_i || trap_commit_Q102H_o;
        // A committed, non-trapping branch, JAL or JALR trains the fetch BTB
        // with its resolved direction and next PC. advance_Q102H excludes a
        // Q103H trap, and the instruction cannot be discarded after it
        // leaves Q102H (it has no memory access to fault).
        btb_update_o = advance_Q102H_o && !exception_Q102H_i &&
            (dec_Q102H_i.is_branch || dec_Q102H_i.is_jal || dec_Q102H_i.is_jalr);
    end

    always_comb begin
        ctrl_d = ctrl_q;
        if (q105_i.valid) ctrl_d.arch_pc = q105_i.next_pc;
        case (ctrl_q.state)
            RUN_STATE_RUN: if (halt_request_i) ctrl_d.state = RUN_STATE_DRAIN;
            RUN_STATE_DRAIN: begin
                if (drain_abandon) ctrl_d.state = RUN_STATE_RUN;
                else if (drained) ctrl_d.state = RUN_STATE_HALTED;
            end
            RUN_STATE_HALTED: begin
                if (reset_pc_request_i) ctrl_d.arch_pc = 32'h0000_0000;
                if (resume_i) ctrl_d.state = RUN_STATE_RUN;
            end
            default: ctrl_d.state = RUN_STATE_HALTED;
        endcase

        halt_ack_o = ctrl_q.state == RUN_STATE_HALTED;
    end

    `N2A_DFF_RST_VAL(ctrl_q, ctrl_d, clk_i, rst_i, CTRL_RESET)

    // Fetch-refill attribution: the flush cause of this edge, so the next
    // clock's IFETCH_WAIT can name it. A Q102H mispredict outranks the
    // predicted redirect of the same slot one clock earlier, which is how a
    // wrong prediction's bubble becomes the first of its two refill clocks.
    always_comb begin
        // Execute will be empty next clock for lack of a fetched instruction:
        // nothing arrived, or a flush discarded what had (redirect refill).
        ifetch_wait = fetch_allow_o && (!slot_valid || advance_Q102H_o) && !take_Q101H_o;
        mispredict_commit_Q102H = advance_Q102H_o && !exception_Q102H_i && redirect_Q102H_i &&
            !dec_Q102H_i.is_mret;
        if (mispredict_commit_Q102H) refill_d = FETCH_REFILL_MISPREDICT;
        else if (late_redirect) refill_d = FETCH_REFILL_REDIRECT;
        else if (predict_redirect_Q101H) refill_d = FETCH_REFILL_PREDICT;
        else refill_d = FETCH_REFILL_NONE;
    end

    `N2A_DFF_RST_VAL(refill_q, refill_d, clk_i, rst_i, FETCH_REFILL_NONE)

    // Passive PMON observations; no event feeds functional control. Hold
    // events exclude clocks already explained by a Q103H memory hold.
    always_comb begin
        pmon_events_o = 128'b0;
        pmon_events_o[1] = 1'b1;
        pmon_events_o[3] = ifetch_wait;
        pmon_events_o[4] = hold_Q103H_i;
        pmon_events_o[5] = source_hazard_Q102H && q103_i.is_load && !hold_Q103H_i;
        pmon_events_o[6] = div_busy_i;
        pmon_events_o[8] = data_request_valid_o && !data_request_ready_i;
        pmon_events_o[9] = advance_Q102H_o && !exception_Q102H_i && taken_Q102H_i &&
            !dec_Q102H_i.is_mret;
        pmon_events_o[10] = trap_commit_Q102H_o || trap_Q103H_i;
        pmon_events_o[68] = slot_valid && serialize_Q102H && !younger_empty &&
            !hold_Q103H_i && !trap_Q103H_i;
        pmon_events_o[69] = source_hazard_Q102H && q103_i.is_mul && !hold_Q103H_i;
        pmon_events_o[76] = ctrl_q.state == RUN_STATE_HALTED;
        // A Q101H slot entering Q102H predicted taken (from fetch, or by a
        // Q101H redirect), and a resolved branch, JAL or JALR whose direction
        // differed from the fetched path (two refill clocks instead of one or
        // none). 92: a fetch-BTB prediction accepted with no refill clock;
        // 93: a fetch-BTB prediction Q101H rejected (one refill clock).
        pmon_events_o[78] = take_Q101H_o && slot_Q101H_i.predicted_taken;
        pmon_events_o[92] = take_Q101H_o && slot_Q101H_i.predicted_taken &&
            !predict_redirect_Q101H_i;
        pmon_events_o[93] = take_Q101H_o && slot_Q101H_i.fetch_taken && !slot_Q101H_i.fetch_error &&
            predict_redirect_Q101H_i;
        pmon_events_o[79] = mispredict_commit_Q102H;
        // IFETCH_WAIT by cause, exactly one per IFETCH_WAIT clock except the
        // unattributed remainder (nothing owed or presented). A flush on
        // this edge or the previous one names its refill clocks: a
        // mispredict (two clocks), any other late redirect (trap, MRET,
        // resume) and a correct prediction (one clock). Any other clock
        // waits on fetch supply: an owed response (I-cache miss or fill,
        // including a stale wrong-path one) or an unaccepted request.
        pmon_events_o[84] = ifetch_wait && (mispredict_commit_Q102H ||
            (!late_redirect && refill_q == FETCH_REFILL_MISPREDICT));
        pmon_events_o[85] = ifetch_wait && !mispredict_commit_Q102H && (late_redirect ||
            refill_q == FETCH_REFILL_REDIRECT);
        pmon_events_o[83] = ifetch_wait && !late_redirect && refill_q == FETCH_REFILL_PREDICT;
        pmon_events_o[86] = ifetch_wait && !late_redirect && refill_q == FETCH_REFILL_NONE &&
            (fetch_owed_i || fetch_request_stalled_i);
    end

    // Halted means drained: nothing in flight and no observation owed.
    `N2A_ASSERT(ctrl_halted_is_drained, clk_i, rst_i,
        (ctrl_q.state != RUN_STATE_HALTED) || drained)
    `N2A_ASSERT_NEVER(ctrl_q102_trap_and_q103_trap, clk_i, rst_i,
        trap_commit_Q102H_o && trap_Q103H_i)
    `N2A_ASSERT_NEVER(ctrl_predict_redirect_with_late_flush, clk_i, rst_i,
        predict_redirect_Q101H && late_flush)
    // The IFETCH_WAIT causes are exclusive and each lies inside IFETCH_WAIT.
    `N2A_ASSERT(ctrl_ifetch_causes_exclusive, clk_i, rst_i,
        $onehot0(pmon_events_o[86:83]) && (ifetch_wait || pmon_events_o[86:83] == 4'b0))
endmodule
