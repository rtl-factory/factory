// Intel MAX 10 M9K backend of the common same-clock true-dual-port RAM
// contract. It instantiates the vendor `altsyncram` primitive in
// BIDIR_DUAL_PORT mode (two read/write ports, one clock) with same-port
// read-during-write OLD_DATA on both ports, mixed-port read-during-write
// OLD_DATA and unregistered outputs, which is the exact one-edge read-first
// behavior of the portable backend on every port relationship.
//
// This file and n2a_tdp_ram_portable.sv define the same module name. Only a
// Quartus source list selects this file; it never enters a Verilator product
// target. Verilator lint of the wiring uses the DV-only port stub in
// src/common/dv/memory/. The owning contract, parameter rationale and the
// Quartus fitter evidence obligation are in
// wiki/src/common/memory/HAS.md.
// The selection boundary needs one module name in two differently named
// files, so the filename lint is waived for this file only.
/* verilator lint_off DECLFILENAME */
`include "src/common/rtl/pkg/macros.vh"

module n2a_tdp_ram #(
    parameter int unsigned DATA_WIDTH = 32,
    parameter int unsigned DEPTH = 256,
    // Vendor selection facts. The DE10-Lite consumers use the defaults; another
    // Intel board wrapper may override them without changing the contract.
    parameter INTEL_DEVICE_FAMILY = "MAX 10",
    parameter INTEL_RAM_BLOCK_TYPE = "M9K"
) (
    input  logic                                             clk_i,
    input  logic                                             a_write_enable_i,
    input  logic [((DEPTH < 2) ? 1 : $clog2(DEPTH))-1:0]    a_address_i,
    input  logic [((DATA_WIDTH < 1) ? 1 : DATA_WIDTH)-1:0]  a_write_data_i,
    output logic [((DATA_WIDTH < 1) ? 1 : DATA_WIDTH)-1:0]  a_read_data_o,
    input  logic                                             b_write_enable_i,
    input  logic [((DEPTH < 2) ? 1 : $clog2(DEPTH))-1:0]    b_address_i,
    input  logic [((DATA_WIDTH < 1) ? 1 : DATA_WIDTH)-1:0]  b_write_data_i,
    output logic [((DATA_WIDTH < 1) ? 1 : DATA_WIDTH)-1:0]  b_read_data_o
);
    // Why: Quartus Lite rejects a localparam in the parameter port list, so
    // the address ports derive their width from DEPTH and this reads it back.
    localparam int unsigned ADDR_WIDTH = $bits(a_address_i);
    localparam int unsigned IMPLEMENTATION_DATA_WIDTH =
        (DATA_WIDTH < 1) ? 1 : DATA_WIDTH;
    localparam bit DEPTH_IS_POWER_OF_TWO =
        (DEPTH >= 2) && ((DEPTH & (DEPTH - 1)) == 0);

    `ASSERT_PARAM(g_invalid_data_width, DATA_WIDTH >= 1,
        "n2a_tdp_ram DATA_WIDTH must be at least 1")
    `ASSERT_PARAM(g_invalid_depth, DEPTH_IS_POWER_OF_TWO,
        "n2a_tdp_ram DEPTH must be a power of two and at least 2")

    // Why: the only pin left unconnected is the output eccstatus, exactly as
    // Intel-generated wrappers leave it; every functional BIDIR_DUAL_PORT pin is
    // connected on both ports. Only the DV port-stub lint sees the missing pin.
    /* verilator lint_off PINMISSING */
    altsyncram #(
        .operation_mode("BIDIR_DUAL_PORT"),
        .intended_device_family(INTEL_DEVICE_FAMILY),
        .ram_block_type(INTEL_RAM_BLOCK_TYPE),
        .lpm_type("altsyncram"),
        .width_a(IMPLEMENTATION_DATA_WIDTH),
        .widthad_a(ADDR_WIDTH),
        .numwords_a(DEPTH),
        .width_byteena_a(1),
        .outdata_reg_a("UNREGISTERED"),
        .read_during_write_mode_port_a("OLD_DATA"),
        .width_b(IMPLEMENTATION_DATA_WIDTH),
        .widthad_b(ADDR_WIDTH),
        .numwords_b(DEPTH),
        .width_byteena_b(1),
        .address_reg_b("CLOCK0"),
        .indata_reg_b("CLOCK0"),
        .wrcontrol_wraddress_reg_b("CLOCK0"),
        .byteena_reg_b("CLOCK0"),
        .rdcontrol_reg_b("CLOCK0"),
        .outdata_reg_b("UNREGISTERED"),
        .read_during_write_mode_port_b("OLD_DATA"),
        .read_during_write_mode_mixed_ports("OLD_DATA"),
        .clock_enable_input_a("BYPASS"),
        .clock_enable_output_a("BYPASS"),
        .clock_enable_input_b("BYPASS"),
        .clock_enable_output_b("BYPASS"),
        .init_file("UNUSED"),
        .power_up_uninitialized("FALSE")
    ) u_ram (
        .clock0(clk_i),
        .wren_a(a_write_enable_i),
        .address_a(a_address_i),
        .data_a(a_write_data_i),
        .q_a(a_read_data_o),
        .wren_b(b_write_enable_i),
        .address_b(b_address_i),
        .data_b(b_write_data_i),
        .q_b(b_read_data_o),
        // Ref: Intel altsyncram documentation: inactive levels of unused inputs.
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
