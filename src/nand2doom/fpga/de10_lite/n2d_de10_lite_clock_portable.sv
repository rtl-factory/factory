`include "src/common/rtl/pkg/macros.vh"

// Portable backend of the DE10-Lite clock contract: the same 25 MHz output
// and lock status as the Intel PLL backend, produced without a vendor model.
// It divides the 50 MHz reference by two and reports lock three reference
// edges after reset release.
//
// This file and n2d_de10_lite_clock_intel.sv define the same module name.
// Simulation targets list this file; the Quartus source list selects the
// Intel file. The board top never selects a backend itself.
/* verilator lint_off DECLFILENAME */
module n2d_de10_lite_clock (
    input logic reference_clk_i,
    input logic reset_i,
    output logic clk_25_o,
    output logic locked_o
);
    logic clock_q;
    logic [2:0] lock_q;
    logic clock_q_next;
    assign clock_q_next = ~clock_q;
    `N2A_DFF_ARST_VAL(clock_q, clock_q_next, reference_clk_i, reset_i, 1'b0)
    logic [2:0] lock_q_next;
    assign lock_q_next = ({lock_q[1:0], 1'b1});
    `N2A_DFF_ARST_VAL(lock_q, lock_q_next, reference_clk_i, reset_i, '0)
    assign clk_25_o = clock_q;
    assign locked_o = lock_q[2];
endmodule
/* verilator lint_on DECLFILENAME */
