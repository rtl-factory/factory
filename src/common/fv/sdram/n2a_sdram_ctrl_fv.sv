// Formal harness for n2a_sdram_ctrl in one project configuration (LINE 1:
// nand2doom, LINE 0: nand2mario). Assumptions a_, properties p_, helpers h_,
// covers c_; each property is a row of wiki/src/common/fv/sdram/fv-plan.md,
// which also records the results. Spec: wiki/src/common/rtl/sdram/mas.md.
module n2a_sdram_ctrl_fv #(
    parameter bit LINE = 1'b1
) (
    input logic          clk_i,
    input logic          reset_i,
    input logic          request_valid_i,
    input logic          request_write_i,
    input logic [25:0]   request_address_i,
    input logic [15:0]   request_byte_enable_i
);
    // Why: data values never steer control, and DV checks them bit for bit;
    // tying them off keeps the proof on the command and handshake state.
    logic [127:0] request_write_data_i;
    logic [15:0]  sdram_dq_i;
    assign request_write_data_i = '0;
    assign sdram_dq_i = '0;
    localparam int unsigned BEATS = LINE ? 8 : 1;
    localparam int unsigned WAIT_RCD = LINE ? 0 : 2;
    localparam int unsigned WAIT_RP = LINE ? 0 : 2;
    localparam int unsigned WAIT_RFC = LINE ? 0 : 3;
    localparam int unsigned WAIT_WR = LINE ? 0 : 2;
    localparam int unsigned WAIT_MRD = LINE ? 0 : 2;
    localparam int unsigned REFRESH_DEADLINE = LINE ? 194 : 195;
    localparam int unsigned REFRESH_DUE = LINE ? 163 : 160;
    localparam int unsigned INIT_REFRESHES = LINE ? 2 : 8;
    // Ref: MAS latency table, the longest case of the configuration.
    localparam int unsigned MAX_LATENCY = LINE ? 12 : 10;
    // Why: yosys-slang cannot reach a DUT enum through the hierarchy, so the
    // helpers name t_sdram_phase members by their declared order.
    localparam logic [3:0] PHASE_WAIT = 4'd4, PHASE_IDLE = 4'd5, PHASE_PRECHARGE = 4'd6,
        PHASE_ACTIVATE = 4'd9, PHASE_READ = 4'd10, PHASE_READ_CAPTURE = 4'd11, PHASE_WRITE = 4'd12,
        PHASE_RESPOND = 4'd13, PHASE_PRECHARGE_ALL = 4'd7, PHASE_REFRESH = 4'd8;

    logic                    request_ready_o, response_valid_o, response_error_o, idle_o, init_done_o;
    logic [16*BEATS-1:0]     response_data_o;
    logic [12:0]             sdram_address_o;
    logic [1:0]              sdram_bank_o, sdram_dqm_o;
    logic                    sdram_cke_o, sdram_cs_n_o, sdram_ras_n_o, sdram_cas_n_o, sdram_we_n_o;
    logic [15:0]             sdram_dq_o;
    logic                    sdram_dq_oe_o;

    // Why: a four-clock power-up keeps the proof's reachable start short; the
    // power-up count is a plain counter that DV checks at full length.
    n2a_sdram_ctrl #(
        .BURST_BEATS      (BEATS),
        .OPEN_ROW         (LINE),
        .ROW_XOR_BANK     (LINE),
        .POWERUP_CLOCKS   (4),
        .INIT_REFRESHES   (INIT_REFRESHES),
        .INIT_WAIT_MIN    (LINE ? 2 : 0),
        .WAIT_RCD         (WAIT_RCD),
        .WAIT_RP          (WAIT_RP),
        .WAIT_RFC         (WAIT_RFC),
        .WAIT_WR          (WAIT_WR),
        .WAIT_MRD         (WAIT_MRD),
        .REFRESH_DUE      (REFRESH_DUE),
        .REFRESH_DEADLINE (REFRESH_DEADLINE)
    ) dut (
        .clk_i, .reset_i, .request_valid_i, .request_ready_o, .request_write_i, .request_address_i,
        .request_write_data_i(request_write_data_i[16*BEATS-1:0]),
        .request_byte_enable_i(request_byte_enable_i[2*BEATS-1:0]),
        .response_valid_o, .response_data_o, .response_error_o, .idle_o, .init_done_o,
        .sdram_address_o, .sdram_bank_o, .sdram_cke_o, .sdram_cs_n_o, .sdram_ras_n_o,
        .sdram_cas_n_o, .sdram_we_n_o, .sdram_dqm_o, .sdram_dq_o, .sdram_dq_oe_o, .sdram_dq_i);

    // Why: every proof starts from the spec's reset state (wiki/bkm/fv/formal.md).
    logic init_q = 1'b1;
    always_ff @(posedge clk_i) init_q <= 1'b0;
    always_comb if (init_q) a_start_in_reset: assume (reset_i);

    typedef enum logic [2:0] {
        CMD_NOP, CMD_ACTIVATE, CMD_READ, CMD_WRITE, CMD_PRECHARGE, CMD_REFRESH, CMD_MODE
    } t_cmd;
    t_cmd cmd;
    always_comb begin
        case ({sdram_ras_n_o, sdram_cas_n_o, sdram_we_n_o})
            3'b011: cmd = CMD_ACTIVATE;
            3'b101: cmd = CMD_READ;
            3'b100: cmd = CMD_WRITE;
            3'b010: cmd = CMD_PRECHARGE;
            3'b001: cmd = CMD_REFRESH;
            3'b000: cmd = CMD_MODE;
            default: cmd = CMD_NOP;
        endcase
    end

    logic accept, misaligned;
    assign accept = request_valid_i && request_ready_o;
    assign misaligned = request_address_i[$clog2(2*BEATS)-1:0] != '0;

    logic                  stalled_q, prev_write_q;
    logic [25:0]           prev_address_q;
    logic [127:0]          prev_write_data_q;
    logic [15:0]           prev_byte_enable_q;
    t_cmd                  last_cmd_q;
    logic [7:0]            nops_q;
    logic                  refreshed_q;
    logic [8:0]            since_refresh_q;
    logic                  outstanding_q, outstanding_misaligned_q, accepted_q;
    logic [4:0]            outstanding_age_q;
    always_ff @(posedge clk_i) begin
        prev_write_q <= request_write_i;
        prev_address_q <= request_address_i;
        prev_write_data_q <= request_write_data_i;
        prev_byte_enable_q <= request_byte_enable_i;
        if (reset_i) begin
            stalled_q <= 1'b0;
            last_cmd_q <= CMD_NOP;
            nops_q <= 8'hff;
            refreshed_q <= 1'b0;
            since_refresh_q <= '0;
            outstanding_q <= 1'b0;
            accepted_q <= 1'b0;
            outstanding_misaligned_q <= 1'b0;
            outstanding_age_q <= '0;
        end else begin
            stalled_q <= request_valid_i && !request_ready_o;
            if (cmd != CMD_NOP) begin
                last_cmd_q <= cmd;
                nops_q <= '0;
            end else if (nops_q != 8'hff) nops_q <= nops_q + 8'd1;
            if (cmd == CMD_REFRESH) begin
                refreshed_q <= 1'b1;
                since_refresh_q <= 9'd1;
            end else if (refreshed_q && since_refresh_q != 9'h1ff) since_refresh_q <= since_refresh_q + 9'd1;
            if (accept && !misaligned) accepted_q <= 1'b1;
            if (accept) begin
                outstanding_q <= 1'b1;
                outstanding_misaligned_q <= misaligned;
                outstanding_age_q <= 5'd1;
            end else if (response_valid_o) begin
                outstanding_q <= 1'b0;
                outstanding_age_q <= '0;
            end else if (outstanding_q && outstanding_age_q != 5'h1f) outstanding_age_q <= outstanding_age_q + 5'd1;
        end
    end

    // Ref: MAS Ports "hold it and its payload until an edge samples request_ready_o high".
    always_comb if (!reset_i && stalled_q) begin
        a_request_held: assume (request_valid_i && request_write_i == prev_write_q &&
            request_address_i == prev_address_q && request_write_data_i == prev_write_data_q &&
            request_byte_enable_i == prev_byte_enable_q);
    end

    // Ref: MAS Parameters, WAIT_*: the idle clocks after each command; IS42S16320D
    // bursts: no command inside a read or write burst.
    function automatic int unsigned required_nops(input t_cmd last);
        case (last)
            CMD_ACTIVATE:  return WAIT_RCD;
            CMD_READ:      return BEATS;
            CMD_WRITE:     return BEATS - 1 + WAIT_WR;
            CMD_PRECHARGE: return WAIT_RP;
            CMD_REFRESH:   return WAIT_RFC;
            CMD_MODE:      return WAIT_MRD;
            default:       return 0;
        endcase
    endfunction

    logic access_phase;
    assign access_phase = dut.regs_q.phase inside {PHASE_PRECHARGE, PHASE_ACTIVATE, PHASE_READ,
        PHASE_READ_CAPTURE, PHASE_WRITE, PHASE_RESPOND} ||
        (dut.regs_q.phase == PHASE_WAIT && dut.regs_q.after_wait inside {PHASE_PRECHARGE,
        PHASE_ACTIVATE, PHASE_READ, PHASE_WRITE, PHASE_RESPOND});

    logic row_open_phase;
    assign row_open_phase = dut.regs_q.phase inside {PHASE_READ, PHASE_READ_CAPTURE, PHASE_WRITE, PHASE_PRECHARGE} ||
        (dut.regs_q.phase == PHASE_WAIT && dut.regs_q.after_wait inside {PHASE_READ, PHASE_WRITE, PHASE_PRECHARGE});

    // Why: the largest refresh age each phase can hold outside an access; the
    // proof needs it because an unreachable old age would otherwise look possible.
    localparam int unsigned AGE_IDLE = REFRESH_DUE + MAX_LATENCY;
    int unsigned age_bound;
    always_comb begin
        case (dut.regs_q.phase)
            PHASE_IDLE: age_bound = AGE_IDLE;
            PHASE_PRECHARGE_ALL: age_bound = AGE_IDLE + 1;
            PHASE_REFRESH: age_bound = AGE_IDLE + 2 + WAIT_RP;
            PHASE_WAIT: age_bound = dut.regs_q.after_wait == PHASE_REFRESH ? AGE_IDLE + 1 + WAIT_RP :
                                    dut.regs_q.after_wait == PHASE_IDLE || dut.regs_q.after_wait <= 4'd3 ? 16 : 511;
            default: age_bound = dut.regs_q.phase <= 4'd3 ? 16 : 511;
        endcase
    end

    always_comb if (!reset_i) begin
        p_command_spacing: assert (cmd == CMD_NOP || int'(nops_q) >= required_nops(last_cmd_q));
        p_column_after_activate_or_column: assert (!(cmd == CMD_READ || cmd == CMD_WRITE) ||
            last_cmd_q inside {CMD_ACTIVATE, CMD_READ, CMD_WRITE});
        p_refresh_interval: assert (!init_done_o || int'(since_refresh_q) <= REFRESH_DEADLINE + 1);
        // Why: ready may rise in the response clock itself, so the next request
        // overlaps the response but never an access in flight.
        p_no_accept_while_outstanding: assert (!(request_ready_o && outstanding_q) || response_valid_o);
        p_response_only_when_outstanding: assert (!response_valid_o || outstanding_q);
        p_response_within_latency: assert (int'(outstanding_age_q) <= MAX_LATENCY);
        p_error_only_for_misaligned: assert (!(response_valid_o && response_error_o) || outstanding_misaligned_q);
        p_misaligned_answers_next_clock: assert (!(outstanding_q && outstanding_misaligned_q) ||
            (response_valid_o && response_error_o));
        p_misaligned_issues_nothing: assert (!(outstanding_q && outstanding_misaligned_q) || cmd == CMD_NOP);
        p_aligned_no_error: assert (!(response_valid_o && !outstanding_misaligned_q) || !response_error_o);
        p_dq_only_writing: assert (!sdram_dq_oe_o || cmd == CMD_WRITE || last_cmd_q == CMD_WRITE);
        p_cke_cs_active: assert (sdram_cke_o && !sdram_cs_n_o);
        // Why: helper invariants tie the harness counters to the DUT's own, so
        // the proof need not rediscover the refresh and access timing.
        h_refresh_age: assert (refreshed_q == dut.regs_q.refresh_tracking &&
            (!refreshed_q || int'(since_refresh_q) == int'(dut.regs_q.refresh_age) + 1 ||
             dut.regs_q.refresh_age == 9'h1ff));
        h_access_only_when_outstanding: assert (outstanding_q == (access_phase || response_valid_o ||
            (outstanding_misaligned_q && outstanding_q)));
        h_phase_encoded: assert (dut.regs_q.phase <= PHASE_RESPOND && dut.regs_q.after_wait <= PHASE_RESPOND &&
            !(dut.regs_q.phase == PHASE_WAIT && dut.regs_q.after_wait inside {PHASE_WAIT, PHASE_READ_CAPTURE}));
        h_init_done_after_init: assert (dut.regs_q.init_done == !(dut.regs_q.phase <= 4'd3 ||
            (dut.regs_q.phase == PHASE_WAIT && dut.regs_q.after_wait <= 4'd3)));
        h_tracking_from_first_refresh: assert (dut.regs_q.refresh_tracking == !(dut.regs_q.phase <= 4'd1 ||
            (dut.regs_q.init_refreshes == '0 && (dut.regs_q.phase == 4'd2 ||
             (dut.regs_q.phase == PHASE_WAIT && dut.regs_q.after_wait == 4'd2)))));
        h_init_refresh_count: assert ((dut.regs_q.phase <= 4'd1 ? dut.regs_q.init_refreshes == '0 :
            dut.regs_q.init_done ? int'(dut.regs_q.init_refreshes) == INIT_REFRESHES :
            int'(dut.regs_q.init_refreshes) <= INIT_REFRESHES) &&
            (dut.regs_q.phase != 4'd2 || int'(dut.regs_q.init_refreshes) < INIT_REFRESHES));
        h_wait_count: assert (!(dut.regs_q.phase == PHASE_WAIT || dut.regs_q.phase == 4'd0) ||
            dut.regs_q.count < 13'd4);
        h_last_command: assert ((!(dut.regs_q.phase == PHASE_IDLE && dut.regs_q.open != '0) ||
                last_cmd_q inside {CMD_ACTIVATE, CMD_READ, CMD_WRITE}) &&
            (dut.regs_q.phase != PHASE_READ_CAPTURE || last_cmd_q == CMD_READ) &&
            (!(dut.regs_q.phase == PHASE_WRITE && dut.regs_q.beat != '0) || last_cmd_q == CMD_WRITE) &&
            (!(dut.regs_q.phase == PHASE_WAIT && dut.regs_q.after_wait inside {PHASE_READ, PHASE_WRITE}) ||
                last_cmd_q == CMD_ACTIVATE));
        h_age_in_access: assert (!(outstanding_q && !outstanding_misaligned_q) ||
            int'(dut.regs_q.refresh_age) + 1 <= REFRESH_DUE + int'(outstanding_age_q));
        h_age_by_phase: assert (!dut.regs_q.refresh_tracking || int'(dut.regs_q.refresh_age) <= age_bound);
        h_idle_not_outstanding: assert (!(dut.regs_q.phase == PHASE_IDLE && outstanding_q) ||
            response_valid_o);
        h_close_page_one_bank_open: assert (LINE || (dut.regs_q.open == '0 ? !row_open_phase :
            (row_open_phase && dut.regs_q.open == 4'(1 << dut.bank))));
    end

    always_comb if (!reset_i) begin
        c_read_response: cover (response_valid_o && !response_error_o && !prev_write_q);
        c_write_response: cover (response_valid_o && !response_error_o && prev_write_q);
        c_error_response: cover (response_valid_o && response_error_o);
        c_second_access: cover (accept && !misaligned && accepted_q);
    end
endmodule
