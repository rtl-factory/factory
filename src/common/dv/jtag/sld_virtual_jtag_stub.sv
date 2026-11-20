// Simulation stand-in for the Intel sld_virtual_jtag primitive as instantiated
// by src/common/rtl/jtag/n2a_jtag_virtual.sv, so a board top elaborates with the
// same common wrapper it synthesizes with (wiki/src/common/jtag/HAS.md). It
// declares exactly the parameters and ports that wrapper uses. Its scan
// outputs come from bench-owned variables that nothing in the design drives:
// without a bench they hold their power-up zero, so TCK never toggles, every
// strobe stays low and the endpoint's scan domain stays in reset, as with the
// cable unused. A board test deposits scan sequences into them to play the
// hub (src/common/dv/jtag/vjtag_stub_host.py). Never a synthesis input.
/* verilator lint_off UNUSEDPARAM */
/* verilator lint_off UNUSEDSIGNAL */
module sld_virtual_jtag #(
    parameter string sld_auto_instance_index = "YES",
    parameter int    sld_instance_index      = 0,
    parameter int    sld_ir_width            = 1
) (
    output logic                    tck,
    output logic                    tdi,
    input  logic                    tdo,
    output logic [sld_ir_width-1:0] ir_in,
    input  logic [sld_ir_width-1:0] ir_out,
    output logic                    virtual_state_cdr,
    output logic                    virtual_state_sdr,
    output logic                    virtual_state_udr,
    output logic                    virtual_state_uir
);
    // Bench-driven hub state; no RTL driver by design.
    /* verilator lint_off UNDRIVEN */
    logic                    tck_q, tdi_q, capture_q, shift_q, update_q, update_ir_q;
    logic [sld_ir_width-1:0] ir_q;
    /* verilator lint_on UNDRIVEN */
    assign tck = tck_q;
    assign tdi = tdi_q;
    assign ir_in = ir_q;
    assign virtual_state_cdr = capture_q;
    assign virtual_state_sdr = shift_q;
    assign virtual_state_udr = update_q;
    assign virtual_state_uir = update_ir_q;
endmodule
/* verilator lint_on UNUSEDSIGNAL */
/* verilator lint_on UNUSEDPARAM */
