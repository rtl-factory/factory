`include "src/common/rtl/pkg/macros.vh"

module n2a_sync_ram #(
  parameter int unsigned DATA_WIDTH = 32,
  parameter int unsigned DEPTH = 256,
  parameter int unsigned ADDR_WIDTH = $clog2(DEPTH)
) (
  input  logic                                             clk_i,
  input  logic                                             write_enable_i,
  input  logic [((ADDR_WIDTH < 1) ? 1 : ADDR_WIDTH)-1:0]  address_i,
  input  logic [((DATA_WIDTH < 1) ? 1 : DATA_WIDTH)-1:0]  write_data_i,
  output logic [((DATA_WIDTH < 1) ? 1 : DATA_WIDTH)-1:0]  read_data_o
);
  localparam int unsigned IMPLEMENTATION_DATA_WIDTH =
      (DATA_WIDTH < 1) ? 1 : DATA_WIDTH;
  localparam int unsigned IMPLEMENTATION_DEPTH = (DEPTH < 2) ? 2 : DEPTH;
  localparam int unsigned REQUIRED_ADDR_WIDTH =
      (DEPTH < 2) ? 1 : $clog2(DEPTH);
  localparam bit DEPTH_IS_POWER_OF_TWO =
      (DEPTH >= 2) && ((DEPTH & (DEPTH - 1)) == 0);

  logic [IMPLEMENTATION_DATA_WIDTH-1:0] memory [0:IMPLEMENTATION_DEPTH-1];
  logic [REQUIRED_ADDR_WIDTH-1:0] implementation_address;

  // This explicit size keeps invalid ADDR_WIDTH configurations lint-clean
  // until their elaboration diagnostic fires. It is a no-op when supported.
  assign implementation_address = REQUIRED_ADDR_WIDTH'(address_i);

  `N2A_ASSERT_PARAM(g_invalid_data_width, DATA_WIDTH >= 1,
      "n2a_sync_ram DATA_WIDTH must be at least 1")
  `N2A_ASSERT_PARAM(g_invalid_depth, DEPTH_IS_POWER_OF_TWO,
      "n2a_sync_ram DEPTH must be a power of two and at least 2")
  `N2A_ASSERT_PARAM(g_invalid_addr_width, ADDR_WIDTH == REQUIRED_ADDR_WIDTH,
      "n2a_sync_ram ADDR_WIDTH must equal $clog2(DEPTH)")

  // Separate read and write ports on one array: nonblocking semantics make a
  // same-edge collision read-first (old data) without bypass logic.
  `N2A_MEM_READ(read_data_o, memory, clk_i, implementation_address)
  `N2A_MEM_WRITE(memory, clk_i, write_enable_i, implementation_address,
      write_data_i)
endmodule
