`include "src/common/rtl/pkg/macros.vh"

// Game Boy rate enables from the 25 MHz system clock. One fractional
// accumulator (65,536/390,625 = 4,194,304/25,000,000) emits the dot enable;
// every fourth dot is the machine-cycle enable, so the two enables are
// phase-locked by construction: a machine pulse always coincides with a dot
// pulse and exactly four dot pulses separate consecutive machine pulses.
module n2m_timebase (
  input  logic clk_i,
  input  logic reset_i,
  output logic dot_enable_o,
  output logic machine_cycle_enable_o
);
  localparam logic [18:0] PHASE_INCREMENT = 19'd65536;
  localparam logic [18:0] PHASE_MODULUS = 19'd390625;

  logic [18:0] phase_q;
  logic [18:0] phase_d;
  logic [18:0] phase_sum;
  logic        dot_pulse;
  logic [1:0]  dot_count_q;
  logic [1:0]  dot_count_d;

  always_comb begin
    phase_sum = phase_q + PHASE_INCREMENT;
    if (phase_sum >= PHASE_MODULUS) begin
      phase_d = phase_sum - PHASE_MODULUS;
    end else begin
      phase_d = phase_sum;
    end
    // Dots within the machine cycle: the count wraps on the fourth pulse.
    dot_count_d = dot_count_q;
    if (dot_pulse) begin
      dot_count_d = dot_count_q + 2'd1;
    end
  end

  // Consumers sample the carry on a rising clk_i edge. The accumulator then
  // advances for the following edge; no clock is generated or gated here.
  assign dot_pulse = !reset_i && (phase_sum >= PHASE_MODULUS);
  assign dot_enable_o = dot_pulse;
  assign machine_cycle_enable_o = dot_pulse && (dot_count_q == 2'd3);

  `N2A_DFF_RST(phase_q, phase_d, clk_i, reset_i)
  `N2A_DFF_RST(dot_count_q, dot_count_d, clk_i, reset_i)

  `N2A_ASSERT(n2m_timebase_phase_in_range, clk_i, reset_i,
    phase_q < PHASE_MODULUS)
  `N2A_ASSERT_KNOWN(n2m_timebase_enable_known, clk_i, reset_i,
    {dot_enable_o, machine_cycle_enable_o})
  `N2A_ASSERT(n2m_timebase_machine_is_dot, clk_i, reset_i,
    !machine_cycle_enable_o || dot_enable_o)
endmodule
