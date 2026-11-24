`include "src/common/rtl/pkg/macros.vh"

// Common UART memory endpoint (https://github.com/amichai-bd/nand2anything/blob/aeebe92/wiki/src/common/uart_mem/HAS.md).
//
// A byte-stream router between the common UART PHYs and the project's own
// UART traffic. At a project frame boundary (down_idle_i) a request SOF is
// claimed; the endpoint then owns the link until its response has left, and
// every other byte passes through unchanged. Commands hold and release the
// CPU, set the entry state, write memory while held and read memory, or its
// CRC-32 digest, while held or running. The project map is data: WINDOWS lists base, size and
// attributes, and the endpoint enforces it before any bus traffic. While the
// CPU runs, accepted bus requests are at least LIVE_GAP_CYCLES apart.
module n2a_uart_mem
    import n2a_uart_mem_pkg::*;
#(
    // Project identity returned by Identify.
    parameter logic [31:0] PROJECT_ID = 32'h0,
    // 1..8 windows, each {attributes, size, base} at [w*96 +: 96].
    parameter int unsigned WINDOW_COUNT = 1,
    parameter logic [WINDOW_COUNT*96-1:0] WINDOWS = '0,
    // Entry: exactly ENTRY_ADDR, or any aligned writable word with ENTRY_ANY.
    parameter logic [31:0] ENTRY_ADDR = 32'h0,
    parameter bit ENTRY_ANY = 1'b0,
    // Largest read or write payload in bytes (multiple of four).
    parameter int unsigned MAX_LENGTH = 4096,
    // Request inter-byte gap, and line quiet time that ends a discard.
    parameter int unsigned GAP_CYCLES = 1_000_000,
    parameter int unsigned HOLD_TIMEOUT_CYCLES = 1_000_000,
    parameter int unsigned MEM_TIMEOUT_CYCLES = 4096,
    // Minimum clocks between accepted bus requests while the CPU runs.
    parameter int unsigned LIVE_GAP_CYCLES = 64,
    parameter bit HOLD_AT_RESET = 1'b0
) (
    input  logic        clk_i,
    input  logic        reset_i,

    // PHY side.
    input  logic [7:0]  rx_byte_i,
    input  logic        rx_valid_i,
    input  logic        rx_error_i,
    output logic [7:0]  tx_byte_o,
    output logic        tx_valid_o,
    input  logic        tx_ready_i,

    // Project traffic side.
    output logic [7:0]  down_rx_byte_o,
    output logic        down_rx_valid_o,
    output logic        down_rx_error_o,
    input  logic [7:0]  down_tx_byte_i,
    input  logic        down_tx_valid_i,
    output logic        down_tx_ready_o,
    // The project is between frames: a SOF here starts an endpoint frame.
    input  logic        down_idle_i,
    output logic        owner_o,

    // CPU control.
    output logic        hold_o,
    input  logic        hold_ack_i,
    output logic        release_o,
    output logic        entry_valid_o,
    output logic [31:0] entry_addr_o,

    // Word bus master: one request outstanding, full-word writes.
    output logic        mem_req_valid_o,
    input  logic        mem_req_ready_i,
    output logic        mem_req_write_o,
    output logic [31:0] mem_req_addr_o,
    output logic [31:0] mem_req_wdata_o,
    input  logic        mem_rsp_valid_i,
    input  logic [31:0] mem_rsp_rdata_i,
    input  logic        mem_rsp_error_i
);
    localparam int unsigned IDENT_WORDS = UART_MEM_IDENT_FIXED_WORDS + 3 * WINDOW_COUNT;
    localparam logic [15:0] IDENT_LENGTH = 16'(4 * IDENT_WORDS);
    localparam logic [15:0] MAX_LENGTH_FIELD = 16'(MAX_LENGTH);
    localparam logic [15:0] LIVE_GAP = 16'(LIVE_GAP_CYCLES);
    localparam logic [31:0] GAP_LAST = 32'(GAP_CYCLES - 1);
    localparam logic [31:0] HOLD_LAST = 32'(HOLD_TIMEOUT_CYCLES - 1);
    localparam logic [31:0] MEM_LAST = 32'(MEM_TIMEOUT_CYCLES - 1);

    `ASSERT_PARAM(g_window_count, (WINDOW_COUNT >= 1) && (WINDOW_COUNT <= 8),
        "n2a_uart_mem WINDOW_COUNT must be 1..8")
    `ASSERT_PARAM(g_max_length, (MAX_LENGTH >= 4) && (MAX_LENGTH <= 32768) &&
        ((MAX_LENGTH % 4) == 0), "n2a_uart_mem MAX_LENGTH must be 4..32768, a multiple of four")
    `ASSERT_PARAM(g_timeouts, (GAP_CYCLES >= 2) && (HOLD_TIMEOUT_CYCLES >= 2) &&
        (MEM_TIMEOUT_CYCLES >= 2), "n2a_uart_mem timeouts must be at least two clocks")
    `ASSERT_PARAM(g_live_gap, (LIVE_GAP_CYCLES >= 1) && (LIVE_GAP_CYCLES <= 65535),
        "n2a_uart_mem LIVE_GAP_CYCLES must be 1..65535")

    localparam t_uart_mem_regs REGS_RESET = '{
        phase: UART_MEM_IDLE, crc32: 32'hffff_ffff, hold: HOLD_AT_RESET, default: '0};

    t_uart_mem_regs r_q, r_d;
    logic [31:0] timer_q, timer_d;
    logic [31:0] mem_timer_q, mem_timer_d;
    logic [15:0] pace_q, pace_d;

    logic [32:0] span_end;
    logic window_write, window_read_held, window_read_live;
    logic [7:0] check_status;
    logic header_crc_ok, header_last;

    logic [15:0] status_index, crc_offset, payload_offset, ident_index;
    logic [1:0]  crc_lane, payload_lane;
    logic [31:0] ident_word, crc_final;
    logic [7:0]  ep_tx_byte;
    logic        ep_tx_valid, tx_fire, responding;
    logic        mem_accept, mem_timeout, read_issue, write_done;
    logic        digesting, digest_done;
    logic [7:0]  rx_payload_byte;
    logic [31:0] base, size, attributes;
    logic [32:0] window_end;
    logic        respond;

    always_comb begin
        span_end = {1'b0, r_q.address} +
            33'((r_q.command == UART_MEM_CMD_ENTRY) ? 16'd4 : r_q.length);
        window_write = 1'b0;
        window_read_held = 1'b0;
        window_read_live = 1'b0;
        for (int w = 0; w < WINDOW_COUNT; w++) begin
            base = WINDOWS[w * UART_MEM_WINDOW_BITS +: 32];
            size = WINDOWS[w * UART_MEM_WINDOW_BITS + 32 +: 32];
            attributes = WINDOWS[w * UART_MEM_WINDOW_BITS + 64 +: 32];
            window_end = {1'b0, base} + {1'b0, size};
            if ((r_q.address >= base) && (span_end <= window_end)) begin
                window_write = window_write ||
                    ((attributes & UART_MEM_ATTR_WRITE) != 32'h0);
                window_read_held = window_read_held ||
                    ((attributes & UART_MEM_ATTR_READ_HELD) != 32'h0);
                window_read_live = window_read_live ||
                    ((attributes & UART_MEM_ATTR_READ_LIVE) != 32'h0);
            end
        end
    end

    // Why: the checks run in a fixed priority, so a request that breaks several
    // rules always gets the same status.
    always_comb begin
        check_status = UART_MEM_ST_OK;
        if (r_q.version != UART_MEM_VERSION) begin
            check_status = UART_MEM_ST_VERSION;
        end else if ((r_q.command == UART_MEM_CMD_IDENTIFY) ||
                     (r_q.command == UART_MEM_CMD_HOLD) ||
                     (r_q.command == UART_MEM_CMD_RELEASE)) begin
            if ((r_q.address != 32'h0) || (r_q.length != 16'h0))
                check_status = UART_MEM_ST_LENGTH;
        end else if (r_q.command == UART_MEM_CMD_ENTRY) begin
            if (r_q.length != 16'h0) check_status = UART_MEM_ST_LENGTH;
            else if (r_q.address[1:0] != 2'b00) check_status = UART_MEM_ST_ALIGNMENT;
            else if (ENTRY_ANY ? !window_write : (r_q.address != ENTRY_ADDR))
                check_status = UART_MEM_ST_RANGE;
            else if (!hold_ack_i) check_status = UART_MEM_ST_NOT_HELD;
        end else if ((r_q.command == UART_MEM_CMD_WRITE) ||
                     (r_q.command == UART_MEM_CMD_READ) ||
                     (r_q.command == UART_MEM_CMD_DIGEST)) begin
            // Why: a Digest answers four bytes whatever its range, so only the
            // 16-bit field bounds it, not MAX_LENGTH.
            if ((r_q.length == 16'h0) || (r_q.length[1:0] != 2'b00) ||
                ((r_q.command != UART_MEM_CMD_DIGEST) && (r_q.length > MAX_LENGTH_FIELD)))
                check_status = UART_MEM_ST_LENGTH;
            else if (r_q.address[1:0] != 2'b00) check_status = UART_MEM_ST_ALIGNMENT;
            else if (r_q.command == UART_MEM_CMD_WRITE) begin
                if (!window_write) check_status = UART_MEM_ST_RANGE;
                else if (!hold_ack_i) check_status = UART_MEM_ST_NOT_HELD;
            end else if (hold_ack_i ? !window_read_held : !window_read_live) begin
                check_status = (!hold_ack_i && window_read_held) ?
                    UART_MEM_ST_NOT_HELD : UART_MEM_ST_RANGE;
            end
        end else begin
            check_status = UART_MEM_ST_COMMAND;
        end
    end

    // Why: the host decodes Identify by word position, so this order is the wire
    // format.
    assign ident_index = (r_q.index - 16'd6) >> 2;
    always_comb begin
        ident_word = 32'h0;
        case (ident_index)
            16'd0: ident_word = UART_MEM_MAGIC;
            16'd1: ident_word = PROJECT_ID;
            16'd2: ident_word = {MAX_LENGTH_FIELD, 8'(WINDOW_COUNT), 6'b0, r_q.hold, hold_ack_i} |
                UART_MEM_CAP_DIGEST;
            16'd3: ident_word = ENTRY_ADDR;
            16'd4: ident_word = {15'b0, ENTRY_ANY, LIVE_GAP};
            default: begin
                for (int w = 0; w < WINDOW_COUNT; w++)
                    for (int f = 0; f < 3; f++)
                        if (ident_index == 16'(UART_MEM_IDENT_FIXED_WORDS + 3 * w + f))
                            ident_word = WINDOWS[w * UART_MEM_WINDOW_BITS + 32 * f +: 32];
            end
        endcase
    end

    assign responding = (r_q.phase == UART_MEM_RESPOND);
    assign status_index = 16'd6 + r_q.rsp_length;
    // Why: only the byte lanes are used; the upper offset bits select the
    // word, which the read buffer already counts.
    assign crc_offset = r_q.index - status_index - 16'd1;
    assign payload_offset = r_q.index - 16'd6;
    assign crc_lane = crc_offset[1:0];
    assign payload_lane = payload_offset[1:0];
    `UNUSED_BITS(unused_offset_words, {crc_offset[15:2], payload_offset[15:2]})
    assign crc_final = ~r_q.crc32;

    always_comb begin
        ep_tx_valid = responding;
        ep_tx_byte = 8'h00;
        case (r_q.index)
            16'd0: ep_tx_byte = UART_MEM_RESPONSE_SOF;
            16'd1: ep_tx_byte = UART_MEM_VERSION;
            16'd2: ep_tx_byte = r_q.command | 8'h80;
            16'd3: ep_tx_byte = r_q.seq;
            16'd4: ep_tx_byte = r_q.rsp_length[7:0];
            16'd5: ep_tx_byte = r_q.rsp_length[15:8];
            default: begin
                if (r_q.index < status_index) begin
                    if (r_q.command == UART_MEM_CMD_IDENTIFY) begin
                        ep_tx_byte = ident_word[8 * payload_lane +: 8];
                    end else if (!r_q.mem_failed) begin
                        ep_tx_byte = r_q.cur_word[8 * payload_lane +: 8];
                        ep_tx_valid = responding && r_q.cur_valid;
                    end
                end else if (r_q.index == status_index) begin
                    ep_tx_byte = r_q.status;
                end else begin
                    ep_tx_byte = crc_final[8 * crc_lane +: 8];
                end
            end
        endcase
    end

    assign tx_fire = ep_tx_valid && tx_ready_i;
    assign mem_accept = r_q.req_valid && mem_req_ready_i;
    assign mem_timeout = (r_q.req_valid || (r_q.outstanding && !r_q.orphan)) &&
        (mem_timer_q == MEM_LAST);
    assign header_last = (r_q.index == 16'd10);
    assign header_crc_ok = (rx_byte_i == r_q.crc8);
    assign write_done = !r_q.req_valid && (!r_q.outstanding || r_q.orphan);
    assign rx_payload_byte = rx_byte_i;
    // Why: one request outstanding and at most two buffered words keep reads
    // within cur/nxt; pacing keeps live reads LIVE_GAP_CYCLES apart. A Digest
    // reads its range during EXEC, folding each word into crc32, and answers
    // once every word is folded.
    assign digesting = (r_q.phase == UART_MEM_EXEC) && (r_q.command == UART_MEM_CMD_DIGEST) &&
        !r_q.reject;
    assign digest_done = (r_q.mem_failed || (r_q.issued == r_q.length)) && write_done;
    assign read_issue = !r_q.reject && !r_q.mem_failed && !r_q.req_valid && !r_q.outstanding &&
        (hold_ack_i || (pace_q >= LIVE_GAP)) &&
        ((responding && (r_q.command == UART_MEM_CMD_READ) && (r_q.issued < r_q.rsp_length) &&
          !(r_q.cur_valid && r_q.nxt_valid)) ||
         (digesting && (r_q.issued < r_q.length)));

    always_comb begin
        r_d = r_q;
        timer_d = (timer_q == 32'hffff_ffff) ? timer_q : timer_q + 32'd1;
        mem_timer_d = mem_timer_q + 32'd1;
        pace_d = (pace_q == 16'hffff) ? pace_q : pace_q + 16'd1;
        r_d.release_pulse = 1'b0;
        r_d.entry_pulse = 1'b0;
        respond = 1'b0;
        down_rx_valid_o = 1'b0;
        down_rx_error_o = 1'b0;

        if (!r_q.cur_valid && r_q.nxt_valid) begin
            r_d.cur_word = r_q.nxt_word;
            r_d.cur_valid = 1'b1;
            r_d.nxt_valid = 1'b0;
        end

        if (!r_q.req_valid && !r_q.outstanding) mem_timer_d = 32'h0;
        if (mem_accept) begin
            r_d.req_valid = 1'b0;
            r_d.outstanding = 1'b1;
            r_d.out_write = r_q.req_write;
            mem_timer_d = 32'h0;
            pace_d = 16'h0;
        end else if (mem_timeout) begin
            // Why: an accepted request cannot be recalled, so it becomes an
            // orphan whose late response is dropped; an unaccepted one is withdrawn.
            r_d.req_valid = 1'b0;
            r_d.orphan = r_q.outstanding;
            r_d.mem_failed = 1'b1;
            if (r_q.status == UART_MEM_ST_OK) r_d.status = UART_MEM_ST_TIMEOUT;
        end
        if (mem_rsp_valid_i && r_q.outstanding) begin
            r_d.outstanding = 1'b0;
            r_d.orphan = 1'b0;
            if (!r_q.orphan) begin
                if (mem_rsp_error_i) begin
                    r_d.mem_failed = 1'b1;
                    if (r_q.status == UART_MEM_ST_OK) r_d.status = UART_MEM_ST_MEMORY;
                end else if (digesting) begin
                    if (!r_q.mem_failed) r_d.crc32 = uart_mem_crc32_word(r_q.crc32, mem_rsp_rdata_i);
                end else if (!r_q.out_write && !r_q.mem_failed) begin
                    if (!r_q.cur_valid && !r_q.nxt_valid) begin
                        r_d.cur_word = mem_rsp_rdata_i;
                        r_d.cur_valid = 1'b1;
                    end else begin
                        r_d.nxt_word = mem_rsp_rdata_i;
                        r_d.nxt_valid = 1'b1;
                    end
                end
            end
        end
        if (read_issue) begin
            r_d.req_valid = 1'b1;
            r_d.req_write = 1'b0;
            r_d.req_addr = r_q.address + 32'(r_q.issued);
            r_d.issued = r_q.issued + 16'd4;
        end

        case (r_q.phase)
            UART_MEM_IDLE: begin
                if (rx_valid_i && (rx_byte_i == UART_MEM_REQUEST_SOF) && down_idle_i) begin
                    r_d.phase = UART_MEM_HEADER;
                    r_d.index = 16'd1;
                    r_d.crc8 = 8'h00;
                    r_d.status = UART_MEM_ST_OK;
                    r_d.reject = 1'b0;
                    r_d.mem_failed = 1'b0;
                    r_d.issued = 16'h0;
                    r_d.cur_valid = 1'b0;
                    r_d.nxt_valid = 1'b0;
                    timer_d = 32'h0;
                end else begin
                    down_rx_valid_o = rx_valid_i;
                    down_rx_error_o = rx_error_i;
                end
            end
            UART_MEM_HEADER: begin
                if (rx_error_i) begin
                    r_d.phase = UART_MEM_DISCARD;
                    timer_d = 32'h0;
                end else if (rx_valid_i) begin
                    timer_d = 32'h0;
                    r_d.index = r_q.index + 16'd1;
                    if (!header_last) r_d.crc8 = uart_mem_crc8(r_q.crc8, rx_byte_i);
                    case (r_q.index)
                        16'd1: r_d.version = rx_byte_i;
                        16'd2: r_d.command = rx_byte_i;
                        16'd3: r_d.seq = rx_byte_i;
                        16'd4: r_d.address[7:0] = rx_byte_i;
                        16'd5: r_d.address[15:8] = rx_byte_i;
                        16'd6: r_d.address[23:16] = rx_byte_i;
                        16'd7: r_d.address[31:24] = rx_byte_i;
                        16'd8: r_d.length[7:0] = rx_byte_i;
                        16'd9: r_d.length[15:8] = rx_byte_i;
                        default: ;
                    endcase
                    if (header_last) begin
                        r_d.index = 16'h0;
                        r_d.crc32 = 32'hffff_ffff;
                        if (!header_crc_ok) begin
                            // Why: an untrusted header has no trusted length: drop
                            // the frame and wait for the host to fall silent.
                            r_d.phase = UART_MEM_DISCARD;
                        end else begin
                            r_d.status = check_status;
                            r_d.reject = (check_status != UART_MEM_ST_OK);
                            if ((r_q.command == UART_MEM_CMD_WRITE) && (r_q.length != 16'h0))
                                r_d.phase = UART_MEM_PAYLOAD;
                            else
                                r_d.phase = UART_MEM_EXEC;
                        end
                    end
                end else if (timer_q == GAP_LAST) begin
                    r_d.phase = UART_MEM_IDLE;
                end
            end
            UART_MEM_PAYLOAD: begin
                if (rx_error_i) begin
                    r_d.phase = UART_MEM_DISCARD;
                    timer_d = 32'h0;
                end else if (rx_valid_i) begin
                    timer_d = 32'h0;
                    r_d.crc32 = uart_mem_crc32(r_q.crc32, rx_payload_byte);
                    r_d.word = {rx_payload_byte, r_q.word[31:8]};
                    r_d.index = r_q.index + 16'd1;
                    if ((r_q.index[1:0] == 2'b11) && !r_q.reject && !r_q.mem_failed) begin
                        if (r_q.req_valid || r_q.outstanding) begin
                            r_d.mem_failed = 1'b1;
                            if (r_q.status == UART_MEM_ST_OK) r_d.status = UART_MEM_ST_OVERRUN;
                        end else begin
                            r_d.req_valid = 1'b1;
                            r_d.req_write = 1'b1;
                            r_d.req_addr = r_q.address + {16'h0, r_q.index[15:2], 2'b00};
                            r_d.req_wdata = {rx_payload_byte, r_q.word[31:8]};
                        end
                    end
                    if (r_q.index == r_q.length - 16'd1) begin
                        r_d.phase = UART_MEM_PCRC;
                        r_d.index = 16'h0;
                    end
                end else if (timer_q == GAP_LAST) begin
                    r_d.phase = UART_MEM_IDLE;
                end
            end
            UART_MEM_PCRC: begin
                if (rx_error_i) begin
                    r_d.phase = UART_MEM_DISCARD;
                    timer_d = 32'h0;
                end else if (rx_valid_i) begin
                    timer_d = 32'h0;
                    r_d.rx_crc = {rx_byte_i, r_q.rx_crc[31:8]};
                    r_d.index = r_q.index + 16'd1;
                    if (r_q.index == 16'd3) begin
                        r_d.phase = UART_MEM_EXEC;
                        // Why: corruption may have caused any earlier error, so
                        // CHECKSUM overrides it.
                        if (!r_q.reject && ({rx_byte_i, r_q.rx_crc[31:8]} != ~r_q.crc32))
                            r_d.status = UART_MEM_ST_CHECKSUM;
                    end
                end else if (timer_q == GAP_LAST) begin
                    r_d.phase = UART_MEM_IDLE;
                end
            end
            UART_MEM_DISCARD: begin
                if (rx_valid_i || rx_error_i) timer_d = 32'h0;
                else if (timer_q == GAP_LAST) r_d.phase = UART_MEM_IDLE;
            end
            UART_MEM_EXEC: begin
                respond = 1'b1;
                r_d.rsp_length = 16'h0;
                if (!r_q.reject) begin
                    case (r_q.command)
                        UART_MEM_CMD_IDENTIFY: r_d.rsp_length = IDENT_LENGTH;
                        UART_MEM_CMD_HOLD: begin
                            r_d.hold = 1'b1;
                            if (!hold_ack_i) begin
                                if (timer_q == HOLD_LAST) r_d.status = UART_MEM_ST_TIMEOUT;
                                else respond = 1'b0;
                            end
                        end
                        UART_MEM_CMD_RELEASE: begin
                            r_d.hold = 1'b0;
                            r_d.release_pulse = 1'b1;
                        end
                        UART_MEM_CMD_ENTRY: begin
                            r_d.entry_pulse = 1'b1;
                            r_d.entry_addr = r_q.address;
                        end
                        UART_MEM_CMD_WRITE: respond = write_done;
                        UART_MEM_CMD_READ: r_d.rsp_length = r_q.length;
                        UART_MEM_CMD_DIGEST: begin
                            respond = digest_done;
                            r_d.rsp_length = 16'd4;
                            r_d.cur_word = ~r_q.crc32;
                            r_d.cur_valid = 1'b1;
                        end
                        default: ;
                    endcase
                end
                if (rx_valid_i && (r_d.status == UART_MEM_ST_OK))
                    r_d.status = UART_MEM_ST_OVERRUN;
                if (respond) begin
                    r_d.phase = UART_MEM_RESPOND;
                    r_d.index = 16'h0;
                    r_d.crc32 = 32'hffff_ffff;
                end
            end
            UART_MEM_RESPOND: begin
                // Why: the host may have only one frame outstanding, so a byte
                // that arrives before the PHY accepts the status byte (even
                // while it waits for tx_ready) is an overrun. A byte on or
                // after that clock is dropped.
                if (rx_valid_i && (r_q.status == UART_MEM_ST_OK) &&
                    ((r_q.index < status_index) ||
                     ((r_q.index == status_index) && !tx_fire)))
                    r_d.status = UART_MEM_ST_OVERRUN;
                if (tx_fire) begin
                    r_d.index = r_q.index + 16'd1;
                    if ((r_q.index != 16'd0) && (r_q.index <= status_index))
                        r_d.crc32 = uart_mem_crc32(r_q.crc32, ep_tx_byte);
                    if ((r_q.index >= 16'd6) && (r_q.index < status_index) &&
                        (r_q.command == UART_MEM_CMD_READ) && (payload_lane == 2'b11))
                        r_d.cur_valid = 1'b0;
                    if (r_q.index == status_index + 16'd4) r_d.phase = UART_MEM_IDLE;
                end
            end
            default: r_d.phase = UART_MEM_IDLE;
        endcase
        if (r_q.phase != r_d.phase) timer_d = 32'h0;
    end

    `DFF_RST_VAL(r_q, r_d, clk_i, reset_i, REGS_RESET)
    `DFF_RST_VAL(timer_q, timer_d, clk_i, reset_i, '0)
    `DFF_RST_VAL(mem_timer_q, mem_timer_d, clk_i, reset_i, '0)
    `DFF_RST_VAL(pace_q, pace_d, clk_i, reset_i, 16'hffff)

    assign down_rx_byte_o = rx_byte_i;
    assign owner_o = (r_q.phase != UART_MEM_IDLE);
    assign down_tx_ready_o = tx_ready_i && !responding;
    assign tx_valid_o = responding ? ep_tx_valid : down_tx_valid_i;
    assign tx_byte_o = responding ? ep_tx_byte : down_tx_byte_i;

    assign hold_o = r_q.hold;
    assign release_o = r_q.release_pulse;
    assign entry_valid_o = r_q.entry_pulse;
    assign entry_addr_o = r_q.entry_addr;

    assign mem_req_valid_o = r_q.req_valid;
    assign mem_req_write_o = r_q.req_write;
    assign mem_req_addr_o = r_q.req_addr;
    assign mem_req_wdata_o = r_q.req_wdata;

    `ASSERT_CLOCKED_FATAL(clk_i, reset_i, !(r_q.req_valid && r_q.outstanding),
        "n2a_uart_mem has more than one request in flight")
    `ASSERT_CLOCKED_FATAL_WHEN(clk_i, reset_i, mem_rsp_valid_i, r_q.outstanding,
        "n2a_uart_mem response without an outstanding request")
    `ASSERT_CLOCKED_FATAL_WHEN(clk_i, reset_i, r_q.req_valid && r_q.req_write,
        hold_ack_i, "n2a_uart_mem write request while the CPU is not held")
    `ASSERT_CLOCKED_FATAL_WHEN(clk_i, reset_i, mem_accept && !hold_ack_i,
        pace_q >= LIVE_GAP, "n2a_uart_mem live request inside the pacing gap")
endmodule
