`default_nettype none
`include "src/common/rtl/pkg/macros.vh"

// Game Boy serial port registers SB ($FF01) and SC ($FF02) in the 25 MHz
// domain with no link partner. SC bit 7 is the transfer enable and bit 0
// the clock select (1 = internal); bits [6:1] read 1. With the internal
// clock selected, an enabled transfer shifts SB left one bit on each
// shift_clock_i pulse (the falling edge of divider bit 6, 8192 Hz, from
// the timer), shifting in 1 (the idle line of an absent partner) and
// collecting the outgoing bit, most significant first. The clock is free
// running: an SC write does not restart it, so the first shift lands
// wherever the divider phase puts it, as on the DMG. The eighth shift
// completes the byte:
// bit 7 clears, interrupt_o pulses for one system cycle and tx_valid_o
// presents the transmitted byte on tx_data_o for the same cycle. With the
// external clock selected nothing shifts and the enable stays set until
// software clears it. Register accesses arrive on the peripheral port: the
// block decodes the two addresses, answers in the same cycle and applies a
// write on the edge that samples the response; a write wins over a shift
// on the same edge and an SC write restarts the bit count.
module n2m_serial (
    input  logic                               clk_i,
    input  logic                               reset_i,
    // Internal shift clock: one-cycle pulse per bit period from the timer.
    input  logic                               shift_clock_i,
    // Peripheral port from the memory map; kind is not consulted.
    /* verilator lint_off UNUSEDSIGNAL */
    input  n2m_interfaces_pkg::t_bus_request   io_request_i,
    /* verilator lint_on UNUSEDSIGNAL */
    output n2m_interfaces_pkg::t_bus_response  io_response_o,
    output logic                               io_selected_o,
    // One-cycle pulse after the edge that completed a byte: IF bit 3 request.
    output logic                               interrupt_o,
    // The transmitted byte, valid for the same cycle as interrupt_o; the
    // data holds until the next completion.
    output logic                               tx_valid_o,
    output logic [7:0]                         tx_data_o
);
    localparam logic [15:0] SB_ADDRESS = 16'hFF01;
    localparam logic [15:0] SC_ADDRESS = 16'hFF02;

    logic [7:0] sb_q, sb_d;
    logic       enable_q, enable_d, clock_q, clock_d;
    logic [2:0] bits_q, bits_d;
    logic [7:0] tx_shift_q, tx_shift_d, tx_data_q, tx_data_d;
    logic       tx_valid_q, tx_valid_d, interrupt_q, interrupt_d;
    logic       access, write_sb, write_sc, active, shift, complete;

    assign io_selected_o = io_request_i.address == SB_ADDRESS ||
                           io_request_i.address == SC_ADDRESS;
    assign access = io_request_i.valid && io_selected_o;
    assign write_sb = access && io_request_i.write && io_request_i.address == SB_ADDRESS;
    assign write_sc = access && io_request_i.write && io_request_i.address == SC_ADDRESS;

    // Ref: Pan Docs "Serial Data Transfer": a transfer is active while enabled
    // with the internal clock; each shift clock pulse shifts one bit and the
    // eighth shift completes the byte.
    always_comb begin
        active = enable_q && clock_q;
        shift = active && shift_clock_i;
        complete = shift && bits_q == 3'd7;
        sb_d = sb_q;
        enable_d = enable_q;
        clock_d = clock_q;
        bits_d = bits_q;
        tx_shift_d = tx_shift_q;
        tx_data_d = tx_data_q;
        if (shift) begin
            sb_d = {sb_q[6:0], 1'b1};
            tx_shift_d = {tx_shift_q[6:0], sb_q[7]};
            bits_d = bits_q + 3'd1;
        end
        if (complete) begin
            enable_d = 1'b0;
            tx_data_d = {tx_shift_q[6:0], sb_q[7]};
        end
        // Why: a write lands after the same edge's shift so the CPU's value wins: an
        // SB write replaces the shifted value, an SC write loads the control bits and
        // restarts the bit count.
        if (write_sb) sb_d = io_request_i.write_data;
        if (write_sc) begin
            enable_d = io_request_i.write_data[7];
            clock_d = io_request_i.write_data[0];
            bits_d = 3'd0;
        end
        interrupt_d = complete;
        tx_valid_d = complete;
    end

    `DFF_RST(sb_q, sb_d, clk_i, reset_i)
    `DFF_RST(enable_q, enable_d, clk_i, reset_i)
    `DFF_RST(clock_q, clock_d, clk_i, reset_i)
    `DFF_RST(bits_q, bits_d, clk_i, reset_i)
    `DFF_RST(tx_shift_q, tx_shift_d, clk_i, reset_i)
    `DFF_RST(tx_data_q, tx_data_d, clk_i, reset_i)
    `DFF_RST(tx_valid_q, tx_valid_d, clk_i, reset_i)
    `DFF_RST(interrupt_q, interrupt_d, clk_i, reset_i)
    assign interrupt_o = interrupt_q;
    assign tx_valid_o = tx_valid_q;
    assign tx_data_o = tx_data_q;

    // Ref: Pan Docs "Serial Data Transfer": SC reads its unused six bits high.
    // Why: read_data is not gated by io_selected_o because the system fan-out
    // answers every other address.
    always_comb begin
        io_response_o.valid = access;
        io_response_o.fault = 1'b0;
        io_response_o.read_data = io_request_i.address == SB_ADDRESS ?
            sb_q : {enable_q, 6'b111111, clock_q};
    end

    `ASSERT_KNOWN(serial_controls_known, clk_i, reset_i,
        {shift_clock_i, io_request_i.valid})
    `ASSERT(serial_access_known, clk_i, reset_i,
        !io_request_i.valid || !$isunknown({io_request_i.write, io_request_i.address}))
    `ASSERT(serial_write_known, clk_i, reset_i,
        !(access && io_request_i.write) || !$isunknown(io_request_i.write_data))
    `ASSERT(serial_counters_idle, clk_i, reset_i,
        $past(shift) || $past(write_sc) || bits_q == $past(bits_q))
    `ASSERT(serial_interrupt_follows_completion, clk_i, reset_i,
        interrupt_q == $past(complete) && tx_valid_q == $past(complete))
endmodule
