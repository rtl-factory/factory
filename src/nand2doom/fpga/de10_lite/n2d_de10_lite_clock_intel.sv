// Intel MAX 10 backend of the DE10-Lite clock contract: one altpll (u_pll)
// dividing the 50 MHz reference to the 25 MHz system clock with lock status.
//
// This file and n2d_de10_lite_clock_portable.sv define the same module name.
// Only the Quartus source list selects this file; simulation targets list
// the portable file. The board top never selects a backend itself.
/* verilator lint_off DECLFILENAME */
module n2d_de10_lite_clock (
    input logic reference_clk_i,
    input logic reset_i,
    output logic clk_25_o,
    output logic locked_o
);
    logic [4:0] pll_clocks;
    altpll #(
        .inclk0_input_frequency(20000),
        .clk0_multiply_by(1), .clk0_divide_by(2),
        .clk0_duty_cycle(50), .clk0_phase_shift("0"),
        .compensate_clock("CLK0"), .operation_mode("NORMAL"),
        .intended_device_family("MAX 10"), .width_clock(5),
        .port_inclk0("PORT_USED"), .port_areset("PORT_USED"),
        .port_locked("PORT_USED"), .port_clk0("PORT_USED"),
        .port_phasecounterselect("PORT_UNUSED"), .port_phasestep("PORT_UNUSED"),
        .port_phaseupdown("PORT_UNUSED"), .port_scanclkena("PORT_UNUSED")
    ) u_pll (
        .inclk({1'b0, reference_clk_i}), .areset(reset_i),
        .clk(pll_clocks), .locked(locked_o),
        .clkena(6'b111111), .extclkena(4'b1111), .pfdena(1'b1),
        .pllena(1'b1), .fbin(1'b1), .scanaclr(1'b0), .scanclk(1'b0),
        .scanread(1'b0), .scanwrite(1'b0),
        .scandata(1'b0), .configupdate(1'b0), .clkswitch(1'b0));
    assign clk_25_o = pll_clocks[0];
endmodule
/* verilator lint_on DECLFILENAME */
