`default_nettype none
`include "src/common/rtl/pkg/macros.vh"

// Board switch and push-button conditioning: each asynchronous input goes
// through the common two-flop synchronizer, is sampled on a periodic tick
// (SAMPLE_CYCLES system clocks, 1 ms at 25 MHz) and reaches the output only
// when two consecutive samples agree, so contact bounce shorter than one
// sample period never produces a glitch. Inputs are taken active-high; the
// board top inverts active-low keys before this block. Reset drives the
// outputs low and restarts the sampler.
module n2m_board_inputs #(
  parameter int unsigned WIDTH = 9,
  parameter int unsigned SAMPLE_CYCLES = 25000
) (
  input  logic             clk_i,
  input  logic             reset_i,
  input  logic [WIDTH-1:0] raw_i,
  output logic [WIDTH-1:0] level_o
);
  localparam int unsigned COUNTER_WIDTH = $clog2(SAMPLE_CYCLES);
  `ASSERT_PARAM(g_invalid_width, WIDTH >= 1, "n2m_board_inputs WIDTH must be at least 1")
  `ASSERT_PARAM(g_invalid_sample, SAMPLE_CYCLES >= 2,
      "n2m_board_inputs SAMPLE_CYCLES must be at least 2")

  logic [WIDTH-1:0]         synced;
  logic [COUNTER_WIDTH-1:0] counter_q, counter_d;
  logic                     tick;
  logic [WIDTH-1:0]         sample_q, level_q, level_d;

  genvar bit_index;
  generate
  for (bit_index = 0; bit_index < WIDTH; bit_index = bit_index + 1) begin : g_sync
    n2a_cdc_bit_sync u_sync (
      .clk_i   (clk_i),
      .reset_i (reset_i),
      .async_i (raw_i[bit_index]),
      .sync_o  (synced[bit_index])
    );
  end
  endgenerate

  assign tick = (counter_q == COUNTER_WIDTH'(SAMPLE_CYCLES - 1));
  assign counter_d = tick ? '0 : (counter_q + 1'b1);
  `DFF_RST(counter_q, counter_d, clk_i, reset_i)
  `DFF_RST_EN(sample_q, synced, clk_i, tick, reset_i, '0)
  // Why: contact bounce must not reach the joypad as extra presses, so a level
  // counts only once it holds across two sample ticks.
  assign level_d = (synced == sample_q) ? synced : level_q;
  `DFF_RST_EN(level_q, level_d, clk_i, tick, reset_i, '0)
  assign level_o = level_q;

  `ASSERT(counter_bounded, clk_i, reset_i, counter_q < COUNTER_WIDTH'(SAMPLE_CYCLES))
endmodule
