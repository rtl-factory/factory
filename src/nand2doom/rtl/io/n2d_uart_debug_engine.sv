`include "src/common/rtl/pkg/macros.vh"

module n2d_uart_debug_engine
    import n2d_pkg::*;
#(
    parameter int unsigned CLKS_PER_BIT = 25,
    parameter int unsigned INTERBYTE_TIMEOUT_CYCLES = 512,
    parameter int unsigned OP_TIMEOUT_CYCLES = 1024,
    parameter int unsigned HALT_TIMEOUT_CYCLES = OP_TIMEOUT_CYCLES,
    parameter int unsigned MAX_WORDS = 8
) (
    input  logic                  clk_i,
    input  logic                  reset_i,
    input  logic [7:0]            rx_byte,
    input  logic                  rx_valid,
    input  logic                  rx_framing_error,
    input  logic                  tx_ready,
    output logic                  tx_valid,
    output logic [7:0]            tx_byte,
    output logic                  idle_o,
    output logic                  frame_idle_o,
    // The UART memory endpoint released the CPU: drop this engine's halt
    // request too. Only while the engine is idle (it owns no frame).
    input  logic                  release_i,

    // Port groups are defined in n2d_pkg; each carries one direction.
    output n2d_debug_control_t    debug_control_o,
    input  n2d_debug_status_t     debug_status_i,
    output n2d_input_event_t      input_event_o,
    input  logic                  input_event_ready_i,
    output n2d_debug_txn_req_t    txn_req_o,
    input  n2d_debug_txn_status_t txn_status_i,
    output n2d_debug_mem_req_t    mem_req_o,
    input  n2d_debug_mem_rsp_t    mem_rsp_i
);
    import n2d_types_pkg::*;

    // CAPS is three words even for a smaller parameterized memory burst.
    localparam int unsigned RX_PAYLOAD_BYTES = (MAX_WORDS < 3) ? 12 : 4 * MAX_WORDS;
    localparam int unsigned RX_INDEX_WIDTH =
        (RX_PAYLOAD_BYTES <= 1) ? 1 : $clog2(RX_PAYLOAD_BYTES + 1);
    localparam int unsigned RX_PAYLOAD_INDEX_WIDTH =
        (RX_PAYLOAD_BYTES <= 1) ? 1 : $clog2(RX_PAYLOAD_BYTES);
    localparam int unsigned WORD_INDEX_WIDTH =
        (MAX_WORDS <= 1) ? 1 : $clog2(MAX_WORDS);
    localparam int unsigned INTERBYTE_COUNTER_WIDTH =
        (INTERBYTE_TIMEOUT_CYCLES <= 1) ? 1 : $clog2(INTERBYTE_TIMEOUT_CYCLES);
    localparam int unsigned OP_COUNTER_WIDTH =
        (OP_TIMEOUT_CYCLES <= 1) ? 1 : $clog2(OP_TIMEOUT_CYCLES);
    localparam int unsigned HALT_COUNTER_WIDTH =
        (HALT_TIMEOUT_CYCLES <= 1) ? 1 : $clog2(HALT_TIMEOUT_CYCLES);

    localparam debug_rx_t RX_RESET = '{
        rx_state: DEBUG_RX_WAIT_SOF,
        rx_header_index: '0,
        rx_discard_remaining: '0,
        rx_crc: '0,
        req_version: '0,
        req_command: '0,
        req_sequence: '0,
        req_address: '0,
        req_count: '0,
        req_byte_enable: '0,
        packet_valid: 1'b0,
        packet_checksum_ok: 1'b0,
        parser_error_valid: 1'b0,
        parser_error_status: N2D_ST_OK,
        parser_error_command: '0,
        parser_error_sequence: '0};
    debug_rx_t rx_q, rx_d;
    // Parser fields whose widths follow MAX_WORDS or INTERBYTE_TIMEOUT_CYCLES
    // are registers of their own beside the packaged parser record.
    logic [RX_PAYLOAD_INDEX_WIDTH-1:0]  rx_payload_index_q, rx_payload_index_d;
    logic [RX_INDEX_WIDTH-1:0]          rx_payload_length_q, rx_payload_length_d;
    logic [INTERBYTE_COUNTER_WIDTH-1:0] interbyte_counter_q, interbyte_counter_d;
    logic [8*RX_PAYLOAD_BYTES-1:0]      req_payload_q, req_payload_d;
    localparam debug_busy_t BUSY_RESET = '{
        busy_rx_state: DEBUG_RX_WAIT_SOF,
        busy_header_index: '0,
        busy_payload_remaining: '0,
        busy_crc: '0,
        busy_command: '0,
        busy_sequence: '0,
        busy_count: '0,
        busy_packet_valid: 1'b0};
    debug_busy_t busy_q, busy_d;
    logic [INTERBYTE_COUNTER_WIDTH-1:0] busy_interbyte_counter_q, busy_interbyte_counter_d;
    localparam debug_exec_t EXEC_RESET = '{
        exec_state: DEBUG_EXEC_IDLE,
        transaction_active: 1'b0,
        abort_status: N2D_ST_MEMORY,
        busy_pending: 1'b0,
        busy_pending_command: '0,
        busy_pending_sequence: '0,
        response_active: 1'b0,
        response_command: '0,
        response_sequence: '0,
        response_status: '0,
        response_count: '0,
        response_crc: '0,
        halt_req: 1'b1,
        cache_invalidate: 1'b0,
        reset_pc_valid: 1'b0,
        reset_pc: '0,
        txn_begin: 1'b0,
        txn_commit: 1'b0,
        txn_abort: 1'b0};
    debug_exec_t exec_q, exec_d;
    // Executor fields whose widths follow the timeout parameters or MAX_WORDS
    // are registers of their own beside the packaged executor record.
    logic [OP_COUNTER_WIDTH-1:0]   op_counter_q, op_counter_d;
    logic [HALT_COUNTER_WIDTH-1:0] halt_counter_q, halt_counter_d;
    logic [WORD_INDEX_WIDTH-1:0]   word_index_q, word_index_d;
    logic [RX_INDEX_WIDTH:0]       response_index_q, response_index_d;
    logic [RX_INDEX_WIDTH:0]       response_payload_bytes_q, response_payload_bytes_d;
    logic [8*RX_PAYLOAD_BYTES-1:0] response_payload_q, response_payload_d;
    // Response start bundle raised by exactly one executor branch per clock.
    logic start_response;
    logic [7:0] start_command, start_sequence, start_status, start_count;
    logic input_event_valid;
    logic [9:0] input_event;
    logic arb_request, mem_txn_begin, mem_txn_commit, mem_txn_abort;
    logic mem_req_valid, mem_req_write;
    logic [31:0] mem_req_addr, mem_req_wdata;
    logic [3:0] mem_req_wstrb;
    logic service_busy;

    assign frame_idle_o = (rx_q.rx_state == DEBUG_RX_WAIT_SOF) &&
                           (busy_q.busy_rx_state == DEBUG_RX_WAIT_SOF) &&
                           !rx_q.packet_valid && !rx_q.parser_error_valid && !busy_q.busy_packet_valid;
    assign idle_o = !service_busy && frame_idle_o;

    always_comb begin
        tx_valid = exec_q.response_active;
        tx_byte = 8'h00;
        if (response_index_q == 0) begin
            tx_byte = N2D_DEBUG_RESPONSE_SOF;
        end else if (response_index_q == 1) begin
            tx_byte = N2D_DEBUG_VERSION;
        end else if (response_index_q == 2) begin
            tx_byte = exec_q.response_command;
        end else if (response_index_q == 3) begin
            tx_byte = exec_q.response_sequence;
        end else if (response_index_q == 4) begin
            tx_byte = exec_q.response_status;
        end else if (response_index_q == 5) begin
            tx_byte = exec_q.response_count;
        end else if (response_index_q < (6 + response_payload_bytes_q)) begin
            tx_byte = response_payload_q[(32'(response_index_q - 6))*8 +: 8];
        end else begin
            tx_byte = exec_q.response_crc;
        end
    end

    assign input_event_valid = (exec_q.exec_state == DEBUG_EXEC_INPUT_WAIT);
    assign input_event = {
        req_payload_q[9:8], req_payload_q[7:0]
    };
    assign mem_txn_begin = exec_q.txn_begin;
    assign mem_txn_commit = exec_q.txn_commit;
    assign mem_txn_abort = exec_q.txn_abort;
    assign arb_request = (exec_q.exec_state == DEBUG_EXEC_ARB_WAIT) ||
                       (exec_q.exec_state == DEBUG_EXEC_MEM_REQ) ||
                       (exec_q.exec_state == DEBUG_EXEC_MEM_RSP) ||
                       (exec_q.exec_state == DEBUG_EXEC_COMMIT_WAIT) ||
                       (exec_q.exec_state == DEBUG_EXEC_ABORT_WAIT);
    assign mem_req_valid = (exec_q.exec_state == DEBUG_EXEC_MEM_REQ) && txn_status_i.arb_grant;
    assign mem_req_write = (rx_q.req_command == N2D_CMD_POKE) ||
                             (rx_q.req_command == N2D_CMD_LOAD);
    assign mem_req_addr = rx_q.req_address + ({29'b0, word_index_q} << 2);
    assign mem_req_wdata = {
        req_payload_q[(32'((word_index_q * 4) + 3))*8 +: 8],
        req_payload_q[(32'((word_index_q * 4) + 2))*8 +: 8],
        req_payload_q[(32'((word_index_q * 4) + 1))*8 +: 8],
        req_payload_q[(32'((word_index_q * 4)))*8 +: 8]
    };
    assign mem_req_wstrb = (rx_q.req_command == N2D_CMD_LOAD) ? 4'hf :
                             (mem_req_write ? rx_q.req_byte_enable : 4'h0);
    assign debug_control_o = '{halt_req: exec_q.halt_req, cache_invalidate: exec_q.cache_invalidate,
                               reset_pc_valid: exec_q.reset_pc_valid, reset_pc: exec_q.reset_pc};
    assign input_event_o = '{valid: input_event_valid, code: input_event};
    assign txn_req_o = '{arb_req: arb_request, txn_begin: mem_txn_begin,
                         txn_commit: mem_txn_commit, txn_abort: mem_txn_abort};
    assign mem_req_o = '{valid: mem_req_valid, write: mem_req_write, addr: mem_req_addr,
                         wdata: mem_req_wdata, wstrb: mem_req_wstrb};
    assign service_busy = (exec_q.exec_state != DEBUG_EXEC_IDLE) || exec_q.response_active ||
                          exec_q.busy_pending;

    always_comb begin
        rx_d = rx_q;
        req_payload_d = req_payload_q;
        interbyte_counter_d = interbyte_counter_q;
        rx_payload_length_d = rx_payload_length_q;
        rx_payload_index_d = rx_payload_index_q;
        rx_d.packet_valid = 1'b0;
        rx_d.parser_error_valid = 1'b0;

        if (rx_q.rx_state != DEBUG_RX_WAIT_SOF) begin
            if (rx_valid) begin
                interbyte_counter_d = '0;
            end else if (interbyte_counter_q ==
                         INTERBYTE_COUNTER_WIDTH'(INTERBYTE_TIMEOUT_CYCLES - 1)) begin
                rx_d.rx_state = DEBUG_RX_WAIT_SOF;
                rx_d.parser_error_valid = 1'b1;
                rx_d.parser_error_status = N2D_ST_TIMEOUT;
                rx_d.parser_error_command = rx_q.req_command;
                rx_d.parser_error_sequence = rx_q.req_sequence;
                interbyte_counter_d = '0;
            end else begin
                interbyte_counter_d = interbyte_counter_q + 1'b1;
            end
        end

        if (rx_framing_error && !service_busy &&
            (busy_q.busy_rx_state == DEBUG_RX_WAIT_SOF)) begin
            rx_d.rx_state = DEBUG_RX_WAIT_SOF;
            rx_d.parser_error_valid = 1'b1;
            rx_d.parser_error_status = N2D_ST_FRAMING;
            rx_d.parser_error_command = rx_q.req_command;
            rx_d.parser_error_sequence = rx_q.req_sequence;
        end else if (rx_valid && !service_busy &&
                     (busy_q.busy_rx_state == DEBUG_RX_WAIT_SOF)) begin
            unique case (rx_q.rx_state)
                DEBUG_RX_WAIT_SOF: begin
                    if (rx_byte == N2D_DEBUG_REQUEST_SOF) begin
                        rx_d.rx_state = DEBUG_RX_HEADER;
                        rx_d.rx_header_index = '0;
                        rx_payload_index_d = '0;
                        rx_d.rx_crc = '0;
                        interbyte_counter_d = '0;
                    end
                end
                DEBUG_RX_HEADER: begin
                    rx_d.rx_crc = n2d_crc8_byte(rx_q.rx_crc, rx_byte);
                    unique case (rx_q.rx_header_index)
                        0: rx_d.req_version = rx_byte;
                        1: rx_d.req_command = rx_byte;
                        2: rx_d.req_sequence = rx_byte;
                        3: rx_d.req_address[7:0] = rx_byte;
                        4: rx_d.req_address[15:8] = rx_byte;
                        5: rx_d.req_address[23:16] = rx_byte;
                        6: rx_d.req_address[31:24] = rx_byte;
                        7: rx_d.req_count = rx_byte;
                        8: begin
                            rx_d.req_byte_enable = rx_byte[3:0];
                            if (n2d_debug_command_has_payload(rx_q.req_command) &&
                                (rx_q.req_count != 0)) begin
                                if (rx_q.req_count <= 8'(MAX_WORDS)) begin
                                    rx_payload_length_d = RX_INDEX_WIDTH'(rx_q.req_count * 4);
                                    rx_d.rx_state = DEBUG_RX_PAYLOAD;
                                end else begin
                                    rx_d.rx_discard_remaining = {rx_q.req_count, 2'b00};
                                    rx_d.rx_state = DEBUG_RX_DISCARD;
                                end
                            end else begin
                                rx_payload_length_d = '0;
                                rx_d.rx_state = DEBUG_RX_CHECKSUM;
                            end
                        end
                        default: rx_d.rx_state = DEBUG_RX_WAIT_SOF;
                    endcase
                    if (rx_q.rx_header_index != 8) begin
                        rx_d.rx_header_index = rx_q.rx_header_index + 1'b1;
                    end
                end
                DEBUG_RX_PAYLOAD: begin
                    req_payload_d[(32'(rx_payload_index_q))*8 +: 8] = rx_byte;
                    rx_d.rx_crc = n2d_crc8_byte(rx_q.rx_crc, rx_byte);
                    if (rx_payload_index_q ==
                        RX_PAYLOAD_INDEX_WIDTH'(rx_payload_length_q - 1'b1)) begin
                        rx_d.rx_state = DEBUG_RX_CHECKSUM;
                    end else begin
                        rx_payload_index_d = rx_payload_index_q + 1'b1;
                    end
                end
                DEBUG_RX_DISCARD: begin
                    rx_d.rx_crc = n2d_crc8_byte(rx_q.rx_crc, rx_byte);
                    if (rx_q.rx_discard_remaining == 10'd1) begin
                        rx_d.rx_state = DEBUG_RX_CHECKSUM;
                    end else begin
                        rx_d.rx_discard_remaining = rx_q.rx_discard_remaining - 1'b1;
                    end
                end
                DEBUG_RX_CHECKSUM: begin
                    rx_d.packet_checksum_ok = (rx_byte == rx_q.rx_crc);
                    rx_d.packet_valid = 1'b1;
                    rx_d.rx_state = DEBUG_RX_WAIT_SOF;
                end
                default: rx_d.rx_state = DEBUG_RX_WAIT_SOF;
            endcase
        end
    end

    `N2A_DFF_ARST_VAL(rx_q, rx_d, clk_i, reset_i, RX_RESET)
    `N2A_DFF_ARST_VAL(rx_payload_index_q, rx_payload_index_d, clk_i, reset_i, '0)
    `N2A_DFF_ARST_VAL(rx_payload_length_q, rx_payload_length_d, clk_i, reset_i, '0)
    `N2A_DFF_ARST_VAL(interbyte_counter_q, interbyte_counter_d, clk_i, reset_i, '0)
    `N2A_DFF_ARST_VAL(req_payload_q, req_payload_d, clk_i, reset_i, '0)

    // A second, discard-only parser preserves the active request registers.
    // One complete valid frame received while service is occupied queues one
    // sequence-matched BUSY response behind the active response.
    always_comb begin
        busy_d = busy_q;
        busy_interbyte_counter_d = busy_interbyte_counter_q;
        busy_d.busy_packet_valid = 1'b0;

        if (busy_q.busy_rx_state != DEBUG_RX_WAIT_SOF) begin
            if (rx_valid) begin
                busy_interbyte_counter_d = '0;
            end else if (busy_interbyte_counter_q ==
                         INTERBYTE_COUNTER_WIDTH'(INTERBYTE_TIMEOUT_CYCLES - 1)) begin
                busy_d.busy_rx_state = DEBUG_RX_WAIT_SOF;
                busy_interbyte_counter_d = '0;
            end else begin
                busy_interbyte_counter_d = busy_interbyte_counter_q + 1'b1;
            end
        end

        if (rx_framing_error &&
            ((busy_q.busy_rx_state != DEBUG_RX_WAIT_SOF) || service_busy)) begin
            busy_d.busy_rx_state = DEBUG_RX_WAIT_SOF;
        end else if (rx_valid &&
                     ((busy_q.busy_rx_state != DEBUG_RX_WAIT_SOF) || service_busy)) begin
            unique case (busy_q.busy_rx_state)
                DEBUG_RX_WAIT_SOF: begin
                    if (rx_byte == N2D_DEBUG_REQUEST_SOF) begin
                        busy_d.busy_rx_state = DEBUG_RX_HEADER;
                        busy_d.busy_header_index = '0;
                        busy_d.busy_crc = '0;
                        busy_interbyte_counter_d = '0;
                    end
                end
                DEBUG_RX_HEADER: begin
                    busy_d.busy_crc = n2d_crc8_byte(busy_q.busy_crc, rx_byte);
                    unique case (busy_q.busy_header_index)
                        1: busy_d.busy_command = rx_byte;
                        2: busy_d.busy_sequence = rx_byte;
                        7: busy_d.busy_count = rx_byte;
                        8: begin
                            if (n2d_debug_command_has_payload(busy_q.busy_command) &&
                                (busy_q.busy_count != 0)) begin
                                busy_d.busy_payload_remaining = {busy_q.busy_count, 2'b00};
                                busy_d.busy_rx_state = DEBUG_RX_DISCARD;
                            end else begin
                                busy_d.busy_rx_state = DEBUG_RX_CHECKSUM;
                            end
                        end
                        default: begin
                        end
                    endcase
                    if (busy_q.busy_header_index != 8) begin
                        busy_d.busy_header_index = busy_q.busy_header_index + 1'b1;
                    end
                end
                DEBUG_RX_DISCARD: begin
                    busy_d.busy_crc = n2d_crc8_byte(busy_q.busy_crc, rx_byte);
                    if (busy_q.busy_payload_remaining == 10'd1) begin
                        busy_d.busy_rx_state = DEBUG_RX_CHECKSUM;
                    end else begin
                        busy_d.busy_payload_remaining = busy_q.busy_payload_remaining - 1'b1;
                    end
                end
                DEBUG_RX_CHECKSUM: begin
                    if (rx_byte == busy_q.busy_crc) begin
                        busy_d.busy_packet_valid = 1'b1;
                    end
                    busy_d.busy_rx_state = DEBUG_RX_WAIT_SOF;
                end
                default: busy_d.busy_rx_state = DEBUG_RX_WAIT_SOF;
            endcase
        end
    end

    `N2A_DFF_ARST_VAL(busy_q, busy_d, clk_i, reset_i, BUSY_RESET)
    `N2A_DFF_ARST_VAL(busy_interbyte_counter_q, busy_interbyte_counter_d, clk_i, reset_i, '0)

    always_comb begin
        exec_d = exec_q;
        response_payload_d = response_payload_q;
        response_payload_bytes_d = response_payload_bytes_q;
        response_index_d = response_index_q;
        word_index_d = word_index_q;
        halt_counter_d = halt_counter_q;
        op_counter_d = op_counter_q;
        start_response = 1'b0;
        start_command = 8'h00;
        start_sequence = 8'h00;
        start_status = N2D_ST_OK;
        start_count = 8'h00;
        exec_d.cache_invalidate = 1'b0;
        exec_d.reset_pc_valid = 1'b0;
        exec_d.txn_begin = 1'b0;
        exec_d.txn_commit = 1'b0;
        exec_d.txn_abort = 1'b0;
        if (release_i) exec_d.halt_req = 1'b0;

        if (busy_q.busy_packet_valid && !exec_q.busy_pending) begin
            exec_d.busy_pending = 1'b1;
            exec_d.busy_pending_command = busy_q.busy_command;
            exec_d.busy_pending_sequence = busy_q.busy_sequence;
        end

        if (exec_q.response_active && tx_ready) begin
            if ((response_index_q >= 1) &&
                (response_index_q < (6 + response_payload_bytes_q))) begin
                exec_d.response_crc = n2d_crc8_byte(exec_q.response_crc, tx_byte);
            end
            if (response_index_q == (6 + response_payload_bytes_q)) begin
                exec_d.response_active = 1'b0;
                response_index_d = '0;
            end else begin
                response_index_d = response_index_q + 1'b1;
            end
        end

        unique case (exec_q.exec_state)
            DEBUG_EXEC_IDLE: begin
                op_counter_d = '0;
                halt_counter_d = '0;
                word_index_d = '0;
                exec_d.transaction_active = 1'b0;
                if (exec_q.busy_pending && !exec_q.response_active) begin
                    begin start_response = 1'b1; start_command = exec_q.busy_pending_command; start_sequence = exec_q.busy_pending_sequence; start_status = N2D_ST_BUSY; start_count = 8'h00; end
                    exec_d.busy_pending = 1'b0;
                end else if (rx_q.parser_error_valid && !exec_q.response_active) begin
                    begin start_response = 1'b1; start_command = rx_q.parser_error_command; start_sequence = rx_q.parser_error_sequence; start_status = rx_q.parser_error_status; start_count = 8'h00; end
                end else if (rx_q.packet_valid && !exec_q.response_active) begin
                    if (!rx_q.packet_checksum_ok) begin
                        begin start_response = 1'b1; start_command = rx_q.req_command; start_sequence = rx_q.req_sequence; start_status = N2D_ST_CHECKSUM; start_count = 8'h00; end
                    end else if (rx_q.req_version != N2D_DEBUG_VERSION) begin
                        begin start_response = 1'b1; start_command = rx_q.req_command; start_sequence = rx_q.req_sequence; start_status = N2D_ST_VERSION; start_count = 8'h00; end
                    end else if (!n2d_debug_command_supported(rx_q.req_command)) begin
                        begin start_response = 1'b1; start_command = rx_q.req_command; start_sequence = rx_q.req_sequence; start_status = N2D_ST_COMMAND; start_count = 8'h00; end
                    end else if ((rx_q.req_command != N2D_CMD_PEEK) &&
                                 (rx_q.req_command != N2D_CMD_POKE) &&
                                 (rx_q.req_command != N2D_CMD_LOAD) &&
                                 (rx_q.req_command != N2D_CMD_INPUT) &&
                                 ((rx_q.req_count != 0) ||
                                  (rx_q.req_byte_enable != 0))) begin
                        begin start_response = 1'b1; start_command = rx_q.req_command; start_sequence = rx_q.req_sequence; start_status = N2D_ST_LENGTH; start_count = 8'h00; end
                    end else if (rx_q.req_command == N2D_CMD_CAPS) begin
                        if (rx_q.req_address != 0) begin
                            begin start_response = 1'b1; start_command = rx_q.req_command; start_sequence = rx_q.req_sequence; start_status = N2D_ST_RANGE; start_count = 8'h00; end
                        end else begin
                            response_payload_d[7:0] = 8'h4e;
                            response_payload_d[15:8] = 8'h32;
                            response_payload_d[23:16] = 8'h42;
                            response_payload_d[31:24] = 8'h32;
                            response_payload_d[39:32] = 8'h00;
                            response_payload_d[47:40] = 8'h10;
                            response_payload_d[55:48] = 8'h00;
                            response_payload_d[63:56] = 8'h00;
                            response_payload_d[71:64] = 8'h03;
                            response_payload_d[79:72] = 8'h00;
                            response_payload_d[87:80] = 8'h00;
                            response_payload_d[95:88] = 8'h00;
                            begin start_response = 1'b1; start_command = rx_q.req_command; start_sequence = rx_q.req_sequence; start_status = N2D_ST_OK; start_count = 8'h03; end
                        end
                    end else if (rx_q.req_command == N2D_CMD_PING) begin
                        response_payload_d[7:0] = 8'h4e;
                        response_payload_d[15:8] = 8'h32;
                        response_payload_d[23:16] = 8'h44;
                        response_payload_d[31:24] = 8'h31;
                        response_payload_d[39:32] = {7'b0, debug_status_i.halt_ack};
                        response_payload_d[47:40] = 8'h00;
                        response_payload_d[55:48] = 8'h00;
                        response_payload_d[63:56] = 8'h00;
                        begin start_response = 1'b1; start_command = rx_q.req_command; start_sequence = rx_q.req_sequence; start_status = N2D_ST_OK; start_count = 8'h02; end
                    end else if (rx_q.req_command == N2D_CMD_INPUT) begin
                        if (rx_q.req_address[1:0] != 2'b00) begin
                            begin start_response = 1'b1; start_command = rx_q.req_command; start_sequence = rx_q.req_sequence; start_status = N2D_ST_ALIGNMENT; start_count = 8'h00; end
                        end else if (rx_q.req_address != N2D_DEBUG_INPUT_ADDRESS) begin
                            begin start_response = 1'b1; start_command = rx_q.req_command; start_sequence = rx_q.req_sequence; start_status = N2D_ST_RANGE; start_count = 8'h00; end
                        end else if ((rx_q.req_count != 8'h01) ||
                                     (rx_q.req_byte_enable != 4'hf) ||
                                     (req_payload_q[31:24] != 8'h00) ||
                                     (req_payload_q[23:16] != 8'h00) ||
                                     (req_payload_q[15:10] != 6'h00)) begin
                            begin start_response = 1'b1; start_command = rx_q.req_command; start_sequence = rx_q.req_sequence; start_status = N2D_ST_LENGTH; start_count = 8'h00; end
                        end else begin
                            exec_d.exec_state = DEBUG_EXEC_INPUT_WAIT;
                        end
                    end else if (rx_q.req_command == N2D_CMD_HALT) begin
                        exec_d.halt_req = 1'b1;
                        exec_d.exec_state = DEBUG_EXEC_HALT_WAIT;
                    end else if (rx_q.req_command == N2D_CMD_RESUME) begin
                        if (!debug_status_i.halt_ack || !debug_status_i.cache_maint_done) begin
                            begin start_response = 1'b1; start_command = rx_q.req_command; start_sequence = rx_q.req_sequence; start_status = N2D_ST_NOT_HALTED; start_count = 8'h00; end
                        end else begin
                            exec_d.cache_invalidate = 1'b1;
                            exec_d.halt_req = 1'b0;
                            exec_d.exec_state = DEBUG_EXEC_RESUME_WAIT;
                        end
                    end else if (rx_q.req_command == N2D_CMD_RESET_PC) begin
                        if (!debug_status_i.halt_ack || !debug_status_i.cache_maint_done) begin
                            begin start_response = 1'b1; start_command = rx_q.req_command; start_sequence = rx_q.req_sequence; start_status = N2D_ST_NOT_HALTED; start_count = 8'h00; end
                        end else if (rx_q.req_address[1:0] != 2'b00) begin
                            begin start_response = 1'b1; start_command = rx_q.req_command; start_sequence = rx_q.req_sequence; start_status = N2D_ST_ALIGNMENT; start_count = 8'h00; end
                        end else if (rx_q.req_address > 32'h03ff_fffc) begin
                            begin start_response = 1'b1; start_command = rx_q.req_command; start_sequence = rx_q.req_sequence; start_status = N2D_ST_RANGE; start_count = 8'h00; end
                        end else begin
                            exec_d.reset_pc = rx_q.req_address;
                            exec_d.reset_pc_valid = 1'b1;
                            begin start_response = 1'b1; start_command = rx_q.req_command; start_sequence = rx_q.req_sequence; start_status = N2D_ST_OK; start_count = 8'h00; end
                        end
                    end else if ((rx_q.req_count == 0) ||
                                 (rx_q.req_count > 8'(MAX_WORDS))) begin
                        begin start_response = 1'b1; start_command = rx_q.req_command; start_sequence = rx_q.req_sequence; start_status = N2D_ST_LENGTH; start_count = 8'h00; end
                    end else if (rx_q.req_address[1:0] != 2'b00) begin
                        begin start_response = 1'b1; start_command = rx_q.req_command; start_sequence = rx_q.req_sequence; start_status = N2D_ST_ALIGNMENT; start_count = 8'h00; end
                    end else if (!n2d_debug_address_range_valid(rx_q.req_address,
                                                     rx_q.req_count)) begin
                        begin start_response = 1'b1; start_command = rx_q.req_command; start_sequence = rx_q.req_sequence; start_status = N2D_ST_RANGE; start_count = 8'h00; end
                    end else if ((rx_q.req_command == N2D_CMD_LOAD) &&
                                 (rx_q.req_byte_enable != 4'hf)) begin
                        begin start_response = 1'b1; start_command = rx_q.req_command; start_sequence = rx_q.req_sequence; start_status = N2D_ST_LENGTH; start_count = 8'h00; end
                    end else if ((rx_q.req_command == N2D_CMD_POKE) &&
                                 (rx_q.req_byte_enable == 4'h0)) begin
                        begin start_response = 1'b1; start_command = rx_q.req_command; start_sequence = rx_q.req_sequence; start_status = N2D_ST_LENGTH; start_count = 8'h00; end
                    end else if (!debug_status_i.halt_ack || !debug_status_i.cache_maint_done) begin
                        begin start_response = 1'b1; start_command = rx_q.req_command; start_sequence = rx_q.req_sequence; start_status = N2D_ST_NOT_HALTED; start_count = 8'h00; end
                    end else begin
                        exec_d.exec_state = DEBUG_EXEC_ARB_WAIT;
                    end
                end
            end
            DEBUG_EXEC_HALT_WAIT: begin
                if (debug_status_i.halt_ack && debug_status_i.cache_maint_done) begin
                    begin start_response = 1'b1; start_command = rx_q.req_command; start_sequence = rx_q.req_sequence; start_status = N2D_ST_OK; start_count = 8'h00; end
                    exec_d.exec_state = DEBUG_EXEC_IDLE;
                    halt_counter_d = '0;
                end else if (halt_counter_q ==
                             HALT_COUNTER_WIDTH'(HALT_TIMEOUT_CYCLES - 1)) begin
                    begin start_response = 1'b1; start_command = rx_q.req_command; start_sequence = rx_q.req_sequence; start_status = N2D_ST_TIMEOUT; start_count = 8'h00; end
                    exec_d.exec_state = DEBUG_EXEC_IDLE;
                    halt_counter_d = '0;
                end else begin
                    halt_counter_d = halt_counter_q + 1'b1;
                end
            end
            DEBUG_EXEC_RESUME_WAIT: begin
                begin start_response = 1'b1; start_command = rx_q.req_command; start_sequence = rx_q.req_sequence; start_status = N2D_ST_OK; start_count = 8'h00; end
                exec_d.exec_state = DEBUG_EXEC_IDLE;
            end
            DEBUG_EXEC_ARB_WAIT: begin
                if (txn_status_i.arb_grant) begin
                    exec_d.txn_begin = 1'b1;
                    exec_d.transaction_active = 1'b1;
                    exec_d.exec_state = DEBUG_EXEC_MEM_REQ;
                    op_counter_d = '0;
                end else if (op_counter_q ==
                             OP_COUNTER_WIDTH'(OP_TIMEOUT_CYCLES - 1)) begin
                    begin start_response = 1'b1; start_command = rx_q.req_command; start_sequence = rx_q.req_sequence; start_status = N2D_ST_GRANT; start_count = 8'h00; end
                    exec_d.exec_state = DEBUG_EXEC_IDLE;
                    op_counter_d = '0;
                end else begin
                    op_counter_d = op_counter_q + 1'b1;
                end
            end
            DEBUG_EXEC_MEM_REQ: begin
                if (!txn_status_i.arb_grant) begin
                    exec_d.txn_abort = exec_q.transaction_active;
                    exec_d.abort_status = N2D_ST_GRANT;
                    exec_d.exec_state = DEBUG_EXEC_ABORT_WAIT;
                    op_counter_d = '0;
                end else if (mem_rsp_i.ready) begin
                    exec_d.exec_state = DEBUG_EXEC_MEM_RSP;
                    op_counter_d = '0;
                end else if (op_counter_q ==
                             OP_COUNTER_WIDTH'(OP_TIMEOUT_CYCLES - 1)) begin
                    exec_d.txn_abort = 1'b1;
                    exec_d.abort_status = N2D_ST_TIMEOUT;
                    exec_d.exec_state = DEBUG_EXEC_ABORT_WAIT;
                    op_counter_d = '0;
                end else begin
                    op_counter_d = op_counter_q + 1'b1;
                end
            end
            DEBUG_EXEC_MEM_RSP: begin
                if (!txn_status_i.arb_grant) begin
                    exec_d.txn_abort = exec_q.transaction_active;
                    exec_d.abort_status = N2D_ST_GRANT;
                    exec_d.exec_state = DEBUG_EXEC_ABORT_WAIT;
                    op_counter_d = '0;
                end else if (mem_rsp_i.rsp_valid) begin
                    op_counter_d = '0;
                    if (mem_rsp_i.rsp_error) begin
                        exec_d.txn_abort = 1'b1;
                        exec_d.abort_status = N2D_ST_MEMORY;
                        exec_d.exec_state = DEBUG_EXEC_ABORT_WAIT;
                    end else begin
                        if (rx_q.req_command == N2D_CMD_PEEK) begin
                            response_payload_d[(32'((word_index_q * 4)))*8 +: 8] = mem_rsp_i.rdata[7:0];
                            response_payload_d[(32'((word_index_q * 4) + 1))*8 +: 8] = mem_rsp_i.rdata[15:8];
                            response_payload_d[(32'((word_index_q * 4) + 2))*8 +: 8] = mem_rsp_i.rdata[23:16];
                            response_payload_d[(32'((word_index_q * 4) + 3))*8 +: 8] = mem_rsp_i.rdata[31:24];
                        end
                        if (word_index_q ==
                            WORD_INDEX_WIDTH'(rx_q.req_count - 1'b1)) begin
                            exec_d.txn_commit = 1'b1;
                            exec_d.exec_state = DEBUG_EXEC_COMMIT_WAIT;
                        end else begin
                            word_index_d = word_index_q + 1'b1;
                            exec_d.exec_state = DEBUG_EXEC_MEM_REQ;
                        end
                    end
                end else if (op_counter_q ==
                             OP_COUNTER_WIDTH'(OP_TIMEOUT_CYCLES - 1)) begin
                    exec_d.txn_abort = 1'b1;
                    exec_d.abort_status = N2D_ST_TIMEOUT;
                    exec_d.exec_state = DEBUG_EXEC_ABORT_WAIT;
                    op_counter_d = '0;
                end else begin
                    op_counter_d = op_counter_q + 1'b1;
                end
            end
            DEBUG_EXEC_COMMIT_WAIT: begin
                if (!txn_status_i.arb_grant) begin
                    exec_d.txn_abort = 1'b1;
                    exec_d.abort_status = N2D_ST_GRANT;
                    exec_d.exec_state = DEBUG_EXEC_ABORT_WAIT;
                    op_counter_d = '0;
                end else if (txn_status_i.txn_done) begin
                    exec_d.transaction_active = 1'b0;
                    begin start_response = 1'b1; start_command = rx_q.req_command; start_sequence = rx_q.req_sequence; start_status = txn_status_i.txn_error ? N2D_ST_MEMORY : N2D_ST_OK; start_count = (!txn_status_i.txn_error &&
                                    rx_q.req_command == N2D_CMD_PEEK) ?
                                       rx_q.req_count : 8'h00; end
                    exec_d.exec_state = DEBUG_EXEC_IDLE;
                    op_counter_d = '0;
                end else if (op_counter_q ==
                             OP_COUNTER_WIDTH'(OP_TIMEOUT_CYCLES - 1)) begin
                    exec_d.txn_abort = 1'b1;
                    exec_d.abort_status = N2D_ST_TIMEOUT;
                    exec_d.exec_state = DEBUG_EXEC_ABORT_WAIT;
                    op_counter_d = '0;
                end else begin
                    op_counter_d = op_counter_q + 1'b1;
                end
            end
            DEBUG_EXEC_ABORT_WAIT: begin
                if (txn_status_i.txn_done) begin
                    exec_d.transaction_active = 1'b0;
                    begin start_response = 1'b1; start_command = rx_q.req_command; start_sequence = rx_q.req_sequence; start_status = exec_q.abort_status; start_count = 8'h00; end
                    exec_d.exec_state = DEBUG_EXEC_IDLE;
                    op_counter_d = '0;
                end
            end
            DEBUG_EXEC_INPUT_WAIT: begin
                if (input_event_ready_i) begin
                    begin start_response = 1'b1; start_command = rx_q.req_command; start_sequence = rx_q.req_sequence; start_status = N2D_ST_OK; start_count = 8'h00; end
                    exec_d.exec_state = DEBUG_EXEC_IDLE;
                    op_counter_d = '0;
                end else if (op_counter_q ==
                             OP_COUNTER_WIDTH'(OP_TIMEOUT_CYCLES - 1)) begin
                    begin start_response = 1'b1; start_command = rx_q.req_command; start_sequence = rx_q.req_sequence; start_status = N2D_ST_BUSY; start_count = 8'h00; end
                    exec_d.exec_state = DEBUG_EXEC_IDLE;
                    op_counter_d = '0;
                end else begin
                    op_counter_d = op_counter_q + 1'b1;
                end
            end
            default: exec_d.exec_state = DEBUG_EXEC_IDLE;
        endcase

        // START_RESPONSE bundle: applied last so it wins over the
        // serializer's own index/CRC advance in the same clock.
        if (start_response) begin
            exec_d.response_active = 1'b1;
            response_index_d = '0;
            response_payload_bytes_d = (start_status == N2D_ST_OK) ?
                ((RX_INDEX_WIDTH+1)'(start_count) << 2) : '0;
            exec_d.response_command = start_command | 8'h80;
            exec_d.response_sequence = start_sequence;
            exec_d.response_status = start_status;
            exec_d.response_count = (start_status == N2D_ST_OK) ? start_count : 8'h00;
            exec_d.response_crc = 8'h00;
        end
    end

    `N2A_DFF_ARST_VAL(exec_q, exec_d, clk_i, reset_i, EXEC_RESET)
    `N2A_DFF_ARST_VAL(op_counter_q, op_counter_d, clk_i, reset_i, '0)
    `N2A_DFF_ARST_VAL(halt_counter_q, halt_counter_d, clk_i, reset_i, '0)
    `N2A_DFF_ARST_VAL(word_index_q, word_index_d, clk_i, reset_i, '0)
    `N2A_DFF_ARST_VAL(response_index_q, response_index_d, clk_i, reset_i, '0)
    `N2A_DFF_ARST_VAL(response_payload_bytes_q, response_payload_bytes_d, clk_i, reset_i, '0)
    `N2A_DFF_ARST_VAL(response_payload_q, response_payload_d, clk_i, reset_i, '0)

    // Checks of the asynchronous-reset executor register (non-reset
    // branch), sampled on its event control and guard path.
    `N2A_ASSERT_CLOCKED_ARST_FATAL(clk_i, reset_i, !(mem_txn_commit && mem_txn_abort),
        "debug memory transaction commit and abort overlap")
    `N2A_ASSERT_CLOCKED_ARST_FATAL_WHEN(clk_i, reset_i, mem_req_valid,
        debug_status_i.halt_ack && debug_status_i.cache_maint_done && txn_status_i.arb_grant,
        "debug memory request escaped halt/grant boundary")
    `N2A_ASSERT_CLOCKED_ARST_FATAL(clk_i, reset_i, !(exec_q.cache_invalidate && exec_q.halt_req),
        "resume invalidation overlaps asserted halt request")

    `N2A_ASSERT_PARAM(g_invalid_max_words, (MAX_WORDS != 0) && (MAX_WORDS <= 63),
        "n2d_uart_debug MAX_WORDS must be in 1..63")
    `N2A_ASSERT_PARAM(g_invalid_timeouts,
        (INTERBYTE_TIMEOUT_CYCLES >= (CLKS_PER_BIT * 10)) &&
        (OP_TIMEOUT_CYCLES >= 2) && (HALT_TIMEOUT_CYCLES >= 2),
        "n2d_uart_debug timeout parameters are too small")
endmodule
