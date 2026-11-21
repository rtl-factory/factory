`include "src/common/rtl/pkg/macros.vh"

// The bit period is CLKS_PER_BIT + FRAC_NUM/FRAC_DEN clocks; see
// n2a_uart_rx. The default zero fraction is the integer divisor.
module n2a_uart_tx #(
    parameter int unsigned CLKS_PER_BIT = 217,
    parameter int unsigned FRAC_NUM = 0,
    parameter int unsigned FRAC_DEN = 1
) (
    input  logic       clk_i,
    input  logic       reset_i,
    input  logic       byte_valid_i,
    output logic       byte_ready_o,
    input  logic [7:0] byte_i,
    output logic       busy_o,
    output logic       tx_o
);
    import n2a_types_pkg::*;

    localparam int unsigned COUNTER_WIDTH = $clog2(CLKS_PER_BIT + 1);
    localparam int unsigned ACC_WIDTH = (FRAC_DEN < 2) ? 1 : $clog2(2 * FRAC_DEN);
    localparam logic [COUNTER_WIDTH-1:0] BIT_CLOCK_LAST =
        COUNTER_WIDTH'(CLKS_PER_BIT - 1);

    // Protocol state: one packaged struct register, one owner. The bit clock
    // and fractional remainder widths follow the parameters, so each is a
    // register of its own beside it with the same reset value.
    localparam uart_protocol_t PROTOCOL_RESET = '{
        state: UART_IDLE,
        bit_index: '0,
        shift: '0,
        extra: 1'b0
    };

    uart_protocol_t protocol_q;
    uart_protocol_t protocol_d;
    logic [COUNTER_WIDTH-1:0] bit_clock_q;
    logic [COUNTER_WIDTH-1:0] bit_clock_d;
    logic [ACC_WIDTH-1:0] frac_acc_q;
    logic [ACC_WIDTH-1:0] frac_acc_d;
    logic bit_done;
    logic [ACC_WIDTH:0] frac_sum;

    `N2A_ASSERT_PARAM(g_invalid_clks_per_bit, CLKS_PER_BIT >= 2,
        "n2a_uart_tx CLKS_PER_BIT must be at least 2")
    `N2A_ASSERT_PARAM(g_invalid_fraction, (FRAC_DEN >= 1) && (FRAC_NUM < FRAC_DEN),
        "n2a_uart_tx FRAC_NUM must be below FRAC_DEN")

    assign bit_done = (bit_clock_q ==
                       (BIT_CLOCK_LAST + COUNTER_WIDTH'(protocol_q.extra)));
    assign frac_sum = (ACC_WIDTH+1)'(frac_acc_q) + (ACC_WIDTH+1)'(FRAC_NUM);

    always_comb begin
        byte_ready_o = (protocol_q.state == UART_IDLE);
        busy_o = (protocol_q.state != UART_IDLE);

        unique case (protocol_q.state)
            UART_START: tx_o = 1'b0;
            UART_DATA:  tx_o = protocol_q.shift[protocol_q.bit_index];
            default:    tx_o = 1'b1;
        endcase
    end

    always_comb begin
        protocol_d = protocol_q;
        bit_clock_d = bit_clock_q;
        frac_acc_d = frac_acc_q;

        unique case (protocol_q.state)
            UART_IDLE: begin
                bit_clock_d = '0;
                protocol_d.bit_index = '0;
                // Why: the start bit is never stretched; its share seeds the remainder.
                frac_acc_d = ACC_WIDTH'(FRAC_NUM);
                protocol_d.extra = 1'b0;
                if (byte_valid_i) begin
                    protocol_d.shift = byte_i;
                    protocol_d.state = UART_START;
                end
            end
            UART_START: begin
                if (bit_done) begin
                    bit_clock_d = '0;
                    protocol_d.extra = (frac_sum >= (ACC_WIDTH+1)'(FRAC_DEN));
                    frac_acc_d = protocol_d.extra ?
                        ACC_WIDTH'(frac_sum - (ACC_WIDTH+1)'(FRAC_DEN)) : ACC_WIDTH'(frac_sum);
                    protocol_d.state = UART_DATA;
                end else begin
                    bit_clock_d = bit_clock_q + 1'b1;
                end
            end
            UART_DATA: begin
                if (bit_done) begin
                    bit_clock_d = '0;
                    protocol_d.extra = (frac_sum >= (ACC_WIDTH+1)'(FRAC_DEN));
                    frac_acc_d = protocol_d.extra ?
                        ACC_WIDTH'(frac_sum - (ACC_WIDTH+1)'(FRAC_DEN)) : ACC_WIDTH'(frac_sum);
                    if (protocol_q.bit_index == 3'd7) begin
                        protocol_d.state = UART_STOP;
                    end else begin
                        protocol_d.bit_index = protocol_q.bit_index + 1'b1;
                    end
                end else begin
                    bit_clock_d = bit_clock_q + 1'b1;
                end
            end
            UART_STOP: begin
                if (bit_done) begin
                    bit_clock_d = '0;
                    protocol_d.state = UART_IDLE;
                end else begin
                    bit_clock_d = bit_clock_q + 1'b1;
                end
            end
            default: begin
                protocol_d.state = UART_IDLE;
                bit_clock_d = '0;
                protocol_d.bit_index = '0;
                frac_acc_d = '0;
                protocol_d.extra = 1'b0;
            end
        endcase
    end

    // Why: an asynchronous reset holds the line idle even before clk_i runs.
    `N2A_DFF_ARST_VAL(protocol_q, protocol_d, clk_i, reset_i, PROTOCOL_RESET)
    `N2A_DFF_ARST_VAL(bit_clock_q, bit_clock_d, clk_i, reset_i, '0)
    `N2A_DFF_ARST_VAL(frac_acc_q, frac_acc_d, clk_i, reset_i, '0)

    `N2A_ASSERT_CLOCKED_ARST_FATAL(clk_i, reset_i, !$isunknown(byte_valid_i),
        "n2a_uart_tx byte_valid_i is unknown")
    `N2A_ASSERT_CLOCKED_ARST_FATAL_WHEN(clk_i, reset_i,
        (byte_valid_i && byte_ready_o), !$isunknown(byte_i),
        "n2a_uart_tx accepted unknown byte_i")
endmodule
