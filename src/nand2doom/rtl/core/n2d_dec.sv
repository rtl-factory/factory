// Q102H instruction decode
//----------------------------------------------------------
// 1. Split the Q102H instruction register into register fields, function
//    fields and every immediate format.
// 2. Classify it into the machine-mode RV32IM classes and decide operand
//    use and destination-write intent for hazard detection and retirement.
// 3. Decide legality of every class except base OP/OP-IMM, whose legality
//    is owned by n2d_rv32i_decode_alu in n2d_exe.
//----------------------------------------------------------
// Purely combinational; a fetch-faulted slot is classified by n2d_ctrl.
//----------------------------------------------------------

module n2d_dec (
    input  logic [31:0]        instruction_i,
    output n2d_core_pkg::t_dec dec_o
);
    import n2d_core_pkg::*;

    logic [6:0] opcode;
    logic [6:0] funct7;
    logic       system_priv;

    always_comb begin
        opcode = instruction_i[6:0];
        funct7 = instruction_i[31:25];
        system_priv = instruction_i[14:12] == 3'b000;

        dec_o = '0;
        dec_o.rs1 = instruction_i[19:15];
        dec_o.rs2 = instruction_i[24:20];
        dec_o.rd = instruction_i[11:7];
        dec_o.funct3 = instruction_i[14:12];
        dec_o.csr_address = instruction_i[31:20];
        dec_o.imm_i = {{20{instruction_i[31]}}, instruction_i[31:20]};
        dec_o.imm_s = {{20{instruction_i[31]}}, instruction_i[31:25], instruction_i[11:7]};
        dec_o.imm_b = imm_b(instruction_i);
        dec_o.imm_u = {instruction_i[31:12], 12'b0};
        dec_o.imm_j = imm_j(instruction_i);

        case (opcode)
            OPCODE_LUI: begin
                dec_o.is_lui = 1'b1;
                dec_o.class_legal = 1'b1;
                dec_o.rd_write = 1'b1;
            end
            OPCODE_AUIPC: begin
                dec_o.is_auipc = 1'b1;
                dec_o.class_legal = 1'b1;
                dec_o.rd_write = 1'b1;
            end
            OPCODE_JAL: begin
                dec_o.is_jal = 1'b1;
                dec_o.class_legal = 1'b1;
                dec_o.rd_write = 1'b1;
            end
            OPCODE_JALR: begin
                dec_o.is_jalr = 1'b1;
                dec_o.class_legal = dec_o.funct3 == 3'b000;
                dec_o.uses_rs1 = 1'b1;
                dec_o.rd_write = 1'b1;
            end
            OPCODE_BRANCH: begin
                dec_o.is_branch = 1'b1;
                dec_o.class_legal = (dec_o.funct3 != 3'b010) && (dec_o.funct3 != 3'b011);
                dec_o.uses_rs1 = 1'b1;
                dec_o.uses_rs2 = 1'b1;
            end
            OPCODE_LOAD: begin
                dec_o.is_load = 1'b1;
                dec_o.class_legal = (dec_o.funct3 == 3'b000) || (dec_o.funct3 == 3'b001) ||
                    (dec_o.funct3 == 3'b010) || (dec_o.funct3 == 3'b100) ||
                    (dec_o.funct3 == 3'b101);
                dec_o.uses_rs1 = 1'b1;
                dec_o.rd_write = 1'b1;
            end
            OPCODE_STORE: begin
                dec_o.is_store = 1'b1;
                dec_o.class_legal = (dec_o.funct3 == 3'b000) || (dec_o.funct3 == 3'b001) ||
                    (dec_o.funct3 == 3'b010);
                dec_o.uses_rs1 = 1'b1;
                dec_o.uses_rs2 = 1'b1;
            end
            OPCODE_OP_IMM: begin
                dec_o.is_base_alu = 1'b1;
                dec_o.class_legal = 1'b1;
                dec_o.uses_rs1 = 1'b1;
                dec_o.rd_write = 1'b1;
            end
            OPCODE_OP: begin
                dec_o.class_legal = 1'b1;
                dec_o.uses_rs1 = 1'b1;
                dec_o.uses_rs2 = 1'b1;
                dec_o.rd_write = 1'b1;
                if (funct7 == FUNCT7_MULDIV) begin
                    dec_o.is_mul = !dec_o.funct3[2];
                    dec_o.is_div = dec_o.funct3[2];
                end else begin
                    dec_o.is_base_alu = 1'b1;
                end
            end
            OPCODE_MISC_MEM: begin
                dec_o.is_fence = 1'b1;
                dec_o.class_legal = dec_o.funct3 == 3'b000;
            end
            OPCODE_SYSTEM: begin
                if (system_priv) begin
                    dec_o.is_ecall = instruction_i == INSN_ECALL;
                    dec_o.is_ebreak = instruction_i == INSN_EBREAK;
                    dec_o.is_mret = instruction_i == INSN_MRET;
                    dec_o.class_legal = dec_o.is_ecall || dec_o.is_ebreak || dec_o.is_mret;
                end else if (dec_o.funct3 != 3'b100) begin
                    // Why: any CSR encoding is class-legal here because CSR legality also
                    // depends on the address, which only n2d_csr checks.
                    dec_o.is_csr = 1'b1;
                    dec_o.class_legal = 1'b1;
                    dec_o.uses_rs1 = !dec_o.funct3[2];
                    dec_o.rd_write = 1'b1;
                end
            end
            default: begin
            end
        endcase

        // Ref: RISC-V Zicsr: CSRRS/CSRRC with rs1 = x0 do not write, keyed on the
        // encoded source index, not its value.
        case (dec_o.funct3)
            3'b001, 3'b101: dec_o.csr_write = 1'b1;
            3'b010, 3'b110, 3'b011, 3'b111: dec_o.csr_write = dec_o.rs1 != 5'd0;
            default: dec_o.csr_write = 1'b0;
        endcase

        dec_o.rd_write = dec_o.rd_write && (dec_o.rd != 5'd0);
    end
endmodule
