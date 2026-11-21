`default_nettype none
module n2m_library_fixture (
    input logic clk_i,
    input logic reset_i,
    input logic host_hold_i,
    input logic backing_initialized_i,
    input n2m_interfaces_pkg::t_bus_request request_i,
    output logic ready_o,
    output n2m_interfaces_pkg::t_bus_response response_o,
    output n2m_cartridge_pkg::t_backing_request backing_request_o,
    input logic backing_ready_i,
    input n2m_cartridge_pkg::t_backing_response backing_response_i,
    output logic core_reset_o
);
    logic hold;
    assign core_reset_o = reset_i || host_hold_i || !backing_initialized_i || hold;
    n2m_library u_library (
        .clk_i (clk_i), .reset_i (reset_i), .core_reset_i (core_reset_o),
        .host_hold_i (host_hold_i), .backing_initialized_i (backing_initialized_i),
        .request_i (request_i), .ready_o (ready_o), .response_o (response_o),
        .backing_request_o (backing_request_o), .backing_ready_i (backing_ready_i),
        .backing_response_i (backing_response_i), .hold_o (hold)
    );
endmodule
