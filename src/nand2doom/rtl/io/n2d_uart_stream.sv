`include "src/common/rtl/pkg/macros.vh"

// Streaming image-load session on the shared debug UART (wiki/src/nand2doom/rtl/io/mas.md
// "Streaming load session"). The router sits in front of the bulk framer and
// legacy engine. At an idle legacy boundary the stream start byte opens a
// session; until the session closes every received byte belongs to this
// block, so no payload residue can reach a legacy parser. Frames carry an
// absolute SDRAM address, a header CRC-8 and a frame CRC-32. Writes are
// idempotent, so a lost, corrupt or dropped frame is recovered by host
// retransmission rather than by device state. Two payload slots let one frame
// commit through the existing atomic eight-word debug transactions while the
// next frame is received; no per-frame stop-and-wait is required.
module n2d_uart_stream
    import n2d_pkg::*;
#(
    parameter int unsigned GAP_TIMEOUT_CYCLES = 1_000_000,
    parameter int unsigned SESSION_IDLE_CYCLES = 50_000_000,
    parameter int unsigned OP_TIMEOUT_CYCLES = 1024
) (
    input  logic                  clk_i,
    input  logic                  reset_i,
    // Shared byte PHY.
    input  logic [7:0]            rx_byte_i,
    input  logic                  rx_valid_i,
    input  logic                  rx_error_i,
    input  logic                  rx_busy_i,
    output logic [7:0]            tx_byte_o,
    output logic                  tx_valid_o,
    input  logic                  tx_ready_i,
    input  logic                  tx_busy_i,
    // Downstream bulk router and legacy engine byte path.
    output logic [7:0]            down_rx_byte_o,
    output logic                  down_rx_valid_o,
    output logic                  down_rx_error_o,
    input  logic [7:0]            down_tx_byte_i,
    input  logic                  down_tx_valid_i,
    output logic                  down_tx_ready_o,
    input  logic                  legacy_idle_i,
    // Session ownership of the debug memory port (n2d_pkg groups).
    output logic                  session_o,
    input  n2d_debug_status_t     debug_status_i,
    output n2d_debug_txn_req_t    txn_req_o,
    input  n2d_debug_txn_status_t txn_status_i,
    output n2d_debug_mem_req_t    mem_req_o,
    input  n2d_debug_mem_rsp_t    mem_rsp_i
);
    import n2d_types_pkg::*;

    localparam int unsigned GAP_WIDTH = $clog2(GAP_TIMEOUT_CYCLES);
    localparam int unsigned IDLE_WIDTH = $clog2(SESSION_IDLE_CYCLES);
    localparam int unsigned OP_WIDTH = $clog2(OP_TIMEOUT_CYCLES);

    `N2A_ASSERT_PARAM(g_gap_timeout, GAP_TIMEOUT_CYCLES >= 2,
        "n2d_uart_stream GAP_TIMEOUT_CYCLES must be at least 2")
    `N2A_ASSERT_PARAM(g_session_idle, SESSION_IDLE_CYCLES >= 2,
        "n2d_uart_stream SESSION_IDLE_CYCLES must be at least 2")
    `N2A_ASSERT_PARAM(g_op_timeout, OP_TIMEOUT_CYCLES >= 2,
        "n2d_uart_stream OP_TIMEOUT_CYCLES must be at least 2")

    localparam stream_regs_t REGS_RESET = '{
        session: 1'b0, closing: 1'b0, rx_state: STREAM_RX_HUNT, header_index: '0,
        header_crc: '0, crc: '1, frame: '0, payload_bytes: '0,
        payload_index: '0, word_acc: '0, crc_index: '0, received_crc: '0,
        store: 1'b0, entries: '0, wr_ptr: 1'b0,
        rd_ptr: 1'b0, count: '0, x_state: STREAM_X_IDLE, x_address: '0,
        x_remaining: '0, x_word: '0, x_chunk_left: '0, x_crc: '0,
        x_abort_status: '0, txn_begin: 1'b0,
        txn_commit: 1'b0, txn_abort: 1'b0, committed: '0,
        reply_active: 1'b0, reply_index: '0, reply_crc: '0, reply_kind: '0,
        reply_seq: '0, reply_status: '0, reply_value: '0};
    stream_regs_t regs_q, regs_d;
    // Why: the gap, session-idle and operation timer widths follow the timeout
    // parameters, so they are registers of their own beside the packaged state.
    logic [GAP_WIDTH-1:0]  gap_q, gap_d;
    logic [IDLE_WIDTH-1:0] idle_q, idle_d;
    logic [OP_WIDTH-1:0]   op_counter_q, op_counter_d;

    stream_entry_t head;
    logic claim, halted, head_write;
    logic [2:0] word_bytes;
    logic [3:0] chunk_words;
    logic [31:0] words_left;
    logic [31:0] ram_read_data, ram_write_data, next_word;
    logic [8:0] ram_write_address;
    logic ram_write;
    logic [7:0] reply_byte;
    logic push, pop, start_reply, header_good, kind_known, length_good;
    logic [7:0] start_status;
    logic [31:0] start_value;
    stream_entry_t push_entry;

    assign head = regs_q.entries[regs_q.rd_ptr];
    assign head_write = (head.kind == N2D_STREAM_WRITE);
    assign halted = debug_status_i.halt_ack && debug_status_i.cache_maint_done;
    assign claim = !regs_q.session && rx_valid_i && legacy_idle_i && !tx_busy_i &&
                   (rx_byte_i == N2D_STREAM_REQUEST_SOF);
    assign word_bytes = (regs_q.x_remaining >= 32'd4) ? 3'd4 : regs_q.x_remaining[2:0];
    assign words_left = (regs_q.x_remaining + 32'd3) >> 2;
    assign chunk_words = (words_left >= 32'd8) ? 4'd8 : words_left[3:0];
    assign next_word = ((regs_q.payload_index[1:0] == 2'd0) ? 32'd0 : regs_q.word_acc) |
                       (32'(rx_byte_i) << (8 * regs_q.payload_index[1:0]));
    assign kind_known = (regs_q.frame.kind == N2D_STREAM_HELLO) ||
                        (regs_q.frame.kind == N2D_STREAM_WRITE) ||
                        (regs_q.frame.kind == N2D_STREAM_DIGEST) ||
                        (regs_q.frame.kind == N2D_STREAM_END);
    assign header_good = (rx_byte_i == regs_q.header_crc);

    always_comb begin
        case (regs_q.frame.kind)
            N2D_STREAM_WRITE: length_good = (regs_q.frame.length != 0) &&
                                            (regs_q.frame.length <= N2D_STREAM_MAX_PAYLOAD);
            N2D_STREAM_DIGEST: length_good = (regs_q.frame.length != 0) &&
                                             (regs_q.frame.length <= N2D_SDRAM_BYTES);
            default: length_good = (regs_q.frame.length == 0);
        endcase
    end

    // Why: two 1 KiB payload slots so receive can fill one while commit reads the
    // other. The read address changes only when the executor advances, so
    // STREAM_X_FETCH waits for the synchronous read.
    n2a_sdp_ram #(.DATA_WIDTH(32), .DEPTH(512)) u_payload (
        .clk_i,
        .write_enable_i(ram_write),
        .write_address_i(ram_write_address),
        .write_data_i(ram_write_data),
        .read_address_i({regs_q.rd_ptr, regs_q.x_word}),
        .read_data_o(ram_read_data)
    );

    always_comb begin
        case (regs_q.reply_index)
            4'd0: reply_byte = N2D_STREAM_RESPONSE_SOF;
            4'd1: reply_byte = regs_q.reply_kind | 8'h80;
            4'd2: reply_byte = regs_q.reply_seq[7:0];
            4'd3: reply_byte = regs_q.reply_seq[15:8];
            4'd4: reply_byte = regs_q.reply_status;
            4'd5: reply_byte = regs_q.reply_value[7:0];
            4'd6: reply_byte = regs_q.reply_value[15:8];
            4'd7: reply_byte = regs_q.reply_value[23:16];
            4'd8: reply_byte = regs_q.reply_value[31:24];
            default: reply_byte = regs_q.reply_crc;
        endcase
    end

    // Why: the legacy path sees every byte outside a session except the claiming
    // start byte, and none inside one, so legacy framing is unchanged by the
    // router.
    assign session_o = regs_q.session;
    assign down_rx_byte_o = rx_byte_i;
    assign down_rx_valid_o = !regs_q.session && rx_valid_i && !claim;
    assign down_rx_error_o = !regs_q.session && rx_error_i;
    assign down_tx_ready_o = !regs_q.session && tx_ready_i;
    assign tx_valid_o = regs_q.session ? regs_q.reply_active : down_tx_valid_i;
    assign tx_byte_o = regs_q.session ? reply_byte : down_tx_byte_i;

    assign txn_req_o = '{
        arb_req: (regs_q.x_state != STREAM_X_IDLE),
        txn_begin: regs_q.txn_begin,
        txn_commit: regs_q.txn_commit,
        txn_abort: regs_q.txn_abort};
    assign mem_req_o = '{
        valid: (regs_q.x_state == STREAM_X_REQ) && txn_status_i.arb_grant,
        write: head_write,
        addr: regs_q.x_address,
        wdata: ram_read_data,
        wstrb: head_write ? ((word_bytes == 3'd4) ? 4'hf :
                             4'((5'd1 << word_bytes) - 5'd1)) : 4'h0};

    always_comb begin
        regs_d = regs_q;
        op_counter_d = op_counter_q;
        idle_d = idle_q;
        gap_d = gap_q;
        regs_d.txn_begin = 1'b0;
        regs_d.txn_commit = 1'b0;
        regs_d.txn_abort = 1'b0;
        push = 1'b0;
        pop = 1'b0;
        push_entry = regs_q.frame;
        start_reply = 1'b0;
        start_status = N2D_ST_OK;
        start_value = '0;
        ram_write = 1'b0;
        ram_write_address = {regs_q.wr_ptr, regs_q.payload_index[9:2]};
        ram_write_data = next_word;

        if (claim) begin
            regs_d = REGS_RESET;
            gap_d = '0;
            idle_d = '0;
            op_counter_d = '0;
            regs_d.session = 1'b1;
            regs_d.rx_state = STREAM_RX_HEADER;
            regs_d.header_index = 4'd1;
        end else if (regs_q.session) begin
            if ((regs_q.rx_state != STREAM_RX_HUNT) && !rx_valid_i)
                gap_d = gap_q + 1'b1;
            else
                gap_d = '0;
            if (rx_error_i ||
                ((regs_q.rx_state != STREAM_RX_HUNT) && !rx_valid_i &&
                 (gap_q == GAP_WIDTH'(GAP_TIMEOUT_CYCLES - 1)))) begin
                // Why: a broken byte or a stalled frame abandons only that frame, so a line
                // glitch does not end the session.
                regs_d.rx_state = STREAM_RX_HUNT;
                gap_d = '0;
            end else if (rx_valid_i) begin
                case (regs_q.rx_state)
                    STREAM_RX_HUNT: if (rx_byte_i == N2D_STREAM_REQUEST_SOF) begin
                        regs_d.rx_state = STREAM_RX_HEADER;
                        regs_d.header_index = 4'd1;
                        regs_d.header_crc = '0;
                        regs_d.crc = '1;
                    end
                    STREAM_RX_HEADER: begin
                        regs_d.crc = n2d_crc32_byte(regs_q.crc, rx_byte_i);
                        regs_d.header_index = regs_q.header_index + 1'b1;
                        if (regs_q.header_index != 4'd12)
                            regs_d.header_crc = n2d_crc8_byte(regs_q.header_crc, rx_byte_i);
                        case (regs_q.header_index)
                            4'd1: regs_d.frame.kind = rx_byte_i;
                            4'd2: regs_d.frame.seq[7:0] = rx_byte_i;
                            4'd3: regs_d.frame.seq[15:8] = rx_byte_i;
                            4'd4: regs_d.frame.address[7:0] = rx_byte_i;
                            4'd5: regs_d.frame.address[15:8] = rx_byte_i;
                            4'd6: regs_d.frame.address[23:16] = rx_byte_i;
                            4'd7: regs_d.frame.address[31:24] = rx_byte_i;
                            4'd8: regs_d.frame.length[7:0] = rx_byte_i;
                            4'd9: regs_d.frame.length[15:8] = rx_byte_i;
                            4'd10: regs_d.frame.length[23:16] = rx_byte_i;
                            4'd11: regs_d.frame.length[31:24] = rx_byte_i;
                            default: begin
                                regs_d.frame.seed = '0;
                                regs_d.payload_index = '0;
                                regs_d.crc_index = '0;
                                regs_d.store = (regs_q.count != 2'd2);
                                regs_d.payload_bytes =
                                    (regs_q.frame.kind == N2D_STREAM_WRITE) ?
                                        regs_q.frame.length[10:0] :
                                    (regs_q.frame.kind == N2D_STREAM_DIGEST) ? 11'd4 : 11'd0;
                                if (!header_good) begin
                                    // Why: an untrusted header gets no reply because its fields cannot be
                                    // trusted; the parser resynchronizes silently.
                                    regs_d.rx_state = STREAM_RX_HUNT;
                                end else if (!kind_known || !length_good) begin
                                    // Why: a trusted header without a usable length is still answered, in order,
                                    // so the host learns of the error; the parser then resynchronizes because
                                    // the payload extent is unknown.
                                    push = (regs_q.count != 2'd2);
                                    push_entry.status = !kind_known ? N2D_ST_COMMAND :
                                                                      N2D_ST_LENGTH;
                                    regs_d.rx_state = STREAM_RX_HUNT;
                                end else if ((regs_q.frame.kind == N2D_STREAM_WRITE) ||
                                             (regs_q.frame.kind == N2D_STREAM_DIGEST)) begin
                                    regs_d.rx_state = STREAM_RX_PAYLOAD;
                                end else begin
                                    regs_d.rx_state = STREAM_RX_CRC;
                                end
                            end
                        endcase
                    end
                    STREAM_RX_PAYLOAD: begin
                        regs_d.crc = n2d_crc32_byte(regs_q.crc, rx_byte_i);
                        regs_d.payload_index = regs_q.payload_index + 1'b1;
                        regs_d.word_acc = next_word;
                        if (regs_q.frame.kind == N2D_STREAM_DIGEST)
                            regs_d.frame.seed = next_word;
                        else if (regs_q.store && ((regs_q.payload_index[1:0] == 2'd3) ||
                                 (regs_q.payload_index == regs_q.payload_bytes - 1'b1)))
                            ram_write = 1'b1;
                        if (regs_q.payload_index == regs_q.payload_bytes - 1'b1)
                            regs_d.rx_state = STREAM_RX_CRC;
                    end
                    STREAM_RX_CRC: begin
                        regs_d.received_crc = {rx_byte_i, regs_q.received_crc[23:8]};
                        regs_d.crc_index = regs_q.crc_index + 1'b1;
                        if (regs_q.crc_index == 2'd3) begin
                            regs_d.rx_state = STREAM_RX_HUNT;
                            push = regs_q.store;
                            if ({rx_byte_i, regs_q.received_crc} != ~regs_q.crc)
                                push_entry.status = N2D_ST_CHECKSUM;
                            else if ((regs_q.frame.kind == N2D_STREAM_WRITE) ||
                                     (regs_q.frame.kind == N2D_STREAM_DIGEST)) begin
                                if (regs_q.frame.address[1:0] != 2'b00)
                                    push_entry.status = N2D_ST_ALIGNMENT;
                                else if (!n2d_stream_range_valid(regs_q.frame.address,
                                                                 regs_q.frame.length))
                                    push_entry.status = N2D_ST_RANGE;
                                else
                                    push_entry.status = N2D_ST_OK;
                            end else
                                push_entry.status = N2D_ST_OK;
                        end
                    end
                    default: regs_d.rx_state = STREAM_RX_HUNT;
                endcase
            end
        end

        if (regs_q.session && !claim) begin
            case (regs_q.x_state)
                STREAM_X_IDLE: if ((regs_q.count != 0) && !regs_q.reply_active && !regs_q.closing) begin
                    if (head.status != N2D_ST_OK) begin
                        start_reply = 1'b1;
                        start_status = head.status;
                    end else if (head.kind == N2D_STREAM_HELLO) begin
                        start_reply = 1'b1;
                        start_value = N2D_STREAM_IDENTITY;
                    end else if (head.kind == N2D_STREAM_END) begin
                        start_reply = 1'b1;
                        start_value = regs_q.committed;
                        regs_d.closing = 1'b1;
                    end else if (!halted) begin
                        start_reply = 1'b1;
                        start_status = N2D_ST_NOT_HALTED;
                    end else begin
                        regs_d.x_state = STREAM_X_ARB;
                        regs_d.x_address = head.address;
                        regs_d.x_remaining = head.length;
                        regs_d.x_word = '0;
                        regs_d.x_crc = head.seed;
                        op_counter_d = '0;
                    end
                end
                STREAM_X_ARB: begin
                    if (txn_status_i.arb_grant) begin
                        regs_d.txn_begin = 1'b1;
                        regs_d.x_chunk_left = chunk_words;
                        op_counter_d = '0;
                        regs_d.x_state = head_write ? STREAM_X_FETCH : STREAM_X_REQ;
                    end else if (op_counter_q == OP_WIDTH'(OP_TIMEOUT_CYCLES - 1)) begin
                        start_reply = 1'b1;
                        start_status = N2D_ST_GRANT;
                        regs_d.x_state = STREAM_X_IDLE;
                    end else op_counter_d = op_counter_q + 1'b1;
                end
                STREAM_X_FETCH: regs_d.x_state = STREAM_X_REQ;
                STREAM_X_REQ: begin
                    if (!txn_status_i.arb_grant) begin
                        regs_d.txn_abort = 1'b1;
                        regs_d.x_abort_status = N2D_ST_GRANT;
                        regs_d.x_state = STREAM_X_ABORT;
                    end else if (mem_rsp_i.ready) begin
                        op_counter_d = '0;
                        regs_d.x_state = STREAM_X_RSP;
                    end else if (op_counter_q == OP_WIDTH'(OP_TIMEOUT_CYCLES - 1)) begin
                        regs_d.txn_abort = 1'b1;
                        regs_d.x_abort_status = N2D_ST_TIMEOUT;
                        regs_d.x_state = STREAM_X_ABORT;
                    end else op_counter_d = op_counter_q + 1'b1;
                end
                STREAM_X_RSP: begin
                    if (!txn_status_i.arb_grant) begin
                        regs_d.txn_abort = 1'b1;
                        regs_d.x_abort_status = N2D_ST_GRANT;
                        regs_d.x_state = STREAM_X_ABORT;
                    end else if (mem_rsp_i.rsp_valid) begin
                        op_counter_d = '0;
                        if (mem_rsp_i.rsp_error) begin
                            regs_d.txn_abort = 1'b1;
                            regs_d.x_abort_status = N2D_ST_MEMORY;
                            regs_d.x_state = STREAM_X_ABORT;
                        end else begin
                            if (!head_write)
                                regs_d.x_crc = n2d_crc32_word(regs_q.x_crc, mem_rsp_i.rdata,
                                                              word_bytes);
                            regs_d.x_remaining = regs_q.x_remaining - 32'(word_bytes);
                            regs_d.x_address = regs_q.x_address + 32'd4;
                            regs_d.x_word = regs_q.x_word + 1'b1;
                            regs_d.x_chunk_left = regs_q.x_chunk_left - 1'b1;
                            if (regs_q.x_chunk_left == 4'd1) begin
                                regs_d.txn_commit = 1'b1;
                                regs_d.x_state = STREAM_X_COMMIT;
                            end else regs_d.x_state = head_write ? STREAM_X_FETCH : STREAM_X_REQ;
                        end
                    end else if (op_counter_q == OP_WIDTH'(OP_TIMEOUT_CYCLES - 1)) begin
                        regs_d.txn_abort = 1'b1;
                        regs_d.x_abort_status = N2D_ST_TIMEOUT;
                        regs_d.x_state = STREAM_X_ABORT;
                    end else op_counter_d = op_counter_q + 1'b1;
                end
                STREAM_X_COMMIT: begin
                    if (!txn_status_i.arb_grant) begin
                        regs_d.txn_abort = 1'b1;
                        regs_d.x_abort_status = N2D_ST_GRANT;
                        regs_d.x_state = STREAM_X_ABORT;
                    end else if (txn_status_i.txn_done) begin
                        op_counter_d = '0;
                        if (txn_status_i.txn_error) begin
                            start_reply = 1'b1;
                            start_status = N2D_ST_MEMORY;
                            regs_d.x_state = STREAM_X_IDLE;
                        end else if (regs_q.x_remaining == 0) begin
                            start_reply = 1'b1;
                            start_value = head_write ? head.address : regs_q.x_crc;
                            if (head_write) regs_d.committed = regs_q.committed + 1'b1;
                            regs_d.x_state = STREAM_X_IDLE;
                        end else regs_d.x_state = STREAM_X_ARB;
                    end else if (op_counter_q == OP_WIDTH'(OP_TIMEOUT_CYCLES - 1)) begin
                        regs_d.txn_abort = 1'b1;
                        regs_d.x_abort_status = N2D_ST_TIMEOUT;
                        regs_d.x_state = STREAM_X_ABORT;
                    end else op_counter_d = op_counter_q + 1'b1;
                end
                STREAM_X_ABORT: if (txn_status_i.txn_done) begin
                    // Ref: wiki/src/nand2doom/rtl/io/mas.md "Host-memory transaction boundary":
                    // txn_done after txn_abort means the adapter has discarded or rolled back the
                    // chunk, so the reply follows the rollback.
                    start_reply = 1'b1;
                    start_status = regs_q.x_abort_status;
                    regs_d.x_state = STREAM_X_IDLE;
                end
                default: regs_d.x_state = STREAM_X_IDLE;
            endcase
        end

        if (regs_q.session && regs_q.reply_active && tx_ready_i) begin
            if ((regs_q.reply_index != 4'd0) && (regs_q.reply_index != 4'd9))
                regs_d.reply_crc = n2d_crc8_byte(regs_q.reply_crc, reply_byte);
            regs_d.reply_index = regs_q.reply_index + 1'b1;
            if (regs_q.reply_index == 4'd9) regs_d.reply_active = 1'b0;
        end
        if (start_reply) begin
            pop = 1'b1;
            regs_d.reply_active = 1'b1;
            regs_d.reply_index = '0;
            regs_d.reply_crc = '0;
            regs_d.reply_kind = head.kind;
            regs_d.reply_seq = head.seq;
            regs_d.reply_status = start_status;
            regs_d.reply_value = start_value;
        end

        if (push) begin
            regs_d.entries[regs_q.wr_ptr] = push_entry;
            regs_d.wr_ptr = !regs_q.wr_ptr;
        end
        if (pop) regs_d.rd_ptr = !regs_q.rd_ptr;
        if (push && !pop) regs_d.count = regs_q.count + 1'b1;
        else if (pop && !push) regs_d.count = regs_q.count - 1'b1;

        if (regs_q.session && !claim) begin
            if ((regs_q.rx_state == STREAM_RX_HUNT) && (regs_q.count == 0) &&
                (regs_q.x_state == STREAM_X_IDLE) && !regs_q.reply_active && !regs_q.closing &&
                !rx_valid_i && !rx_error_i && !rx_busy_i && !tx_busy_i)
                idle_d = idle_q + 1'b1;
            else
                idle_d = '0;
            if ((regs_q.closing && !regs_q.reply_active && !tx_busy_i) ||
                (idle_q == IDLE_WIDTH'(SESSION_IDLE_CYCLES - 1))) begin
                regs_d = REGS_RESET;
                gap_d = '0;
                idle_d = '0;
                op_counter_d = '0;
            end
        end
    end

    `N2A_DFF_ARST_VAL(regs_q, regs_d, clk_i, reset_i, REGS_RESET)
    `N2A_DFF_ARST_VAL(gap_q, gap_d, clk_i, reset_i, '0)
    `N2A_DFF_ARST_VAL(idle_q, idle_d, clk_i, reset_i, '0)
    `N2A_DFF_ARST_VAL(op_counter_q, op_counter_d, clk_i, reset_i, '0)

    `N2A_ASSERT_CLOCKED_ARST_FATAL(clk_i, reset_i, regs_q.count <= 2'd2,
        "stream queue overflow")
    `N2A_ASSERT_CLOCKED_ARST_FATAL_WHEN(clk_i, reset_i, regs_q.session,
        (!down_rx_valid_o && !down_rx_error_o), "stream byte reached legacy path")
    `N2A_ASSERT_CLOCKED_ARST_FATAL_WHEN(clk_i, reset_i, mem_req_o.valid,
        (halted && regs_q.session), "stream memory access outside maintained halt")
    `N2A_ASSERT_CLOCKED_ARST_FATAL(clk_i, reset_i,
        !(regs_q.txn_commit && regs_q.txn_abort), "stream commit and abort overlap")
    `N2A_ASSERT_CLOCKED_ARST_FATAL_WHEN(clk_i, reset_i, ram_write,
        (regs_q.count != 2'd2), "stream wrote an occupied slot")
endmodule
