`include "src/common/rtl/pkg/macros.vh"

// One held command/response mailbox between a host on Virtual JTAG and the
// consumer's PS/2 byte stream (https://github.com/amichai-bd/nand2anything/blob/aeebe92/wiki/src/common/jtag/HAS.md). The endpoint
// holds no byte FIFO: the consumer accepts each offered byte or refuses it.
module n2a_jtag_keyboard (
    input logic clk_i, reset_i, tck_i,
    input logic [1:0] ir_i,
    input logic capture_i, shift_i, update_i, update_ir_i, tdi_i,
    output logic tdo_o,
    output logic inject_valid_o,
    output logic [7:0] inject_byte_o,
    input logic inject_ready_i
);
    import n2a_types_pkg::*;

    localparam logic [63:0] RESET_RESPONSE = 64'h5a00_0000_0000_0000;
    // Why: reset_i is synchronous to clk_i and resets the system domain directly.
    // The scan domain's synchronizer takes it from a flop, so reset_i is never an
    // asynchronous reset itself and the crossing starts glitch-free.
    logic scan_reset_request_q, scan_reset;
    `DFF(scan_reset_request_q, reset_i, clk_i)
    n2a_reset_sync u_scan_reset (.clk_i(tck_i), .async_reset_i(scan_reset_request_q),
        .reset_o(scan_reset));

    t_jtag_keyboard_scan scan_q, scan_d;
    localparam t_jtag_keyboard_scan SCAN_RESET = '{response: RESET_RESPONSE, default: '0};
    logic ack_sync, req_sync, ack_q;
    logic [63:0] response_q;
    n2a_cdc_bit_sync u_req (.clk_i(clk_i), .reset_i(reset_i),
        .async_i(scan_q.request), .sync_o(req_sync));
    // Why: u_ack has no reset, so scan_reset is only ever an asynchronous
    // reset. Its value is never read stale: scan_q reads ack_sync only while
    // busy, and busy needs a 64-bit scan first, long after the chain fills.
    n2a_cdc_bit_sync u_ack (.clk_i(tck_i), .reset_i(1'b0),
        .async_i(ack_q), .sync_o(ack_sync));

    always_comb begin
        scan_d = scan_q;
        if (scan_q.busy && ack_sync == scan_q.request) begin
            // Why: response_q crosses from clk_i without a synchronizer. It is safe
            // because clk_i sets it with ack and holds it until the next request.
            scan_d.response = response_q;
            scan_d.busy = 1'b0;
        end
        if (update_ir_i || ir_i != scan_q.instruction) begin
            scan_d.instruction = ir_i;
            scan_d.scan_open = 1'b0;
            scan_d.bits_shifted = '0;
        end else if (capture_i) begin
            scan_d.shift_data = scan_q.response;
            scan_d.shift_data[55] = scan_q.busy;
            scan_d.shift_data[54] = scan_q.error_seen;
            scan_d.bits_shifted = '0;
            scan_d.scan_open = ir_i == 2'd1 || ir_i == 2'd2;
            scan_d.bypass = 1'b0;
        end else if (shift_i) begin
            scan_d.bypass = tdi_i;
            if (scan_q.scan_open) begin
                scan_d.shift_data = {tdi_i, scan_q.shift_data[63:1]};
                if (scan_q.bits_shifted < 7'd65)
                    scan_d.bits_shifted = scan_q.bits_shifted + 1'b1;
            end
        end else if (update_i) begin
            scan_d.scan_open = 1'b0;
            if (ir_i == 2'd1) begin
                // Why: the Intel hub delivers TDI to the node late and pads the scan with
                // extra Shift-DR cycles (seven on the DE2-115), so the command is the
                // last 64 bits shifted in and a scan may exceed 64 Shift-DR cycles.
                if (!scan_q.scan_open || scan_q.bits_shifted < 7'd64 || scan_q.busy) begin
                    scan_d.error_seen = 1'b1;
                end else if (!scan_q.error_seen) begin
                    scan_d.command = scan_q.shift_data;
                    scan_d.request = !scan_q.request;
                    scan_d.busy = 1'b1;
                end
            end
        end
    end
    // Why: TCK runs only during a scan, so scan_q must reset while TCK is stopped.
    // A stale request toggle would otherwise reach the system domain after its
    // reset and replay an old command.
    `DFF_ARST_VAL(scan_q, scan_d, tck_i, scan_reset, SCAN_RESET)
    assign tdo_o = (ir_i == 2'd1 || ir_i == 2'd2) ? scan_q.shift_data[0] : scan_q.bypass;

    t_jtag_keyboard_system q, d;
    localparam t_jtag_keyboard_system SYSTEM_RESET = '{phase: KBD_IDLE, response: RESET_RESPONSE, default: '0};
    logic [63:0] completion;
    logic [7:0] command_seq;
    assign command_seq = q.command[47:40];
    assign completion = {8'h5a, 2'b00, q.code, q.accepted, 8'h00, command_seq, q.total};
    assign ack_q = q.ack;
    assign response_q = q.response;
    // Why: byte and valid leave from registers and inject_ready_i is registered
    // into take, so only the consumer's FIFO write edge counts as acceptance,
    // never a byte merely staged.
    assign inject_valid_o = q.valid && !reset_i;
    assign inject_byte_o = q.byte_data;

    always_comb begin
        d = q;
        d.valid = 1'b0;
        case (q.phase)
            KBD_IDLE: if (req_sync != q.ack) begin
                d.command = scan_q.command;
                d.accepted = '0;
                d.phase = KBD_DECODE;
            end
            KBD_DECODE: begin
                if (q.fault) begin
                    // Why: once faulted, commands are acknowledged but ignored, so the
                    // first protocol failure stays reported until a system reset.
                    d.ack = req_sync;
                    d.phase = KBD_IDLE;
                end else if (q.command[63:56] != 8'ha3 || q.command[55:48] < 8'd1 ||
                    q.command[55:48] > 8'd4 || q.command[39:32] != 0) begin
                    d.code = 3'd3;
                    d.fault = 1'b1;
                    d.phase = KBD_FINISH;
                end else if (q.have_last && q.command == q.last_command) begin
                    d.ack = req_sync;
                    d.phase = KBD_IDLE;
                end else if (command_seq != q.expected_seq) begin
                    d.code = 3'd4;
                    d.fault = 1'b1;
                    d.phase = KBD_FINISH;
                end else begin
                    d.expected_seq = q.expected_seq + 1'b1;
                    d.code = 3'd1;
                    d.byte_data = q.command[7:0];
                    d.valid = 1'b1;
                    d.phase = KBD_OFFER;
                end
            end
            KBD_OFFER: begin
                d.take = inject_ready_i;
                d.phase = KBD_ACCOUNT;
            end
            KBD_ACCOUNT: begin
                if (!q.take) begin
                    d.code = 3'd2;
                    d.phase = KBD_FINISH;
                end else begin
                    d.accepted = q.accepted + 1'b1;
                    d.total = q.total + 1'b1;
                    if ({5'b0, d.accepted} == q.command[55:48]) d.phase = KBD_FINISH;
                    else begin
                        d.byte_data = q.command[8*int'(d.accepted) +: 8];
                        d.valid = 1'b1;
                        d.phase = KBD_OFFER;
                    end
                end
            end
            KBD_FINISH: begin
                d.response = completion;
                d.ack = req_sync;
                if (q.code == 3'd1 || q.code == 3'd2) begin
                    d.last_command = q.command;
                    d.have_last = 1'b1;
                end
                d.phase = KBD_IDLE;
            end
            default: d.phase = KBD_IDLE;
        endcase
    end
    `DFF_RST_VAL(q, d, clk_i, reset_i, SYSTEM_RESET)
    `ASSERT_CLOCKED_FATAL(clk_i, reset_i, q.accepted <= 3'd4, "Prefix exceeded batch limit")
    `ASSERT_CLOCKED_FATAL(clk_i, reset_i, !q.valid || q.phase == KBD_OFFER, "Byte outside offer phase")
endmodule
