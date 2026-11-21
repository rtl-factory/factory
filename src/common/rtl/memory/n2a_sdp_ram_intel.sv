// Intel MAX 10 M9K backend of the common same-clock simple-dual-port RAM
// contract. It instantiates the vendor `altsyncram` primitive in DUAL_PORT
// mode (one write port A, one read port B, one clock) with mixed-port
// read-during-write OLD_DATA and an unregistered output, which is the exact
// one-edge read-first behavior of the portable backend.
//
// This file and n2a_sdp_ram_portable.sv define the same module name. Only a
// Quartus source list selects this file; it never enters a Verilator product
// target. Verilator lint of the wiring uses the DV-only port stub in
// src/common/dv/memory/. The owning contract, parameter rationale and the
// Quartus fitter evidence obligation are in
// wiki/src/common/rtl/memory/mas.md.
// The selection boundary needs one module name in two differently named
// files, so the filename lint is waived for this file only.
/* verilator lint_off DECLFILENAME */
`include "src/common/rtl/pkg/macros.vh"

module n2a_sdp_ram_enabled #(
  parameter int unsigned DATA_WIDTH = 32,
  parameter int unsigned DEPTH = 256,
  parameter int unsigned ADDR_WIDTH = $clog2(DEPTH),
  // Vendor selection facts. The DE10-Lite consumers use the defaults; another
  // Intel board wrapper may override them without changing the contract.
  parameter INTEL_DEVICE_FAMILY = "MAX 10",
  parameter INTEL_RAM_BLOCK_TYPE = "M9K"
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
  localparam int unsigned REQUIRED_ADDR_WIDTH =
      (DEPTH < 2) ? 1 : $clog2(DEPTH);
  localparam bit DEPTH_IS_POWER_OF_TWO =
      (DEPTH >= 2) && ((DEPTH & (DEPTH - 1)) == 0);

  `N2A_ASSERT_PARAM(g_invalid_data_width, DATA_WIDTH >= 1,
      "n2a_sdp_ram DATA_WIDTH must be at least 1")
  `N2A_ASSERT_PARAM(g_invalid_depth, DEPTH_IS_POWER_OF_TWO,
      "n2a_sdp_ram DEPTH must be a power of two and at least 2")
  `N2A_ASSERT_PARAM(g_invalid_addr_width, ADDR_WIDTH == REQUIRED_ADDR_WIDTH,
      "n2a_sdp_ram ADDR_WIDTH must equal $clog2(DEPTH)")

  // Why: pins that do not exist in DUAL_PORT mode stay unconnected, exactly as
  // Intel-generated wrappers leave them: the port-B write data input data_b
  // (port B is read-only) and the outputs q_a and eccstatus. Every input
  // that the mode uses is connected; the remaining vendor inputs are tied
  // to their inactive levels. Only the DV port-stub lint sees missing pins.
  /* verilator lint_off PINMISSING */
  altsyncram #(
    .operation_mode("DUAL_PORT"),
    .intended_device_family(INTEL_DEVICE_FAMILY),
    .ram_block_type(INTEL_RAM_BLOCK_TYPE),
    .lpm_type("altsyncram"),
    .width_a(IMPLEMENTATION_DATA_WIDTH),
    .widthad_a(REQUIRED_ADDR_WIDTH),
    .numwords_a(DEPTH),
    .width_byteena_a(1),
    .outdata_reg_a("UNREGISTERED"),
    .width_b(IMPLEMENTATION_DATA_WIDTH),
    .widthad_b(REQUIRED_ADDR_WIDTH),
    .numwords_b(DEPTH),
    .address_reg_b("CLOCK0"),
    .rdcontrol_reg_b("CLOCK0"),
    .outdata_reg_b("UNREGISTERED"),
    .read_during_write_mode_mixed_ports("OLD_DATA"),
    .clock_enable_input_a("BYPASS"),
    .clock_enable_output_a("BYPASS"),
    .clock_enable_input_b("BYPASS"),
    .clock_enable_output_b("BYPASS"),
    .init_file("UNUSED"),
    .power_up_uninitialized("FALSE")
  ) u_ram (
    .clock0(clk_i),
    .wren_a(write_enable_i),
    .address_a(write_address_i),
    .data_a(write_data_i),
    .address_b(read_address_i),
    .q_b(read_data_o),
    // Ref: Intel altsyncram documentation: inactive levels of unused inputs.
    .wren_b(1'b0),
    .rden_a(1'b1),
    .rden_b(read_enable_i),
    .clock1(1'b1),
    .clocken0(1'b1),
    .clocken1(1'b1),
    .clocken2(1'b1),
    .clocken3(1'b1),
    .aclr0(1'b0),
    .aclr1(1'b0),
    .byteena_a(1'b1),
    .byteena_b(1'b1),
    .addressstall_a(1'b0),
    .addressstall_b(1'b0)
  );
  /* verilator lint_on PINMISSING */
endmodule

// Existing consumers retain the always-active one-edge read contract.
module n2a_sdp_ram #(
  parameter int unsigned DATA_WIDTH = 32,
  parameter int unsigned DEPTH = 256,
  parameter int unsigned ADDR_WIDTH = $clog2(DEPTH),
  parameter INTEL_DEVICE_FAMILY = "MAX 10",
  parameter INTEL_RAM_BLOCK_TYPE = "M9K"
) (
  input logic clk_i,
  input logic write_enable_i,
  input logic [((ADDR_WIDTH < 1) ? 1 : ADDR_WIDTH)-1:0] write_address_i,
  input logic [((DATA_WIDTH < 1) ? 1 : DATA_WIDTH)-1:0] write_data_i,
  input logic [((ADDR_WIDTH < 1) ? 1 : ADDR_WIDTH)-1:0] read_address_i,
  output logic [((DATA_WIDTH < 1) ? 1 : DATA_WIDTH)-1:0] read_data_o
);
  n2a_sdp_ram_enabled #(
    .DATA_WIDTH(DATA_WIDTH), .DEPTH(DEPTH), .ADDR_WIDTH(ADDR_WIDTH),
    .INTEL_DEVICE_FAMILY(INTEL_DEVICE_FAMILY),
    .INTEL_RAM_BLOCK_TYPE(INTEL_RAM_BLOCK_TYPE)
  ) u_enabled (
    .clk_i(clk_i), .read_enable_i(1'b1),
    .write_enable_i(write_enable_i), .write_address_i(write_address_i),
    .write_data_i(write_data_i), .read_address_i(read_address_i),
    .read_data_o(read_data_o)
  );
endmodule
