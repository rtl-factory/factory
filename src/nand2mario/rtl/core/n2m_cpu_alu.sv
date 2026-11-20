module n2m_cpu_alu (
    input  logic [4:0] operation_i,
    input  logic [7:0] lhs_i,
    input  logic [7:0] rhs_i,
    input  logic [7:0] flags_i,
    output logic [7:0] value_o,
    output logic [7:0] flags_o,
    output logic       valid_o
);
    // The five-bit operation space leaves room for the complete LR35902 set.
    localparam logic [4:0] ALU_ADD = 5'd0;
    localparam logic [4:0] ALU_ADC = 5'd1;
    localparam logic [4:0] ALU_SUB = 5'd2;
    localparam logic [4:0] ALU_SBC = 5'd3;
    localparam logic [4:0] ALU_AND = 5'd4;
    localparam logic [4:0] ALU_XOR = 5'd5;
    localparam logic [4:0] ALU_OR  = 5'd6;
    localparam logic [4:0] ALU_CP  = 5'd7;
    localparam logic [4:0] ALU_INC = 5'd8;
    localparam logic [4:0] ALU_DEC = 5'd9;

    logic       carry_in;
    logic [8:0] arithmetic_result;
    logic [4:0] nibble_result;

    always_comb begin
        value_o = lhs_i;
        flags_o = {flags_i[7:4], 4'b0000};
        valid_o = 1'b1;
        carry_in = 1'b0;
        arithmetic_result = 9'h000;
        nibble_result = 5'h00;

        case (operation_i)
            ALU_ADD, ALU_ADC: begin
                carry_in = (operation_i == ALU_ADC) && flags_i[4];
                arithmetic_result = {1'b0, lhs_i} + {1'b0, rhs_i}
                    + {8'b0, carry_in};
                nibble_result = {1'b0, lhs_i[3:0]} + {1'b0, rhs_i[3:0]}
                    + {4'b0, carry_in};
                value_o = arithmetic_result[7:0];
                flags_o = {
                    arithmetic_result[7:0] == 8'h00,
                    1'b0,
                    nibble_result[4],
                    arithmetic_result[8],
                    4'b0000
                };
            end

            ALU_SUB, ALU_SBC, ALU_CP: begin
                carry_in = (operation_i == ALU_SBC) && flags_i[4];
                arithmetic_result = {1'b0, lhs_i} - {1'b0, rhs_i}
                    - {8'b0, carry_in};
                nibble_result = {1'b0, lhs_i[3:0]} - {1'b0, rhs_i[3:0]}
                    - {4'b0, carry_in};
                value_o = (operation_i == ALU_CP)
                    ? lhs_i
                    : arithmetic_result[7:0];
                flags_o = {
                    arithmetic_result[7:0] == 8'h00,
                    1'b1,
                    nibble_result[4],
                    arithmetic_result[8],
                    4'b0000
                };
            end

            ALU_AND, ALU_XOR, ALU_OR: begin
                case (operation_i)
                    ALU_AND: value_o = lhs_i & rhs_i;
                    ALU_XOR: value_o = lhs_i ^ rhs_i;
                    default: value_o = lhs_i | rhs_i;
                endcase
                flags_o = {
                    value_o == 8'h00,
                    1'b0,
                    operation_i == ALU_AND,
                    1'b0,
                    4'b0000
                };
            end

            ALU_INC: begin
                value_o = lhs_i + 8'h01;
                flags_o = {
                    value_o == 8'h00,
                    1'b0,
                    lhs_i[3:0] == 4'hf,
                    flags_i[4],
                    4'b0000
                };
            end

            ALU_DEC: begin
                value_o = lhs_i - 8'h01;
                flags_o = {
                    value_o == 8'h00,
                    1'b1,
                    lhs_i[3:0] == 4'h0,
                    flags_i[4],
                    4'b0000
                };
            end

            default: begin
                value_o = lhs_i;
                flags_o = {flags_i[7:4], 4'b0000};
                valid_o = 1'b0;
            end
        endcase
    end
endmodule
