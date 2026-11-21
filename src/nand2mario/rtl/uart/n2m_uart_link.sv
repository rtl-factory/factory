`default_nettype none
`include "src/common/rtl/pkg/macros.vh"

// nand2mario board UART link (wiki/src/nand2mario/rtl/has.md, uart row): the
// common 8-N-1 PHYs, the common UART memory endpoint and the cartridge
// loader behind it. At a loader frame boundary a common request (0xA8) goes
// to the endpoint; every other byte, the loader's own 0xA5 frames included,
// passes through to the loader. The endpoint's word bus reaches the
// cartridge backing's load port through n2m_uart_mem_port; its hold joins the
// loader's, so either one holds the console core in reset. Entry is
// accepted only at the cartridge entry point, where the boot ROM hands over.
//
// reset_i is the synchronous system reset. The common UART PHYs and the
// endpoint assert it asynchronously by their own contract; because reset_i
// is a registered system signal whose release is synchronous to clk_i, the
// mixed use is intended (the same waiver as the Doom and Hack system tops).
/* verilator lint_off SYNCASYNCNET */
module n2m_uart_link #(
    parameter int unsigned CLKS_PER_BIT = 217,
    parameter int unsigned FRAC_NUM = 0,
    parameter int unsigned FRAC_DEN = 1,
    // Idle clocks inside a loader frame after which it is dropped.
    parameter int unsigned RX_TIMEOUT_CLOCKS = 65536,
    // Backing capacity, the size of the endpoint's ROM and RAM windows.
    parameter int unsigned CART_ROM_BYTES = 2097152,
    parameter int unsigned CART_RAM_BYTES = 32768,
    // Endpoint request gap and hold timeout (clocks); benches shorten them.
    parameter int unsigned GAP_CYCLES = 1_000_000,
    parameter int unsigned HOLD_TIMEOUT_CYCLES = 1_000_000
) (
    input  logic                                   clk_i,
    input  logic                                   reset_i,
    input  logic                                   uart_rx_i,
    output logic                                   uart_tx_o,
    // Core reset request: the loader's hold or the endpoint's.
    output logic                                   hold_o,
    input  logic                                   backing_initialized_i,
    // Load port of the cartridge backing (one outstanding request).
    output n2m_cartridge_pkg::t_backing_request    load_request_o,
    input  logic                                   load_ready_i,
    input  n2m_cartridge_pkg::t_backing_response   load_response_i,
    // High while a load-port request is presented or awaits its response.
    output logic                                   load_busy_o,
    // Display frame snapshot, served by the loader.
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

    `ASSERT_PARAM(g_invalid_timeout, RX_TIMEOUT_CLOCKS >= 10 * CLKS_PER_BIT,
        "n2m_uart_link RX_TIMEOUT_CLOCKS must cover at least one serial frame")
    `ASSERT_PARAM(g_windows, (CART_ROM_BYTES <= MAX_ROM_BYTES) && (CART_RAM_BYTES <= MAX_RAM_BYTES),
        "n2m_uart_link windows must fit the loader address map")

    logic [7:0]  rx_byte, tx_byte, down_rx_byte, down_tx_byte;
    logic        rx_valid, rx_error, tx_valid, tx_ready;
    logic        down_rx_valid, down_rx_error, down_tx_valid, down_tx_ready, loader_idle;
    logic        loader_hold, ep_hold, loader_busy, port_busy;
    logic        ep_req_valid, ep_req_ready, ep_req_write, ep_rsp_valid, ep_rsp_error;
    logic [31:0] ep_req_addr, ep_req_wdata, ep_rsp_rdata;
    t_backing_request  loader_request, port_request;
    t_backing_response loader_response, port_response;

    // Why: the PHY busy levels, the endpoint's owner flag, release pulse and
    // entry state are not consumed: the core leaves reset when the hold drops
    // and always enters the cartridge through the boot ROM.
    /* verilator lint_off PINCONNECTEMPTY */
    n2a_uart_rx #(.CLKS_PER_BIT(CLKS_PER_BIT), .FRAC_NUM(FRAC_NUM), .FRAC_DEN(FRAC_DEN)) u_rx (
        .clk_i(clk_i), .reset_i(reset_i), .rx_i(uart_rx_i),
        .byte_o(rx_byte), .byte_valid_o(rx_valid), .busy_o(), .framing_error_o(rx_error)
    );

    n2a_uart_tx #(.CLKS_PER_BIT(CLKS_PER_BIT), .FRAC_NUM(FRAC_NUM), .FRAC_DEN(FRAC_DEN)) u_tx (
        .clk_i(clk_i), .reset_i(reset_i), .byte_valid_i(tx_valid), .byte_ready_o(tx_ready),
        .byte_i(tx_byte), .busy_o(), .tx_o(uart_tx_o)
    );

    n2a_uart_mem #(
        .PROJECT_ID(N2M_MEM_PROJECT_ID), .WINDOW_COUNT(2),
        .WINDOWS({N2M_MEM_WINDOW_ATTRIBUTES, 32'(CART_RAM_BYTES), N2M_MEM_RAM_BASE,
                  N2M_MEM_WINDOW_ATTRIBUTES, 32'(CART_ROM_BYTES), N2M_MEM_ROM_BASE}),
        .ENTRY_ADDR(N2M_MEM_ENTRY), .ENTRY_ANY(1'b0),
        .GAP_CYCLES(GAP_CYCLES), .HOLD_TIMEOUT_CYCLES(HOLD_TIMEOUT_CYCLES)
    ) u_endpoint (
        .clk_i(clk_i), .reset_i(reset_i),
        .rx_byte_i(rx_byte), .rx_valid_i(rx_valid), .rx_error_i(rx_error),
        .tx_byte_o(tx_byte), .tx_valid_o(tx_valid), .tx_ready_i(tx_ready),
        .down_rx_byte_o(down_rx_byte), .down_rx_valid_o(down_rx_valid),
        .down_rx_error_o(down_rx_error), .down_tx_byte_i(down_tx_byte),
        .down_tx_valid_i(down_tx_valid), .down_tx_ready_o(down_tx_ready),
        .down_idle_i(loader_idle), .owner_o(),
        .hold_o(ep_hold), .hold_ack_i(ep_hold && backing_initialized_i),
        .release_o(), .entry_valid_o(), .entry_addr_o(),
        .mem_req_valid_o(ep_req_valid), .mem_req_ready_i(ep_req_ready),
        .mem_req_write_o(ep_req_write), .mem_req_addr_o(ep_req_addr),
        .mem_req_wdata_o(ep_req_wdata), .mem_rsp_valid_i(ep_rsp_valid),
        .mem_rsp_rdata_i(ep_rsp_rdata), .mem_rsp_error_i(ep_rsp_error)
    );
    /* verilator lint_on PINCONNECTEMPTY */

    n2m_uart_mem_port u_mem_port (
        .clk_i(clk_i), .reset_i(reset_i),
        .ep_req_valid_i(ep_req_valid), .ep_req_ready_o(ep_req_ready),
        .ep_req_write_i(ep_req_write), .ep_req_addr_i(ep_req_addr),
        .ep_req_wdata_i(ep_req_wdata), .ep_rsp_valid_o(ep_rsp_valid),
        .ep_rsp_rdata_o(ep_rsp_rdata), .ep_rsp_error_o(ep_rsp_error),
        .busy_o(port_busy), .load_request_o(port_request),
        .load_ready_i(load_ready_i && port_busy), .load_response_i(port_response)
    );

    n2m_uart_loader #(.RX_TIMEOUT_CLOCKS(RX_TIMEOUT_CLOCKS)) u_loader (
        .clk_i                 (clk_i),
        .reset_i               (reset_i),
        .rx_byte_i             (down_rx_byte),
        .rx_valid_i            (down_rx_valid),
        .rx_error_i            (down_rx_error),
        .tx_byte_o             (down_tx_byte),
        .tx_valid_o            (down_tx_valid),
        .tx_ready_i            (down_tx_ready),
        .idle_o                (loader_idle),
        .hold_o                (loader_hold),
        .backing_initialized_i (backing_initialized_i),
        .load_request_o        (loader_request),
        .load_ready_i          (load_ready_i && !port_busy),
        .load_response_i       (loader_response),
        .load_busy_o           (loader_busy),
        .snapshot_pin_o        (snapshot_pin_o),
        .snapshot_release_o    (snapshot_release_o),
        .snapshot_pinned_i     (snapshot_pinned_i),
        .snapshot_available_i  (snapshot_available_i),
        .snapshot_request_o    (snapshot_request_o),
        .snapshot_ready_i      (snapshot_ready_i),
        .snapshot_response_i   (snapshot_response_i)
    );

    // Why: the endpoint claims the link only while the loader is idle, and the
    // loader sees no bytes while the endpoint owns it, so the two never use the
    // load port at once; the port's busy level steers requests and responses.
    assign hold_o = loader_hold || ep_hold;
    assign load_busy_o = loader_busy || port_busy;
    assign load_request_o = port_busy ? port_request : loader_request;
    always_comb begin
        port_response = load_response_i;
        port_response.valid = load_response_i.valid && port_busy;
        loader_response = load_response_i;
        loader_response.valid = load_response_i.valid && !port_busy;
    end

    `ASSERT_NEVER(load_port_shared, clk_i, reset_i, port_busy && loader_busy)
endmodule
/* verilator lint_on SYNCASYNCNET */
