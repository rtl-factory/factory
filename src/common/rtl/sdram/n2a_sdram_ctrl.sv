`include "src/common/rtl/pkg/macros.vh"
`include "src/common/rtl/pkg/assertion_macros.vh"

// One-transaction x16 SDRAM controller (IS42S16320D, CAS latency two) shared by
// every project (wiki/src/common/rtl/sdram/mas.md). A request moves BURST_BEATS
// x16 beats: one 16-bit word or one 128-bit line. OPEN_ROW keeps one row per
// bank open and precharges only on a row miss or before a refresh; without it
// every access is ACTIVATE, column command, PRECHARGE, and the response comes
// one clock after the precharge wait. The WAIT_* parameters are the idle
// clocks inserted after each command; init waits are at least INIT_WAIT_MIN.
// The defaults are legal for the IS42S16320D-7 at 25 MHz.
// Commands, DQ output enable and DQM are driven from registered state; the
// board top drives DRAM_CLK as the inverted system clock, so read data
// launched by the device is captured at the edge ending the clock after the
// READ command, one beat per clock.
module n2a_sdram_ctrl #(
    parameter int unsigned BURST_BEATS = 1,
    parameter bit OPEN_ROW = 1'b0,
    parameter bit ROW_XOR_BANK = 1'b0,
    parameter int unsigned POWERUP_CLOCKS = 5000,
    parameter int unsigned INIT_REFRESHES = 2,
    parameter int unsigned INIT_WAIT_MIN = 2,
    parameter int unsigned WAIT_RCD = 0,
    parameter int unsigned WAIT_RP = 0,
    parameter int unsigned WAIT_RFC = 0,
    parameter int unsigned WAIT_WR = 1,
    parameter int unsigned WAIT_MRD = 0,
    parameter int unsigned REFRESH_DUE = 163,
    parameter int unsigned REFRESH_DEADLINE = 194,
    parameter bit REFRESH_AGE_FROM_MODE = 1'b0
) (
    input  logic                       clk_i,
    input  logic                       reset_i,
    // Request: held until the edge that samples request_ready_o high. The byte
    // address must be aligned to the burst; a misaligned request is answered
    // with response_error_o and no access. Reads ignore the byte enables.
    input  logic                       request_valid_i,
    output logic                       request_ready_o,
    input  logic                       request_write_i,
    input  logic [25:0]                request_address_i,
    input  logic [16*BURST_BEATS-1:0]  request_write_data_i,
    input  logic [2*BURST_BEATS-1:0]   request_byte_enable_i,
    // Exactly one response pulse per accepted request, writes included.
    output logic                       response_valid_o,
    output logic [16*BURST_BEATS-1:0]  response_data_o,
    output logic                       response_error_o,
    output logic                       idle_o,
    output logic                       init_done_o,

    output logic [12:0]                sdram_address_o,
    output logic [1:0]                 sdram_bank_o,
    output logic                       sdram_cke_o,
    output logic                       sdram_cs_n_o,
    output logic                       sdram_ras_n_o,
    output logic                       sdram_cas_n_o,
    output logic                       sdram_we_n_o,
    output logic [1:0]                 sdram_dqm_o,
    output logic [15:0]                sdram_dq_o,
    output logic                       sdram_dq_oe_o,
    input  logic [15:0]                sdram_dq_i
);
    import n2a_sdram_pkg::*;

    localparam int unsigned DATA_BITS = 16 * BURST_BEATS;
    localparam int unsigned LANES = 2 * BURST_BEATS;
    localparam int unsigned ALIGN_BITS = $clog2(LANES);
    localparam t_sdram_map MAP = ROW_XOR_BANK ? SDRAM_MAP_ROW_XOR_BANK : SDRAM_MAP_BANK_ROW_COLUMN;
    localparam logic [2:0] LAST_BEAT = 3'(BURST_BEATS - 1);
    localparam int unsigned INIT_WAIT_RP = WAIT_RP > INIT_WAIT_MIN ? WAIT_RP : INIT_WAIT_MIN;
    localparam int unsigned INIT_WAIT_RFC = WAIT_RFC > INIT_WAIT_MIN ? WAIT_RFC : INIT_WAIT_MIN;
    localparam int unsigned INIT_WAIT_MRD = WAIT_MRD > INIT_WAIT_MIN ? WAIT_MRD : INIT_WAIT_MIN;
    // An upper bound on the clocks from accepting a request at age
    // REFRESH_DUE - 1 to the REFRESH command: it adds the precharge, activate,
    // recovery and burst terms of every path, so it is not the exact worst case.
    localparam int unsigned WORST_TO_REFRESH = 6 + 2 * WAIT_RP + WAIT_RCD + WAIT_WR + BURST_BEATS;

    `ASSERT_PARAM(g_invalid_burst, BURST_BEATS == 1 || BURST_BEATS == 8,
        "n2a_sdram_ctrl BURST_BEATS must be 1 or 8")
    `ASSERT_PARAM(g_invalid_refresh, REFRESH_DUE + WORST_TO_REFRESH <= REFRESH_DEADLINE &&
        REFRESH_DEADLINE < 511, "n2a_sdram_ctrl refresh due leaves too little margin before the deadline")
    `ASSERT_PARAM(g_invalid_waits, POWERUP_CLOCKS < 8192 && INIT_REFRESHES >= 1 && INIT_REFRESHES <= 15 &&
        INIT_WAIT_RP < 8192 && INIT_WAIT_RFC < 8192 && INIT_WAIT_MRD < 8192 && WAIT_RCD < 8192 &&
        WAIT_WR < 8192, "n2a_sdram_ctrl wait counts must fit their counters")
    // Ref: IS42S16320D-7 at 25 MHz: tRFC (two clocks) needs one idle clock between
    // the init refreshes, and tWR (two clocks) one after a close-page write beat;
    // every other minimum is met by the command sequence itself.
    `ASSERT_PARAM(g_unsafe_waits, INIT_WAIT_RFC >= 1 && (OPEN_ROW || WAIT_WR >= 1),
        "n2a_sdram_ctrl waits are below the IS42S16320D-7 minimums at 25 MHz")

    typedef enum logic [3:0] {
        SDRAM_POWERUP,
        SDRAM_INIT_PRECHARGE,
        SDRAM_INIT_REFRESH,
        SDRAM_INIT_MODE,
        SDRAM_WAIT,
        SDRAM_IDLE,
        SDRAM_PRECHARGE,
        SDRAM_PRECHARGE_ALL,
        SDRAM_REFRESH,
        SDRAM_ACTIVATE,
        SDRAM_READ,
        SDRAM_READ_CAPTURE,
        SDRAM_WRITE,
        SDRAM_RESPOND
    } t_sdram_phase;

    typedef struct packed {
        t_sdram_phase           phase;
        t_sdram_phase           after_wait;
        logic [12:0]            count;
        logic [8:0]             refresh_age;
        logic                   refresh_tracking;
        logic [3:0]             init_refreshes;
        logic                   init_done;
        logic [3:0]             open;
        logic [3:0][12:0]       open_row;
        logic                   write;
        logic [25:0]            address;
        logic [DATA_BITS-1:0]   write_data;
        logic [LANES-1:0]       byte_enable;
        logic [DATA_BITS-1:0]   read_data;
        logic [2:0]             beat;
        logic                   response_valid;
        logic                   response_error;
    } t_sdram_regs;

    localparam t_sdram_regs REGS_RESET = '{
        phase: SDRAM_POWERUP, after_wait: SDRAM_POWERUP, count: '0, refresh_age: '0,
        refresh_tracking: 1'b0, init_refreshes: '0, init_done: 1'b0, open: '0, open_row: '0,
        write: 1'b0, address: '0, write_data: '0, byte_enable: '0, read_data: '0, beat: '0,
        response_valid: 1'b0, response_error: 1'b0};

    t_sdram_regs regs_q, regs_d;
    logic [1:0]  bank, request_bank;
    logic [12:0] row, request_row;
    logic        request_misaligned;

    assign bank = sdram_bank(MAP, regs_q.address);
    assign row = sdram_row(MAP, regs_q.address);
    assign request_bank = sdram_bank(MAP, request_address_i);
    assign request_row = sdram_row(MAP, request_address_i);
    assign request_misaligned = request_address_i[ALIGN_BITS-1:0] != '0;

    assign idle_o = !reset_i && regs_q.phase == SDRAM_IDLE;
    assign init_done_o = !reset_i && regs_q.init_done;
    assign request_ready_o = idle_o && regs_q.init_done && int'(regs_q.refresh_age) < REFRESH_DUE;
    assign response_valid_o = !reset_i && regs_q.response_valid;
    assign response_error_o = !reset_i && regs_q.response_error;
    assign response_data_o = regs_q.read_data;

    always_comb begin
        sdram_address_o = '0;
        sdram_bank_o = bank;
        sdram_cke_o = !reset_i;
        sdram_cs_n_o = reset_i;
        sdram_ras_n_o = 1'b1;
        sdram_cas_n_o = 1'b1;
        sdram_we_n_o = 1'b1;
        // Why: masks stay high until the mode register is set, so no stray write
        // lands during power-up.
        sdram_dqm_o = regs_q.init_done ? 2'b00 : 2'b11;
        sdram_dq_o = regs_q.write_data[regs_q.beat*16 +: 16];
        sdram_dq_oe_o = 1'b0;
        case (regs_q.phase)
            SDRAM_INIT_PRECHARGE, SDRAM_PRECHARGE_ALL: begin
                sdram_address_o[10] = 1'b1;
                sdram_ras_n_o = 1'b0; sdram_we_n_o = 1'b0;
            end
            SDRAM_PRECHARGE: begin
                // Ref: IS42S16320D datasheet PRECHARGE: A10 low precharges only the
                // addressed bank. Why: close page has one bank open, and precharges
                // all with A10 high, as the nand2mario controller did.
                sdram_address_o[10] = !OPEN_ROW;
                sdram_ras_n_o = 1'b0; sdram_we_n_o = 1'b0;
            end
            SDRAM_INIT_REFRESH, SDRAM_REFRESH: begin
                sdram_ras_n_o = 1'b0; sdram_cas_n_o = 1'b0;
            end
            SDRAM_INIT_MODE: begin
                sdram_address_o = sdram_mode(BURST_BEATS);
                sdram_bank_o = 2'b00;
                sdram_ras_n_o = 1'b0; sdram_cas_n_o = 1'b0; sdram_we_n_o = 1'b0;
            end
            SDRAM_ACTIVATE: begin
                sdram_address_o = row;
                sdram_ras_n_o = 1'b0;
            end
            SDRAM_READ: begin
                // Ref: IS42S16320D datasheet READ: A10 low leaves auto-precharge off.
                sdram_address_o[9:0] = sdram_column(regs_q.address);
                sdram_cas_n_o = 1'b0;
            end
            SDRAM_WRITE: begin
                // Why: WRITE goes out with beat zero and the later beats follow on NOP
                // clocks, so the beats form one burst; each drives DQ with its own
                // byte mask so masked lanes stay unwritten.
                sdram_address_o[9:0] = sdram_column(regs_q.address);
                if (regs_q.beat == 3'd0) begin
                    sdram_cas_n_o = 1'b0; sdram_we_n_o = 1'b0;
                end
                sdram_dqm_o = ~regs_q.byte_enable[regs_q.beat*2 +: 2];
                sdram_dq_oe_o = 1'b1;
            end
            default: begin end
        endcase
    end

    function automatic t_sdram_regs after(input t_sdram_regs regs, input int unsigned clocks,
                                          input t_sdram_phase target);
        t_sdram_regs next = regs;
        if (clocks == 0) begin
            next.phase = target;
        end else begin
            next.phase = SDRAM_WAIT;
            next.after_wait = target;
            next.count = 13'(clocks - 1);
        end
        return next;
    endfunction

    always_comb begin
        regs_d = regs_q;
        regs_d.response_valid = 1'b0;
        regs_d.response_error = 1'b0;
        // Why: the age counts clocks since the last REFRESH and saturates rather
        // than wrapping, so an overdue refresh never looks fresh.
        if (regs_q.refresh_tracking && regs_q.refresh_age != 9'h1ff)
            regs_d.refresh_age = regs_q.refresh_age + 9'd1;
        unique case (regs_q.phase)
            SDRAM_POWERUP: begin
                if (int'(regs_q.count) == POWERUP_CLOCKS - 1) begin
                    regs_d.count = '0;
                    regs_d.phase = SDRAM_INIT_PRECHARGE;
                end else regs_d.count = regs_q.count + 13'd1;
            end
            SDRAM_INIT_PRECHARGE: regs_d = after(regs_d, INIT_WAIT_RP, SDRAM_INIT_REFRESH);
            SDRAM_INIT_REFRESH: begin
                regs_d.refresh_age = '0;
                regs_d.refresh_tracking = 1'b1;
                regs_d.init_refreshes = regs_q.init_refreshes + 4'd1;
                regs_d = after(regs_d, INIT_WAIT_RFC,
                    int'(regs_q.init_refreshes) == INIT_REFRESHES - 1 ? SDRAM_INIT_MODE : SDRAM_INIT_REFRESH);
            end
            SDRAM_INIT_MODE: begin
                regs_d.init_done = 1'b1;
                if (REFRESH_AGE_FROM_MODE) regs_d.refresh_age = '0;
                regs_d = after(regs_d, INIT_WAIT_MRD, SDRAM_IDLE);
            end
            SDRAM_WAIT: begin
                if (regs_q.count == '0) regs_d.phase = regs_q.after_wait;
                else regs_d.count = regs_q.count - 13'd1;
            end
            SDRAM_IDLE: begin
                if (int'(regs_q.refresh_age) >= REFRESH_DUE) begin
                    regs_d.phase = regs_q.open != '0 ? SDRAM_PRECHARGE_ALL : SDRAM_REFRESH;
                end else if (request_valid_i) begin
                    if (request_misaligned) begin
                        regs_d.response_valid = 1'b1;
                        regs_d.response_error = 1'b1;
                    end else begin
                        regs_d.write = request_write_i;
                        regs_d.address = request_address_i;
                        regs_d.write_data = request_write_data_i;
                        regs_d.byte_enable = request_byte_enable_i;
                        regs_d.read_data = '0;
                        regs_d.beat = '0;
                        if (!regs_q.open[request_bank]) regs_d.phase = SDRAM_ACTIVATE;
                        else if (regs_q.open_row[request_bank] != request_row) regs_d.phase = SDRAM_PRECHARGE;
                        else regs_d.phase = request_write_i ? SDRAM_WRITE : SDRAM_READ;
                    end
                end
            end
            SDRAM_PRECHARGE: begin
                regs_d.open[bank] = 1'b0;
                regs_d = after(regs_d, WAIT_RP, OPEN_ROW ? SDRAM_ACTIVATE : SDRAM_RESPOND);
            end
            SDRAM_PRECHARGE_ALL: begin
                regs_d.open = '0;
                regs_d = after(regs_d, WAIT_RP, SDRAM_REFRESH);
            end
            SDRAM_REFRESH: begin
                regs_d.refresh_age = '0;
                regs_d = after(regs_d, WAIT_RFC, SDRAM_IDLE);
            end
            SDRAM_ACTIVATE: begin
                regs_d.open[bank] = 1'b1;
                regs_d.open_row[bank] = row;
                regs_d = after(regs_d, WAIT_RCD, regs_q.write ? SDRAM_WRITE : SDRAM_READ);
            end
            SDRAM_READ: regs_d.phase = SDRAM_READ_CAPTURE;
            SDRAM_READ_CAPTURE: begin
                regs_d.read_data[regs_q.beat*16 +: 16] = sdram_dq_i;
                regs_d.beat = regs_q.beat + 3'd1;
                if (regs_q.beat == LAST_BEAT) begin
                    if (OPEN_ROW) begin
                        regs_d.response_valid = 1'b1;
                        regs_d.phase = SDRAM_IDLE;
                    end else regs_d.phase = SDRAM_PRECHARGE;
                end
            end
            SDRAM_WRITE: begin
                regs_d.beat = regs_q.beat + 3'd1;
                if (regs_q.beat == LAST_BEAT) begin
                    if (OPEN_ROW) begin
                        regs_d.response_valid = 1'b1;
                        regs_d.phase = SDRAM_IDLE;
                    end else regs_d = after(regs_d, WAIT_WR, SDRAM_PRECHARGE);
                end
            end
            SDRAM_RESPOND: regs_d.phase = SDRAM_IDLE;
            default: regs_d = REGS_RESET;
        endcase
        // Why: a close-page access answers in the clock after its precharge wait,
        // which is the RESPOND clock, whichever path reached it.
        if (regs_d.phase == SDRAM_RESPOND) regs_d.response_valid = 1'b1;
    end

    `DFF_RST_VAL(regs_q, regs_d, clk_i, reset_i, REGS_RESET)

    `ASSERT_CLOCKED(clk_i, reset_i, regs_q.phase <= SDRAM_RESPOND && regs_q.after_wait <= SDRAM_RESPOND)
    // Why: immediate clocked forms, so the same checks run in simulation and in
    // the formal proof (wiki/src/common/fv/sdram/fv-plan.md).
    `ASSERT_CLOCKED_WHEN(clk_i, reset_i, sdram_dq_oe_o, regs_q.phase == SDRAM_WRITE)
    `ASSERT_CLOCKED_WHEN(clk_i, reset_i, regs_q.phase == SDRAM_ACTIVATE, !regs_q.open[bank])
    `ASSERT_CLOCKED_WHEN(clk_i, reset_i, regs_q.phase == SDRAM_REFRESH, regs_q.open == '0)
    `ASSERT_CLOCKED_WHEN(clk_i, reset_i, !OPEN_ROW && regs_q.phase == SDRAM_IDLE, regs_q.open == '0)
    `ASSERT_CLOCKED_FATAL_WHEN(clk_i, reset_i, regs_q.refresh_tracking,
        int'(regs_q.refresh_age) <= REFRESH_DEADLINE, "SDRAM refresh deadline exceeded")
    // Why: a newly presented request may differ from the preceding idle payload,
    // so stability is required only after a request was stalled at an edge.
    `ASSERT_HISTORY_RST(logic, request_stalled_q, request_valid_i && !request_ready_o, clk_i, reset_i)
    `ASSERT_HISTORY_RST(logic [26+DATA_BITS+LANES:0], request_payload_q,
        {request_write_i, request_address_i, request_write_data_i, request_byte_enable_i}, clk_i, reset_i)
    `ASSERT_CLOCKED_WHEN(clk_i, reset_i, request_stalled_q && request_valid_i,
        {request_write_i, request_address_i, request_write_data_i, request_byte_enable_i} == request_payload_q)
endmodule
