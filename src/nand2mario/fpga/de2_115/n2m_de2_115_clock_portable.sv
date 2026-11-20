`include "src/common/rtl/pkg/macros.vh"

// Portable backend of the nand2mario DE2-115 clock contract: the same
// 25 MHz output and lock status as the Intel PLL backend, produced without a
// vendor model (wiki/src/nand2mario/fpga/HAS.md).
//
// This file and n2m_de2_115_clock_intel.sv define the same module name.
// Simulation targets list this file; the Quartus source list selects the
// Intel file. The board top never selects a backend itself.
/* verilator lint_off DECLFILENAME */
module n2m_de2_115_clock (
  input  logic reference_clk_i,
  input  logic reset_i,
  output logic clk_25_o,
  output logic locked_o
);
  // Behavioral stand-in: divide the 50 MHz reference by two and report
  // lock three reference edges after reset release.
  logic       clock_q, clock_d;
  logic [2:0] lock_q, lock_d;
  assign clock_d = ~clock_q;
  assign lock_d = {lock_q[1:0], 1'b1};
  `N2A_DFF_ARST_VAL(clock_q, clock_d, reference_clk_i, reset_i, 1'b0)
  `N2A_DFF_ARST_VAL(lock_q, lock_d, reference_clk_i, reset_i, '0)
  assign clk_25_o = clock_q;
  assign locked_o = lock_q[2];
endmodule
/* verilator lint_on DECLFILENAME */
