`default_nettype none
`include "src/common/rtl/pkg/macros.vh"

// Silent DMG APU register block on the peripheral port
// (wiki/src/nand2mario/rtl/audio/mas.md). Full register behaviour, no audio:
// NR52 power control, channel status bits, the 512 Hz frame sequencer with
// length counters and the channel-1 sweep overflow check, DAC-enable effects
// and wave RAM. There is no waveform generator, envelope output, DAC or mixer.
// Read-back masks restate the SameBoy Core/apu.c read_mask table (MIT); each
// read is (mask | stored byte). Accesses answer in the same cycle; a write
// lands on the edge that samples the response, after any frame-sequencer
// step of that edge.
module n2m_apu (
    input  logic                               clk_i,
    input  logic                               reset_i,
    // DIV bit 4 (machine-cycle divider bit 10); each falling edge is one
    // frame-sequencer step.
    input  logic                               sequencer_bit_i,
    // Peripheral port from the memory map; kind is not consulted.
    /* verilator lint_off UNUSEDSIGNAL */
    input  n2m_interfaces_pkg::t_bus_request   io_request_i,
    /* verilator lint_on UNUSEDSIGNAL */
    output n2m_interfaces_pkg::t_bus_response  io_response_o,
    output logic                               io_selected_o
);
    localparam logic [15:0] REGISTER_START = 16'hFF10;
    localparam logic [15:0] NR52_ADDRESS   = 16'hFF26;
    localparam logic [15:0] WAVE_START     = 16'hFF30;
    localparam logic [15:0] WAVE_END       = 16'hFF3F;
    // Register file index = address - $FF10 for $FF10-$FF25.
    localparam int NR10 = 0, NR11 = 1, NR12 = 2, NR13 = 3, NR14 = 4;
    localparam int NR21 = 6, NR22 = 7, NR24 = 9;
    localparam int NR30 = 10, NR31 = 11, NR34 = 14;
    localparam int NR41 = 16, NR42 = 17, NR44 = 19;
    localparam int REGISTERS = 22;
    localparam int NR50 = 20, NR51 = 21;
    // DMG post-boot state (Pan Docs): there is no boot ROM, so core reset
    // leaves the APU as the boot ROM does - power on, channel 1 active,
    // NR11 $BF, NR12 $F3, NR50 $77, NR51 $F3, every other register zero.
    localparam logic [REGISTERS-1:0][7:0] BOOT_REGS =
        (REGISTERS*8)'(8'hF3) << (NR51 * 8) | (REGISTERS*8)'(8'h77) << (NR50 * 8) |
        (REGISTERS*8)'(8'hF3) << (NR12 * 8) | (REGISTERS*8)'(8'hBF) << (NR11 * 8);

    logic [7:0] wave_q [0:15];
    logic [7:0] wave_d [0:15];
    logic [REGISTERS-1:0][7:0] regs_q, regs_d;
    logic       power_q, power_d;
    logic [3:0] active_q, active_d;
    logic [2:0] step_q, step_d;
    logic       sequencer_bit_q;
    logic [3:0][8:0] length_q, length_d;
    logic [10:0] shadow_q, shadow_d;
    logic [3:0] sweep_timer_q, sweep_timer_d;
    logic       sweep_enable_q, sweep_enable_d;

    logic       wave_selected, register_selected, access, write, wave_write, sequencer_tick;
    logic [3:0] wave_index;
    logic [4:0] register_index;
    logic [7:0] mask, value, read_data;
    logic [3:0] dac;

    assign io_selected_o = io_request_i.address >= REGISTER_START &&
                           io_request_i.address <= WAVE_END;
    assign wave_selected = io_request_i.address >= WAVE_START &&
                           io_request_i.address <= WAVE_END;
    assign register_selected = io_request_i.address >= REGISTER_START &&
                               io_request_i.address < NR52_ADDRESS;
    assign wave_index = io_request_i.address[3:0];
    assign register_index = 5'(io_request_i.address - REGISTER_START);
    assign access = io_request_i.valid && io_selected_o;
    assign write = access && io_request_i.write;
    assign value = io_request_i.write_data;
    assign wave_write = write && wave_selected;
    assign sequencer_tick = sequencer_bit_q && !sequencer_bit_i;

    // Ref: SameBoy read_mask; unlisted addresses (FF15, FF1F, FF27-FF2F) read $FF.
    always_comb begin
        case (io_request_i.address)
            16'hFF10: mask = 8'h80;  16'hFF11: mask = 8'h3F;  16'hFF12: mask = 8'h00;
            16'hFF13: mask = 8'hFF;  16'hFF14: mask = 8'hBF;  16'hFF16: mask = 8'h3F;
            16'hFF17: mask = 8'h00;  16'hFF18: mask = 8'hFF;  16'hFF19: mask = 8'hBF;
            16'hFF1A: mask = 8'h7F;  16'hFF1B: mask = 8'hFF;  16'hFF1C: mask = 8'h9F;
            16'hFF1D: mask = 8'hFF;  16'hFF1E: mask = 8'hBF;  16'hFF20: mask = 8'hFF;
            16'hFF21: mask = 8'h00;  16'hFF22: mask = 8'h00;  16'hFF23: mask = 8'hBF;
            16'hFF24: mask = 8'h00;  16'hFF25: mask = 8'h00;  16'hFF26: mask = 8'h70;
            default:  mask = 8'hFF;
        endcase
    end

    // Ref: Pan Docs "NR10": bit 3 negates, bits 2-0 shift. The shadow frequency
    // is checked twice (step and second check), and a channel-1 trigger loads
    // its own; a zero period reloads the timer with eight.
    logic [10:0] trigger_frequency, step_delta, second_delta, trigger_delta;
    logic [11:0] step_next, second_next, trigger_next;
    logic [3:0]  sweep_reload;
    assign trigger_frequency = {value[2:0], regs_q[NR13]};
    assign step_delta    = shadow_q >> regs_q[NR10][2:0];
    assign step_next     = regs_q[NR10][3] ? 12'(shadow_q) - 12'(step_delta)
                                           : 12'(shadow_q) + 12'(step_delta);
    assign second_delta  = step_next[10:0] >> regs_q[NR10][2:0];
    assign second_next   = regs_q[NR10][3] ? 12'(step_next[10:0]) - 12'(second_delta)
                                           : 12'(step_next[10:0]) + 12'(second_delta);
    assign trigger_delta = trigger_frequency >> regs_q[NR10][2:0];
    assign trigger_next  = regs_q[NR10][3] ? 12'(trigger_frequency) - 12'(trigger_delta)
                                           : 12'(trigger_frequency) + 12'(trigger_delta);
    assign sweep_reload  = regs_q[NR10][6:4] == 3'd0 ? 4'd8 : {1'b0, regs_q[NR10][6:4]};

    always_comb begin
        logic [3:0][8:0] counter;
        logic       next_clocks_length, trigger, enabling;
        int         ch;
        logic [8:0] full;

        regs_d = regs_q;
        for (int i = 0; i < 16; i++) wave_d[i] = wave_q[i];
        power_d = power_q;
        active_d = active_q;
        step_d = step_q;
        shadow_d = shadow_q;
        sweep_timer_d = sweep_timer_q;
        sweep_enable_d = sweep_enable_q;
        counter = length_q;

        // Ref: Pan Docs "Audio details" (DIV-APU): length on even steps, sweep on 2 and 6.
        if (power_q && sequencer_tick) begin
            if (!step_q[0]) begin
                for (int i = 0; i < 4; i++) begin
                    if (regs_q[NR14 + 5 * i][6] && counter[i] != 9'd0) begin
                        counter[i] = counter[i] - 9'd1;
                        if (counter[i] == 9'd0) active_d[i] = 1'b0;
                    end
                end
            end
            if (step_q == 3'd2 || step_q == 3'd6) begin
                if (sweep_timer_q != 4'd0) sweep_timer_d = sweep_timer_q - 4'd1;
                if (sweep_timer_q == 4'd1) begin
                    sweep_timer_d = sweep_reload;
                    if (sweep_enable_q && regs_q[NR10][6:4] != 3'd0) begin
                        if (step_next > 12'd2047) active_d[0] = 1'b0;
                        else if (regs_q[NR10][2:0] != 3'd0) begin
                            shadow_d = step_next[10:0];
                            regs_d[NR13] = step_next[7:0];
                            regs_d[NR14] = {regs_q[NR14][7:3], step_next[10:8]};
                            if (second_next > 12'd2047) active_d[0] = 1'b0;
                        end
                    end
                end
            end
            step_d = step_q + 3'd1;
        end

        // Ref: SameBoy length quirks: length is clocked on the edge that leaves an
        // even step; when the next step is odd the extra-clock quirks apply.
        next_clocks_length = !step_d[0];

        wave_d[wave_index] = value;

        if (write && io_request_i.address == NR52_ADDRESS) begin
            if (power_q && !value[7]) begin
                regs_d = '0;
                active_d = 4'h0;
                shadow_d = '0;
                sweep_timer_d = '0;
                sweep_enable_d = 1'b0;
            end
            if (!power_q && value[7]) step_d = 3'd0;
            power_d = value[7];
        end

        if (write && register_selected) begin
            if (power_q) regs_d[register_index] = value;
            // Ref: Pan Docs: on the DMG, length loads stay writable with power off.
            case (int'(register_index))
                NR11, NR21, NR41: begin
                    ch = int'(register_index) / 5;
                    counter[ch] = 9'd64 - 9'(value[5:0]);
                end
                NR31: counter[2] = 9'd256 - 9'(value);
                default: ;
            endcase
            if (power_q) begin
                case (int'(register_index))
                    NR14, NR24, NR34, NR44: begin
                        ch = int'(register_index) / 5;
                        full = ch == 2 ? 9'd256 : 9'd64;
                        trigger = value[7];
                        enabling = value[6] && !regs_q[register_index][6];
                        // Ref: Pan Docs "Audio details": enabling length in the first half of a
                        // period clocks it once.
                        if (enabling && !next_clocks_length && counter[ch] != 9'd0) begin
                            counter[ch] = counter[ch] - 9'd1;
                            if (counter[ch] == 9'd0 && !trigger) active_d[ch] = 1'b0;
                        end
                        if (trigger) begin
                            if (dac[ch]) active_d[ch] = 1'b1;
                            if (counter[ch] == 9'd0) begin
                                counter[ch] = full;
                                if (value[6] && !next_clocks_length) counter[ch] = full - 9'd1;
                            end
                            if (ch == 0) begin
                                shadow_d = trigger_frequency;
                                sweep_timer_d = sweep_reload;
                                sweep_enable_d = regs_q[NR10][6:4] != 3'd0 || regs_q[NR10][2:0] != 3'd0;
                                if (regs_q[NR10][2:0] != 3'd0 && trigger_next > 12'd2047)
                                    active_d[0] = 1'b0;
                            end
                        end
                    end
                    NR12, NR22, NR42: if (value[7:3] == 5'd0) active_d[int'(register_index) / 5] = 1'b0;
                    NR30: if (!value[7]) active_d[2] = 1'b0;
                    default: ;
                endcase
            end
        end

        length_d = counter;
    end

    // Ref: Pan Docs "DACs": NRx2 bits 7-3 nonzero (channels 1, 2, 4), NR30 bit 7 (3).
    assign dac = {regs_q[NR42][7:3] != 5'd0, regs_q[NR30][7],
                  regs_q[NR22][7:3] != 5'd0, regs_q[NR12][7:3] != 5'd0};

    always_comb begin
        if (wave_selected) read_data = wave_q[wave_index];
        else if (io_request_i.address == NR52_ADDRESS) read_data = {power_q, 3'b111, active_q};
        else if (register_selected) read_data = mask | regs_q[register_index];
        else read_data = 8'hFF;
    end

    `DFF_ARRAY_RST_EN(wave_q, wave_d, clk_i, wave_write, reset_i)
    `DFF_RST_VAL(regs_q, regs_d, clk_i, reset_i, BOOT_REGS)
    `DFF_RST(length_q, length_d, clk_i, reset_i)
    `DFF_RST_VAL(power_q, power_d, clk_i, reset_i, 1'b1)
    `DFF_RST_VAL(active_q, active_d, clk_i, reset_i, 4'b0001)
    `DFF_RST(step_q, step_d, clk_i, reset_i)
    `DFF_RST(sequencer_bit_q, sequencer_bit_i, clk_i, reset_i)
    `DFF_RST(shadow_q, shadow_d, clk_i, reset_i)
    `DFF_RST(sweep_timer_q, sweep_timer_d, clk_i, reset_i)
    `DFF_RST(sweep_enable_q, sweep_enable_d, clk_i, reset_i)

    always_comb begin
        io_response_o.valid = access;
        io_response_o.fault = 1'b0;
        io_response_o.read_data = read_data;
    end
endmodule
