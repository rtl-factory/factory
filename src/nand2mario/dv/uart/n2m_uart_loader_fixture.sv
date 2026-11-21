// Simulation-only composition for the n2m_uart_loader target: the loader
// with a host-side pair of common UART PHYs so the bench exchanges bytes,
// and a line override so the bench can also drive the receive line bit by
// bit (framing errors, line noise). The loader's load port and snapshot
// ports are exposed so the bench plays a stalling backing and the display's
// snapshot side.
`default_nettype none

module n2m_uart_loader_fixture #(
  parameter int unsigned CLKS_PER_BIT = 8,
  parameter int unsigned RX_TIMEOUT_CLOCKS = 4000
) (
  input  logic                                   clk_i,
  input  logic                                   reset_i,
  // Host byte interface: bytes to the loader and bytes from it.
  input  logic                                   host_tx_valid_i,
  input  logic [7:0]                             host_tx_data_i,
  output logic                                   host_tx_ready_o,
  output logic                                   host_rx_valid_o,
  output logic [7:0]                             host_rx_data_o,
  output logic                                   host_rx_framing_o,
  // Bit-level override of the loader's receive line.
  input  logic                                   line_override_i,
  input  logic                                   line_i,
  // Loader status and its load port, played by the bench.
  output logic                                   hold_o,
  input  logic                                   backing_initialized_i,
  output n2m_cartridge_pkg::t_backing_request    load_request_o,
  input  logic                                   load_ready_i,
  input  n2m_cartridge_pkg::t_backing_response   load_response_i,
  output logic                                   load_busy_o,
  output logic                                   snapshot_pin_o,
  output logic                                   snapshot_release_o,
  input  logic                                   snapshot_pinned_i,
  input  logic                                   snapshot_available_i,
  output n2m_snapshot_pkg::t_snapshot_request     snapshot_request_o,
  input  logic                                   snapshot_ready_i,
  input  n2m_snapshot_pkg::t_snapshot_response    snapshot_response_i
);
  logic host_line, loader_line, loader_rx;

  assign loader_rx = line_override_i ? line_i : host_line;

  /* verilator lint_off PINCONNECTEMPTY */
  n2a_uart_tx #(.CLKS_PER_BIT (CLKS_PER_BIT)) u_host_tx (
    .clk_i        (clk_i),
    .reset_i      (reset_i),
    .byte_valid_i (host_tx_valid_i),
    .byte_ready_o (host_tx_ready_o),
    .byte_i       (host_tx_data_i),
    .busy_o       (),
    .tx_o         (host_line)
  );

  n2a_uart_rx #(.CLKS_PER_BIT (CLKS_PER_BIT)) u_host_rx (
    .clk_i           (clk_i),
    .reset_i         (reset_i),
    .rx_i            (loader_line),
    .byte_o          (host_rx_data_o),
    .byte_valid_o    (host_rx_valid_o),
    .busy_o          (),
    .framing_error_o (host_rx_framing_o)
  );
  /* verilator lint_on PINCONNECTEMPTY */

  n2m_uart_loader #(
    .CLKS_PER_BIT      (CLKS_PER_BIT),
    .RX_TIMEOUT_CLOCKS (RX_TIMEOUT_CLOCKS)
  ) u_loader (
    .clk_i                 (clk_i),
    .reset_i               (reset_i),
    .uart_rx_i             (loader_rx),
    .uart_tx_o             (loader_line),
    .hold_o                (hold_o),
    .backing_initialized_i (backing_initialized_i),
    .load_request_o        (load_request_o),
    .load_ready_i          (load_ready_i),
    .load_response_i       (load_response_i),
    .load_busy_o           (load_busy_o),
    .snapshot_pin_o        (snapshot_pin_o),
    .snapshot_release_o    (snapshot_release_o),
    .snapshot_pinned_i     (snapshot_pinned_i),
    .snapshot_available_i  (snapshot_available_i),
    .snapshot_request_o    (snapshot_request_o),
    .snapshot_ready_i      (snapshot_ready_i),
    .snapshot_response_i   (snapshot_response_i)
  );
endmodule
