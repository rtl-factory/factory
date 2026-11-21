`include "src/common/rtl/pkg/macros.vh"

// Bulk serial framing around the existing atomic eight-word engine.
// No external memory ownership is released by this module: the engine completes
// commit/rollback before responding. One byte RAM is reused for RX and readout.
module n2d_uart_bulk
    import n2d_pkg::*;
#(
    parameter int unsigned INTERBYTE_TIMEOUT_CYCLES = 1_000_000,
    parameter int unsigned FRAME_TIMEOUT_CYCLES = 50_000_000,
    parameter int unsigned MAX_WORDS = 8
) (
    input  logic clk_i,
    input  logic reset_i,
    input  logic [7:0] rx_byte_i,
    input  logic rx_valid_i,
    input  logic rx_error_i,
    output logic [7:0] tx_byte_o,
    output logic tx_valid_o,
    input  logic tx_ready_i,
    input  logic tx_busy_i,
    input  logic engine_idle_i,
    input  logic engine_frame_idle_i,
    output logic [7:0] engine_rx_byte_o,
    output logic engine_rx_valid_o,
    output logic engine_rx_error_o,
    input  logic [7:0] engine_tx_byte_i,
    input  logic engine_tx_valid_i,
    output logic engine_tx_ready_o,
    // Router idle: no bulk frame, execution, response or quarantine. The
    // stream router claims the link only while this is high.
    output logic idle_o
);
    import n2d_types_pkg::*;

    localparam logic [7:0] WRITE = 8'h31, READ = 8'h32;
    localparam int unsigned CHUNK_BYTES = 4 * ((MAX_WORDS < 8) ? MAX_WORDS : 8);
    logic legacy_drain;
    localparam t_bulk_regs REGS_RESET = '{
        state: BULK_IDLE,
        version: '0,
        command: '0,
        seq: '0,
        status: '0,
        address: '0,
        crc: '1,
        received_crc: '0,
        length: '0,
        received: '0,
        completed: '0,
        data_index: '0,
        header_index: '0,
        crc_index: '0,
        phase: '0,
        quarantine: 1'b0,
        busy_entry: 1'b0,
        chunk_bytes: '0,
        engine_index: '0,
        engine_crc: '0,
        engine_status: '0,
        engine_count: '0,
        engine_bad: 1'b0};
    t_bulk_regs regs_q, regs_d;
    // Why: the inter-byte gap and whole-frame timer widths follow the timeout
    // parameters, so they are registers of their own beside the packaged state.
    logic [$clog2(INTERBYTE_TIMEOUT_CYCLES)-1:0] gap_q, gap_d;
    logic [$clog2(FRAME_TIMEOUT_CYCLES)-1:0]     frame_cycles_q, frame_cycles_d;
    logic [31:0] chunk_address;
    logic [12:0] response_length;
    logic start_bulk, receiving;

    logic [11:0] ram_read_address, ram_write_address;
    logic [7:0] ram_read_q, ram_write_data;
    logic ram_write;

    assign idle_o = (regs_q.state == BULK_IDLE);
    assign receiving = (regs_q.state == BULK_RX_HEADER) || (regs_q.state == BULK_RX_BODY) ||
                       (regs_q.state == BULK_RX_CRC);
    assign start_bulk = (regs_q.state == BULK_IDLE) && engine_frame_idle_i &&
                        rx_valid_i && (rx_byte_i == 8'ha6);
    assign legacy_drain = regs_q.busy_entry &&
                          (receiving || (regs_q.state == BULK_CHECK) || (regs_q.state == BULK_WAIT_OLD));
    assign chunk_address = regs_q.address + {19'b0, regs_q.completed};
    assign response_length = ((regs_q.status == 0) && (regs_q.command == READ)) ?
                             regs_q.length[12:0] : 13'd0;

    `ASSERT_AFTER_RESET(bulk_prefix_advances_after_confirmation, clk_i, reset_i,
        ((regs_q.completed != $past(regs_q.completed)) && ($past(regs_q.state) != BULK_IDLE)) |->
        (($past(regs_q.state) == BULK_CHECK_ENGINE) && $past(engine_idle_i) &&
         !$past(regs_q.engine_bad) && ($past(regs_q.engine_status) == 0) &&
         (regs_q.completed == $past(regs_q.completed) + {7'b0, $past(regs_q.chunk_bytes)})))
    `ASSERT_AFTER_RESET(bulk_tx_stable, clk_i, reset_i,
        ((regs_q.state == BULK_TX_HEADER || regs_q.state == BULK_TX_DATA || regs_q.state == BULK_TX_CRC) &&
         tx_valid_o && !tx_ready_i) |=> (tx_valid_o && $stable(tx_byte_o)))

    // Why: no reset on the RAM or its read register and no same-address collision
    // data (reads and writes never need it), so the staging buffer infers one
    // synchronous-read block RAM.
    n2a_sync_ram #(.DATA_WIDTH(8), .DEPTH(4096)) u_staging (
        .clk_i,
        .write_enable_i(ram_write),
        .address_i(ram_write ? ram_write_address : ram_read_address),
        .write_data_i(ram_write_data),
        .read_data_o(ram_read_q)
    );

    always_comb begin
        ram_write = 1'b0;
        ram_write_address = regs_q.received[11:0];
        ram_write_data = rx_byte_i;
        ram_read_address = regs_q.data_index[11:0];
        if ((regs_q.state == BULK_RX_BODY) && rx_valid_i && !rx_error_i &&
            (regs_q.length <= 16'd4096)) ram_write = 1'b1;
        if (regs_q.state == BULK_SEND_ENGINE)
            ram_read_address = regs_q.completed[11:0] + 12'(regs_q.engine_index) - 12'd10;
        if ((regs_q.state == BULK_WAIT_ENGINE) && engine_tx_valid_i &&
            (regs_q.command == READ) && (regs_q.engine_status == 0) &&
            (regs_q.engine_index >= 7'd6) &&
            (regs_q.engine_index < (7'd6 + {1'b0, regs_q.chunk_bytes}))) begin
            ram_write = 1'b1;
            ram_write_address = regs_q.completed[11:0] + 12'(regs_q.engine_index) - 12'd6;
            ram_write_data = engine_tx_byte_i;
        end
    end

    always_comb begin
        engine_rx_byte_o = rx_byte_i;
        engine_rx_valid_o = (regs_q.state == BULK_IDLE) && rx_valid_i && !start_bulk;
        engine_rx_error_o = (regs_q.state == BULK_IDLE) && rx_error_i;
        engine_tx_ready_o = ((regs_q.state == BULK_IDLE) || legacy_drain) ? tx_ready_i :
                            (regs_q.state == BULK_WAIT_ENGINE);
        tx_byte_o = engine_tx_byte_i;
        tx_valid_o = ((regs_q.state == BULK_IDLE) || legacy_drain) && engine_tx_valid_i;
        if (regs_q.state == BULK_SEND_ENGINE) begin
            engine_rx_valid_o = (regs_q.phase == 2);
            case (regs_q.engine_index)
                0: engine_rx_byte_o = 8'ha5;
                1: engine_rx_byte_o = 8'h01;
                2: engine_rx_byte_o = (regs_q.command == WRITE) ? 8'h12 : 8'h10;
                3: engine_rx_byte_o = regs_q.seq;
                4: engine_rx_byte_o = chunk_address[7:0];
                5: engine_rx_byte_o = chunk_address[15:8];
                6: engine_rx_byte_o = chunk_address[23:16];
                7: engine_rx_byte_o = chunk_address[31:24];
                8: engine_rx_byte_o = {4'b0, regs_q.chunk_bytes[5:2]};
                9: engine_rx_byte_o = (regs_q.command == WRITE) ? 8'h0f : 8'h00;
                default: engine_rx_byte_o = ram_read_q;
            endcase
            if (regs_q.engine_index == (7'd10 +
                ((regs_q.command == WRITE) ? {1'b0, regs_q.chunk_bytes} : 7'd0)))
                engine_rx_byte_o = regs_q.engine_crc;
        end
        if (regs_q.state == BULK_TX_HEADER) begin
            tx_valid_o = 1'b1;
            case (regs_q.header_index)
                0: tx_byte_o = 8'h6a;
                1: tx_byte_o = 8'h02;
                2: tx_byte_o = regs_q.command | 8'h80;
                3: tx_byte_o = regs_q.seq;
                4: tx_byte_o = regs_q.status;
                5: tx_byte_o = regs_q.address[7:0];
                6: tx_byte_o = regs_q.address[15:8];
                7: tx_byte_o = regs_q.address[23:16];
                8: tx_byte_o = regs_q.address[31:24];
                9: tx_byte_o = response_length[7:0];
                10: tx_byte_o = {3'b0, response_length[12:8]};
                11: tx_byte_o = regs_q.completed[7:0];
                default: tx_byte_o = {3'b0, regs_q.completed[12:8]};
            endcase
        end else if (regs_q.state == BULK_TX_DATA) begin
            tx_valid_o = (regs_q.phase == 2);
            tx_byte_o = ram_read_q;
        end else if (regs_q.state == BULK_TX_CRC) begin
            tx_valid_o = 1'b1;
            tx_byte_o = ~regs_q.crc[8*regs_q.crc_index +: 8];
        end
    end

    always_comb begin
        regs_d = regs_q;
        frame_cycles_d = frame_cycles_q;
        gap_d = gap_q;
        // Why: interbyte silence applies only to the externally received frame, so an
        // in-flight memory rollback or an outgoing UART byte never trips the gap
        // timeout.
        if (receiving && !rx_valid_i) gap_d = gap_q + 1'b1;
        else gap_d = '0;
        if (receiving) frame_cycles_d = frame_cycles_q + 1'b1;
        else frame_cycles_d = '0;
        if ((regs_q.state != BULK_IDLE) && !receiving &&
            (rx_valid_i || rx_error_i)) regs_d.quarantine = 1'b1;
        if (receiving && (rx_error_i ||
            (frame_cycles_q == $bits(frame_cycles_q)'(FRAME_TIMEOUT_CYCLES-1)) ||
            (!rx_valid_i && gap_q == $bits(gap_q)'(INTERBYTE_TIMEOUT_CYCLES-1)))) begin
            regs_d.status = rx_error_i ? 8'h0a : 8'h08;
            regs_d.quarantine = 1'b1;
            regs_d.state = regs_q.busy_entry ? BULK_WAIT_OLD : BULK_TX_HEADER;
            regs_d.header_index = '0;
            regs_d.crc = '1;
        end else begin
            case (regs_q.state)
                BULK_IDLE: if (start_bulk) begin
                    regs_d.state = BULK_RX_HEADER;
                    regs_d.header_index = 4'd1;
                    regs_d.version = 0;
                    regs_d.command = 0;
                    regs_d.seq = 0;
                    regs_d.address = 0;
                    regs_d.length = 0;
                    regs_d.received = 0;
                    regs_d.completed = 0;
                    regs_d.status = 0;
                    regs_d.crc = '1;
                    regs_d.received_crc = 0;
                    regs_d.crc_index = 0;
                    regs_d.quarantine = 0;
                    regs_d.busy_entry = !engine_idle_i || tx_busy_i;
                end
                BULK_RX_HEADER: if (rx_valid_i) begin
                    regs_d.crc = n2d_crc32_byte(regs_q.crc, rx_byte_i);
                    regs_d.header_index = regs_q.header_index + 1'b1;
                    case (regs_q.header_index)
                        1: regs_d.version = rx_byte_i;
                        2: regs_d.command = rx_byte_i;
                        3: regs_d.seq = rx_byte_i;
                        4: regs_d.address[7:0] = rx_byte_i;
                        5: regs_d.address[15:8] = rx_byte_i;
                        6: regs_d.address[23:16] = rx_byte_i;
                        7: regs_d.address[31:24] = rx_byte_i;
                        8: regs_d.length[7:0] = rx_byte_i;
                        default: begin
                            regs_d.length[15:8] = rx_byte_i;
                            regs_d.state = ((regs_q.command == WRITE) &&
                                ({rx_byte_i, regs_q.length[7:0]} != 0)) ? BULK_RX_BODY : BULK_RX_CRC;
                        end
                    endcase
                end
                BULK_RX_BODY: if (rx_valid_i) begin
                    regs_d.crc = n2d_crc32_byte(regs_q.crc, rx_byte_i);
                    regs_d.received = regs_q.received + 1'b1;
                    if (regs_q.received == regs_q.length - 1'b1) regs_d.state = BULK_RX_CRC;
                end
                BULK_RX_CRC: if (rx_valid_i) begin
                    regs_d.received_crc[8*regs_q.crc_index +: 8] = rx_byte_i;
                    regs_d.crc_index = regs_q.crc_index + 1'b1;
                    if (regs_q.crc_index == 3) regs_d.state = BULK_CHECK;
                end
                BULK_CHECK: begin
                    regs_d.state = regs_q.busy_entry ? BULK_WAIT_OLD : BULK_TX_HEADER;
                    regs_d.header_index = 0;
                    regs_d.crc = '1;
                    if (regs_q.received_crc != ~regs_q.crc) regs_d.status = 8'h03;
                    else if (regs_q.version != 2) regs_d.status = 8'h01;
                    else if ((regs_q.command != WRITE) && (regs_q.command != READ)) regs_d.status = 8'h02;
                    else if ((regs_q.length == 0) || (regs_q.length > 4096) ||
                                 (regs_q.length[1:0] != 0)) regs_d.status = 8'h06;
                    else if (regs_q.address[1:0] != 0) regs_d.status = 8'h04;
                    else if (!n2d_bulk_range_valid(regs_q.address, regs_q.length, regs_q.command == READ)) regs_d.status = 8'h05;
                    else if (regs_q.busy_entry) regs_d.status = 8'h0c;
                    else regs_d.state = BULK_PREPARE;
                end
                BULK_PREPARE: if (engine_idle_i) begin
                    regs_d.chunk_bytes = ((regs_q.length - {3'b0, regs_q.completed}) >= 16'(CHUNK_BYTES)) ?
                                     6'(CHUNK_BYTES) : 6'(regs_q.length - {3'b0, regs_q.completed});
                    regs_d.engine_index = 0;
                    regs_d.engine_crc = 0;
                    regs_d.engine_status = 0;
                    regs_d.engine_count = 0;
                    regs_d.engine_bad = 0;
                    regs_d.phase = 0;
                    regs_d.state = BULK_SEND_ENGINE;
                end
                BULK_SEND_ENGINE: begin
                    if (regs_q.phase != 2) regs_d.phase = regs_q.phase + 1'b1;
                    else begin
                        regs_d.phase = 0;
                        regs_d.engine_index = regs_q.engine_index + 1'b1;
                        if (regs_q.engine_index != 0)
                            regs_d.engine_crc = n2d_crc8_byte(regs_q.engine_crc, engine_rx_byte_o);
                        if (regs_q.engine_index == (7'd10 +
                            ((regs_q.command == WRITE) ? {1'b0, regs_q.chunk_bytes} : 7'd0))) begin
                            regs_d.state = BULK_WAIT_ENGINE;
                            regs_d.engine_index = 0;
                            regs_d.engine_crc = 0;
                        end
                    end
                end
                BULK_WAIT_ENGINE: if (engine_tx_valid_i) begin
                    regs_d.engine_index = regs_q.engine_index + 1'b1;
                    if (regs_q.engine_index != 0)
                        regs_d.engine_crc = n2d_crc8_byte(regs_q.engine_crc, engine_tx_byte_i);
                    case (regs_q.engine_index)
                        0: if (engine_tx_byte_i != 8'h5a) regs_d.engine_bad = 1'b1;
                        1: if (engine_tx_byte_i != 1) regs_d.engine_bad = 1'b1;
                        2: if (engine_tx_byte_i != ((regs_q.command == WRITE) ? 8'h92 : 8'h90)) regs_d.engine_bad = 1'b1;
                        3: if (engine_tx_byte_i != regs_q.seq) regs_d.engine_bad = 1'b1;
                        4: regs_d.engine_status = engine_tx_byte_i;
                        5: regs_d.engine_count = engine_tx_byte_i;
                        default: begin end
                    endcase
                    if (regs_q.engine_index == (7'd6 +
                        (((regs_q.command == READ) && (regs_q.engine_status == 0)) ?
                         {1'b0, regs_q.chunk_bytes} : 7'd0))) begin
                        if (engine_tx_byte_i != regs_q.engine_crc) regs_d.engine_bad = 1'b1;
                        regs_d.state = BULK_CHECK_ENGINE;
                    end
                end
                BULK_CHECK_ENGINE: if (engine_idle_i) begin
                    if (regs_q.engine_bad || regs_q.engine_count !=
                        (((regs_q.command == READ) && (regs_q.engine_status == 0)) ?
                         {4'b0, regs_q.chunk_bytes[5:2]} : 8'd0)) begin
                        regs_d.status = 8'h0d;
                        regs_d.state = BULK_TX_HEADER;
                    end else if (regs_q.engine_status != 0) begin
                        regs_d.status = regs_q.engine_status;
                        regs_d.state = BULK_TX_HEADER;
                    end else begin
                        regs_d.completed = regs_q.completed + {7'b0, regs_q.chunk_bytes};
                        regs_d.state = ((regs_q.completed + {7'b0, regs_q.chunk_bytes}) == regs_q.length[12:0]) ?
                                   BULK_TX_HEADER : BULK_PREPARE;
                    end
                end
                BULK_TX_HEADER: if (tx_ready_i) begin
                    if (regs_q.status != 0) regs_d.quarantine = 1'b1;
                    if (regs_q.header_index != 0) regs_d.crc = n2d_crc32_byte(regs_q.crc, tx_byte_o);
                    regs_d.header_index = regs_q.header_index + 1'b1;
                    if (regs_q.header_index == 12) begin
                        regs_d.state = (response_length != 0) ? BULK_TX_DATA : BULK_TX_CRC;
                        regs_d.data_index = 0;
                        regs_d.phase = 0;
                        regs_d.crc_index = 0;
                    end
                end
                BULK_TX_DATA: begin
                    if (regs_q.phase != 2) regs_d.phase = regs_q.phase + 1'b1;
                    else if (tx_ready_i) begin
                        regs_d.crc = n2d_crc32_byte(regs_q.crc, tx_byte_o);
                        regs_d.data_index = regs_q.data_index + 1'b1;
                        regs_d.phase = 0;
                        if (regs_q.data_index == response_length - 1'b1) regs_d.state = BULK_TX_CRC;
                    end
                end
                BULK_TX_CRC: if (tx_ready_i) begin
                    regs_d.crc_index = regs_q.crc_index + 1'b1;
                    if (regs_q.crc_index == 3) regs_d.state = BULK_DRAIN;
                end
                BULK_DRAIN: if (!tx_busy_i)
                    regs_d.state = (regs_q.quarantine || rx_valid_i || rx_error_i) ? BULK_QUARANTINE : BULK_IDLE;
                BULK_WAIT_OLD: if (engine_idle_i && !tx_busy_i) regs_d.state = BULK_TX_HEADER;
                BULK_QUARANTINE: begin end
                default: regs_d.state = BULK_QUARANTINE;
            endcase
        end
    end

    `DFF_ARST_VAL(regs_q, regs_d, clk_i, reset_i, REGS_RESET)
    `DFF_ARST_VAL(gap_q, gap_d, clk_i, reset_i, '0)
    `DFF_ARST_VAL(frame_cycles_q, frame_cycles_d, clk_i, reset_i, '0)

    `ASSERT_CLOCKED_ARST_FATAL_WHEN(clk_i, reset_i, regs_q.state != BULK_IDLE,
        ({3'b0, regs_q.completed} <= regs_q.length && regs_q.completed[1:0] == 0),
        "bulk completed prefix escaped request")
    `ASSERT_CLOCKED_ARST_FATAL_WHEN(clk_i, reset_i, regs_q.state != BULK_IDLE,
        !(engine_rx_valid_o && regs_q.state != BULK_SEND_ENGINE),
        "bulk residue reached legacy engine")

endmodule
