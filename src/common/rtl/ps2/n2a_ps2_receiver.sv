`include "src/common/rtl/pkg/macros.vh"

// Receive-only PS/2 line receiver: device clock and data pins to bytes
// (wiki/src/common/rtl/ps2/mas.md#receiver). Each pin passes its own
// two-stage synchronizer; data is sampled on registered falling clock edges.
// A frame is a low start bit, eight data bits LSB first, odd parity and a
// high stop bit. A good frame gives one byte_valid_o cycle on its stop edge;
// a bad start, parity or stop bit, or no falling edge for
// FRAME_TIMEOUT_CYCLES inside a frame, gives one errors_o cycle instead and
// discards the frame.
module n2a_ps2_receiver #(
    // Ref: no falling PS/2 clock edge for 200 us inside a frame discards it;
    // 5,000 clocks at the 25 MHz system clock.
    parameter int unsigned FRAME_TIMEOUT_CYCLES = 5000
) (
    input  logic                        clk_i,
    input  logic                        reset_i,
    // Asynchronous PS/2 device pins, idle high.
    input  logic                        ps2_clk_i,
    input  logic                        ps2_data_i,
    // Low: the line is ignored and a frame in progress is held.
    input  logic                        enable_i,
    // Abandons any frame in progress, including one whose stop edge is on
    // this clock; no byte or error is reported on an abort cycle.
    input  logic                        abort_i,
    output logic                        byte_valid_o,
    output logic [7:0]                  byte_o,
    output n2a_ps2_pkg::t_ps2_rx_errors errors_o
);
    import n2a_types_pkg::*;

    localparam int unsigned TIMEOUT_WIDTH = $clog2(FRAME_TIMEOUT_CYCLES);
    localparam logic [TIMEOUT_WIDTH-1:0] FRAME_TIMEOUT_LIMIT =
        TIMEOUT_WIDTH'(FRAME_TIMEOUT_CYCLES - 1);
    localparam logic [3:0] PARITY_BIT_INDEX = 4'd8;
    localparam logic [3:0] STOP_BIT_INDEX = 4'd9;
    localparam t_ps2_rx_state REGS_RESET = '{
        previous: 1'b1,
        active: 1'b0,
        index: 4'b0,
        data: 8'b0,
        parity: 1'b0};

    `ASSERT_PARAM(g_invalid_frame_timeout, FRAME_TIMEOUT_CYCLES >= 2,
        "n2a_ps2_receiver FRAME_TIMEOUT_CYCLES must be at least 2")

    logic ps2_clk_sync, ps2_data_sync;
    t_ps2_rx_state regs_q, regs_d;
    // Why: the frame timeout counter's width follows FRAME_TIMEOUT_CYCLES, so
    // it is a register of its own beside the packaged state record.
    logic [TIMEOUT_WIDTH-1:0] frame_timeout_q, frame_timeout_d;
    logic receiving, falling_edge, frame_complete, parity_ok, stop_ok;
    logic bad_start, timed_out;

    n2a_cdc_bit_sync #(.STAGES(2), .RESET_VALUE(1'b1)) u_clock_sync (
        .clk_i(clk_i), .reset_i(reset_i), .async_i(ps2_clk_i), .sync_o(ps2_clk_sync)
    );
    n2a_cdc_bit_sync #(.STAGES(2), .RESET_VALUE(1'b1)) u_data_sync (
        .clk_i(clk_i), .reset_i(reset_i), .async_i(ps2_data_i), .sync_o(ps2_data_sync)
    );

    assign receiving = enable_i && !abort_i;
    assign falling_edge = regs_q.previous && !ps2_clk_sync;
    assign frame_complete = receiving && regs_q.active && falling_edge &&
                            (regs_q.index == STOP_BIT_INDEX);
    // Ref: PS/2 parity is odd over the eight data bits and the parity bit.
    assign parity_ok = ^{regs_q.data, regs_q.parity};
    assign stop_ok = ps2_data_sync;
    assign bad_start = receiving && !regs_q.active && falling_edge && ps2_data_sync;
    assign timed_out = receiving && regs_q.active && !falling_edge &&
                       (frame_timeout_q == FRAME_TIMEOUT_LIMIT);

    assign byte_valid_o = frame_complete && parity_ok && stop_ok;
    assign byte_o = regs_q.data;
    assign errors_o = '{parity: frame_complete && !parity_ok,
                        frame: (frame_complete && !stop_ok) || bad_start,
                        timeout: timed_out};

    always_comb begin
        regs_d = regs_q;
        frame_timeout_d = frame_timeout_q;
        regs_d.previous = ps2_clk_sync;
        if (abort_i) begin
            regs_d.active = 1'b0;
            regs_d.index = 4'b0;
            frame_timeout_d = '0;
        end
        if (receiving) begin
            if (regs_q.active) begin
                if (falling_edge) begin
                    frame_timeout_d = '0;
                    if (regs_q.index < PARITY_BIT_INDEX) begin
                        regs_d.data[regs_q.index[2:0]] = ps2_data_sync;
                        regs_d.index = regs_q.index + 4'd1;
                    end else if (regs_q.index == PARITY_BIT_INDEX) begin
                        regs_d.parity = ps2_data_sync;
                        regs_d.index = STOP_BIT_INDEX;
                    end else begin
                        regs_d.active = 1'b0;
                        regs_d.index = 4'b0;
                    end
                end else if (timed_out) begin
                    regs_d.active = 1'b0;
                    regs_d.index = 4'b0;
                    frame_timeout_d = '0;
                end else begin
                    frame_timeout_d = frame_timeout_q + 1'b1;
                end
            end else if (falling_edge && !ps2_data_sync) begin
                regs_d.active = 1'b1;
                regs_d.index = 4'b0;
                frame_timeout_d = '0;
            end
        end
    end

    // Why: the receiver resets with its pin synchronizers, whose reset is
    // asynchronous (wiki/src/common/rtl/ps2/mas.md#clocks-and-resets).
    `DFF_ARST_VAL(regs_q, regs_d, clk_i, reset_i, REGS_RESET)
    `DFF_ARST_VAL(frame_timeout_q, frame_timeout_d, clk_i, reset_i, '0)

    `ASSERT_CLOCKED_ARST_FATAL(clk_i, reset_i,
        regs_q.index <= STOP_BIT_INDEX,
        "n2a_ps2_receiver bit index past the stop bit")
    `ASSERT_CLOCKED_ARST_FATAL(clk_i, reset_i,
        regs_q.active || (regs_q.index == 4'd0),
        "n2a_ps2_receiver bit index outside a frame")
    `ASSERT_CLOCKED_ARST_FATAL(clk_i, reset_i,
        !(byte_valid_o && (errors_o != '0)),
        "n2a_ps2_receiver reported a byte and an error together")
endmodule
