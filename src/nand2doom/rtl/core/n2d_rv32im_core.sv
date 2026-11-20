// nand2doom RV32IM core: six-stage in-order pipeline (Q100H..Q105H)
//----------------------------------------------------------
// Instruction/data request and response ports with the pipelined memory
// handshake, debug halt/resume/Reset-PC controls, the registered retirement
// observation record, and the performance-monitor event vectors.
//
//   Q100H  n2d_if    fetch PC and instruction request
//   Q101H  n2d_if    two-entry instruction queue / response bypass
//   Q102H  n2d_dec, n2d_exe, n2d_csr, n2d_muldiv (divide)
//          register read and forwarding, ALU, branch resolution, data
//          request, serialized SYSTEM work
//   Q103H  n2d_ma    data response, load align, data trap;
//                    n2d_muldiv multiply operand registers
//   Q104H            registered result; n2d_muldiv product register
//   Q105H  n2d_wb    register write and observation
//
// n2d_ctrl owns every valid, stall, flush, redirect and halt decision.
//----------------------------------------------------------

`include "src/common/rtl/pkg/macros.vh"

module n2d_rv32im_core #(
    // Fetch predictors (wiki/src/nand2doom/rtl/core/mas.md); 0 removes either one
    parameter int RAS_DEPTH = n2d_core_pkg::RAS_DEPTH_DEFAULT,
    parameter int BTB_ENTRIES = n2d_core_pkg::BTB_ENTRIES_DEFAULT
) (
    input  logic        clk_i,
    input  logic        rst_i,
    input  logic        enable_i,
    input  logic        halt_request_i,
    input  logic        reset_pc_request_i,
    input  logic        resume_i,

    output logic        instruction_request_valid_o,
    input  logic        instruction_request_ready_i,
    output logic [31:0] instruction_request_address_o,
    input  logic        instruction_response_valid_i,
    input  logic [31:0] instruction_response_data_i,
    input  logic        instruction_response_error_i,

    output logic        data_request_valid_o,
    input  logic        data_request_ready_i,
    output logic        data_request_write_o,
    output logic [31:0] data_request_address_o,
    output logic [31:0] data_request_write_data_o,
    output logic [3:0]  data_request_byte_enable_o,
    input  logic        data_response_valid_i,
    input  logic [31:0] data_response_read_data_i,
    input  logic        data_response_error_i,

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
    output logic        halt_ack_o,

    // Performance monitor: platform event sources, one bit per event ID
    // (wiki/src/nand2doom/rtl/core/mas.md "Performance monitor"), and the registered vector of all
    // legal events for direct observation by simulation harnesses.
    input  logic [127:0] pmon_platform_events_i,
    output logic [127:0] pmon_events_o
);
    import n2d_core_pkg::*;

    //----------------------------------------------------------
    // Stage registers
    //----------------------------------------------------------

    t_pipe_regs pipe_q, pipe_d;

    //----------------------------------------------------------
    // Internal signals
    //----------------------------------------------------------

    t_fetch_slot slot_Q101H;
    logic        fetch_owed;
    logic        fetch_allow;
    logic        if_flush;
    logic        if_redirect_valid;
    logic [31:0] if_redirect_pc;
    logic        take_Q101H;
    logic        ras_clear;
    logic        btb_update;
    logic        predict_redirect_Q101H;
    logic [31:0] predict_target_Q101H;

    t_dec        dec_Q102H;
    logic [4:0]  rf_rs1_index;
    logic [4:0]  rf_rs2_index;
    logic [31:0] rf_rs1_data;
    logic [31:0] rf_rs2_data;
    logic        rf_write_enable;
    logic [4:0]  rf_write_index;
    logic [31:0] rf_write_data;

    logic [31:0] rs1_value_Q102H;
    logic [31:0] rs2_value_Q102H;
    logic [31:0] csr_operand_Q102H;
    logic [31:0] result_Q102H;
    logic        taken_Q102H;
    logic        redirect_Q102H;
    logic [31:0] next_pc_Q102H;
    logic [31:0] mem_address_Q102H;
    logic [31:0] mem_write_data_Q102H;
    logic [3:0]  mem_mask_Q102H;
    logic        exception_Q102H;
    logic [31:0] exception_cause_Q102H;
    logic [31:0] exception_value_Q102H;

    logic        advance_Q102H;
    logic        trap_commit_Q102H;
    logic        csr_commit_Q102H;
    logic        mret_commit_Q102H;
    logic        mul_issue;
    logic        div_issue;
    logic        div_consume;
    logic        div_kill;
    logic        muldiv_issue;
    logic        div_busy;
    logic        div_result_valid;
    logic [31:0] div_result;
    logic [31:0] mul_result;

    logic [31:0] csr_read_data;
    logic        csr_legal;
    logic [31:0] mtvec;
    logic [31:0] mepc;
    logic        csr_trap_valid;
    logic [31:0] csr_trap_epc;
    logic [31:0] csr_trap_cause;
    logic [31:0] csr_trap_value;
    logic        instret_increment;
    logic [127:0] pmon_core_events;
    logic [127:0] pmon_all_events;
    logic [1:0]  csr_op_Q102H;

    logic        hold_Q103H;
    logic        trap_Q103H;
    logic [31:0] trap_cause_Q103H;
    t_retire     retire_Q104H;
    t_retire     q104_Q104H;
    t_q103       q103_next;

    //----------------------------------------------------------
    // Q100H/Q101H fetch and instruction queue
    //----------------------------------------------------------

    n2d_if #(
        .RAS_DEPTH(RAS_DEPTH),
        .BTB_ENTRIES(BTB_ENTRIES)
    ) u_if (
        .clk_i(clk_i),
        .rst_i(rst_i),
        .enable_i(enable_i),
        .fetch_allow_i(fetch_allow),
        .flush_i(if_flush),
        .redirect_valid_i(if_redirect_valid),
        .redirect_pc_i(if_redirect_pc),
        .take_i(take_Q101H),
        .ras_clear_i(ras_clear),
        .btb_update_i(btb_update),
        .btb_update_slot_i(pipe_q.slot_Q102H),
        .btb_update_taken_i(taken_Q102H),
        .btb_update_target_i(next_pc_Q102H),
        .instruction_request_valid_o(instruction_request_valid_o),
        .instruction_request_ready_i(instruction_request_ready_i),
        .instruction_request_address_o(instruction_request_address_o),
        .instruction_response_valid_i(instruction_response_valid_i),
        .instruction_response_data_i(instruction_response_data_i),
        .instruction_response_error_i(instruction_response_error_i),
        .slot_Q101H_o(slot_Q101H),
        .predict_redirect_Q101H_o(predict_redirect_Q101H),
        .predict_target_Q101H_o(predict_target_Q101H),
        .fetch_owed_o(fetch_owed)
    );

    //----------------------------------------------------------
    // Q102H decode, register read, execute
    //----------------------------------------------------------

    n2d_dec u_dec (
        .instruction_i(pipe_q.slot_Q102H.instruction),
        .dec_o(dec_Q102H)
    );

    n2a_rv32_regfile u_register_file (
        .clk_i(clk_i),
        .rst_i(rst_i),
        .read_a_index_i(rf_rs1_index),
        .read_a_data_o(rf_rs1_data),
        .read_b_index_i(rf_rs2_index),
        .read_b_data_o(rf_rs2_data),
        .write_enable_i(rf_write_enable),
        .write_index_i(rf_write_index),
        .write_data_i(rf_write_data)
    );

    n2d_exe u_exe (
        .slot_Q102H_i(pipe_q.slot_Q102H),
        .dec_Q102H_i(dec_Q102H),
        .rf_rs1_index_o(rf_rs1_index),
        .rf_rs2_index_o(rf_rs2_index),
        .rf_rs1_data_i(rf_rs1_data),
        .rf_rs2_data_i(rf_rs2_data),
        .q103_i(pipe_q.q103),
        .q104_i(q104_Q104H),
        .q105_i(pipe_q.q105),
        .csr_read_data_i(csr_read_data),
        .csr_legal_i(csr_legal),
        .mepc_i(mepc),
        .div_result_i(div_result),
        .rs1_value_o(rs1_value_Q102H),
        .rs2_value_o(rs2_value_Q102H),
        .csr_operand_o(csr_operand_Q102H),
        .result_o(result_Q102H),
        .taken_o(taken_Q102H),
        .redirect_o(redirect_Q102H),
        .next_pc_o(next_pc_Q102H),
        .mem_address_o(mem_address_Q102H),
        .mem_write_data_o(mem_write_data_Q102H),
        .mem_mask_o(mem_mask_Q102H),
        .exception_o(exception_Q102H),
        .exception_cause_o(exception_cause_Q102H),
        .exception_value_o(exception_value_Q102H)
    );

    assign muldiv_issue = mul_issue || div_issue;

    n2d_muldiv u_muldiv (
        .clk_i(clk_i),
        .rst_i(rst_i),
        .issue_i(muldiv_issue),
        .funct3_i(dec_Q102H.funct3),
        .rs1_i(rs1_value_Q102H),
        .rs2_i(rs2_value_Q102H),
        .consume_i(div_consume),
        .kill_i(div_kill),
        .busy_o(div_busy),
        .result_valid_o(div_result_valid),
        .div_result_o(div_result),
        .mul_result_o(mul_result)
    );

    always_comb begin
        csr_trap_valid = trap_Q103H || trap_commit_Q102H;
        csr_trap_epc = trap_Q103H ? pipe_q.q103.pc : pipe_q.slot_Q102H.pc;
        csr_trap_cause = trap_Q103H ? trap_cause_Q103H : exception_cause_Q102H;
        csr_trap_value = trap_Q103H ? pipe_q.q103.mem_address : exception_value_Q102H;
        csr_op_Q102H = dec_Q102H.funct3[1:0];
        // CSR and MRET retire architecturally on their serialized Q102H
        // commit edge, so their own minstret and INSTRET count use the
        // controls of that edge (a minstret write replaces the increment).
        instret_increment = (pipe_q.q104.valid && !pipe_q.q104.trap &&
            !pipe_q.q104.counted_at_commit) || csr_commit_Q102H || mret_commit_Q102H;
    end

    always_comb begin
        pmon_all_events = pmon_core_events | pmon_platform_events_i;
        pmon_all_events[2] = instret_increment;
    end

    n2d_csr u_csr (
        .clk_i(clk_i),
        .rst_i(rst_i),
        .csr_address_i(dec_Q102H.csr_address),
        .csr_op_i(csr_op_Q102H),
        .csr_write_intent_i(dec_Q102H.csr_write),
        .csr_operand_i(csr_operand_Q102H),
        .csr_read_data_o(csr_read_data),
        .csr_legal_o(csr_legal),
        .csr_commit_i(csr_commit_Q102H),
        .trap_valid_i(csr_trap_valid),
        .trap_epc_i(csr_trap_epc),
        .trap_cause_i(csr_trap_cause),
        .trap_value_i(csr_trap_value),
        .mret_commit_i(mret_commit_Q102H),
        .instret_increment_i(instret_increment),
        .pmon_events_i(pmon_all_events),
        .pmon_events_o(pmon_events_o),
        .mtvec_o(mtvec),
        .mepc_o(mepc)
    );

    //----------------------------------------------------------
    // Q103H memory completion
    //----------------------------------------------------------

    n2d_ma u_ma (
        .q103_i(pipe_q.q103),
        .data_response_valid_i(data_response_valid_i),
        .data_response_read_data_i(data_response_read_data_i),
        .data_response_error_i(data_response_error_i),
        .mtvec_i(mtvec),
        .hold_Q103H_o(hold_Q103H),
        .trap_Q103H_o(trap_Q103H),
        .trap_cause_o(trap_cause_Q103H),
        .retire_Q104H_o(retire_Q104H)
    );

    //----------------------------------------------------------
    // Control
    //----------------------------------------------------------

    n2d_ctrl u_ctrl (
        .clk_i(clk_i),
        .rst_i(rst_i),
        .enable_i(enable_i),
        .halt_request_i(halt_request_i),
        .reset_pc_request_i(reset_pc_request_i),
        .resume_i(resume_i),
        .slot_Q101H_i(slot_Q101H),
        .fetch_owed_i(fetch_owed),
        .fetch_request_stalled_i(instruction_request_valid_o && !instruction_request_ready_i),
        .slot_Q102H_i(pipe_q.slot_Q102H),
        .dec_Q102H_i(dec_Q102H),
        .exception_Q102H_i(exception_Q102H),
        .taken_Q102H_i(taken_Q102H),
        .redirect_Q102H_i(redirect_Q102H),
        .next_pc_Q102H_i(next_pc_Q102H),
        .q103_i(pipe_q.q103),
        .hold_Q103H_i(hold_Q103H),
        .trap_Q103H_i(trap_Q103H),
        .q104_valid_i(pipe_q.q104.valid),
        .q105_i(pipe_q.q105),
        .div_busy_i(div_busy),
        .div_result_valid_i(div_result_valid),
        .mtvec_i(mtvec),
        .predict_redirect_Q101H_i(predict_redirect_Q101H),
        .predict_target_Q101H_i(predict_target_Q101H),
        .data_request_ready_i(data_request_ready_i),
        .fetch_allow_o(fetch_allow),
        .if_flush_o(if_flush),
        .if_redirect_valid_o(if_redirect_valid),
        .if_redirect_pc_o(if_redirect_pc),
        .take_Q101H_o(take_Q101H),
        .ras_clear_o(ras_clear),
        .btb_update_o(btb_update),
        .advance_Q102H_o(advance_Q102H),
        .data_request_valid_o(data_request_valid_o),
        .trap_commit_Q102H_o(trap_commit_Q102H),
        .csr_commit_Q102H_o(csr_commit_Q102H),
        .mret_commit_Q102H_o(mret_commit_Q102H),
        .mul_issue_o(mul_issue),
        .div_issue_o(div_issue),
        .div_consume_o(div_consume),
        .div_kill_o(div_kill),
        .pmon_events_o(pmon_core_events),
        .halt_ack_o(halt_ack_o)
    );

    always_comb begin
        data_request_write_o = dec_Q102H.is_store;
        data_request_address_o = mem_address_Q102H;
        data_request_write_data_o = mem_write_data_Q102H;
        data_request_byte_enable_o = dec_Q102H.is_store ? mem_mask_Q102H : 4'b0000;
    end

    //----------------------------------------------------------
    // Stage advance
    //----------------------------------------------------------

    // Q104H record with a multiply's registered product: the source of
    // Q104H forwarding and of the Q105H record.
    always_comb begin
        q104_Q104H = pipe_q.q104;
        if (pipe_q.q104.valid && pipe_q.q104.is_mul && pipe_q.q104.rd_write)
            q104_Q104H.rd_write_data = mul_result;
    end

    always_comb begin
        // Q102H to Q103H record; a trap record carries no effect.
        q103_next = '0;
        q103_next.valid = advance_Q102H;
        q103_next.pc = pipe_q.slot_Q102H.pc;
        q103_next.instruction = pipe_q.slot_Q102H.fetch_error ? 32'b0 : pipe_q.slot_Q102H.instruction;
        if (exception_Q102H) begin
            q103_next.next_pc = mtvec;
            q103_next.trap = 1'b1;
            q103_next.cause = exception_cause_Q102H;
            q103_next.trap_value = exception_value_Q102H;
        end else begin
            q103_next.next_pc = next_pc_Q102H;
            q103_next.rd = dec_Q102H.rd;
            q103_next.rd_write = dec_Q102H.rd_write;
            q103_next.result = result_Q102H;
            q103_next.is_load = dec_Q102H.is_load;
            q103_next.is_store = dec_Q102H.is_store;
            q103_next.is_mul = dec_Q102H.is_mul;
            q103_next.funct3 = dec_Q102H.funct3;
            if (dec_Q102H.is_load || dec_Q102H.is_store) begin
                q103_next.mem_address = mem_address_Q102H;
                q103_next.mem_write_data = dec_Q102H.is_store ? mem_write_data_Q102H : 32'b0;
                q103_next.mem_mask = mem_mask_Q102H;
            end
            q103_next.counted_at_commit = dec_Q102H.is_csr || dec_Q102H.is_mret;
        end

        pipe_d = pipe_q;

        if (trap_Q103H) pipe_d.slot_Q102H = '0;
        else if (!pipe_q.slot_Q102H.valid || advance_Q102H)
            pipe_d.slot_Q102H = take_Q101H ? slot_Q101H : '0;

        if (!hold_Q103H) pipe_d.q103 = q103_next;

        pipe_d.q104 = retire_Q104H;

        pipe_d.q105 = q104_Q104H;
        pipe_d.q105.order = pipe_q.order;
        if (pipe_q.q104.valid) pipe_d.order = pipe_q.order + 64'd1;
    end

    `N2A_DFF_RST(pipe_q, pipe_d, clk_i, rst_i)

    //----------------------------------------------------------
    // Q105H writeback and observation
    //----------------------------------------------------------

    n2d_wb u_wb (
        .q105_i(pipe_q.q105),
        .rf_write_enable_o(rf_write_enable),
        .rf_write_index_o(rf_write_index),
        .rf_write_data_o(rf_write_data),
        .observation_valid_o(observation_valid_o),
        .observation_order_o(observation_order_o),
        .observation_pc_o(observation_pc_o),
        .observation_instruction_o(observation_instruction_o),
        .observation_next_pc_o(observation_next_pc_o),
        .observation_rd_address_o(observation_rd_address_o),
        .observation_rd_write_o(observation_rd_write_o),
        .observation_rd_write_data_o(observation_rd_write_data_o),
        .observation_memory_valid_o(observation_memory_valid_o),
        .observation_memory_write_o(observation_memory_write_o),
        .observation_memory_address_o(observation_memory_address_o),
        .observation_memory_read_data_o(observation_memory_read_data_o),
        .observation_memory_write_data_o(observation_memory_write_data_o),
        .observation_memory_read_mask_o(observation_memory_read_mask_o),
        .observation_memory_write_mask_o(observation_memory_write_mask_o),
        .observation_trap_o(observation_trap_o),
        .observation_cause_o(observation_cause_o),
        .observation_trap_value_o(observation_trap_value_o)
    );

    // Stage invariants: a held Q103H always owes a memory response, and a
    // Q103H access trap never coincides with a Q102H commit.
    `N2A_ASSERT_NEVER(pipe_hold_without_memory, clk_i, rst_i,
        hold_Q103H && !(pipe_q.q103.is_load || pipe_q.q103.is_store))
    `N2A_ASSERT_NEVER(pipe_q102_advance_under_q103_trap, clk_i, rst_i,
        advance_Q102H && trap_Q103H)
    // Only a fetched JAL, conditional branch or JALR is ever predicted taken.
    `N2A_ASSERT(pipe_prediction_is_control_transfer, clk_i, rst_i,
        !(pipe_q.slot_Q102H.valid && pipe_q.slot_Q102H.predicted_taken) ||
        ((dec_Q102H.is_jal || dec_Q102H.is_branch || dec_Q102H.is_jalr) &&
         !pipe_q.slot_Q102H.fetch_error))
endmodule
