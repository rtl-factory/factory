// Simulation-only composition of the nand2doom input path exactly as wired in
// n2d_system, without the vendor Virtual JTAG primitive: the common endpoint
// offers host Set-2 bytes to the keyboard's scan-inject port beside the
// physical PS/2 pins, the UART input-event producer port and the CPU MMIO
// channel. The test drives the endpoint's neutral scan ports directly and
// observes the offer link (wiki/src/nand2doom/dv/io/plan.md).
module n2d_jtag_input_fixture (
    input  logic        clk_i,
    input  logic        reset_i,
    input  logic        ps2_clk_i,
    input  logic        ps2_data_i,
    input  logic        inject_valid_i,
    output logic        inject_ready_o,
    input  logic [9:0]  inject_event_i,
    input  logic        mmio_request_valid_i,
    output logic        mmio_request_ready_o,
    input  logic        mmio_request_write_i,
    input  logic [11:0] mmio_request_address_i,
    input  logic [31:0] mmio_request_write_data_i,
    input  logic [3:0]  mmio_request_write_strobe_i,
    output logic        mmio_response_valid_o,
    input  logic        mmio_response_ready_i,
    output logic [31:0] mmio_response_read_data_o,
    output logic        mmio_response_error_o,
    input  logic        tck_i,
    input  logic [1:0]  ir_i,
    input  logic        capture_i,
    input  logic        shift_i,
    input  logic        update_i,
    input  logic        update_ir_i,
    input  logic        tdi_i,
    output logic        tdo_o,
    output logic        scan_inject_valid_o,
    output logic [7:0]  scan_inject_byte_o,
    output logic        scan_inject_ready_o
);
    n2a_jtag_keyboard u_jtag_keyboard (
        .clk_i, .reset_i, .tck_i, .ir_i, .capture_i, .shift_i, .update_i,
        .update_ir_i, .tdi_i, .tdo_o,
        .inject_valid_o(scan_inject_valid_o), .inject_byte_o(scan_inject_byte_o),
        .inject_ready_i(scan_inject_ready_o)
    );

    n2d_ps2_keyboard u_keyboard (
        .clk_i, .reset_i, .ps2_clk_i, .ps2_data_i,
        .inject_valid_i, .inject_ready_o, .inject_event_i,
        .scan_inject_valid_i(scan_inject_valid_o),
        .scan_inject_ready_o(scan_inject_ready_o),
        .scan_inject_byte_i(scan_inject_byte_o),
        .mmio_request_valid_i, .mmio_request_ready_o, .mmio_request_write_i,
        .mmio_request_address_i, .mmio_request_write_data_i,
        .mmio_request_write_strobe_i, .mmio_response_valid_o,
        .mmio_response_ready_i, .mmio_response_read_data_o,
        .mmio_response_error_o
    );
endmodule
