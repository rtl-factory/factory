// Intel MAX 10 M9K backend of the common one-clock single-port RAM contract
// owned by wiki/src/common/memory/HAS.md. It instantiates `altsyncram` in
// SINGLE_PORT mode with port-A read-during-write OLD_DATA and an unregistered
// output: one-edge registered read on every edge, optional full-word write,
// old word on a same-address collision, no reset or initialization.
//
// This file and n2a_sync_ram.sv define the same module name. Only a Quartus
// source list selects this file; Verilator product targets keep the portable
// n2a_sync_ram.sv. Verilator lint of the wiring uses the DV-only port stub in
// src/common/dv/memory/. Parameter rationale and the synthesis evidence
// obligation are in wiki/src/common/memory/HAS.md.
// The selection boundary needs one module name in two differently named
// files, so the filename lint is waived for this file only.
/* verilator lint_off DECLFILENAME */
`include "src/common/rtl/pkg/macros.vh"

module n2a_sync_ram #(
  parameter int unsigned DATA_WIDTH = 32,
  parameter int unsigned DEPTH = 256,
  parameter int unsigned ADDR_WIDTH = $clog2(DEPTH),
  parameter INTEL_DEVICE_FAMILY = "MAX 10",
  parameter INTEL_RAM_BLOCK_TYPE = "M9K"
) (
  input  logic                                             clk_i,
  input  logic                                             write_enable_i,
  input  logic [((ADDR_WIDTH < 1) ? 1 : ADDR_WIDTH)-1:0]  address_i,
  input  logic [((DATA_WIDTH < 1) ? 1 : DATA_WIDTH)-1:0]  write_data_i,
  output logic [((DATA_WIDTH < 1) ? 1 : DATA_WIDTH)-1:0]  read_data_o
);
  localparam int unsigned IMPLEMENTATION_DATA_WIDTH =
      (DATA_WIDTH < 1) ? 1 : DATA_WIDTH;
  localparam int unsigned REQUIRED_ADDR_WIDTH =
      (DEPTH < 2) ? 1 : $clog2(DEPTH);
  localparam bit DEPTH_IS_POWER_OF_TWO =
      (DEPTH >= 2) && ((DEPTH & (DEPTH - 1)) == 0);

  `N2A_ASSERT_PARAM(g_invalid_data_width, DATA_WIDTH >= 1,
      "n2a_sync_ram DATA_WIDTH must be at least 1")
  `N2A_ASSERT_PARAM(g_invalid_depth, DEPTH_IS_POWER_OF_TWO,
      "n2a_sync_ram DEPTH must be a power of two and at least 2")
  `N2A_ASSERT_PARAM(g_invalid_addr_width, ADDR_WIDTH == REQUIRED_ADDR_WIDTH,
      "n2a_sync_ram ADDR_WIDTH must equal $clog2(DEPTH)")

  // Pins that do not exist in SINGLE_PORT mode stay unconnected, exactly as
  // Intel-generated wrappers leave them: the port-B inputs address_b and
  // data_b and the outputs q_b and eccstatus. Every input that the mode uses
  // is connected; the remaining vendor inputs are tied to their inactive
  // levels. Only the DV port-stub lint sees missing pins.
  /* verilator lint_off PINMISSING */
  altsyncram #(
    .operation_mode("SINGLE_PORT"),
    .intended_device_family(INTEL_DEVICE_FAMILY),
    .ram_block_type(INTEL_RAM_BLOCK_TYPE),
    .lpm_type("altsyncram"),
    .width_a(IMPLEMENTATION_DATA_WIDTH),
    .widthad_a(REQUIRED_ADDR_WIDTH),
    .numwords_a(DEPTH),
    .width_byteena_a(1),
    .outdata_reg_a("UNREGISTERED"),
    .read_during_write_mode_port_a("OLD_DATA"),
    .clock_enable_input_a("BYPASS"),
    .clock_enable_output_a("BYPASS"),
    .init_file("UNUSED"),
    .power_up_uninitialized("FALSE")
  ) u_ram (
    .clock0(clk_i),
    .wren_a(write_enable_i),
    .address_a(address_i),
    .data_a(write_data_i),
    .q_a(read_data_o),
    // Unused vendor inputs are tied to their documented inactive levels.
    .wren_b(1'b0),
    .rden_a(1'b1),
    .rden_b(1'b1),
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
