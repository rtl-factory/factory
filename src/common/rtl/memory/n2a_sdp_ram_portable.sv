// Portable backend of the common same-clock simple-dual-port RAM contract.
//
// This file and n2a_sdp_ram_intel.sv define the same module name. A build
// selects exactly one of them: Verilator targets list this file; a Quartus
// project lists the Intel file. Product RTL instantiates `n2a_sdp_ram` and
// never selects a backend itself. The owning contract is
// wiki/src/common/memory/HAS.md.
// The selection boundary needs one module name in two differently named
// files, so the filename lint is waived for this file only.
/* verilator lint_off DECLFILENAME */
`include "src/common/rtl/pkg/macros.vh"

module n2a_sdp_ram_enabled #(
  parameter int unsigned DATA_WIDTH = 32,
  parameter int unsigned DEPTH = 256,
  parameter int unsigned ADDR_WIDTH = $clog2(DEPTH)
) (
  input  logic                                             clk_i,
  input  logic                                             read_enable_i,
  input  logic                                             write_enable_i,
  input  logic [((ADDR_WIDTH < 1) ? 1 : ADDR_WIDTH)-1:0]  write_address_i,
  input  logic [((DATA_WIDTH < 1) ? 1 : DATA_WIDTH)-1:0]  write_data_i,
  input  logic [((ADDR_WIDTH < 1) ? 1 : ADDR_WIDTH)-1:0]  read_address_i,
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
  logic [REQUIRED_ADDR_WIDTH-1:0] write_address;
  logic [REQUIRED_ADDR_WIDTH-1:0] read_address;

  // Explicit sizes keep an invalid ADDR_WIDTH probe lint-clean until its
  // elaboration diagnostic fires. Both are no-ops for supported values.
  assign write_address = REQUIRED_ADDR_WIDTH'(write_address_i);
  assign read_address = REQUIRED_ADDR_WIDTH'(read_address_i);

  `N2A_ASSERT_PARAM(g_invalid_data_width, DATA_WIDTH >= 1,
      "n2a_sdp_ram DATA_WIDTH must be at least 1")
  `N2A_ASSERT_PARAM(g_invalid_depth, DEPTH_IS_POWER_OF_TWO,
      "n2a_sdp_ram DEPTH must be a power of two and at least 2")
  `N2A_ASSERT_PARAM(g_invalid_addr_width, ADDR_WIDTH == REQUIRED_ADDR_WIDTH,
      "n2a_sdp_ram ADDR_WIDTH must equal $clog2(DEPTH)")

  // One read port and one write port on one array. Nonblocking semantics make
  // a same-edge same-address collision read-first: the output captures the
  // pre-edge word and the write lands afterwards. This is the conventional
  // inferred simple-dual-port template. Disabled reads retain the output;
  // writes remain independent. There is no reset, initialization or bypass.
  `N2A_MEM_READ_EN(read_data_o, memory, clk_i, read_enable_i, read_address)
  `N2A_MEM_WRITE(memory, clk_i, write_enable_i, write_address, write_data_i)
endmodule

// Existing consumers retain the always-active one-edge read contract.
module n2a_sdp_ram #(
  parameter int unsigned DATA_WIDTH = 32,
  parameter int unsigned DEPTH = 256,
  parameter int unsigned ADDR_WIDTH = $clog2(DEPTH)
) (
  input logic clk_i,
  input logic write_enable_i,
  input logic [((ADDR_WIDTH < 1) ? 1 : ADDR_WIDTH)-1:0] write_address_i,
  input logic [((DATA_WIDTH < 1) ? 1 : DATA_WIDTH)-1:0] write_data_i,
  input logic [((ADDR_WIDTH < 1) ? 1 : ADDR_WIDTH)-1:0] read_address_i,
  output logic [((DATA_WIDTH < 1) ? 1 : DATA_WIDTH)-1:0] read_data_o
);
  n2a_sdp_ram_enabled #(
    .DATA_WIDTH(DATA_WIDTH), .DEPTH(DEPTH), .ADDR_WIDTH(ADDR_WIDTH)
  ) u_enabled (
    .clk_i(clk_i), .read_enable_i(1'b1),
    .write_enable_i(write_enable_i), .write_address_i(write_address_i),
    .write_data_i(write_data_i), .read_address_i(read_address_i),
    .read_data_o(read_data_o)
  );
endmodule
