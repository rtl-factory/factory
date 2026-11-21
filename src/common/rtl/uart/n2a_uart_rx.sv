`include "src/common/rtl/pkg/macros.vh"

// The bit period is CLKS_PER_BIT + FRAC_NUM/FRAC_DEN clocks. A nonzero
// fraction stretches individual bits by one clock from a per-frame remainder
// accumulator, so an exact baud such as 3 Mbaud at 25 MHz (8 1/3 clocks) has
// no cumulative drift; the default zero fraction is the integer divisor.
// byte_valid_o and framing_error_o are one-cycle pulses at the stop sample.
module n2a_uart_rx #(
    parameter int unsigned CLKS_PER_BIT = 217,
    parameter int unsigned FRAC_NUM = 0,
    parameter int unsigned FRAC_DEN = 1
) (
    input  logic       clk_i,
    input  logic       reset_i,
    input  logic       rx_i,
    output logic [7:0] byte_o,
    output logic       byte_valid_o,
    output logic       busy_o,
    output logic       framing_error_o
);
    import n2a_types_pkg::*;

    localparam int unsigned COUNTER_WIDTH = $clog2(CLKS_PER_BIT + 1);
    localparam int unsigned ACC_WIDTH = (FRAC_DEN < 2) ? 1 : $clog2(2 * FRAC_DEN);
    localparam int unsigned HALF_CLKS_PER_BIT =
        ((CLKS_PER_BIT * FRAC_DEN) + FRAC_NUM) / (2 * FRAC_DEN);
    localparam logic [COUNTER_WIDTH-1:0] BIT_CLOCK_LAST =
        COUNTER_WIDTH'(CLKS_PER_BIT - 1);
    localparam logic [COUNTER_WIDTH-1:0] HALF_BIT_CLOCK_LAST =
        COUNTER_WIDTH'(HALF_CLKS_PER_BIT - 1);

    // Protocol state: one packaged struct register, one owner. The bit clock
    // and fractional remainder widths follow the parameters, so each is a
    // register of its own beside it with the same reset value.
    localparam t_uart_protocol PROTOCOL_RESET = '{
        state: UART_IDLE,
        bit_index: '0,
        shift: '0,
        extra: 1'b0
    };

    t_uart_protocol protocol_q;
    t_uart_protocol protocol_d;
    logic [COUNTER_WIDTH-1:0] bit_clock_q;
    logic [COUNTER_WIDTH-1:0] bit_clock_d;
    logic [ACC_WIDTH-1:0] frac_acc_q;
    logic [ACC_WIDTH-1:0] frac_acc_d;
    logic       rx_sync;
    logic [7:0] byte_d;
    logic       byte_valid_d;
    logic       framing_error_d;
    logic       bit_done;
    logic [ACC_WIDTH:0] frac_sum;

    `ASSERT_PARAM(g_invalid_clks_per_bit, CLKS_PER_BIT >= 2,
        "n2a_uart_rx CLKS_PER_BIT must be at least 2")
    `ASSERT_PARAM(g_invalid_fraction, (FRAC_DEN >= 1) && (FRAC_NUM < FRAC_DEN),
        "n2a_uart_rx FRAC_NUM must be below FRAC_DEN")

    assign bit_done = (bit_clock_q ==
                       (BIT_CLOCK_LAST + COUNTER_WIDTH'(protocol_q.extra)));
    assign frac_sum = (ACC_WIDTH+1)'(frac_acc_q) + (ACC_WIDTH+1)'(FRAC_NUM);

    n2a_cdc_bit_sync #(
        .STAGES(2),
        .RESET_VALUE(1'b1)
    ) rx_synchronizer (
        .clk_i(clk_i),
        .reset_i(reset_i),
        .async_i(rx_i),
        .sync_o(rx_sync)
    );

    assign busy_o = (protocol_q.state != UART_IDLE);

    // Why: the start bit is sampled at mid-bit so each later sample, one bit
    // period apart, lands at a bit centre, farthest from both edges.
    always_comb begin
        protocol_d = protocol_q;
        bit_clock_d = bit_clock_q;
        frac_acc_d = frac_acc_q;
        byte_d = byte_o;
        byte_valid_d = 1'b0;
        framing_error_d = 1'b0;

        unique case (protocol_q.state)
            UART_IDLE: begin
                bit_clock_d = '0;
                protocol_d.bit_index = '0;
                frac_acc_d = '0;
                protocol_d.extra = 1'b0;
                if (!rx_sync) begin
                    protocol_d.state = UART_START;
                end
            end
            UART_START: begin
                if (bit_clock_q == HALF_BIT_CLOCK_LAST) begin
                    bit_clock_d = '0;
                    protocol_d.extra = (frac_sum >= (ACC_WIDTH+1)'(FRAC_DEN));
                    frac_acc_d = protocol_d.extra ?
                        ACC_WIDTH'(frac_sum - (ACC_WIDTH+1)'(FRAC_DEN)) : ACC_WIDTH'(frac_sum);
                    if (!rx_sync) begin
                        protocol_d.state = UART_DATA;
                    end else begin
                        protocol_d.state = UART_IDLE;
                    end
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
                    protocol_d.shift[protocol_q.bit_index] = rx_sync;
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
                    if (rx_sync) begin
                        byte_d = protocol_q.shift;
                        byte_valid_d = 1'b1;
                    end else begin
                        framing_error_d = 1'b1;
                    end
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

    // Why: every register shares one asynchronous reset, so the receiver leaves
    // reset as a whole and no half-received frame survives it.
    `DFF_ARST_VAL(protocol_q, protocol_d, clk_i, reset_i, PROTOCOL_RESET)
    `DFF_ARST_VAL(bit_clock_q, bit_clock_d, clk_i, reset_i, '0)
    `DFF_ARST_VAL(frac_acc_q, frac_acc_d, clk_i, reset_i, '0)
    `DFF_ARST_VAL(byte_o, byte_d, clk_i, reset_i, 8'h00)
    `DFF_ARST_VAL(byte_valid_o, byte_valid_d, clk_i, reset_i, 1'b0)
    `DFF_ARST_VAL(framing_error_o, framing_error_d, clk_i, reset_i, 1'b0)

    `ASSERT_CLOCKED_ARST_FATAL(clk_i, reset_i, !$isunknown(rx_sync),
        "n2a_uart_rx synchronized input is unknown")
    `ASSERT_CLOCKED_ARST_FATAL(clk_i, reset_i,
        !(byte_valid_o && framing_error_o),
        "n2a_uart_rx valid and error overlap")
endmodule
