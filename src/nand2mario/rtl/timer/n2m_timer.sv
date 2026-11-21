`default_nettype none
`include "src/common/rtl/pkg/macros.vh"

// Game Boy timer registers DIV ($FF04), TIMA ($FF05), TMA ($FF06) and TAC
// ($FF07) at machine-cycle granularity in the 25 MHz domain. A 14-bit
// divider counts every machine-cycle enable; DIV reads its top eight bits.
// Reset leaves the divider at the DMG ABC boot-ROM handoff value (internal
// counter $ABCC in T-cycles, $2AF3 machine cycles, so DIV reads $AB).
// TIMA increments on each falling edge of the TAC-selected divider bit,
// including edges that a DIV write, a TAC write or the CPU's STOP divider
// reset produces. An increment from $FF leaves TIMA at $00 for one machine
// cycle; the next enable reloads TMA and pulses interrupt_o for one system
// cycle. Register accesses arrive on the peripheral port: the block decodes
// the four addresses, answers in the same cycle and applies a write on the
// edge that samples the response.
module n2m_timer (
    input  logic                               clk_i,
    input  logic                               reset_i,
    // Machine-cycle enable from the timebase (one system cycle per tick).
    input  logic                               machine_cycle_enable_i,
    // CPU STOP entry: zero the divider at this edge (coincides with a tick).
    input  logic                               divider_reset_i,
    // Peripheral port from the memory map; kind is not consulted.
    /* verilator lint_off UNUSEDSIGNAL */
    input  n2m_interfaces_pkg::t_bus_request   io_request_i,
    /* verilator lint_on UNUSEDSIGNAL */
    output n2m_interfaces_pkg::t_bus_response  io_response_o,
    output logic                               io_selected_o,
    // One-cycle pulse after the edge that reloaded TIMA from TMA.
    output logic                               interrupt_o,
    // DIV bit 4 (divider bit 10) for the APU frame sequencer.
    output logic                               sequencer_bit_o,
    // One-cycle pulse on each falling edge of divider bit 6 (8192 Hz, the
    // serial port's internal shift clock), including edges a divider reset
    // produces.
    output logic                               serial_clock_o
);
    import n2m_timer_pkg::*;

    localparam logic [15:0] DIV_ADDRESS  = 16'hFF04;
    localparam logic [15:0] TIMA_ADDRESS = 16'hFF05;
    localparam logic [15:0] TMA_ADDRESS  = 16'hFF06;
    localparam logic [15:0] TAC_ADDRESS  = 16'hFF07;

    logic [13:0] divider_q, divider_d, divider_advanced;
    logic [13:0] select_mask_q, select_mask_d;
    logic [7:0]  tima_q, tima_d, tima_base;
    logic [7:0]  tma_q, tma_d;
    logic [2:0]  tac_q, tac_d;
    t_timer_reload reload_q, reload_d;
    logic        interrupt_q, interrupt_d;
    logic        tick, access, write_div, write_tima, write_tma, write_tac;
    logic        signal_old, signal_advanced, signal_new, falling;
    logic        reload_now, tima_write_accepted;
    logic [7:0]  write_data;

    assign tick = machine_cycle_enable_i;
    assign io_selected_o = io_request_i.address >= DIV_ADDRESS &&
                           io_request_i.address <= TAC_ADDRESS;
    assign access = io_request_i.valid && io_selected_o;
    assign write_data = io_request_i.write_data;
    assign write_div = access && io_request_i.write && io_request_i.address == DIV_ADDRESS;
    assign write_tima = access && io_request_i.write && io_request_i.address == TIMA_ADDRESS;
    assign write_tma = access && io_request_i.write && io_request_i.address == TMA_ADDRESS;
    assign write_tac = access && io_request_i.write && io_request_i.address == TAC_ADDRESS;

    // Why: the advance is applied first so a DIV write or the STOP divider reset
    // on the same edge wins and leaves zero; TAC and TMA take a written value on
    // the write edge.
    always_comb begin
        divider_advanced = divider_q + 14'(tick);
        divider_d = divider_advanced;
        if (write_div || divider_reset_i) divider_d = 14'd0;
        tac_d = write_tac ? write_data[2:0] : tac_q;
        tma_d = write_tma ? write_data : tma_q;
    end

    // Ref: Pan Docs "TAC": divider bit 7, 1, 3 or 5 (DMG system-counter bits 9,
    // 3, 5, 7 in machine-cycle units), periods 256, 4, 16 and 64 machine cycles.
    always_comb begin
        case (tac_q[1:0])
            2'd0:    select_mask_q = 14'h0080;
            2'd1:    select_mask_q = 14'h0002;
            2'd2:    select_mask_q = 14'h0008;
            default: select_mask_q = 14'h0020;
        endcase
        case (tac_d[1:0])
            2'd0:    select_mask_d = 14'h0080;
            2'd1:    select_mask_d = 14'h0002;
            2'd2:    select_mask_d = 14'h0008;
            default: select_mask_d = 14'h0020;
        endcase
    end

    // Ref: Pan Docs "Timer obscure behaviour": a falling edge of the enabled
    // selected signal across either step of the divider update (advance, then
    // reset/TAC replacement) increments TIMA; at most one increment per edge.
    always_comb begin
        signal_old = tac_q[2] && |(divider_q & select_mask_q);
        signal_advanced = tac_q[2] && |(divider_advanced & select_mask_q);
        signal_new = tac_d[2] && |(divider_d & select_mask_d);
        falling = (signal_old && !signal_advanced) || (signal_advanced && !signal_new);
    end

    // Ref: Pan Docs "Timer overflow behaviour". Priority on one edge: the
    // reload itself, then an accepted TIMA write, then a falling-edge increment.
    // Falling edges are ignored while the reload is pending.
    always_comb begin
        reload_now = reload_q == RELOAD_PENDING && tick;
        tima_write_accepted = write_tima && reload_q != RELOAD_HOLD && !reload_now;
        tima_base = tima_q;
        tima_d = tima_q;
        reload_d = reload_q;
        interrupt_d = 1'b0;
        if (reload_now) begin
            tima_d = tma_d;
            reload_d = RELOAD_HOLD;
            interrupt_d = 1'b1;
        end else if (reload_q == RELOAD_HOLD) begin
            if (tick) reload_d = RELOAD_IDLE;
            tima_base = write_tma ? write_data : tima_q;
            tima_d = tima_base;
            if (falling) begin
                tima_d = tima_base + 8'd1;
                if (tima_base == 8'hFF) reload_d = RELOAD_PENDING;
            end
        end else if (reload_q == RELOAD_PENDING) begin
            if (tima_write_accepted) begin
                tima_d = write_data;
                reload_d = RELOAD_IDLE;
            end
        end else begin
            if (tima_write_accepted) begin
                tima_d = write_data;
            end else if (falling) begin
                tima_d = tima_q + 8'd1;
                if (tima_q == 8'hFF) reload_d = RELOAD_PENDING;
            end
        end
    end

    `DFF_RST_VAL(divider_q, divider_d, clk_i, reset_i, DIVIDER_RESET)
    `DFF_RST(tima_q, tima_d, clk_i, reset_i)
    `DFF_RST(tma_q, tma_d, clk_i, reset_i)
    `DFF_RST(tac_q, tac_d, clk_i, reset_i)
    `DFF_RST_VAL(reload_q, reload_d, clk_i, reset_i, RELOAD_IDLE)
    `DFF_RST(interrupt_q, interrupt_d, clk_i, reset_i)
    assign interrupt_o = interrupt_q;

    // Ref: Pan Docs "Timer and Divider Registers": DIV is the divider's top
    // byte and TAC's five unused bits read high. Why: read_data is not gated by
    // io_selected_o because the system fan-out answers every other address.
    always_comb begin
        io_response_o.valid = access;
        io_response_o.fault = 1'b0;
        case (io_request_i.address)
            DIV_ADDRESS:  io_response_o.read_data = divider_q[13:6];
            TIMA_ADDRESS: io_response_o.read_data = tima_q;
            TMA_ADDRESS:  io_response_o.read_data = tma_q;
            TAC_ADDRESS:  io_response_o.read_data = {5'b11111, tac_q};
            default:      io_response_o.read_data = 8'h00;
        endcase
    end

    `ASSERT_KNOWN(timer_controls_known, clk_i, reset_i,
        {machine_cycle_enable_i, divider_reset_i, io_request_i.valid})
    `ASSERT(timer_access_known, clk_i, reset_i,
        !io_request_i.valid || !$isunknown({io_request_i.write, io_request_i.address}))
    `ASSERT(timer_write_known, clk_i, reset_i,
        !(access && io_request_i.write) || !$isunknown(write_data))
    `ASSERT(timer_divider_reset_on_tick, clk_i, reset_i,
        !divider_reset_i || machine_cycle_enable_i)
    `ASSERT(timer_reload_state_legal, clk_i, reset_i,
        reload_q inside {RELOAD_IDLE, RELOAD_PENDING, RELOAD_HOLD})
    `ASSERT(timer_interrupt_follows_reload, clk_i, reset_i,
        interrupt_q |-> $past(reload_q) == RELOAD_PENDING && $past(tick))
    assign sequencer_bit_o = divider_q[10];
    assign serial_clock_o = divider_q[6] && !divider_d[6];
endmodule
