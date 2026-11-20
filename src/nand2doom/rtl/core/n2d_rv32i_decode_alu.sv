module n2d_rv32i_decode_alu (
    input  logic [31:0] instruction_i,
    input  logic [31:0] rs1_data_i,
    input  logic [31:0] rs2_data_i,
    output logic [4:0]  rs1_index_o,
    output logic [4:0]  rs2_index_o,
    output logic [4:0]  rd_index_o,
    output logic        rs1_read_o,
    output logic        rs2_read_o,
    output logic        rd_write_o,
    output logic [31:0] immediate_o,
    output logic [31:0] alu_result_o,
    output logic        illegal_o
);
    localparam logic [6:0] OPCODE_OP     = 7'b0110011;
    localparam logic [6:0] OPCODE_OP_IMM = 7'b0010011;
    localparam logic [6:0] FUNCT7_BASE   = 7'b0000000;
    localparam logic [6:0] FUNCT7_ALT    = 7'b0100000;

    logic [6:0] opcode;
    logic [2:0] funct3;
    logic [6:0] funct7;
    logic [31:0] i_immediate;
    logic decode_legal;
    logic decode_uses_rs2;
    logic [31:0] decode_immediate;
    logic [31:0] decode_result;

    always_comb begin
        opcode = instruction_i[6:0];
        funct3 = instruction_i[14:12];
        funct7 = instruction_i[31:25];
        i_immediate = {{20{instruction_i[31]}}, instruction_i[31:20]};

        rs1_index_o = instruction_i[19:15];
        rs2_index_o = instruction_i[24:20];
        rd_index_o = instruction_i[11:7];

        decode_legal = 1'b0;
        decode_uses_rs2 = 1'b0;
        decode_immediate = 32'h0000_0000;
        decode_result = 32'h0000_0000;

        case (opcode)
            OPCODE_OP: begin
                decode_uses_rs2 = 1'b1;
                case (funct3)
                    3'b000: begin
                        if (funct7 == FUNCT7_BASE) begin
                            decode_legal = 1'b1;
                            decode_result = rs1_data_i + rs2_data_i;
                        end else if (funct7 == FUNCT7_ALT) begin
                            decode_legal = 1'b1;
                            decode_result = rs1_data_i - rs2_data_i;
                        end
                    end
                    3'b001: begin
                        if (funct7 == FUNCT7_BASE) begin
                            decode_legal = 1'b1;
                            decode_result = rs1_data_i << rs2_data_i[4:0];
                        end
                    end
                    3'b010: begin
                        if (funct7 == FUNCT7_BASE) begin
                            decode_legal = 1'b1;
                            decode_result = {
                                31'b0,
                                $signed(rs1_data_i) < $signed(rs2_data_i)
                            };
                        end
                    end
                    3'b011: begin
                        if (funct7 == FUNCT7_BASE) begin
                            decode_legal = 1'b1;
                            decode_result = {
                                31'b0,
                                rs1_data_i < rs2_data_i
                            };
                        end
                    end
                    3'b100: begin
                        if (funct7 == FUNCT7_BASE) begin
                            decode_legal = 1'b1;
                            decode_result = rs1_data_i ^ rs2_data_i;
                        end
                    end
                    3'b101: begin
                        if (funct7 == FUNCT7_BASE) begin
                            decode_legal = 1'b1;
                            decode_result = rs1_data_i >> rs2_data_i[4:0];
                        end else if (funct7 == FUNCT7_ALT) begin
                            decode_legal = 1'b1;
                            decode_result = $signed(rs1_data_i)
                                >>> rs2_data_i[4:0];
                        end
                    end
                    3'b110: begin
                        if (funct7 == FUNCT7_BASE) begin
                            decode_legal = 1'b1;
                            decode_result = rs1_data_i | rs2_data_i;
                        end
                    end
                    3'b111: begin
                        if (funct7 == FUNCT7_BASE) begin
                            decode_legal = 1'b1;
                            decode_result = rs1_data_i & rs2_data_i;
                        end
                    end
                    default: begin
                    end
                endcase
            end

            OPCODE_OP_IMM: begin
                decode_immediate = i_immediate;
                case (funct3)
                    3'b000: begin
                        decode_legal = 1'b1;
                        decode_result = rs1_data_i + i_immediate;
                    end
                    3'b001: begin
                        if (funct7 == FUNCT7_BASE) begin
                            decode_legal = 1'b1;
                            decode_result = rs1_data_i << instruction_i[24:20];
                        end
                    end
                    3'b010: begin
                        decode_legal = 1'b1;
                        decode_result = {
                            31'b0,
                            $signed(rs1_data_i) < $signed(i_immediate)
                        };
                    end
                    3'b011: begin
                        decode_legal = 1'b1;
                        decode_result = {
                            31'b0,
                            rs1_data_i < i_immediate
                        };
                    end
                    3'b100: begin
                        decode_legal = 1'b1;
                        decode_result = rs1_data_i ^ i_immediate;
                    end
                    3'b101: begin
                        if (funct7 == FUNCT7_BASE) begin
                            decode_legal = 1'b1;
                            decode_result = rs1_data_i >> instruction_i[24:20];
                        end else if (funct7 == FUNCT7_ALT) begin
                            decode_legal = 1'b1;
                            decode_result = $signed(rs1_data_i)
                                >>> instruction_i[24:20];
                        end
                    end
                    3'b110: begin
                        decode_legal = 1'b1;
                        decode_result = rs1_data_i | i_immediate;
                    end
                    3'b111: begin
                        decode_legal = 1'b1;
                        decode_result = rs1_data_i & i_immediate;
                    end
                    default: begin
                    end
                endcase
            end

            default: begin
            end
        endcase

        illegal_o = !decode_legal;
        rs1_read_o = decode_legal;
        rs2_read_o = decode_legal && decode_uses_rs2;
        rd_write_o = decode_legal && (rd_index_o != 5'd0);
        immediate_o = decode_legal ? decode_immediate : 32'h0000_0000;
        alu_result_o = decode_legal ? decode_result : 32'h0000_0000;
    end
endmodule
