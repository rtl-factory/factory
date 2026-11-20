`include "src/common/rtl/pkg/macros.vh"

module n2d_uart_debug
    import n2d_pkg::*;
#(
    // The bit period follows the system clock: CLOCK_HZ / BAUD clocks, with
    // any remainder spread by the PHY's fractional accumulator (io/HAS.md
    // "Link rate"). CLKS_PER_BIT alone selects an integer divisor.
    parameter int unsigned CLOCK_HZ = N2D_SYSTEM_CLOCK_HZ,
    parameter int unsigned BAUD = N2D_UART_BAUD,
    parameter int unsigned CLKS_PER_BIT = CLOCK_HZ / BAUD,
    parameter int unsigned FRAC_NUM = CLOCK_HZ % BAUD,
    parameter int unsigned FRAC_DEN = ((CLOCK_HZ % BAUD) == 0) ? 1 : BAUD,
    parameter int unsigned INTERBYTE_TIMEOUT_CYCLES = 512,
    parameter int unsigned OP_TIMEOUT_CYCLES = 1024,
    parameter int unsigned HALT_TIMEOUT_CYCLES = OP_TIMEOUT_CYCLES,
    parameter int unsigned MAX_WORDS = 8,
    // Stream session bounds: 40 ms within a frame, 2 s idle session.
    parameter int unsigned STREAM_GAP_TIMEOUT_CYCLES = CLOCK_HZ / 25,
    parameter int unsigned STREAM_IDLE_CYCLES = 2 * CLOCK_HZ
) (
    input  logic                  clk_i,
    input  logic                  reset_i,
    input  logic                  uart_rx_i,
    output logic                  uart_tx_o,

    // Port groups (n2d_pkg), one direction each; see io/HAS.md.
    output n2d_debug_control_t    debug_control_o,
    input  n2d_debug_status_t     debug_status_i,
    output n2d_input_event_t      input_event_o,
    input  logic                  input_event_ready_i,
    output n2d_debug_txn_req_t    txn_req_o,
    input  n2d_debug_txn_status_t txn_status_i,
    output n2d_debug_mem_req_t    mem_req_o,
    input  n2d_debug_mem_rsp_t    mem_rsp_i,
    // UART memory endpoint screen reads (io/HAS.md "UART memory endpoint").
    output n2d_screen_req_t       screen_req_o,
    input  n2d_screen_rsp_t       screen_rsp_i
);
    logic [7:0] rx_byte, tx_byte, engine_rx_byte, engine_tx_byte;
    logic [7:0] phy_rx_byte, phy_tx_byte;
    logic phy_rx_valid, phy_rx_error, phy_tx_valid, phy_tx_ready;
    logic ep_owner, ep_hold, ep_release, ep_entry_valid, port_busy;
    logic ep_req_valid, ep_req_ready, ep_req_write, ep_rsp_valid, ep_rsp_error;
    logic [31:0] ep_entry_addr, ep_req_addr, ep_req_wdata, ep_rsp_rdata;
    n2d_debug_control_t    engine_control;
    n2d_debug_txn_req_t    port_txn_req;
    n2d_debug_mem_req_t    port_mem_req;
    logic [7:0] bulk_rx_byte, bulk_tx_byte;
    logic rx_valid, rx_error, rx_busy, tx_valid, tx_ready, tx_busy;
    logic bulk_rx_valid, bulk_rx_error, bulk_tx_valid, bulk_tx_ready, bulk_idle;
    logic engine_rx_valid, engine_rx_error, engine_tx_valid, engine_tx_ready;
    logic engine_idle, engine_frame_idle, stream_session;
    n2d_debug_txn_req_t    engine_txn_req, stream_txn_req;
    n2d_debug_txn_status_t engine_txn_status;
    n2d_debug_mem_req_t    engine_mem_req, stream_mem_req;
    n2d_debug_mem_rsp_t    engine_mem_rsp;

    n2a_uart_rx #(.CLKS_PER_BIT(CLKS_PER_BIT), .FRAC_NUM(FRAC_NUM),
                  .FRAC_DEN(FRAC_DEN)) uart_rx (
        .clk_i(clk_i), .reset_i(reset_i), .rx_i(uart_rx_i),
        .byte_o(phy_rx_byte), .byte_valid_o(phy_rx_valid), .busy_o(rx_busy),
        .framing_error_o(phy_rx_error)
    );
    n2a_uart_tx #(.CLKS_PER_BIT(CLKS_PER_BIT), .FRAC_NUM(FRAC_NUM),
                  .FRAC_DEN(FRAC_DEN)) uart_tx (
        .clk_i(clk_i), .reset_i(reset_i), .byte_valid_i(phy_tx_valid),
        .byte_ready_o(phy_tx_ready), .byte_i(phy_tx_byte), .busy_o(tx_busy),
        .tx_o(uart_tx_o)
    );
    // The common UART memory endpoint is outermost: it claims its SOF only
    // when the stream, bulk and legacy paths are all between frames, and
    // passes every other byte through to them.
    n2a_uart_mem #(
        .PROJECT_ID(N2D_MEM_PROJECT_ID), .WINDOW_COUNT(3), .WINDOWS(N2D_MEM_WINDOWS),
        .ENTRY_ADDR(32'h0000_0000), .MAX_LENGTH(4096),
        .GAP_CYCLES(STREAM_GAP_TIMEOUT_CYCLES), .HOLD_TIMEOUT_CYCLES(HALT_TIMEOUT_CYCLES),
        .MEM_TIMEOUT_CYCLES(OP_TIMEOUT_CYCLES), .LIVE_GAP_CYCLES(N2D_MEM_LIVE_GAP_CYCLES)
    ) u_mem_endpoint (
        .clk_i(clk_i), .reset_i(reset_i),
        .rx_byte_i(phy_rx_byte), .rx_valid_i(phy_rx_valid), .rx_error_i(phy_rx_error),
        .tx_byte_o(phy_tx_byte), .tx_valid_o(phy_tx_valid), .tx_ready_i(phy_tx_ready),
        .down_rx_byte_o(rx_byte), .down_rx_valid_o(rx_valid), .down_rx_error_o(rx_error),
        .down_tx_byte_i(tx_byte), .down_tx_valid_i(tx_valid), .down_tx_ready_o(tx_ready),
        .down_idle_i(!stream_session && bulk_idle && engine_idle && engine_frame_idle &&
            !port_busy),
        .owner_o(ep_owner),
        .hold_o(ep_hold),
        .hold_ack_i(debug_status_i.halt_ack && debug_status_i.cache_maint_done),
        .release_o(ep_release), .entry_valid_o(ep_entry_valid), .entry_addr_o(ep_entry_addr),
        .mem_req_valid_o(ep_req_valid), .mem_req_ready_i(ep_req_ready),
        .mem_req_write_o(ep_req_write), .mem_req_addr_o(ep_req_addr),
        .mem_req_wdata_o(ep_req_wdata), .mem_rsp_valid_i(ep_rsp_valid),
        .mem_rsp_rdata_i(ep_rsp_rdata), .mem_rsp_error_i(ep_rsp_error)
    );
    n2d_uart_mem_port u_mem_port (
        .clk_i(clk_i), .reset_i(reset_i),
        .ep_req_valid_i(ep_req_valid), .ep_req_ready_o(ep_req_ready),
        .ep_req_write_i(ep_req_write), .ep_req_addr_i(ep_req_addr),
        .ep_req_wdata_i(ep_req_wdata), .ep_rsp_valid_o(ep_rsp_valid),
        .ep_rsp_rdata_o(ep_rsp_rdata), .ep_rsp_error_o(ep_rsp_error),
        .busy_o(port_busy), .txn_req_o(port_txn_req), .txn_status_i(txn_status_i),
        .mem_req_o(port_mem_req), .mem_rsp_i(mem_rsp_i),
        .screen_req_o(screen_req_o), .screen_rsp_i(screen_rsp_i)
    );
    // Hold joins the engine's halt request; release drops both, so a bench
    // release runs the CPU even though the engine resets with halt requested.
    // Entry restarts the core at its reset vector, as Reset-PC does.
    always_comb begin
        debug_control_o = engine_control;
        debug_control_o.halt_req = engine_control.halt_req || ep_hold;
        if (ep_entry_valid) begin
            debug_control_o.reset_pc_valid = 1'b1;
            debug_control_o.reset_pc = ep_entry_addr;
        end
    end

    // The stream router is first on the shared PHY: it claims the link only
    // at an idle bulk/legacy boundary and owns memory only during a session.
    n2d_uart_stream #(
        .GAP_TIMEOUT_CYCLES(STREAM_GAP_TIMEOUT_CYCLES),
        .SESSION_IDLE_CYCLES(STREAM_IDLE_CYCLES),
        .OP_TIMEOUT_CYCLES(OP_TIMEOUT_CYCLES)
    ) u_stream (
        .clk_i(clk_i), .reset_i(reset_i),
        .rx_byte_i(rx_byte), .rx_valid_i(rx_valid), .rx_error_i(rx_error),
        .rx_busy_i(rx_busy), .tx_byte_o(tx_byte), .tx_valid_o(tx_valid),
        .tx_ready_i(tx_ready), .tx_busy_i(tx_busy),
        .down_rx_byte_o(bulk_rx_byte), .down_rx_valid_o(bulk_rx_valid),
        .down_rx_error_o(bulk_rx_error), .down_tx_byte_i(bulk_tx_byte),
        .down_tx_valid_i(bulk_tx_valid), .down_tx_ready_o(bulk_tx_ready),
        .legacy_idle_i(bulk_idle && engine_idle && engine_frame_idle),
        .session_o(stream_session), .debug_status_i(debug_status_i),
        .txn_req_o(stream_txn_req), .txn_status_i(txn_status_i),
        .mem_req_o(stream_mem_req), .mem_rsp_i(mem_rsp_i)
    );
    n2d_uart_bulk #(.MAX_WORDS(MAX_WORDS)) u_bulk (
        .clk_i(clk_i), .reset_i(reset_i), .rx_byte_i(bulk_rx_byte),
        .rx_valid_i(bulk_rx_valid), .rx_error_i(bulk_rx_error),
        .tx_byte_o(bulk_tx_byte), .tx_valid_o(bulk_tx_valid), .tx_ready_i(bulk_tx_ready),
        .tx_busy_i(tx_busy), .engine_idle_i(engine_idle),
        .engine_frame_idle_i(engine_frame_idle),
        .engine_rx_byte_o(engine_rx_byte), .engine_rx_valid_o(engine_rx_valid),
        .engine_rx_error_o(engine_rx_error), .engine_tx_byte_i(engine_tx_byte),
        .engine_tx_valid_i(engine_tx_valid), .engine_tx_ready_o(engine_tx_ready),
        .idle_o(bulk_idle)
    );
    n2d_uart_debug_engine #(
        .CLKS_PER_BIT(CLKS_PER_BIT),
        .INTERBYTE_TIMEOUT_CYCLES(INTERBYTE_TIMEOUT_CYCLES),
        .OP_TIMEOUT_CYCLES(OP_TIMEOUT_CYCLES),
        .HALT_TIMEOUT_CYCLES(HALT_TIMEOUT_CYCLES), .MAX_WORDS(MAX_WORDS)
    ) u_engine (
        .rx_byte(engine_rx_byte), .rx_valid(engine_rx_valid),
        .rx_framing_error(engine_rx_error), .tx_byte(engine_tx_byte),
        .tx_valid(engine_tx_valid), .tx_ready(engine_tx_ready), .idle_o(engine_idle),
        .frame_idle_o(engine_frame_idle),
        .release_i(ep_release),
        .clk_i(clk_i),
        .reset_i(reset_i),
        .debug_control_o(engine_control),
        .debug_status_i(debug_status_i),
        .input_event_o(input_event_o),
        .input_event_ready_i(input_event_ready_i),
        .txn_req_o(engine_txn_req),
        .txn_status_i(engine_txn_status),
        .mem_req_o(engine_mem_req),
        .mem_rsp_i(engine_mem_rsp)
    );

    // One memory owner: the endpoint port while it is busy, else the stream
    // during a session, else the engine; an idle owner sees no completions.
    assign txn_req_o = port_busy ? port_txn_req :
        (stream_session ? stream_txn_req : engine_txn_req);
    assign mem_req_o = port_busy ? port_mem_req :
        (stream_session ? stream_mem_req : engine_mem_req);
    // Explicit field assignments: Quartus cannot type an assignment pattern
    // that is an operand of the conditional operator.
    always_comb begin
        engine_txn_status = txn_status_i;
        if (stream_session || port_busy) begin
            engine_txn_status.txn_done = 1'b0;
            engine_txn_status.txn_error = 1'b0;
        end
    end
    assign engine_mem_rsp = (stream_session || port_busy) ? '0 : mem_rsp_i;

    `N2A_ASSERT_CLOCKED_FATAL(clk_i, reset_i, (!$isunknown({rx_busy, tx_busy})),
        "UART PHY busy status is unknown")
    `N2A_ASSERT_CLOCKED_ARST_FATAL_WHEN(clk_i, reset_i, stream_session,
        ((engine_txn_req == '0) && !engine_mem_req.valid),
        "legacy engine requested memory during a stream session")
    `N2A_ASSERT_CLOCKED_ARST_FATAL_WHEN(clk_i, reset_i, port_busy,
        ((engine_txn_req == '0) && !engine_mem_req.valid && !stream_session),
        "another path requested memory while the endpoint port is busy")
    `N2A_ASSERT_CLOCKED_ARST_FATAL_WHEN(clk_i, reset_i, ep_owner, !rx_valid && !rx_error,
        "a byte reached the n2d routers during an endpoint frame")
    `N2A_ASSERT_CLOCKED_ARST_FATAL_WHEN(clk_i, reset_i, ep_release,
        engine_idle && engine_frame_idle, "endpoint release while the engine owns a frame")
endmodule
