`default_nettype none
`include "src/common/rtl/pkg/macros.vh"

// UART cartridge loader: the host side of the nand2mario cartridge slot. It
// receives checksummed frames as bytes from the UART link, holds and
// releases the console core's reset, writes image bytes into the cartridge
// backing through the load port and reads them back, pins and reads back
// the displayed frame through the display's snapshot port, and answers every
// frame with one checksummed response (wiki/src/nand2mario/scripts/board/spec.md#loader-protocol).
// A WRITE payload is buffered and committed only after its CRC verified, so
// a corrupted frame changes nothing. Frames are stop-and-wait: bytes that
// arrive while a frame is executed or answered are dropped and flagged, a
// framing error or a receive timeout aborts the frame in progress and is
// flagged in the next response. The loader lives in the system reset domain
// so it keeps working while it holds the core in reset. The PHYs and the
// common UART memory endpoint in front of it are in n2m_uart_link.
module n2m_uart_loader #(
    // Idle clocks inside a frame after which the partial frame is dropped.
    parameter int unsigned RX_TIMEOUT_CLOCKS = 65536
) (
    input  logic                                   clk_i,
    input  logic                                   reset_i,
    // Byte stream from and to the link (receive pulses, transmit handshake).
    input  logic [7:0]                             rx_byte_i,
    input  logic                                   rx_valid_i,
    input  logic                                   rx_error_i,
    output logic [7:0]                             tx_byte_o,
    output logic                                   tx_valid_o,
    input  logic                                   tx_ready_i,
    // Between frames: no frame received, executed or answered.
    output logic                                   idle_o,
    // Core reset request: high from an accepted HOLD to an accepted RELEASE.
    output logic                                   hold_o,
    // Reported in every status byte.
    input  logic                                   backing_initialized_i,
    // Load port of the cartridge backing (one outstanding request).
    output n2m_cartridge_pkg::t_backing_request    load_request_o,
    input  logic                                   load_ready_i,
    input  n2m_cartridge_pkg::t_backing_response   load_response_i,
    // High while a load-port request is presented or awaits its response.
    output logic                                   load_busy_o,
    // Display frame snapshot: pin/release requests, the pin and display-valid
    // levels, and the snapshot byte port (one outstanding request).
    output logic                                   snapshot_pin_o,
    output logic                                   snapshot_release_o,
    input  logic                                   snapshot_pinned_i,
    input  logic                                   snapshot_available_i,
    output n2m_snapshot_pkg::t_snapshot_request     snapshot_request_o,
    input  logic                                   snapshot_ready_i,
    input  n2m_snapshot_pkg::t_snapshot_response    snapshot_response_i
);
    import n2m_cartridge_pkg::*;
    import n2m_loader_pkg::*;

    localparam int unsigned TIMEOUT_WIDTH = $clog2(RX_TIMEOUT_CLOCKS + 1);
    localparam int unsigned RESPONSE_HEADER_BYTES = 7;

    logic        rx_valid, rx_framing;
    logic [7:0]  rx_byte;
    logic        tx_ready;

    t_loader_state state_q, state_d;
    logic [7:0]  cmd_q, cmd_d, len_q, len_d, crc_lo_q, crc_lo_d;
    logic [23:0] addr_q, addr_d;
    logic [15:0] crc_q, crc_d, resp_crc_q, resp_crc_d;
    logic        crc_match_q, crc_match_d;
    logic [8:0]  index_q, index_d;
    logic [2:0]  resp_index_q, resp_index_d;
    logic [7:0]  status_q, status_d, read_byte_q, read_byte_d;
    logic        hold_q, hold_d, framing_q, framing_d, timeout_q, timeout_d, overrun_q, overrun_d;
    logic        tx_pending_q, tx_pending_d;
    logic [7:0]  tx_byte_q, tx_byte_d;
    logic [TIMEOUT_WIDTH-1:0] timeout_count_q, timeout_count_d;

    // Why: the payload is buffered so nothing is committed before the whole
    // frame, CRC included, is in.
    logic        buffer_write, buffer_read, buffer_valid;
    logic [7:0]  buffer_address, buffer_data;
    logic        receiving, executing, timed_out;
    logic [2:0]  status_code;
    logic [7:0]  header_byte;
    logic [20:0] byte_address;
    logic        load_accept, tx_accept;
    logic        snap_read, snap_accept, snap_lost;
    logic [12:0] snap_offset;

    assign rx_byte = rx_byte_i;
    assign rx_valid = rx_valid_i;
    assign rx_framing = rx_error_i;
    assign tx_ready = tx_ready_i;
    assign tx_byte_o = tx_byte_q;
    assign tx_valid_o = tx_pending_q;
    assign idle_o = (state_q == IDLE) && !tx_pending_q;

    assign buffer_address = index_q[7:0];
    assign buffer_write = (state_q == PAYLOAD) && rx_valid;
    assign buffer_read = state_q == COMMIT_READ;
    n2a_sync_ram_enabled #(
        .DATA_WIDTH (8),
        .DEPTH      (MAX_PAYLOAD_BYTES)
    ) u_buffer (
        .clk_i          (clk_i),
        .reset_i        (reset_i),
        .read_enable_i  (buffer_read),
        .write_enable_i (buffer_write),
        .address_i      (buffer_address),
        .write_data_i   (rx_byte),
        .read_data_o    (buffer_data),
        .read_valid_o   (buffer_valid)
    );

    assign receiving = (state_q == CMD) || (state_q == ADDR0) || (state_q == ADDR1) ||
                       (state_q == ADDR2) || (state_q == LEN) || (state_q == PAYLOAD) ||
                       (state_q == CRC_LO) || (state_q == CRC_HI);
    assign executing = !(state_q == IDLE) && !receiving;
    assign timed_out = receiving && (timeout_count_q == TIMEOUT_WIDTH'(RX_TIMEOUT_CLOCKS));
    assign hold_o = hold_q;
    assign snap_read = cmd_q == CMD_SNAP_READ;
    assign load_busy_o = (state_q == COMMIT_REQUEST) || (state_q == COMMIT_WAIT) ||
                         (((state_q == RESPOND_READ_REQUEST) || (state_q == RESPOND_READ_WAIT)) && !snap_read);
    assign load_accept = load_request_o.valid && load_ready_i;
    // Why: a pin that vanished under a request (the display reset with the
    // core) will never be answered, so the byte is taken as zero instead.
    assign snap_offset = addr_q[12:0] + 13'(index_q);
    assign snapshot_request_o.valid = (state_q == RESPOND_READ_REQUEST) && snap_read && snapshot_pinned_i;
    assign snapshot_request_o.offset = snap_offset;
    assign snap_accept = snapshot_request_o.valid && snapshot_ready_i;
    assign snap_lost = snap_read && !snapshot_pinned_i;
    assign snapshot_pin_o = (state_q == DECIDE) && (status_code == STATUS_OK) && (cmd_q == CMD_SNAP_PIN);
    assign snapshot_release_o = (state_q == DECIDE) && (status_code == STATUS_OK) && (cmd_q == CMD_SNAP_RELEASE);
    assign tx_accept = tx_pending_q && tx_ready;

    // Why: the status is decided once the whole frame is in so a bad CRC is
    // reported ahead of anything else, then the command, its arguments, the hold
    // requirement, the pin, the address range and finally whether a frame is
    // there to pin.
    always_comb begin
        status_code = STATUS_OK;
        if (!crc_match_q) status_code = STATUS_BAD_CRC;
        else if (!is_known_command(cmd_q)) status_code = STATUS_BAD_COMMAND;
        else if (!needs_address(cmd_q) && ((addr_q != 24'd0) || (len_q != 8'd0))) status_code = STATUS_BAD_ARGUMENT;
        else if (needs_hold(cmd_q) && !hold_q) status_code = STATUS_NOT_HELD;
        else if (snap_read && !snapshot_pinned_i) status_code = STATUS_NOT_PINNED;
        else if (needs_hold(cmd_q) && !address_valid(addr_q, len_q)) status_code = STATUS_BAD_ADDRESS;
        else if (snap_read && !n2m_snapshot_pkg::snapshot_range_valid(addr_q, len_q))
            status_code = STATUS_BAD_ADDRESS;
        else if ((cmd_q == CMD_SNAP_PIN) && !snapshot_pinned_i && !snapshot_available_i) status_code = STATUS_NO_FRAME;
    end

    always_comb begin
        case (resp_index_q)
            3'd0: header_byte = SYNC_BYTE;
            3'd1: header_byte = status_q;
            3'd2: header_byte = cmd_q;
            3'd3: header_byte = addr_q[7:0];
            3'd4: header_byte = addr_q[15:8];
            3'd5: header_byte = addr_q[23:16];
            default: header_byte = len_q;
        endcase
    end

    assign byte_address = addr_q[20:0] + 21'(index_q);
    always_comb begin
        load_request_o.valid = (state_q == COMMIT_REQUEST) || ((state_q == RESPOND_READ_REQUEST) && !snap_read);
        load_request_o.write = state_q == COMMIT_REQUEST;
        load_request_o.ram = addr_q[ADDRESS_RAM_BIT];
        load_request_o.address = byte_address;
        load_request_o.write_data = buffer_data;
    end

    always_comb begin
        state_d = state_q;
        cmd_d = cmd_q;
        addr_d = addr_q;
        len_d = len_q;
        crc_lo_d = crc_lo_q;
        crc_d = crc_q;
        crc_match_d = crc_match_q;
        index_d = index_q;
        resp_index_d = resp_index_q;
        resp_crc_d = resp_crc_q;
        status_d = status_q;
        read_byte_d = read_byte_q;
        hold_d = hold_q;
        framing_d = framing_q || rx_framing;
        timeout_d = timeout_q;
        overrun_d = overrun_q || (executing && rx_valid);
        tx_pending_d = tx_pending_q && !tx_accept;
        tx_byte_d = tx_byte_q;
        timeout_count_d = (receiving && !rx_valid) ? timeout_count_q + 1'b1 : '0;

        case (state_q)
            IDLE: begin
                if (rx_valid && (rx_byte == SYNC_BYTE)) begin
                    state_d = CMD;
                    crc_d = CRC_INIT;
                end
            end
            CMD: if (rx_valid) begin
                cmd_d = rx_byte;
                crc_d = crc16_byte(crc_q, rx_byte);
                state_d = ADDR0;
            end
            ADDR0: if (rx_valid) begin
                addr_d[7:0] = rx_byte;
                crc_d = crc16_byte(crc_q, rx_byte);
                state_d = ADDR1;
            end
            ADDR1: if (rx_valid) begin
                addr_d[15:8] = rx_byte;
                crc_d = crc16_byte(crc_q, rx_byte);
                state_d = ADDR2;
            end
            ADDR2: if (rx_valid) begin
                addr_d[23:16] = rx_byte;
                crc_d = crc16_byte(crc_q, rx_byte);
                state_d = LEN;
            end
            LEN: if (rx_valid) begin
                len_d = rx_byte;
                crc_d = crc16_byte(crc_q, rx_byte);
                index_d = '0;
                state_d = has_payload(cmd_q) ? PAYLOAD : CRC_LO;
            end
            PAYLOAD: if (rx_valid) begin
                crc_d = crc16_byte(crc_q, rx_byte);
                index_d = index_q + 9'd1;
                if (index_q[7:0] == len_q) state_d = CRC_LO;
            end
            CRC_LO: if (rx_valid) begin
                crc_lo_d = rx_byte;
                state_d = CRC_HI;
            end
            CRC_HI: if (rx_valid) begin
                crc_match_d = {rx_byte, crc_lo_q} == crc_q;
                state_d = DECIDE;
            end
            DECIDE: begin
                // Why: the flags clear when latched into the status byte, so each event is
                // reported in exactly one response.
                if ((status_code == STATUS_OK) && (cmd_q == CMD_HOLD)) hold_d = 1'b1;
                if ((status_code == STATUS_OK) && (cmd_q == CMD_RELEASE)) hold_d = 1'b0;
                status_d = {hold_d, backing_initialized_i, framing_q, timeout_q, overrun_q, status_code};
                framing_d = rx_framing;
                timeout_d = 1'b0;
                overrun_d = rx_valid;
                index_d = '0;
                resp_index_d = '0;
                resp_crc_d = CRC_INIT;
                state_d = ((status_code == STATUS_OK) && (cmd_q == CMD_WRITE)) ? COMMIT_READ : RESPOND_HEADER;
            end
            COMMIT_READ: state_d = COMMIT_REQUEST;
            COMMIT_REQUEST: if (load_accept) state_d = COMMIT_WAIT;
            COMMIT_WAIT: if (load_response_i.valid) begin
                if (index_q[7:0] == len_q) state_d = RESPOND_HEADER;
                else begin
                    index_d = index_q + 9'd1;
                    state_d = COMMIT_READ;
                end
            end
            RESPOND_HEADER: if (!tx_pending_q) begin
                tx_byte_d = header_byte;
                tx_pending_d = 1'b1;
                if (resp_index_q != 3'd0) resp_crc_d = crc16_byte(resp_crc_q, header_byte);
                resp_index_d = resp_index_q + 3'd1;
                if (resp_index_q == 3'(RESPONSE_HEADER_BYTES - 1)) begin
                    index_d = '0;
                    state_d = ((status_q[2:0] == STATUS_OK) && reads_payload(cmd_q)) ?
                              RESPOND_READ_REQUEST : RESPOND_CRC_LO;
                end
            end
            RESPOND_READ_REQUEST: begin
                if (snap_lost) begin
                    read_byte_d = 8'h00;
                    state_d = RESPOND_READ_BYTE;
                end else if (load_accept || snap_accept) begin
                    state_d = RESPOND_READ_WAIT;
                end
            end
            RESPOND_READ_WAIT: begin
                if (snap_read && snapshot_response_i.valid) begin
                    read_byte_d = snapshot_response_i.data;
                    state_d = RESPOND_READ_BYTE;
                end else if (snap_lost) begin
                    read_byte_d = 8'h00;
                    state_d = RESPOND_READ_BYTE;
                end else if (!snap_read && load_response_i.valid) begin
                    read_byte_d = load_response_i.read_data;
                    state_d = RESPOND_READ_BYTE;
                end
            end
            RESPOND_READ_BYTE: if (!tx_pending_q) begin
                tx_byte_d = read_byte_q;
                tx_pending_d = 1'b1;
                resp_crc_d = crc16_byte(resp_crc_q, read_byte_q);
                if (index_q[7:0] == len_q) state_d = RESPOND_CRC_LO;
                else begin
                    index_d = index_q + 9'd1;
                    state_d = RESPOND_READ_REQUEST;
                end
            end
            RESPOND_CRC_LO: if (!tx_pending_q) begin
                tx_byte_d = resp_crc_q[7:0];
                tx_pending_d = 1'b1;
                state_d = RESPOND_CRC_HI;
            end
            RESPOND_CRC_HI: if (!tx_pending_q) begin
                tx_byte_d = resp_crc_q[15:8];
                tx_pending_d = 1'b1;
                state_d = IDLE;
            end
            default: state_d = IDLE;
        endcase

        // Why: a framing error or a receive timeout drops the frame in progress so
        // the parser resynchronizes instead of waiting forever.
        if (receiving && (rx_framing || timed_out)) begin
            state_d = IDLE;
            timeout_d = timeout_q || timed_out;
        end
    end

    `DFF_RST_VAL(state_q, state_d, clk_i, reset_i, IDLE)
    `DFF_RST(cmd_q, cmd_d, clk_i, reset_i)
    `DFF_RST(addr_q, addr_d, clk_i, reset_i)
    `DFF_RST(len_q, len_d, clk_i, reset_i)
    `DFF_RST(crc_lo_q, crc_lo_d, clk_i, reset_i)
    `DFF_RST(crc_q, crc_d, clk_i, reset_i)
    `DFF_RST(crc_match_q, crc_match_d, clk_i, reset_i)
    `DFF_RST(index_q, index_d, clk_i, reset_i)
    `DFF_RST(resp_index_q, resp_index_d, clk_i, reset_i)
    `DFF_RST(resp_crc_q, resp_crc_d, clk_i, reset_i)
    `DFF_RST(status_q, status_d, clk_i, reset_i)
    `DFF_RST(read_byte_q, read_byte_d, clk_i, reset_i)
    `DFF_RST(hold_q, hold_d, clk_i, reset_i)
    `DFF_RST(framing_q, framing_d, clk_i, reset_i)
    `DFF_RST(timeout_q, timeout_d, clk_i, reset_i)
    `DFF_RST(overrun_q, overrun_d, clk_i, reset_i)
    `DFF_RST(tx_pending_q, tx_pending_d, clk_i, reset_i)
    `DFF_RST(tx_byte_q, tx_byte_d, clk_i, reset_i)
    `DFF_RST(timeout_count_q, timeout_count_d, clk_i, reset_i)

    `ASSERT(load_response_requested, clk_i, reset_i,
        !load_response_i.valid || (state_q == COMMIT_WAIT) || (state_q == RESPOND_READ_WAIT))
    `ASSERT(snapshot_response_requested, clk_i, reset_i,
        !snapshot_response_i.valid || ((state_q == RESPOND_READ_WAIT) && snap_read))
    `ASSERT(commit_data_valid, clk_i, reset_i,
        !$rose(state_q == COMMIT_REQUEST) || buffer_valid)
    `ASSERT(load_response_known, clk_i, reset_i,
        !load_response_i.valid || !$isunknown(load_response_i))
    `ASSERT_KNOWN(load_ready_known, clk_i, reset_i, load_ready_i)
    `ASSERT_KNOWN(snapshot_levels_known, clk_i, reset_i,
        {snapshot_pinned_i, snapshot_available_i, snapshot_ready_i, snapshot_response_i.valid})
    `ASSERT(snapshot_response_known, clk_i, reset_i,
        !snapshot_response_i.valid || !$isunknown(snapshot_response_i))
    `ASSERT_KNOWN(byte_port_known, clk_i, reset_i, {rx_valid_i, rx_error_i, tx_ready_i})
endmodule
