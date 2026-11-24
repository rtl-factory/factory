`include "src/common/rtl/pkg/macros.vh"

module n2d_ps2_keyboard #(
    parameter int unsigned FRAME_TIMEOUT_CYCLES = 5000
) (
    input  logic        clk_i,
    input  logic        reset_i,
    input  logic        ps2_clk_i,
    input  logic        ps2_data_i,
    input  logic        inject_valid_i,
    output logic        inject_ready_o,
    input  logic [9:0]  inject_event_i,
    // Raw Set-2 byte stream (Virtual JTAG keyboard endpoint): one-cycle offer,
    // accepted only on the edge where ready is high; the producer retries a
    // refused byte (wiki/src/nand2doom/rtl/io/mas.md#ps2-keyboard-receiver).
    input  logic        scan_inject_valid_i,
    output logic        scan_inject_ready_o,
    input  logic [7:0]  scan_inject_byte_i,
    input  logic        mmio_request_valid_i,
    output logic        mmio_request_ready_o,
    input  logic        mmio_request_write_i,
    input  logic [11:0] mmio_request_address_i,
    /* verilator lint_off UNUSEDSIGNAL */
    input  logic [31:0] mmio_request_write_data_i,
    /* verilator lint_on UNUSEDSIGNAL */
    input  logic [3:0]  mmio_request_write_strobe_i,
    output logic        mmio_response_valid_o,
    input  logic        mmio_response_ready_i,
    output logic [31:0] mmio_response_read_data_o,
    output logic        mmio_response_error_o
);
    import n2d_types_pkg::*;

    localparam int unsigned FIFO_DEPTH = 8;
    localparam logic [3:0] FIFO_COUNT_LIMIT = 4'd8;
    localparam logic [11:0] DATA_ADDRESS = 12'h000;
    localparam logic [11:0] STATUS_ADDRESS = 12'h004;
    localparam logic [11:0] CONTROL_ADDRESS = 12'h008;
    localparam logic [11:0] INJECT_ADDRESS = 12'h00c;

    localparam t_ps2_rx RX_RESET = '{
        receiver_enable: 1'b1,
        parity_error: 1'b0,
        frame_error: 1'b0,
        timeout_error: 1'b0};
    t_ps2_rx rx_q, rx_d;
    logic [9:0] fifo_memory [0:FIFO_DEPTH-1];
    localparam t_ps2_fifo FIFO_RESET = '{
        fifo_read_pointer: 3'b0,
        fifo_write_pointer: 3'b0,
        fifo_count: 4'b0,
        overflow_error: 1'b0};
    t_ps2_fifo fifo_q, fifo_d;
    logic fifo_write;
    logic mmio_response_valid_q;
    logic [31:0] mmio_response_read_data_q;
    logic mmio_response_error_q;
    logic mmio_response_valid_d;
    logic [31:0] mmio_response_read_data_d;
    logic mmio_response_error_d;
    logic response_stalled_q;
    logic ps2_byte_valid;
    logic [7:0] ps2_byte;
    n2a_ps2_pkg::t_ps2_rx_errors ps2_errors;
    logic physical_byte_ready;
    logic physical_event_push;
    logic [9:0] physical_event_data;
    logic request_accept;
    logic base_mmio_ready;
    logic data_read_accept;
    logic fifo_pop;
    logic fifo_has_push_space;
    logic physical_push_accept;
    logic uart_push_accept;
    logic mmio_inject_request;
    logic mmio_inject_legal;
    logic mmio_inject_accept;
    logic scan_event_valid;
    logic scan_event_ready;
    logic scan_inject_accept;
    logic [9:0] scan_inject_event;
    logic fifo_push_accept;
    logic [9:0] fifo_push_data;
    logic control_write_accept;
    logic clear_fifo;
    logic clear_diagnostics;
    logic [31:0] status_word;
    logic [31:0] request_read_data;
    logic request_error;

    // Why: a CONTROL write is a receive boundary: it abandons the frame in
    // progress and every discard, like it, drops the pending prefixes.
    n2a_ps2_receiver #(.FRAME_TIMEOUT_CYCLES(FRAME_TIMEOUT_CYCLES)) u_receiver (
        .clk_i        (clk_i),
        .reset_i      (reset_i),
        .ps2_clk_i    (ps2_clk_i),
        .ps2_data_i   (ps2_data_i),
        .enable_i     (rx_q.receiver_enable),
        .abort_i      (control_write_accept),
        .byte_valid_o (ps2_byte_valid),
        .byte_o       (ps2_byte),
        .errors_o     (ps2_errors)
    );
    // Why: the physical source has no handshake, so its decoder never waits;
    // an event dropped by a full FIFO still completes its prefixes.
    n2a_ps2_decoder u_physical_decoder (
        .clk_i         (clk_i),
        .reset_i       (reset_i),
        .byte_valid_i  (ps2_byte_valid),
        .byte_i        (ps2_byte),
        .byte_ready_o  (physical_byte_ready),
        .clear_i       (control_write_accept || (ps2_errors != '0)),
        .event_valid_o (physical_event_push),
        .event_o       (physical_event_data),
        .event_ready_i (1'b1)
    );
    `UNUSED_BITS(physical_byte_ready_unused, physical_byte_ready)

    assign mmio_inject_request = mmio_request_valid_i &&
                                 mmio_request_write_i &&
                                 (mmio_request_address_i == INJECT_ADDRESS);
    assign mmio_inject_legal = (mmio_request_write_strobe_i == 4'hf) &&
                               (mmio_request_write_data_i[31:10] == 0);
    assign base_mmio_ready = !mmio_response_valid_q || mmio_response_ready_i;
    assign mmio_request_ready_o =
        base_mmio_ready &&
        (!mmio_inject_request || !mmio_inject_legal ||
         (!physical_event_push && !inject_valid_i &&
          (fifo_q.fifo_count < FIFO_COUNT_LIMIT)));
    assign request_accept = mmio_request_valid_i && mmio_request_ready_o;
    assign data_read_accept = request_accept && !mmio_request_write_i &&
                              (mmio_request_address_i == DATA_ADDRESS);
    assign fifo_pop = data_read_accept && (fifo_q.fifo_count != 0);
    assign control_write_accept = base_mmio_ready && mmio_request_valid_i &&
                                  mmio_request_write_i &&
                                  (mmio_request_address_i == CONTROL_ADDRESS) &&
                                  mmio_request_write_strobe_i[0] &&
                                  ((mmio_request_write_strobe_i & 4'b1110) == 0);
    assign clear_fifo = control_write_accept && mmio_request_write_data_i[1];
    assign clear_diagnostics = control_write_accept && mmio_request_write_data_i[2];
    assign fifo_has_push_space = (fifo_q.fifo_count < FIFO_COUNT_LIMIT) || fifo_pop;
    assign physical_push_accept = physical_event_push && fifo_has_push_space;
    // Why: the serialized physical source has priority because its frames have
    // no handshake to hold them off; UART valid is retained by its producer
    // until this ready signal is observed, so it can wait.
    assign inject_ready_o = !control_write_accept && !physical_event_push &&
                            fifo_has_push_space;
    assign uart_push_accept = inject_valid_i && inject_ready_o;
    assign mmio_inject_accept = request_accept && mmio_inject_request &&
                                mmio_inject_legal;
    // Why: the byte stream is the lowest-priority producer: its decoder takes
    // a prefix byte at once; a completing byte owns a slot only when no other
    // source pushes on this edge, so it never displaces a physical or UART
    // event. Its qualifiers are its own: CONTROL and receive errors keep them.
    assign scan_event_ready = !control_write_accept && !physical_event_push &&
                              !inject_valid_i && !mmio_inject_accept &&
                              fifo_has_push_space;
    n2a_ps2_decoder u_scan_decoder (
        .clk_i         (clk_i),
        .reset_i       (reset_i),
        .byte_valid_i  (scan_inject_valid_i),
        .byte_i        (scan_inject_byte_i),
        .byte_ready_o  (scan_inject_ready_o),
        .clear_i       (1'b0),
        .event_valid_o (scan_event_valid),
        .event_o       (scan_inject_event),
        .event_ready_i (scan_event_ready)
    );
    assign scan_inject_accept = scan_event_valid && scan_event_ready;
    assign fifo_push_accept = physical_push_accept || uart_push_accept ||
                              mmio_inject_accept || scan_inject_accept;
    assign fifo_push_data = physical_push_accept ? physical_event_data :
                            (uart_push_accept ? inject_event_i :
                             (mmio_inject_accept ? mmio_request_write_data_i[9:0] :
                              scan_inject_event));

    always_comb begin
        status_word = 32'b0;
        status_word[0] = (fifo_q.fifo_count != 0);
        status_word[1] = (fifo_q.fifo_count == 0);
        status_word[2] = (fifo_q.fifo_count == FIFO_COUNT_LIMIT);
        status_word[6:3] = fifo_q.fifo_count;
        status_word[8] = fifo_q.overflow_error;
        status_word[9] = rx_q.parity_error;
        status_word[10] = rx_q.frame_error;
        status_word[11] = rx_q.timeout_error;
        status_word[12] = rx_q.receiver_enable;
        request_read_data = 32'b0;
        request_error = 1'b0;
        unique case (mmio_request_address_i)
            DATA_ADDRESS: begin
                if (mmio_request_write_i) request_error = 1'b1;
                else if (fifo_q.fifo_count != 0) begin
                    request_read_data[31] = 1'b1;
                    request_read_data[9:0] = fifo_memory[fifo_q.fifo_read_pointer];
                end
            end
            STATUS_ADDRESS: begin
                if (mmio_request_write_i) request_error = 1'b1;
                else request_read_data = status_word;
            end
            CONTROL_ADDRESS: begin
                if (mmio_request_write_i)
                    request_error = (mmio_request_write_strobe_i & 4'b1110) != 0;
                else request_read_data[0] = rx_q.receiver_enable;
            end
            INJECT_ADDRESS: begin
                request_error = !mmio_request_write_i ||
                                (mmio_request_write_strobe_i != 4'hf) ||
                                (mmio_request_write_data_i[31:10] != 0);
            end
            default: request_error = 1'b1;
        endcase
    end

    assign mmio_response_valid_o = mmio_response_valid_q;
    assign mmio_response_read_data_o = mmio_response_read_data_q;
    assign mmio_response_error_o = mmio_response_error_q;

    always_comb begin
        rx_d = rx_q;
        if (clear_diagnostics) begin
            rx_d.parity_error = 1'b0;
            rx_d.frame_error = 1'b0;
            rx_d.timeout_error = 1'b0;
        end
        if (control_write_accept) rx_d.receiver_enable = mmio_request_write_data_i[0];
        if (ps2_errors.parity) rx_d.parity_error = 1'b1;
        if (ps2_errors.frame) rx_d.frame_error = 1'b1;
        if (ps2_errors.timeout) rx_d.timeout_error = 1'b1;
    end

    `DFF_RST_VAL(rx_q, rx_d, clk_i, reset_i, RX_RESET)

    always_comb begin
        fifo_d = fifo_q;
        fifo_write = 1'b0;
        if (clear_fifo) begin
            fifo_d.fifo_read_pointer = 3'b0;
            fifo_d.fifo_write_pointer = 3'b0;
            fifo_d.fifo_count = 4'b0;
        end else begin
            if (fifo_push_accept) begin
                fifo_write = 1'b1;
                fifo_d.fifo_write_pointer = fifo_q.fifo_write_pointer + 1'b1;
            end
            if (fifo_pop) fifo_d.fifo_read_pointer = fifo_q.fifo_read_pointer + 1'b1;
            unique case ({fifo_push_accept, fifo_pop})
                2'b10: fifo_d.fifo_count = fifo_q.fifo_count + 1'b1;
                2'b01: fifo_d.fifo_count = fifo_q.fifo_count - 1'b1;
                default: fifo_d.fifo_count = fifo_q.fifo_count;
            endcase
            if (physical_event_push && !physical_push_accept)
                fifo_d.overflow_error = 1'b1;
        end
        if (clear_diagnostics) fifo_d.overflow_error = 1'b0;
    end

    `DFF_RST_VAL(fifo_q, fifo_d, clk_i, reset_i, FIFO_RESET)
    // Why: the storage is never reset so it can infer RAM; reset only inhibits
    // the entry write, as it holds the FIFO bookkeeping.
    logic [2:0] fifo_write_address;
    logic fifo_write_enable;
    assign fifo_write_address = fifo_q.fifo_write_pointer;
    assign fifo_write_enable = fifo_write && !reset_i;
    `MEM_WRITE(fifo_memory, clk_i, fifo_write_enable, fifo_write_address, fifo_push_data)

    always_comb begin
        mmio_response_valid_d = mmio_response_valid_q;
        mmio_response_read_data_d = mmio_response_read_data_q;
        mmio_response_error_d = mmio_response_error_q;
        if (request_accept) begin
            mmio_response_valid_d = 1'b1;
            mmio_response_read_data_d = request_read_data;
            mmio_response_error_d = request_error;
        end else if (mmio_response_valid_q && mmio_response_ready_i) begin
            mmio_response_valid_d = 1'b0;
        end
    end

    `DFF_RST_VAL(mmio_response_valid_q, mmio_response_valid_d, clk_i, reset_i, 1'b0)
    `DFF_RST_VAL(mmio_response_read_data_q, mmio_response_read_data_d, clk_i, reset_i, 32'b0)
    `DFF_RST_VAL(mmio_response_error_q, mmio_response_error_d, clk_i, reset_i, 1'b0)

    // Why: previous-edge flops stand in for $stable, so the unguarded check needs
    // no sampled-value function in synthesis.
    logic response_stalled_q_next;
    assign response_stalled_q_next = mmio_response_valid_q && !mmio_response_ready_i;
    `DFF_RST_VAL(response_stalled_q, response_stalled_q_next, clk_i, reset_i, 1'b0)
    logic [31:0] response_read_data_previous_q;
    logic response_error_previous_q;
    `DFF_RST_VAL(response_read_data_previous_q, mmio_response_read_data_q, clk_i, reset_i, 32'b0)
    `DFF_RST_VAL(response_error_previous_q, mmio_response_error_q, clk_i, reset_i, 1'b0)
    `ASSERT_CLOCKED_FATAL_UNGUARDED(clk_i, reset_i,
        fifo_q.fifo_count <= FIFO_COUNT_LIMIT,
        "n2d_ps2_keyboard FIFO count exceeded depth")
    `ASSERT_CLOCKED_FATAL_UNGUARDED(clk_i, reset_i,
        ($onehot0({physical_push_accept, uart_push_accept, mmio_inject_accept,
                   scan_inject_accept})),
        "n2d_ps2_keyboard accepted multiple event sources")
    `ASSERT_CLOCKED_FATAL_WHEN_UNGUARDED(clk_i, reset_i, response_stalled_q,
        (mmio_response_read_data_q == response_read_data_previous_q) &&
        (mmio_response_error_q == response_error_previous_q),
        "n2d_ps2_keyboard response changed under backpressure")
endmodule
