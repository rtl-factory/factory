`ifndef SYNTHESIS
// Cycle-level x16 SDRAM device (IS42S16320D) for simulation of any project's
// SDRAM controller (wiki/src/common/dv/sdram/dv-plan.md). It implements the
// controller-edge abstraction of a board whose DRAM_CLK is the inverted
// system clock: a command presented during controller clock N is taken at
// the edge ending clock N; a READ taken there drives beat k during clock
// N+1+k; a WRITE takes beat k with its byte mask in its own clock N+k. The
// mode register must be sequential, CAS latency two, burst BURST_BEATS.
//
// Every violation is fatal: command spacing in controller clocks (tRCD, tRP
// one; tRC, tRAS, tRFC, tMRD, tWR two; one clock from READ to PRECHARGE),
// POWERUP_CLOCKS of NOP after CKE before the first command, PRECHARGE ALL
// first, INIT_REFRESHES refreshes before the mode register set, refreshes at
// most REFRESH_GAP_MAX clocks apart once the mode is set, no command inside a
// burst, no auto-precharge, bursts starting at an aligned group, and DQ driven
// only by a write beat and never during or one clock after a read beat.
//
// Storage is indexed by {bank, row, column}. FILL seeds it with nonzero
// garbage at time zero (SDRAM has no power-up value). The preload port
// writes byte-enabled bytes at byte addresses mapped by ROW_XOR_BANK; a bench
// uses it only while the controller is held in reset.
module n2a_sdram_model #(
    parameter int unsigned BURST_BEATS = 1,
    // 0: bank [25:24], row [23:11], column [10:1] (nand2mario).
    // 1: row [25:13], bank [12:11] XOR [14:13], column [10:1] (nand2doom).
    parameter bit ROW_XOR_BANK = 1'b0,
    parameter int unsigned INIT_REFRESHES = 2,
    parameter int unsigned POWERUP_CLOCKS = 2500,
    parameter int unsigned REFRESH_GAP_MAX = 195,
    parameter bit FILL = 1'b1,
    parameter int unsigned FILL_SEED = 32'h2545F491
) (
    input  logic          clk_i,
    input  logic          cke_i,
    input  logic          cs_n_i,
    input  logic          ras_n_i,
    input  logic          cas_n_i,
    input  logic          we_n_i,
    input  logic [1:0]    ba_i,
    input  logic [12:0]   addr_i,
    input  logic [1:0]    dqm_i,
    input  logic [15:0]   dq_i,
    input  logic          dq_oe_i,
    output logic [15:0]   dq_o,
    input  logic          preload_valid_i,
    input  logic [25:0]   preload_address_i,
    input  logic [2047:0] preload_data_i,
    input  logic [255:0]  preload_byte_enable_i
);
    localparam logic [15:0] POISON = 16'h0BAD;
    // Ref: IS42S16320D datasheet mode register: sequential, CAS latency two, burst
    // length in A[2:0].
    localparam logic [12:0] MODE_VALUE = BURST_BEATS == 8 ? 13'h023 : 13'h020;
    localparam int unsigned GROUP_BITS = $clog2(BURST_BEATS);
    localparam longint TRCD_MIN = 1, TRP_MIN = 1, TRC_MIN = 2, TRAS_MIN = 2, TRFC_MIN = 2;
    localparam longint TMRD_MIN = 2, TWR_MIN = 2, READ_TO_PRECHARGE_MIN = 1;

    initial begin
        if (BURST_BEATS != 1 && BURST_BEATS != 8)
            $fatal(1, "n2a_sdram_model: BURST_BEATS must be 1 or 8");
    end

    logic [15:0] memory [0:(1 << 25) - 1];

    // Why: two-state device state starts at zero without a reset, which is
    // safe because DQ is gated by read_out_q.
    bit [12:0] open_row_q [0:3];
    bit [3:0]  open_q;
    bit        mode_set_q, first_command_q, precharged_all_q;
    int        init_refreshes_q;
    bit        read_out_q, read_out_prev_q;
    bit [15:0] dq_q;
    bit [24:0] read_word_q, write_word_q;
    bit [1:0]  write_bank_q;
    bit [2:0]  read_left_q, write_left_q;
    bit        read_busy_q, write_busy_q;
    // Why: the edge counter and last-command edges can start at zero, because
    // the power-up NOP period keeps the first command far from edge zero.
    longint    now_q, cke_rise_q;
    longint    last_activate_q [0:3];
    longint    last_precharge_q [0:3];
    longint    last_read_q [0:3];
    longint    last_write_q [0:3];
    longint    last_refresh_q, last_mode_q;
    logic [24:0] word;
    logic        selected, activate, read, write, precharge, refresh, mode, nop;

    assign selected  = cke_i && !cs_n_i;
    assign activate  = selected && !ras_n_i &&  cas_n_i &&  we_n_i;
    assign read      = selected &&  ras_n_i && !cas_n_i &&  we_n_i;
    assign write     = selected &&  ras_n_i && !cas_n_i && !we_n_i;
    assign precharge = selected && !ras_n_i &&  cas_n_i && !we_n_i;
    assign refresh   = selected && !ras_n_i && !cas_n_i &&  we_n_i;
    assign mode      = selected && !ras_n_i && !cas_n_i && !we_n_i;
    assign nop       = !selected || (ras_n_i && cas_n_i && we_n_i);
    assign word = {ba_i, open_row_q[ba_i], addr_i[9:0]};
    assign dq_o = read_out_q ? dq_q : POISON;

    // Sequential burst order wraps inside the aligned group of BURST_BEATS.
    function automatic bit [24:0] next_beat(input bit [24:0] current);
        bit [24:0] next = current;
        if (GROUP_BITS > 0)
            next[2:0] = (current[2:0] & ~3'(BURST_BEATS - 1)) | ((current[2:0] + 3'd1) & 3'(BURST_BEATS - 1));
        return next;
    endfunction

    // The device word {bank, row, column} that holds a byte address; bit 0 is
    // the byte lane.
    /* verilator lint_off UNUSEDSIGNAL */
    function automatic logic [24:0] preload_word(input logic [25:0] address);
        if (ROW_XOR_BANK) return {address[12:11] ^ address[14:13], address[25:13], address[10:1]};
        return address[25:1];
    endfunction
    /* verilator lint_on UNUSEDSIGNAL */

    logic [31:0] lfsr;
    initial begin
        if (FILL) begin
            lfsr = FILL_SEED == 0 ? 32'h2545F491 : FILL_SEED;
            for (int i = 0; i < (1 << 25); i++) begin
                lfsr = {lfsr[30:0], lfsr[31] ^ lfsr[21] ^ lfsr[1] ^ lfsr[0]};
                memory[i] = lfsr[15:0] == 16'h0000 ? 16'h5A5A : lfsr[15:0];
            end
        end
    end

    always_ff @(posedge clk_i) begin
        now_q <= now_q + 1;
        if (!cke_i) cke_rise_q <= now_q + 1;
        read_out_prev_q <= read_out_q;
        read_out_q <= read || read_busy_q;
        dq_q <= read ? memory[word] : read_busy_q ? memory[read_word_q] : POISON;

        if (read_busy_q) begin
            assert (nop) else $fatal(1, "n2a_sdram_model: command interrupts a read burst");
            read_word_q <= next_beat(read_word_q);
            read_left_q <= read_left_q - 3'd1;
            if (read_left_q == 3'd0) read_busy_q <= 1'b0;
        end
        if (write_busy_q) begin
            assert (nop) else $fatal(1, "n2a_sdram_model: command interrupts a write burst");
            assert (dq_oe_i) else $fatal(1, "n2a_sdram_model: write beat without DQ drive");
            if (!dqm_i[0]) memory[write_word_q][7:0] <= dq_i[7:0];
            if (!dqm_i[1]) memory[write_word_q][15:8] <= dq_i[15:8];
            last_write_q[write_bank_q] <= now_q;
            write_word_q <= next_beat(write_word_q);
            write_left_q <= write_left_q - 3'd1;
            if (write_left_q == 3'd0) write_busy_q <= 1'b0;
        end

        // Why: the controller may drive DQ only for a write beat, never while the
        // device drives or in the clock after it stopped, or the bus contends.
        if (dq_oe_i) begin
            assert (write || write_busy_q) else $fatal(1, "n2a_sdram_model: DQ driven outside a write burst");
            assert (!read_out_q && !read_out_prev_q)
                else $fatal(1, "n2a_sdram_model: DQ bus turnaround violation");
        end
        if (!nop) begin
            assert (now_q - cke_rise_q >= longint'(POWERUP_CLOCKS))
                else $fatal(1, "n2a_sdram_model: command inside the power-up wait");
            if (!first_command_q) begin
                assert (precharge && addr_i[10])
                    else $fatal(1, "n2a_sdram_model: first command is not PRECHARGE ALL");
                first_command_q <= 1'b1;
            end
        end
        if (preload_valid_i) begin
            for (int lane = 0; lane < 256; lane++) begin
                if (preload_byte_enable_i[lane]) begin
                    logic [25:0] byte_address;
                    byte_address = preload_address_i + 26'(lane);
                    if (byte_address[0])
                        memory[preload_word(byte_address)][15:8] <= preload_data_i[lane*8 +: 8];
                    else
                        memory[preload_word(byte_address)][7:0] <= preload_data_i[lane*8 +: 8];
                end
            end
        end
        if (activate) begin
            assert (mode_set_q) else $fatal(1, "n2a_sdram_model: ACTIVATE before the mode register set");
            assert (!open_q[ba_i]) else $fatal(1, "n2a_sdram_model: ACTIVATE to an open bank");
            assert (now_q - last_precharge_q[ba_i] >= TRP_MIN) else $fatal(1, "n2a_sdram_model: tRP violation");
            assert (now_q - last_activate_q[ba_i] >= TRC_MIN) else $fatal(1, "n2a_sdram_model: tRC violation");
            assert (now_q - last_refresh_q >= TRFC_MIN) else $fatal(1, "n2a_sdram_model: tRFC violation");
            assert (now_q - last_mode_q >= TMRD_MIN) else $fatal(1, "n2a_sdram_model: tMRD violation");
            open_row_q[ba_i] <= addr_i;
            open_q[ba_i] <= 1'b1;
            last_activate_q[ba_i] <= now_q;
        end
        if (read || write) begin
            assert (mode_set_q) else $fatal(1, "n2a_sdram_model: column command before the mode register set");
            assert (open_q[ba_i]) else $fatal(1, "n2a_sdram_model: column command without an open row");
            assert (now_q - last_activate_q[ba_i] >= TRCD_MIN) else $fatal(1, "n2a_sdram_model: tRCD violation");
            assert (!addr_i[10]) else $fatal(1, "n2a_sdram_model: auto-precharge is not modeled");
            assert ((addr_i[2:0] & 3'(BURST_BEATS - 1)) == 3'b0)
                else $fatal(1, "n2a_sdram_model: burst does not start an aligned group");
        end
        if (read) begin
            assert (dqm_i == 2'b00) else $fatal(1, "n2a_sdram_model: masked READ is not modeled");
            last_read_q[ba_i] <= now_q;
            if (BURST_BEATS > 1) begin
                read_busy_q <= 1'b1;
                read_word_q <= next_beat(word);
                read_left_q <= 3'(BURST_BEATS - 2);
            end
        end
        if (write) begin
            assert (dq_oe_i) else $fatal(1, "n2a_sdram_model: WRITE without DQ drive");
            if (!dqm_i[0]) memory[word][7:0] <= dq_i[7:0];
            if (!dqm_i[1]) memory[word][15:8] <= dq_i[15:8];
            last_write_q[ba_i] <= now_q;
            if (BURST_BEATS > 1) begin
                write_busy_q <= 1'b1;
                write_bank_q <= ba_i;
                write_word_q <= next_beat(word);
                write_left_q <= 3'(BURST_BEATS - 2);
            end
        end
        if (precharge) begin
            for (int b = 0; b < 4; b++) begin
                if (addr_i[10] || ba_i == 2'(b)) begin
                    if (open_q[b]) begin
                        assert (now_q - last_activate_q[b] >= TRAS_MIN) else $fatal(1, "n2a_sdram_model: tRAS violation");
                        assert (now_q - last_write_q[b] >= TWR_MIN) else $fatal(1, "n2a_sdram_model: tWR violation");
                        assert (now_q - last_read_q[b] >= READ_TO_PRECHARGE_MIN)
                            else $fatal(1, "n2a_sdram_model: READ to PRECHARGE violation");
                    end
                    open_q[b] <= 1'b0;
                    last_precharge_q[b] <= now_q;
                end
            end
            if (addr_i[10]) precharged_all_q <= 1'b1;
        end
        if (refresh) begin
            assert (open_q == '0) else $fatal(1, "n2a_sdram_model: REFRESH with an open row");
            assert (precharged_all_q) else $fatal(1, "n2a_sdram_model: REFRESH before the initial PRECHARGE ALL");
            for (int b = 0; b < 4; b++)
                assert (now_q - last_precharge_q[b] >= TRP_MIN) else $fatal(1, "n2a_sdram_model: refresh tRP violation");
            assert (now_q - last_refresh_q >= TRFC_MIN) else $fatal(1, "n2a_sdram_model: tRFC violation");
            if (mode_set_q)
                assert (now_q - last_refresh_q <= longint'(REFRESH_GAP_MAX))
                    else $fatal(1, "n2a_sdram_model: refresh interval exceeded");
            if (!mode_set_q) init_refreshes_q <= init_refreshes_q + 1;
            last_refresh_q <= now_q;
        end
        if (mode) begin
            assert (open_q == '0) else $fatal(1, "n2a_sdram_model: mode register set with an open row");
            assert (init_refreshes_q >= int'(INIT_REFRESHES))
                else $fatal(1, "n2a_sdram_model: mode register set before the initial refreshes");
            assert (now_q - last_refresh_q >= TRFC_MIN) else $fatal(1, "n2a_sdram_model: MRS tRFC violation");
            assert (addr_i == MODE_VALUE && ba_i == 2'b00)
                else $fatal(1, "n2a_sdram_model: mode register is not sequential CAS-two with the configured burst");
            mode_set_q <= 1'b1;
            last_mode_q <= now_q;
        end
        // Why: the refresh branch checks only gaps between refreshes, so the first
        // refresh after the mode register set needs this check too.
        if (mode_set_q && !refresh)
            assert (now_q - last_refresh_q <= longint'(REFRESH_GAP_MAX))
                else $fatal(1, "n2a_sdram_model: refresh interval exceeded");
    end
endmodule
`endif
