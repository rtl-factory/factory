`include "src/common/rtl/pkg/macros.vh"

module n2m_dual_port_ram #(
  parameter int unsigned DATA_WIDTH = 32,
  parameter int unsigned DEPTH = 256,
  parameter int unsigned ADDR_WIDTH = $clog2(DEPTH),
  parameter int unsigned BYTE_LANES = DATA_WIDTH / 8,
  parameter bit DUAL_CLOCK = 1'b0
) (
  input  logic                   clk_a_i,
  input  logic                   reset_a_i,
  input  logic                   read_a_i,
  input  logic                   write_a_i,
  input  logic [ADDR_WIDTH-1:0]  address_a_i,
  input  logic [DATA_WIDTH-1:0]  write_data_a_i,
  input  logic [BYTE_LANES-1:0]  byte_enable_a_i,
  output logic [DATA_WIDTH-1:0]  read_data_a_o,
  output logic                   read_valid_a_o,

  input  logic                   clk_b_i,
  input  logic                   reset_b_i,
  input  logic                   read_b_i,
  input  logic [ADDR_WIDTH-1:0]  address_b_i,
  output logic [DATA_WIDTH-1:0]  read_data_b_o,
  output logic                   read_valid_b_o
);
  localparam int unsigned IMPLEMENTATION_DATA_WIDTH =
      (DATA_WIDTH < 8) ? 8 : DATA_WIDTH;
  localparam int unsigned IMPLEMENTATION_DEPTH = (DEPTH < 2) ? 2 : DEPTH;
  localparam int unsigned REQUIRED_ADDR_WIDTH =
      (DEPTH < 2) ? 1 : $clog2(DEPTH);
  localparam bit DEPTH_IS_POWER_OF_TWO =
      (DEPTH >= 2) && ((DEPTH & (DEPTH - 1)) == 0);

  logic [IMPLEMENTATION_DATA_WIDTH-1:0] memory [0:IMPLEMENTATION_DEPTH-1];
  logic write_a;

  // Why: a request under reset must not change the store or the outputs, so
  // reset suppresses it: no write, no data update (the valid register below is
  // reset, so the outputs hold), valid low.
  assign write_a = write_a_i && !reset_a_i;

  // Why: the array is the conventional inferred true-dual-port template: port A
  // writes enabled lanes and both ports read their address on every edge,
  // with no read enable on either output. Block RAM (MAX 10 M9K) supports no
  // per-port output enable in true-dual-port mode, so the contract's read
  // enables, hold and same-port bypass live in the registers below instead
  // of in the array's output register. Both array reads are read-first.
  logic [DATA_WIDTH-1:0] array_a, array_b;
  `MEM_WRITE_LANES(memory, clk_a_i, write_a, address_a_i, write_data_a_i,
      byte_enable_a_i, 8)
  `MEM_READ(array_a, memory, clk_a_i, address_a_i)

  // Why: the array read is read-first, so port A gets its contract's
  // write-first result from this bypass; it can return the full input word
  // because the contract enables all lanes on a port-A write.
  logic                  bypass_a_q, bypass_a_d;
  logic [DATA_WIDTH-1:0] write_data_a_q, held_a_q;
  assign bypass_a_d = write_a_i;
  `DFF_RST(read_valid_a_o, read_a_i, clk_a_i, reset_a_i)
  `DFF(bypass_a_q, bypass_a_d, clk_a_i)
  `DFF(write_data_a_q, write_data_a_i, clk_a_i)
  assign read_data_a_o = !read_valid_a_o ? held_a_q :
                         (bypass_a_q ? write_data_a_q : array_a);
  `DFF(held_a_q, read_data_a_o, clk_a_i)

  logic [DATA_WIDTH-1:0] held_b_q;
  generate
    if (DUAL_CLOCK) begin : g_dual_clock_b
      `DFF_RST(read_valid_b_o, read_b_i, clk_b_i, reset_b_i)
      `MEM_READ(array_b, memory, clk_b_i, address_b_i)
      `DFF(held_b_q, read_data_b_o, clk_b_i)

      // Why: a dual-clock access to the word being written is outside the contract.
      // Checking both observation edges catches an overlapping request window;
      // owners must arbitrate or transfer ownership before that window exists.
      `ASSERT(dual_clock_collision_a, clk_a_i,
          (reset_a_i || reset_b_i),
          !(write_a_i && read_b_i && (address_a_i == address_b_i)))
      `ASSERT(dual_clock_collision_b, clk_b_i,
          (reset_a_i || reset_b_i),
          !(write_a_i && read_b_i && (address_a_i == address_b_i)))
      `ASSERT_KNOWN(b_control_known, clk_b_i, reset_b_i,
          {read_b_i, address_b_i})
    end else begin : g_single_clock_b
      // Why: the A write port and this read port both use nonblocking assignments
      // on clk_a_i, so a coincident same-address B read returns the pre-write
      // word (read-first).
      `DFF_RST(read_valid_b_o, read_b_i, clk_a_i, reset_b_i)
      `MEM_READ(array_b, memory, clk_a_i, address_b_i)
      `DFF(held_b_q, read_data_b_o, clk_a_i)

      `ASSERT_KNOWN(b_control_known, clk_a_i, reset_b_i,
          {read_b_i, address_b_i})
    end
  endgenerate
  assign read_data_b_o = read_valid_b_o ? array_b : held_b_q;

  `ASSERT_PARAM(g_invalid_data_width,
      (DATA_WIDTH >= 8) && ((DATA_WIDTH % 8) == 0),
      "n2m_dual_port_ram DATA_WIDTH must be a positive byte multiple")
  `ASSERT_PARAM(g_invalid_depth, DEPTH_IS_POWER_OF_TWO,
      "n2m_dual_port_ram DEPTH must be a power of two and at least 2")
  `ASSERT_PARAM(g_invalid_addr_width, ADDR_WIDTH == REQUIRED_ADDR_WIDTH,
      "n2m_dual_port_ram ADDR_WIDTH must equal $clog2(DEPTH)")
  `ASSERT_PARAM(g_invalid_byte_lanes, BYTE_LANES == (DATA_WIDTH / 8),
      "n2m_dual_port_ram BYTE_LANES must equal DATA_WIDTH/8")

  `ASSERT_KNOWN(a_control_known, clk_a_i, reset_a_i,
      {read_a_i, write_a_i, address_a_i, byte_enable_a_i})
  `ASSERT(a_write_data_known, clk_a_i, reset_a_i,
      !write_a_i || !$isunknown(write_data_a_i))
  `ASSERT(a_read_write_all_lanes, clk_a_i, reset_a_i,
      !(read_a_i && write_a_i) || (&byte_enable_a_i))
endmodule
