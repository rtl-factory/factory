// Simulation-only loopback for the common UART wire helper's self-test
// (wiki/src/common/dv/uart/dv-plan.md): the bench drives rx_i into the common
// receiver and receives tx_o from the common transmitter. force_i puts
// force_level_i on tx_o, so the bench can show the helper rejects a bad frame.
`default_nettype none

module n2a_uart_wire_fixture #(
    parameter int unsigned CLKS_PER_BIT = 12,
    parameter int unsigned FRAC_NUM = 1,
    parameter int unsigned FRAC_DEN = 2
) (
    input  logic       clk_i,
    input  logic       reset_i,
    input  logic       rx_i,
    output logic [7:0] rx_byte_o,
    output logic       rx_valid_o,
    output logic       rx_framing_error_o,
    input  logic       tx_valid_i,
    input  logic [7:0] tx_byte_i,
    output logic       tx_ready_o,
    input  logic       force_i,
    input  logic       force_level_i,
    output logic       tx_o
);
    logic phy_tx;
    logic rx_busy, tx_busy;

    n2a_uart_rx #(.CLKS_PER_BIT(CLKS_PER_BIT), .FRAC_NUM(FRAC_NUM), .FRAC_DEN(FRAC_DEN)) u_rx (
        .clk_i, .reset_i, .rx_i, .byte_o(rx_byte_o), .byte_valid_o(rx_valid_o), .busy_o(rx_busy),
        .framing_error_o(rx_framing_error_o));
    n2a_uart_tx #(.CLKS_PER_BIT(CLKS_PER_BIT), .FRAC_NUM(FRAC_NUM), .FRAC_DEN(FRAC_DEN)) u_tx (
        .clk_i, .reset_i, .byte_valid_i(tx_valid_i), .byte_ready_o(tx_ready_o), .byte_i(tx_byte_i),
        .busy_o(tx_busy), .tx_o(phy_tx));

    assign tx_o = force_i ? force_level_i : phy_tx;

    /* verilator lint_off UNUSEDSIGNAL */
    logic busy_unused;
    assign busy_unused = rx_busy ^ tx_busy;
    /* verilator lint_on UNUSEDSIGNAL */
endmodule
