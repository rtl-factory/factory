`include "src/common/rtl/pkg/macros.vh"

// Q102H execute: operand forwarding, ALU, control transfer, address, traps
//----------------------------------------------------------
// 1. Read rs1/rs2 from the common register file and forward the newest
//    in-flight value: Q103H (results already formed in Q102H), then Q104H,
//    then Q105H. Q105H forwarding is required because the common register
//    file has no write-through. A Q103H load or multiply result is not
//    forwardable; n2d_ctrl stalls its consumer for one cycle instead. The
//    Q104H record arrives with a multiply's product already substituted
//    from the n2d_muldiv product register.
// 2. Form the Q102H result: base OP/OP-IMM through n2d_rv32i_decode_alu,
//    upper immediates, link value, CSR read value or divide result.
// 3. Resolve branches, JAL, JALR and MRET and report the architectural
//    next PC. Fetch followed the slot's prediction (predicted_taken: JAL,
//    backward branches and returns with a stacked address taken, everything
//    else sequential; forward branches and other JALRs taken on a BTB hit),
//    so a redirect is reported only when the resolved direction differs or
//    a predicted transfer resolves to another target.
// 4. Form the effective address, byte lanes and aligned store word.
// 5. Report the precise synchronous exception of the Q102H slot, if any.
//----------------------------------------------------------

module n2d_exe (
    // Shared stage records: this stage reads only the fields it needs.
    /* verilator lint_off UNUSEDSIGNAL */
    input  n2d_core_pkg::t_fetch_slot slot_Q102H_i,
    input  n2d_core_pkg::t_dec        dec_Q102H_i,

    // Register-file read ports
    output logic [4:0]               rf_rs1_index_o,
    output logic [4:0]               rf_rs2_index_o,
    input  logic [31:0]              rf_rs1_data_i,
    input  logic [31:0]              rf_rs2_data_i,

    // Forwarding sources, newest first
    input  n2d_core_pkg::t_q103      q103_i,
    input  n2d_core_pkg::t_retire    q104_i,
    input  n2d_core_pkg::t_retire    q105_i,
    /* verilator lint_on UNUSEDSIGNAL */

    input  logic [31:0]              csr_read_data_i,
    input  logic                     csr_legal_i,
    input  logic [31:0]              mepc_i,
    input  logic [31:0]              div_result_i,

    output logic [31:0]              rs1_value_o,
    output logic [31:0]              rs2_value_o,
    output logic [31:0]              csr_operand_o,
    output logic [31:0]              result_o,
    output logic                     taken_o,
    output logic                     redirect_o,
    output logic [31:0]              next_pc_o,
    output logic [31:0]              mem_address_o,
    output logic [31:0]              mem_write_data_o,
    output logic [3:0]               mem_mask_o,
    output logic                     exception_o,
    output logic [31:0]              exception_cause_o,
    output logic [31:0]              exception_value_o
);
    import n2d_core_pkg::*;

    logic [4:0]  base_rs1_index;
    logic [4:0]  base_rs2_index;
    logic [4:0]  base_rd_index;
    logic        base_rs1_read;
    logic        base_rs2_read;
    logic        base_rd_write;
    logic [31:0] base_immediate;
    logic [31:0] base_alu_result;
    logic        base_alu_illegal;

    logic        q103_forwardable;
    logic        legal;
    logic        taken;
    logic        target_misaligned;
    logic        mem_aligned;
    logic [31:0] pc_plus4;
    logic [31:0] control_target;

    assign rf_rs1_index_o = dec_Q102H_i.rs1;
    assign rf_rs2_index_o = dec_Q102H_i.rs2;

    // The common decode/ALU slice computes from the forwarded operands.
    n2d_rv32i_decode_alu u_base_decode_alu (
        .instruction_i(slot_Q102H_i.instruction),
        .rs1_data_i(rs1_value_o),
        .rs2_data_i(rs2_value_o),
        .rs1_index_o(base_rs1_index),
        .rs2_index_o(base_rs2_index),
        .rd_index_o(base_rd_index),
        .rs1_read_o(base_rs1_read),
        .rs2_read_o(base_rs2_read),
        .rd_write_o(base_rd_write),
        .immediate_o(base_immediate),
        .alu_result_o(base_alu_result),
        .illegal_o(base_alu_illegal)
    );

    always_comb begin
        // Forwarding, newest producer first; x0 never forwards because a
        // record's rd_write is zero for x0.
        q103_forwardable = q103_i.valid && q103_i.rd_write && !q103_i.is_load && !q103_i.is_mul;

        rs1_value_o = rf_rs1_data_i;
        if (q103_forwardable && (q103_i.rd == dec_Q102H_i.rs1)) rs1_value_o = q103_i.result;
        else if (q104_i.valid && q104_i.rd_write && (q104_i.rd_address == dec_Q102H_i.rs1))
            rs1_value_o = q104_i.rd_write_data;
        else if (q105_i.valid && q105_i.rd_write && (q105_i.rd_address == dec_Q102H_i.rs1))
            rs1_value_o = q105_i.rd_write_data;

        rs2_value_o = rf_rs2_data_i;
        if (q103_forwardable && (q103_i.rd == dec_Q102H_i.rs2)) rs2_value_o = q103_i.result;
        else if (q104_i.valid && q104_i.rd_write && (q104_i.rd_address == dec_Q102H_i.rs2))
            rs2_value_o = q104_i.rd_write_data;
        else if (q105_i.valid && q105_i.rd_write && (q105_i.rd_address == dec_Q102H_i.rs2))
            rs2_value_o = q105_i.rd_write_data;
    end

    always_comb begin

        csr_operand_o = dec_Q102H_i.funct3[2] ? {27'b0, dec_Q102H_i.rs1} : rs1_value_o;

        pc_plus4 = slot_Q102H_i.pc + 32'd4;

        result_o = 32'b0;
        if (dec_Q102H_i.is_lui) result_o = dec_Q102H_i.imm_u;
        else if (dec_Q102H_i.is_auipc) result_o = slot_Q102H_i.pc + dec_Q102H_i.imm_u;
        else if (dec_Q102H_i.is_jal || dec_Q102H_i.is_jalr) result_o = pc_plus4;
        else if (dec_Q102H_i.is_base_alu) result_o = base_alu_result;
        else if (dec_Q102H_i.is_csr) result_o = csr_read_data_i;
        else if (dec_Q102H_i.is_div) result_o = div_result_i;

        case (dec_Q102H_i.funct3)
            3'b000: taken = rs1_value_o == rs2_value_o;
            3'b001: taken = rs1_value_o != rs2_value_o;
            3'b100: taken = $signed(rs1_value_o) < $signed(rs2_value_o);
            3'b101: taken = $signed(rs1_value_o) >= $signed(rs2_value_o);
            3'b110: taken = rs1_value_o < rs2_value_o;
            3'b111: taken = rs1_value_o >= rs2_value_o;
            default: taken = 1'b0;
        endcase

        control_target = slot_Q102H_i.pc + dec_Q102H_i.imm_b;
        if (dec_Q102H_i.is_jal) control_target = slot_Q102H_i.pc + dec_Q102H_i.imm_j;
        else if (dec_Q102H_i.is_jalr)
            control_target = (rs1_value_o + dec_Q102H_i.imm_i) & 32'hFFFF_FFFE;
        else if (dec_Q102H_i.is_mret) control_target = mepc_i;

        taken_o = dec_Q102H_i.is_jal || dec_Q102H_i.is_jalr || dec_Q102H_i.is_mret ||
            (dec_Q102H_i.is_branch && taken);
        next_pc_o = taken_o ? control_target : pc_plus4;
        target_misaligned = taken_o && (control_target[1:0] != 2'b00);
        // Fetch is on the wrong path: an unpredicted transfer, a
        // predicted-taken branch that falls through (next_pc_o = pc + 4), or
        // a predicted transfer whose stacked or buffered target differs
        // (a stale entry, a BTB partial-tag alias, or a JALR whose rs1
        // changed). Static JAL and branch targets always match.
        redirect_o = (taken_o != slot_Q102H_i.predicted_taken) ||
            (slot_Q102H_i.predicted_taken && taken_o &&
             (control_target[31:1] != slot_Q102H_i.predicted_target[31:1]));

        mem_address_o = rs1_value_o + (dec_Q102H_i.is_store ? dec_Q102H_i.imm_s : dec_Q102H_i.imm_i);
        mem_write_data_o = rs2_value_o << {mem_address_o[1:0], 3'b000};
        case (dec_Q102H_i.funct3[1:0])
            2'b00: begin
                mem_mask_o = 4'b0001 << mem_address_o[1:0];
                mem_aligned = 1'b1;
            end
            2'b01: begin
                mem_mask_o = mem_address_o[1] ? 4'b1100 : 4'b0011;
                mem_aligned = !mem_address_o[0];
            end
            default: begin
                mem_mask_o = 4'b1111;
                mem_aligned = mem_address_o[1:0] == 2'b00;
            end
        endcase

        legal = dec_Q102H_i.class_legal &&
            (!dec_Q102H_i.is_base_alu || !base_alu_illegal) &&
            (!dec_Q102H_i.is_csr || csr_legal_i);

        exception_o = 1'b1;
        exception_cause_o = 32'b0;
        exception_value_o = 32'b0;
        if (slot_Q102H_i.fetch_error) begin
            exception_cause_o = CAUSE_INSN_ACCESS;
            exception_value_o = slot_Q102H_i.pc;
        end else if (!legal) begin
            exception_cause_o = CAUSE_ILLEGAL;
            exception_value_o = slot_Q102H_i.instruction;
        end else if (dec_Q102H_i.is_ecall) begin
            exception_cause_o = CAUSE_ECALL_M;
        end else if (dec_Q102H_i.is_ebreak) begin
            exception_cause_o = CAUSE_BREAKPOINT;
        end else if (target_misaligned) begin
            exception_cause_o = CAUSE_INSN_MISALIGNED;
            exception_value_o = control_target;
        end else if (dec_Q102H_i.is_load && !mem_aligned) begin
            exception_cause_o = CAUSE_LOAD_MISALIGNED;
            exception_value_o = mem_address_o;
        end else if (dec_Q102H_i.is_store && !mem_aligned) begin
            exception_cause_o = CAUSE_STORE_MISALIGNED;
            exception_value_o = mem_address_o;
        end else begin
            exception_o = 1'b0;
        end
    end

    // Base decode/ALU outputs not used by the pipeline: operand-use and
    // destination intent are decoded by n2d_dec for every class.
    `N2A_UNUSED_BITS(unused_base, {base_rs1_index, base_rs2_index, base_rd_index, base_rs1_read,
                           base_rs2_read, base_rd_write, base_immediate})
endmodule
