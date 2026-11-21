`include "src/common/rtl/pkg/macros.vh"

// One-transaction x16 SDRAM line controller at 25 MHz. Every 128-bit line is
// one burst of eight x16 beats (mode register: burst length eight,
// sequential, CAS latency two). Each bank keeps its last row open: a row hit
// issues READ/WRITE directly, a row miss precharges only that bank and then
// activates, and refresh precharges all banks first. Command spacing follows
// the IS42S16320D-7 minimums in whole 40 ns clocks (wiki/src/nand2doom/rtl/memory/mas.md): PRECHARGE to
// ACTIVE (tRP 15 ns), ACTIVE to READ/WRITE (tRCD 15 ns) and precharge-all to
// REFRESH are one clock; REFRESH to the next ACTIVE (tRFC 60 ns) and ACTIVE to
// ACTIVE of a bank (tRC 60 ns) are at least two. The byte-address map is row
// [25:13], x16 column [10:1] and bank [12:11] XOR [14:13]: neighbouring 2 KB
// regions use different banks, and a cache victim and its refill (same set,
// different tag) usually do too, so both rows can stay open.
module n2d_sdram_controller #(
    parameter int unsigned POWERUP_CYCLES = 5000,
    parameter int unsigned REFRESH_CYCLES = 195,
    parameter int unsigned REFRESH_RESERVE_CYCLES = 32
) (
    input  logic         clk_i,
    input  logic         reset_i,
    input  logic         request_valid_i,
    output logic         request_ready_o,
    input  logic         request_write_i,
    input  logic [25:0]  request_address_i,
    input  logic [127:0] request_write_data_i,
    input  logic [15:0]  request_byte_enable_i,
    output logic         response_valid_o,
    output logic [127:0] response_data_o,
    output logic         response_error_o,
    output logic         init_done_o,

    output logic [12:0]  sdram_address_o,
    output logic [1:0]   sdram_bank_o,
    output logic         sdram_cke_o,
    output logic         sdram_cs_n_o,
    output logic         sdram_ras_n_o,
    output logic         sdram_cas_n_o,
    output logic         sdram_we_n_o,
    output logic [1:0]   sdram_dqm_o,
    output logic [15:0]  sdram_dq_o,
    output logic         sdram_dq_oe_o,
    input  logic [15:0]  sdram_dq_i
);
    import n2d_ls_types_pkg::*;

    localparam sdram_regs_t REGS_RESET = '{
        state: SDRAM_POWERUP, wait_count: '0, refresh: '0,
        refresh_tracking: 1'b0, open: '0, open_row: '0, write: 1'b0,
        address: '0, write_data: '0, byte_enable: '0, read_data: '0,
        beat: '0, init_done: 1'b0, response_valid: 1'b0,
        response_error: 1'b0};
    sdram_regs_t regs_q, regs_d;
    localparam logic [12:0] POWERUP_LIMIT = 13'(POWERUP_CYCLES - 1);
    localparam logic [8:0] REFRESH_LIMIT = 9'(REFRESH_CYCLES - 1);
    localparam logic [8:0] REFRESH_START =
        9'(REFRESH_CYCLES - REFRESH_RESERVE_CYCLES);
    // Mode register: burst length eight (3'b011), sequential, CAS latency two.
    localparam logic [12:0] MODE_VALUE = 13'h023;

    logic [1:0]  bank, request_bank;
    logic [12:0] row, request_row;
    assign bank = regs_q.address[12:11] ^ regs_q.address[14:13];
    assign row = regs_q.address[25:13];
    assign request_bank = request_address_i[12:11] ^ request_address_i[14:13];
    assign request_row = request_address_i[25:13];

    assign request_ready_o = (regs_q.state == SDRAM_IDLE) && (regs_q.refresh < REFRESH_START);

    always_comb begin
        sdram_address_o = 13'b0;
        sdram_bank_o = bank;
        sdram_cke_o = 1'b1;
        sdram_cs_n_o = 1'b0;
        sdram_ras_n_o = 1'b1;
        sdram_cas_n_o = 1'b1;
        sdram_we_n_o = 1'b1;
        sdram_dqm_o = 2'b00;
        sdram_dq_o = regs_q.write_data[regs_q.beat*16 +: 16];
        sdram_dq_oe_o = 1'b0;
        case (regs_q.state)
            SDRAM_INIT_PRE, SDRAM_PRECHARGE_ALL: begin
                sdram_address_o[10] = 1'b1;
                sdram_ras_n_o = 1'b0; sdram_we_n_o = 1'b0;
            end
            SDRAM_PRECHARGE: begin
                // Ref: IS42S16320D datasheet PRECHARGE: A10 low precharges only the
                // addressed bank.
                sdram_ras_n_o = 1'b0; sdram_we_n_o = 1'b0;
            end
            SDRAM_INIT_REF1, SDRAM_INIT_REF2, SDRAM_REFRESH: begin
                sdram_ras_n_o = 1'b0; sdram_cas_n_o = 1'b0;
            end
            SDRAM_INIT_MRS: begin
                sdram_address_o = MODE_VALUE;
                sdram_bank_o = 2'b00;
                sdram_ras_n_o = 1'b0; sdram_cas_n_o = 1'b0; sdram_we_n_o = 1'b0;
            end
            SDRAM_ACTIVE: begin
                sdram_address_o = row;
                sdram_ras_n_o = 1'b0;
            end
            SDRAM_READ: begin
                // Ref: IS42S16320D datasheet READ: A10 low leaves auto-precharge off. The
                // start column is line aligned.
                sdram_address_o[9:0] = regs_q.address[10:1];
                sdram_cas_n_o = 1'b0;
            end
            SDRAM_WRITE: begin
                // Why: WRITE goes out with beat zero, then beats one to seven on NOP cycles,
                // so the eight beats form one burst; every beat drives DQ and its own byte
                // mask so masked lanes stay unwritten.
                sdram_address_o[9:0] = regs_q.address[10:1];
                if (regs_q.beat == 3'd0) begin
                    sdram_cas_n_o = 1'b0; sdram_we_n_o = 1'b0;
                end
                sdram_dqm_o = ~regs_q.byte_enable[regs_q.beat*2 +: 2];
                sdram_dq_oe_o = 1'b1;
            end
            default: begin end
        endcase
    end

    assign init_done_o = regs_q.init_done;
    assign response_valid_o = regs_q.response_valid;
    assign response_data_o = regs_q.read_data;
    assign response_error_o = regs_q.response_error;

    always_comb begin
        regs_d = regs_q;
        regs_d.response_valid = 1'b0;
        regs_d.response_error = 1'b0;
        if (regs_q.refresh_tracking && regs_q.state != SDRAM_REFRESH && regs_q.state != SDRAM_INIT_REF2) begin
            if (regs_q.refresh <= REFRESH_LIMIT) regs_d.refresh = regs_q.refresh + 1'b1;
        end
        case (regs_q.state)
            SDRAM_POWERUP: begin
                if (regs_q.wait_count == POWERUP_LIMIT) begin regs_d.wait_count = '0; regs_d.state = SDRAM_INIT_PRE; end
                else regs_d.wait_count = regs_q.wait_count + 1'b1;
            end
            SDRAM_INIT_PRE: regs_d.state = SDRAM_INIT_PRE_WAIT;
            SDRAM_INIT_PRE_WAIT: if (regs_q.wait_count == 1) begin regs_d.wait_count = '0; regs_d.state = SDRAM_INIT_REF1; end else regs_d.wait_count = regs_q.wait_count + 1'b1;
            SDRAM_INIT_REF1: regs_d.state = SDRAM_INIT_REF1_WAIT;
            SDRAM_INIT_REF1_WAIT: if (regs_q.wait_count == 1) begin regs_d.wait_count = '0; regs_d.state = SDRAM_INIT_REF2; end else regs_d.wait_count = regs_q.wait_count + 1'b1;
            SDRAM_INIT_REF2: begin
                regs_d.refresh = '0;
                regs_d.refresh_tracking = 1'b1;
                regs_d.state = SDRAM_INIT_REF2_WAIT;
            end
            SDRAM_INIT_REF2_WAIT: if (regs_q.wait_count == 1) begin regs_d.wait_count = '0; regs_d.state = SDRAM_INIT_MRS; end else regs_d.wait_count = regs_q.wait_count + 1'b1;
            SDRAM_INIT_MRS: regs_d.state = SDRAM_INIT_MRS_WAIT;
            SDRAM_INIT_MRS_WAIT: if (regs_q.wait_count == 1) begin
                regs_d.wait_count = '0;
                regs_d.init_done = 1'b1;
                regs_d.state = SDRAM_IDLE;
            end else regs_d.wait_count = regs_q.wait_count + 1'b1;
            SDRAM_IDLE: begin
                if (regs_q.refresh >= REFRESH_START) begin
                    regs_d.state = (regs_q.open != 4'b0) ? SDRAM_PRECHARGE_ALL : SDRAM_REFRESH;
                end else if (request_valid_i) begin
                    if ((request_address_i[3:0] != 4'b0) ||
                        (!request_write_i && request_byte_enable_i != 16'b0)) begin
                        regs_d.response_error = 1'b1;
                        regs_d.response_valid = 1'b1;
                    end else begin
                        regs_d.write = request_write_i;
                        regs_d.address = request_address_i[25:1];
                        regs_d.write_data = request_write_data_i;
                        regs_d.byte_enable = request_byte_enable_i;
                        regs_d.read_data = '0;
                        regs_d.beat = '0;
                        if (!regs_q.open[request_bank]) regs_d.state = SDRAM_ACTIVE;
                        else if (regs_q.open_row[request_bank] != request_row) regs_d.state = SDRAM_PRECHARGE;
                        else regs_d.state = request_write_i ? SDRAM_WRITE : SDRAM_READ;
                    end
                end
            end
            // Ref: wiki/src/nand2doom/rtl/memory/mas.md "SDRAM controller" (IS42S16320D-7
            // timing): a row miss closes this bank; ACTIVE on the next clock meets tRP.
            SDRAM_PRECHARGE: begin
                regs_d.open[bank] = 1'b0;
                regs_d.state = SDRAM_ACTIVE;
            end
            // Ref: wiki/src/nand2doom/rtl/memory/mas.md "SDRAM controller": the column
            // command on the next clock meets tRCD.
            SDRAM_ACTIVE: begin
                regs_d.open[bank] = 1'b1;
                regs_d.open_row[bank] = row;
                regs_d.state = regs_q.write ? SDRAM_WRITE : SDRAM_READ;
            end
            // Ref: wiki/src/nand2doom/rtl/memory/mas.md "SDRAM controller": READ launches
            // after controller edge C0; the inverted device clock registers it at
            // C0+20 ns. CL2 beat zero is driven after C0+60 ns+tAC and captured at
            // C0+80 ns.
            SDRAM_READ: regs_d.state = SDRAM_READ_CAPTURE;
            SDRAM_READ_CAPTURE: begin
                regs_d.read_data[regs_q.beat*16 +: 16] = sdram_dq_i;
                regs_d.beat = regs_q.beat + 1'b1;
                if (regs_q.beat == 3'd7) begin
                    regs_d.response_valid = 1'b1;
                    regs_d.state = SDRAM_IDLE;
                end
            end
            // Ref: wiki/src/nand2doom/rtl/memory/mas.md "SDRAM controller": the final beat
            // is written at the edge that leaves this state; IDLE then leaves at least
            // two clocks before any precharge (tWR needs one).
            SDRAM_WRITE: begin
                regs_d.beat = regs_q.beat + 1'b1;
                if (regs_q.beat == 3'd7) begin
                    regs_d.response_valid = 1'b1;
                    regs_d.state = SDRAM_IDLE;
                end
            end
            // Ref: wiki/src/nand2doom/rtl/memory/mas.md "SDRAM controller": REFRESH on the
            // next clock meets tRP.
            SDRAM_PRECHARGE_ALL: begin
                regs_d.open = '0;
                regs_d.state = SDRAM_REFRESH;
            end
            // Ref: wiki/src/nand2doom/rtl/memory/mas.md "SDRAM controller": IDLE follows,
            // so the next ACTIVE is at least two clocks later (tRFC).
            SDRAM_REFRESH: begin regs_d.refresh = '0; regs_d.state = SDRAM_IDLE; end
            default: begin regs_d.state = SDRAM_POWERUP; regs_d.init_done = 1'b0; end
        endcase
    end

    `N2A_DFF_RST_VAL(regs_q, regs_d, clk_i, reset_i, REGS_RESET)

    `N2A_ASSERT_CLOCKED(clk_i, reset_i, !(sdram_dq_oe_o && regs_q.state != SDRAM_WRITE))
    `N2A_ASSERT_CLOCKED(clk_i, reset_i, !(regs_q.state == SDRAM_ACTIVE && regs_q.open[bank]))
    `N2A_ASSERT_CLOCKED(clk_i, reset_i, !(regs_q.state == SDRAM_REFRESH && regs_q.open != 4'b0))
    `N2A_ASSERT_CLOCKED_FATAL_WHEN(clk_i, reset_i, regs_q.refresh_tracking,
        regs_q.refresh <= REFRESH_LIMIT, "SDRAM refresh deadline exceeded")
    // Why: a newly presented request may differ from the preceding idle payload,
    // so stability is required only after a request was stalled at an edge.
    `N2A_ASSERT_PROPERTY(sdram_stalled_request_stable, clk_i, reset_i,
        (request_valid_i && !request_ready_o) |=>
        (request_valid_i && $stable({request_write_i, request_address_i,
                                    request_write_data_i, request_byte_enable_i})))
    // Why: REFRESH_RESERVE_CYCLES must be at least 14 because the worst in-flight
    // line (row-miss read), IDLE and precharge-all put the REFRESH command in the
    // fourteenth clock after acceptance.
    `N2A_ASSERT_INITIAL(REFRESH_RESERVE_CYCLES >= 14)
    `N2A_ASSERT_INITIAL(REFRESH_CYCLES > REFRESH_RESERVE_CYCLES)
endmodule
