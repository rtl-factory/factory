module n2d_uart_bulk_limits (
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
    output logic idle_o
);
    n2d_uart_bulk #(
        .INTERBYTE_TIMEOUT_CYCLES(1024), .FRAME_TIMEOUT_CYCLES(70000)
    ) u_bulk (
        .clk_i(clk_i),
        .reset_i(reset_i),
        .rx_byte_i(rx_byte_i),
        .rx_valid_i(rx_valid_i),
        .rx_error_i(rx_error_i),
        .tx_byte_o(tx_byte_o),
        .tx_valid_o(tx_valid_o),
        .tx_ready_i(tx_ready_i),
        .tx_busy_i(tx_busy_i),
        .engine_idle_i(engine_idle_i),
        .engine_frame_idle_i(engine_frame_idle_i),
        .engine_rx_byte_o(engine_rx_byte_o),
        .engine_rx_valid_o(engine_rx_valid_o),
        .engine_rx_error_o(engine_rx_error_o),
        .engine_tx_byte_i(engine_tx_byte_i),
        .engine_tx_valid_i(engine_tx_valid_i),
        .engine_tx_ready_o(engine_tx_ready_o),
        .idle_o(idle_o)
    );
endmodule
