// Owned cycle-level x16 SDRAM device model for the nand2mario SDRAM
// controller (simulation only; independent of the controller RTL). It
// implements the controller-edge abstraction of the DE10-Lite board
// relationship the storage HAS states: the controller launches a command at
// its rising edge, the device samples it at its own rising edge 20 ns later
// (dram_clk is the inverted system clock), and read data launched by the
// device at its next rising edge is captured at the following controller
// edge. Here that is: a command presented during controller clock N is taken
// at the edge ending clock N; a READ taken at that edge drives the word
// during clock N+1 only (CL2, burst length one); a WRITE takes the data and
// byte mask presented in its own clock. Any other phase relationship on the
// board is a board-top concern this model does not hide: the controller must
// capture at exactly that edge or it reads the poison value.
//
// Timing is checked in controller clocks (40 ns) against the IS42S16320D
// minimums: tRCD and tRP one clock (15 ns), tRC, tRAS, tRFC and tMRD two
// clocks (60/42/60 ns, 2 clocks), tWR two clocks after the write beat, one
// clock from a READ to its PRECHARGE, 100 us (2500 clocks) of NOP after CKE
// rises before the first command, PRECHARGE ALL first, eight AUTO REFRESH
// before the mode register set, the mode register exactly BL1/sequential/CL2,
// consecutive refreshes at most 195 clocks apart (64 ms / 8192 rows) once the
// mode register is set, and a bus turnaround of one idle clock after the
// device drove DQ. Every violation is fatal. Storage is indexed by
// {bank, row, column} and filled at time zero with seeded nonzero garbage
// (contents are undefined at power-up; nothing may rely on zeros).
`default_nettype none

module n2m_sdram_model #(
    parameter int unsigned FILL_SEED = 32'h2545F491
) (
    input  logic        clk_i,
    input  logic        cke_i,
    input  logic        cs_n_i,
    input  logic        ras_n_i,
    input  logic        cas_n_i,
    input  logic        we_n_i,
    input  logic [1:0]  ba_i,
    input  logic [12:0] addr_i,
    input  logic [1:0]  dqm_i,        // {DQMH, DQML}
    input  logic [15:0] dq_i,
    input  logic        dq_oe_i,
    output logic [15:0] dq_o
);
    localparam logic [15:0] POISON = 16'h0BAD;
    localparam logic [12:0] MODE_BL1_SEQ_CL2 = 13'h020;
    localparam longint TRCD_MIN = 1, TRP_MIN = 1, TRC_MIN = 2, TRAS_MIN = 2, TRFC_MIN = 2;
    localparam longint TMRD_MIN = 2, TWR_MIN = 2, READ_TO_PRECHARGE_MIN = 1;
    localparam longint POWERUP_MIN = 2500, REFRESH_GAP_MAX = 195;
    localparam int INIT_REFRESHES = 8;

    // Ref: IS42S16320D datasheet: 4 banks x 8192 rows x 1024 columns of 16 bits.
    logic [15:0] memory [0:(1 << 25) - 1];

    bit [12:0] open_row_q [0:3];
    bit [3:0]  open_q;
    bit        mode_set_q, first_command_q, precharged_all_q;
    int        init_refreshes_q;
    bit        read_out_q, read_out_prev_q;
    bit [15:0] dq_q;
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

    // Why: SDRAM has no power-up value, so seed nonzero garbage everywhere before the first edge.
    logic [31:0] lfsr;
    initial begin
        lfsr = FILL_SEED == 0 ? 32'h2545F491 : FILL_SEED;
        for (int i = 0; i < (1 << 25); i++) begin
            lfsr = {lfsr[30:0], lfsr[31] ^ lfsr[21] ^ lfsr[1] ^ lfsr[0]};
            memory[i] = lfsr[15:0] == 16'h0000 ? 16'h5A5A : lfsr[15:0];
        end
    end

    always_ff @(posedge clk_i) begin
        now_q <= now_q + 1;
        if (!cke_i) cke_rise_q <= now_q + 1;
        read_out_prev_q <= read_out_q;
        read_out_q <= read;
        dq_q <= read ? memory[word] : POISON;

        // Why: the controller may drive DQ only for a WRITE, never while the device
        // drives or in the clock after it stopped, or the bus contends.
        if (dq_oe_i) begin
            assert (write) else $fatal(1, "n2m_sdram_model: DQ driven outside a WRITE");
            assert (!read_out_q && !read_out_prev_q)
                else $fatal(1, "n2m_sdram_model: DQ bus turnaround violation");
        end
        if (!nop) begin
            assert (now_q - cke_rise_q >= POWERUP_MIN)
                else $fatal(1, "n2m_sdram_model: command inside the 100 us power-up wait");
            if (!first_command_q) begin
                assert (precharge && addr_i[10])
                    else $fatal(1, "n2m_sdram_model: first command is not PRECHARGE ALL");
                first_command_q <= 1'b1;
            end
        end
        if (activate) begin
            assert (mode_set_q) else $fatal(1, "n2m_sdram_model: ACTIVATE before the mode register set");
            assert (!open_q[ba_i]) else $fatal(1, "n2m_sdram_model: ACTIVATE to an open bank");
            assert (now_q - last_precharge_q[ba_i] >= TRP_MIN) else $fatal(1, "n2m_sdram_model: tRP violation");
            assert (now_q - last_activate_q[ba_i] >= TRC_MIN) else $fatal(1, "n2m_sdram_model: tRC violation");
            assert (now_q - last_refresh_q >= TRFC_MIN) else $fatal(1, "n2m_sdram_model: tRFC violation");
            assert (now_q - last_mode_q >= TMRD_MIN) else $fatal(1, "n2m_sdram_model: tMRD violation");
            open_row_q[ba_i] <= addr_i;
            open_q[ba_i] <= 1'b1;
            last_activate_q[ba_i] <= now_q;
        end
        if (read || write) begin
            assert (mode_set_q) else $fatal(1, "n2m_sdram_model: column command before the mode register set");
            assert (open_q[ba_i]) else $fatal(1, "n2m_sdram_model: column command without an open row");
            assert (now_q - last_activate_q[ba_i] >= TRCD_MIN) else $fatal(1, "n2m_sdram_model: tRCD violation");
            assert (!addr_i[10]) else $fatal(1, "n2m_sdram_model: auto-precharge is not modeled");
        end
        if (read) begin
            assert (dqm_i == 2'b00) else $fatal(1, "n2m_sdram_model: masked READ is not modeled");
            last_read_q[ba_i] <= now_q;
        end
        if (write) begin
            assert (dq_oe_i) else $fatal(1, "n2m_sdram_model: WRITE without DQ drive");
            if (!dqm_i[0]) memory[word][7:0] <= dq_i[7:0];
            if (!dqm_i[1]) memory[word][15:8] <= dq_i[15:8];
            last_write_q[ba_i] <= now_q;
        end
        if (precharge) begin
            for (int b = 0; b < 4; b++) begin
                if (addr_i[10] || ba_i == 2'(b)) begin
                    if (open_q[b]) begin
                        assert (now_q - last_activate_q[b] >= TRAS_MIN) else $fatal(1, "n2m_sdram_model: tRAS violation");
                        assert (now_q - last_write_q[b] >= TWR_MIN) else $fatal(1, "n2m_sdram_model: tWR violation");
                        assert (now_q - last_read_q[b] >= READ_TO_PRECHARGE_MIN)
                            else $fatal(1, "n2m_sdram_model: READ to PRECHARGE violation");
                    end
                    open_q[b] <= 1'b0;
                    last_precharge_q[b] <= now_q;
                end
            end
            if (addr_i[10]) precharged_all_q <= 1'b1;
        end
        if (refresh) begin
            assert (open_q == '0) else $fatal(1, "n2m_sdram_model: REFRESH with an open row");
            assert (precharged_all_q) else $fatal(1, "n2m_sdram_model: REFRESH before the initial PRECHARGE ALL");
            for (int b = 0; b < 4; b++)
                assert (now_q - last_precharge_q[b] >= TRP_MIN) else $fatal(1, "n2m_sdram_model: refresh tRP violation");
            assert (now_q - last_refresh_q >= TRFC_MIN) else $fatal(1, "n2m_sdram_model: tRFC violation");
            if (mode_set_q)
                assert (now_q - last_refresh_q <= REFRESH_GAP_MAX)
                    else $fatal(1, "n2m_sdram_model: refresh interval exceeded");
            if (!mode_set_q) init_refreshes_q <= init_refreshes_q + 1;
            last_refresh_q <= now_q;
        end
        if (mode) begin
            assert (open_q == '0) else $fatal(1, "n2m_sdram_model: mode register set with an open row");
            assert (init_refreshes_q >= INIT_REFRESHES)
                else $fatal(1, "n2m_sdram_model: mode register set before eight initial refreshes");
            assert (now_q - last_refresh_q >= TRFC_MIN) else $fatal(1, "n2m_sdram_model: MRS tRFC violation");
            assert (addr_i == MODE_BL1_SEQ_CL2 && ba_i == 2'b00)
                else $fatal(1, "n2m_sdram_model: mode register is not burst-one sequential CAS-two");
            mode_set_q <= 1'b1;
            last_mode_q <= now_q;
        end
        // Why: the refresh branch checks only gaps between refreshes, so the first refresh
        // after the mode register set needs this check to stay within the interval.
        if (mode_set_q && !refresh)
            assert (now_q - last_refresh_q <= REFRESH_GAP_MAX)
                else $fatal(1, "n2m_sdram_model: refresh interval exceeded");
    end
endmodule
