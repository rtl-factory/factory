// RV32M multiply/divide unit
//----------------------------------------------------------
// 1. Multiply (fixed two-edge latency, one issue per cycle):
//    issue_i with a MUL-class funct3 on the Q102H-to-Q103H edge registers
//    the two operands as 33-bit signed values (sign or zero extended per
//    funct3). The Q103H-to-Q104H edge registers their full product. In
//    Q104H mul_result_o selects the low or high product word. Operand
//    register -> plain signed multiply -> product register is the standard
//    inferable form for the embedded 18x18 multipliers with their input and
//    output registers; neither register has a reset.
// 2. Divide (DIV/DIVU/REM/REMU, radix-2 restoring with early out):
//    issue_i with a DIV-class funct3 while Q102H holds the instruction
//    registers the raw operands. A setup cycle forms the magnitudes, aligns
//    the dividend to the divisor's leading one, and starts only as many
//    restoring iterations as the quotient can have bits:
//    msb(|dividend|) - msb(|divisor|) + 1. busy_o covers setup and the
//    iterations; result_valid_o then holds the sign-corrected quotient or
//    remainder until the slot leaves Q102H (consume_i) or is killed.
//    Fast paths finish without iterating: a divisor of zero (decided at
//    issue), a dividend magnitude with fewer significant bits than the
//    divisor (decided in setup), and a divide whose raw operands and
//    signedness equal the last completed divide (decided at issue), which
//    reuses its quotient and remainder, so REM after DIV (and REMU after
//    DIVU) over the same operands costs no second division.
//----------------------------------------------------------
// Divide-by-zero returns all ones (quotient) or the dividend (remainder);
// signed 0x80000000 / -1 returns 0x80000000 with remainder zero.
//----------------------------------------------------------

`include "src/common/rtl/pkg/macros.vh"

module n2d_muldiv (
    input  logic        clk_i,
    input  logic        rst_i,
    input  logic        issue_i,
    input  logic [2:0]  funct3_i,
    input  logic [31:0] rs1_i,
    input  logic [31:0] rs2_i,
    // The Q102H divide leaves Q102H with its result (clears result_valid_o)
    input  logic        consume_i,
    // The Q102H divide was flushed: abandon it
    input  logic        kill_i,
    output logic        busy_o,
    output logic        result_valid_o,
    output logic [31:0] div_result_o,
    // Product word of the multiply now in Q104H
    output logic [31:0] mul_result_o
);
    import n2d_core_types_pkg::*;

    //----------------------------------------------------------
    // Multiply datapath: operand registers with the issue enable, a free
    // running product register, no resets, so the inference keeps the
    // embedded multipliers' input and output register stages.
    //----------------------------------------------------------

    t_mul_issue   mul_issue_q, mul_issue_d;
    t_mul_product mul_product_q, mul_product_d;
    logic         mul_issue;
    logic signed [65:0] mul_full;

    always_comb begin
        mul_issue = issue_i && !funct3_i[2];

        // MULH and MULHSU take rs1 as signed; only MULH takes rs2 as signed.
        // MUL's low word is the same for every extension.
        mul_issue_d.a = {((funct3_i[1:0] == 2'b01) || (funct3_i[1:0] == 2'b10)) && rs1_i[31], rs1_i};
        mul_issue_d.b = {(funct3_i[1:0] == 2'b01) && rs2_i[31], rs2_i};
        mul_issue_d.high = funct3_i[1:0] != 2'b00;

        mul_full = mul_issue_q.a * mul_issue_q.b;
        mul_product_d.product = mul_full[63:0];
        mul_product_d.high = mul_issue_q.high;

        mul_result_o = mul_product_q.high ? mul_product_q.product[63:32]
                                          : mul_product_q.product[31:0];
    end

    // The 33x33 product's two extension bits never reach a result word.
    `N2A_UNUSED_BITS(unused_mul_full, mul_full[65:64])

    `N2A_DFF_EN(mul_issue_q, mul_issue_d, clk_i, mul_issue)
    `N2A_DFF(mul_product_q, mul_product_d, clk_i)

    //----------------------------------------------------------
    // Divider
    //----------------------------------------------------------

    t_div_regs div_q, div_d;

    logic        div_issue;
    logic        issue_signed;
    logic        issue_reuse;
    logic        issue_zero;
    logic [31:0] dividend_magnitude;
    logic [31:0] divisor_magnitude;
    logic [5:0]  dividend_msb;
    logic [5:0]  divisor_msb;
    logic        dividend_small;
    logic [5:0]  iterations;
    logic [63:0] aligned;
    logic [32:0] remainder_shift;
    logic [32:0] remainder_difference;
    logic [31:0] quotient_next;
    logic [31:0] remainder_next;
    logic        step_bit;

    // Setup: magnitudes, leading ones, and the aligned dividend. The quotient
    // has at most msb(n) - msb(d) + 1 bits, and the dividend bits above that
    // window already form a partial remainder below the divisor.
    always_comb begin
        dividend_magnitude = (div_q.signed_op && div_q.rs1[31]) ? -div_q.rs1 : div_q.rs1;
        divisor_magnitude = (div_q.signed_op && div_q.rs2[31]) ? -div_q.rs2 : div_q.rs2;

        dividend_msb = 6'd0;
        divisor_msb = 6'd0;
        for (int bit_index = 0; bit_index < 32; bit_index++) begin
            if (dividend_magnitude[bit_index]) dividend_msb = 6'(bit_index);
            if (divisor_magnitude[bit_index]) divisor_msb = 6'(bit_index);
        end

        // Zero dividend, or fewer significant bits than the divisor: the
        // quotient is zero and the remainder is the dividend magnitude.
        dividend_small = (dividend_magnitude == 32'b0) || (dividend_msb < divisor_msb);
        iterations = dividend_msb - divisor_msb + 6'd1;
        // {remainder, quotient} = dividend << (32 - iterations), iterations 1..32
        aligned = {32'b0, dividend_magnitude} << (6'd32 - iterations);
    end

    // One restoring step: shift the next dividend bit into the remainder.
    always_comb begin
        remainder_shift = {div_q.remainder, div_q.quotient[31]};
        remainder_difference = remainder_shift - {1'b0, div_q.divisor};
        step_bit = !remainder_difference[32];
        remainder_next = step_bit ? remainder_difference[31:0] : remainder_shift[31:0];
        quotient_next = {div_q.quotient[30:0], step_bit};
    end

    always_comb begin
        div_issue = issue_i && funct3_i[2];
        issue_signed = !funct3_i[0];
        issue_reuse = div_q.reusable && (rs1_i == div_q.rs1) && (rs2_i == div_q.rs2) &&
            (issue_signed == div_q.signed_op);
        issue_zero = rs2_i == 32'b0;

        div_d = div_q;
        if (kill_i) begin
            // Only a finished divide keeps its quotient and remainder.
            div_d.state = DIV_IDLE;
            if (div_q.state != DIV_DONE) div_d.reusable = 1'b0;
        end else if (div_issue) begin
            div_d.remainder_op = funct3_i[1];
            div_d.signed_op = issue_signed;
            div_d.rs1 = rs1_i;
            div_d.rs2 = rs2_i;
            if (issue_reuse) begin
                div_d.state = DIV_DONE;
            end else if (issue_zero) begin
                // Quotient all ones, remainder the dividend, no sign fix-up.
                div_d.state = DIV_DONE;
                div_d.reusable = 1'b1;
                div_d.quotient = 32'hFFFF_FFFF;
                div_d.remainder = rs1_i;
                div_d.negative_quotient = 1'b0;
                div_d.negative_remainder = 1'b0;
            end else begin
                div_d.state = DIV_SETUP;
                div_d.reusable = 1'b0;
            end
        end else begin
            case (div_q.state)
                DIV_SETUP: begin
                    div_d.divisor = divisor_magnitude;
                    div_d.negative_quotient = div_q.signed_op && (div_q.rs1[31] ^ div_q.rs2[31]);
                    div_d.negative_remainder = div_q.signed_op && div_q.rs1[31];
                    if (dividend_small) begin
                        div_d.state = DIV_DONE;
                        div_d.reusable = 1'b1;
                        div_d.quotient = 32'b0;
                        div_d.remainder = dividend_magnitude;
                    end else begin
                        div_d.state = DIV_ITER;
                        div_d.count = iterations;
                        div_d.remainder = aligned[63:32];
                        div_d.quotient = aligned[31:0];
                    end
                end
                DIV_ITER: begin
                    div_d.remainder = remainder_next;
                    div_d.quotient = quotient_next;
                    div_d.count = div_q.count - 6'd1;
                    if (div_q.count == 6'd1) begin
                        div_d.state = DIV_DONE;
                        div_d.reusable = 1'b1;
                    end
                end
                DIV_DONE: if (consume_i) div_d.state = DIV_IDLE;
                default: ;
            endcase
        end

        busy_o = (div_q.state == DIV_SETUP) || (div_q.state == DIV_ITER);
        result_valid_o = div_q.state == DIV_DONE;
        if (div_q.remainder_op)
            div_result_o = div_q.negative_remainder ? -div_q.remainder : div_q.remainder;
        else
            div_result_o = div_q.negative_quotient ? -div_q.quotient : div_q.quotient;
    end

    `N2A_DFF_RST(div_q, div_d, clk_i, rst_i)

    `N2A_ASSERT_NEVER(muldiv_divide_issue_while_busy, clk_i, rst_i,
        div_issue && (div_q.state != DIV_IDLE) && !kill_i)
    `N2A_ASSERT_NEVER(muldiv_iteration_count_range, clk_i, rst_i,
        (div_q.state == DIV_ITER) && ((div_q.count == 6'd0) || (div_q.count > 6'd32)))
    `N2A_ASSERT_NEVER(muldiv_consume_without_result, clk_i, rst_i,
        consume_i && (div_q.state != DIV_DONE))
endmodule
