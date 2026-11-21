// DV-only port and parameter stub for the Intel `altsyncram` primitive.
//
// This is NOT a vendor simulation model and never enters a product target or
// a Quartus source list. It exists so Verilator can lint the common Intel
// memory backends and drive their public ports through the same contract
// tests as the portable backends. It rejects, at elaboration, every
// parameter combination outside the five shapes the common backends are
// allowed to request: SINGLE_PORT with port-A OLD_DATA; single-clock
// DUAL_PORT (port B on CLOCK0) with mixed-port OLD_DATA; dual-clock
// DUAL_PORT (port B registered on CLOCK1) with mixed-port DONT_CARE;
// single-clock BIDIR_DUAL_PORT (every port-B register on CLOCK0) with
// OLD_DATA on both ports and on the mixed relationship; and ROM with an
// init_file and PORT_A layout. All have unregistered outputs on a
// MAX 10 M9K. Its behavior is each contract's semantics (read-first on
// every port, or a simulator-ordered colliding read for two clocks, or fixed
// contents loaded with $readmemh so the stub target sees the same hex as the
// portable backend); it does not claim to reproduce any other vendor
// behavior. Vendor mapping is proven only by Quartus synthesis evidence.
// The stub must carry the vendor module name, not this file's name.
/* verilator lint_off DECLFILENAME */
/* verilator lint_off UNUSEDSIGNAL */
/* verilator lint_off UNUSEDPARAM */
module altsyncram #(
    parameter string operation_mode = "BIDIR_DUAL_PORT",
    parameter string intended_device_family = "Stratix",
    parameter string ram_block_type = "AUTO",
    parameter string lpm_type = "altsyncram",
    parameter int width_a = 1,
    parameter int widthad_a = 1,
    parameter int numwords_a = 0,
    parameter int width_byteena_a = 1,
    parameter string outdata_reg_a = "UNREGISTERED",
    parameter string read_during_write_mode_port_a = "NEW_DATA_NO_NBE_READ",
    parameter int width_b = 1,
    parameter int widthad_b = 1,
    parameter int numwords_b = 0,
    parameter int width_byteena_b = 1,
    parameter string address_reg_b = "CLOCK1",
    parameter string rdcontrol_reg_b = "CLOCK1",
    // Port-B write-side registers (vendor defaults); only BIDIR_DUAL_PORT
    // requests them, on CLOCK0.
    parameter string indata_reg_b = "CLOCK1",
    parameter string wrcontrol_wraddress_reg_b = "CLOCK1",
    parameter string byteena_reg_b = "CLOCK1",
    parameter string outdata_reg_b = "UNREGISTERED",
    parameter string read_during_write_mode_port_b = "NEW_DATA_NO_NBE_READ",
    parameter string read_during_write_mode_mixed_ports = "DONT_CARE",
    parameter string clock_enable_input_a = "NORMAL",
    parameter string clock_enable_output_a = "NORMAL",
    parameter string clock_enable_input_b = "NORMAL",
    parameter string clock_enable_output_b = "NORMAL",
    parameter string init_file = "UNUSED",
    parameter string init_file_layout = "UNUSED",
    parameter string power_up_uninitialized = "FALSE",
    parameter int width_eccstatus = 3
) (
    input  logic                        wren_a,
    input  logic                        wren_b,
    input  logic                        rden_a,
    input  logic                        rden_b,
    input  logic [width_a-1:0]          data_a,
    input  logic [width_b-1:0]          data_b,
    input  logic [widthad_a-1:0]        address_a,
    input  logic [widthad_b-1:0]        address_b,
    input  logic                        clock0,
    input  logic                        clock1,
    input  logic                        clocken0,
    input  logic                        clocken1,
    input  logic                        clocken2,
    input  logic                        clocken3,
    input  logic                        aclr0,
    input  logic                        aclr1,
    input  logic [width_byteena_a-1:0]  byteena_a,
    input  logic [width_byteena_b-1:0]  byteena_b,
    input  logic                        addressstall_a,
    input  logic                        addressstall_b,
    output logic [width_a-1:0]          q_a,
    output logic [width_b-1:0]          q_b,
    output logic [width_eccstatus-1:0]  eccstatus
);
    localparam bit SINGLE_PORT = (operation_mode == "SINGLE_PORT");
    localparam bit DUAL_PORT = (operation_mode == "DUAL_PORT");
    localparam bit ROM = (operation_mode == "ROM");
    localparam bit BIDIR = (operation_mode == "BIDIR_DUAL_PORT");
    // Any mode with a live port B (read-only in DUAL_PORT, read/write in
    // BIDIR_DUAL_PORT).
    localparam bit TWO_PORT = DUAL_PORT || BIDIR;
    // Port B registered on clock1 is the vendor dual-clock mode; on clock0 it
    // is the single-clock mode. The two are distinct contracts here. The
    // true-dual-port contract is single-clock only.
    localparam bit DUAL_CLOCK = DUAL_PORT && (address_reg_b == "CLOCK1");

    generate
        if (!(SINGLE_PORT || DUAL_PORT || BIDIR || ROM)) begin : g_unsupported_mode
            initial $fatal(1, "altsyncram stub supports only SINGLE_PORT or DUAL_PORT or ROM or BIDIR_DUAL_PORT");
        end
        if (intended_device_family != "MAX 10") begin : g_unsupported_family
            initial $fatal(1, "altsyncram stub supports only intended_device_family MAX 10");
        end
        if ((ram_block_type != "M9K") && (ram_block_type != "AUTO")) begin : g_unsupported_block
            initial $fatal(1, "altsyncram stub supports only ram_block_type M9K or AUTO");
        end
        if ((numwords_a != (1 << widthad_a)) || (width_a < 1)) begin : g_invalid_geometry_a
            initial $fatal(1, "altsyncram stub requires numwords_a == 2**widthad_a and width_a >= 1");
        end
        if (outdata_reg_a != "UNREGISTERED") begin : g_registered_output_a
            initial $fatal(1, "altsyncram stub requires outdata_reg_a UNREGISTERED (one-edge latency)");
        end
        if (width_byteena_a != 1) begin : g_byteena
            initial $fatal(1, "altsyncram stub supports only full-word writes (width_byteena_a == 1)");
        end
        if ((clock_enable_input_a != "BYPASS") || (clock_enable_output_a != "BYPASS")) begin : g_clock_enable_a
            initial $fatal(1, "altsyncram stub requires port-A clock enables BYPASS (clocken pins are tied)");
        end
        if (TWO_PORT && ((clock_enable_input_b != "BYPASS") || (clock_enable_output_b != "BYPASS"))) begin : g_clock_enable_b
            initial $fatal(1, "altsyncram stub requires port-B clock enables BYPASS in DUAL_PORT and BIDIR_DUAL_PORT modes");
        end
        if (BIDIR && ((read_during_write_mode_port_a != "OLD_DATA") || (read_during_write_mode_port_b != "OLD_DATA"))) begin : g_bidir_port_rdw
            initial $fatal(1, "altsyncram stub requires OLD_DATA on both ports in BIDIR_DUAL_PORT mode");
        end
        if (BIDIR && (read_during_write_mode_mixed_ports != "OLD_DATA")) begin : g_bidir_mixed_rdw
            initial $fatal(1, "altsyncram stub requires mixed-port OLD_DATA in BIDIR_DUAL_PORT mode");
        end
        if (BIDIR && ((address_reg_b != "CLOCK0") || (rdcontrol_reg_b != "CLOCK0") || (indata_reg_b != "CLOCK0") ||
                      (wrcontrol_wraddress_reg_b != "CLOCK0") || (byteena_reg_b != "CLOCK0"))) begin : g_bidir_port_b_clock
            initial $fatal(1, "altsyncram stub requires every port-B register on CLOCK0 in BIDIR_DUAL_PORT mode (single clock)");
        end
        if (BIDIR && (width_byteena_b != 1)) begin : g_bidir_byteena_b
            initial $fatal(1, "altsyncram stub supports only full-word port-B writes (width_byteena_b == 1)");
        end
        if (power_up_uninitialized != "FALSE") begin : g_power_up
            initial $fatal(1, "altsyncram stub requires power_up_uninitialized FALSE");
        end
        if (!ROM && (init_file != "UNUSED")) begin : g_init_file
            initial $fatal(1, "altsyncram stub forbids init_file outside ROM mode");
        end
        if (!ROM && (init_file_layout != "UNUSED")) begin : g_init_file_layout
            initial $fatal(1, "altsyncram stub forbids init_file_layout outside ROM mode");
        end
        if (ROM && ((init_file == "UNUSED") || (init_file == ""))) begin : g_rom_without_image
            initial $fatal(1, "altsyncram stub requires a non-empty init_file in ROM mode");
        end
        if (ROM && (init_file_layout != "PORT_A")) begin : g_rom_layout
            initial $fatal(1, "altsyncram stub requires init_file_layout PORT_A in ROM mode");
        end
        if (SINGLE_PORT && (read_during_write_mode_port_a != "OLD_DATA")) begin : g_port_a_rdw
            initial $fatal(1, "altsyncram stub requires port-A OLD_DATA in SINGLE_PORT mode");
        end
        if (DUAL_PORT && !DUAL_CLOCK && (read_during_write_mode_mixed_ports != "OLD_DATA")) begin : g_mixed_rdw
            initial $fatal(1, "altsyncram stub requires mixed-port OLD_DATA in single-clock DUAL_PORT mode");
        end
        if (DUAL_PORT && !DUAL_CLOCK && ((address_reg_b != "CLOCK0") || (rdcontrol_reg_b != "CLOCK0"))) begin : g_port_b_clock
            initial $fatal(1, "altsyncram stub requires port B entirely on CLOCK0 (single clock mode) or CLOCK1 (dual clock mode)");
        end
        if (DUAL_CLOCK && (read_during_write_mode_mixed_ports != "DONT_CARE")) begin : g_dual_clock_rdw
            initial $fatal(1, "altsyncram stub requires mixed-port DONT_CARE in dual-clock DUAL_PORT mode (collision-undefined)");
        end
        if (DUAL_CLOCK && (rdcontrol_reg_b != "CLOCK1")) begin : g_dual_clock_rdcontrol
            initial $fatal(1, "altsyncram stub requires rdcontrol_reg_b CLOCK1 in dual-clock DUAL_PORT mode");
        end
        if (TWO_PORT && (outdata_reg_b != "UNREGISTERED")) begin : g_registered_output_b
            initial $fatal(1, "altsyncram stub requires outdata_reg_b UNREGISTERED (one-edge latency)");
        end
        if (TWO_PORT && ((width_b != width_a) || (widthad_b != widthad_a) ||
                         (numwords_b != numwords_a))) begin : g_mixed_width
            initial $fatal(1, "altsyncram stub does not model mixed-width ports");
        end
    endgenerate

    logic [width_a-1:0] memory [0:numwords_a-1];

    assign eccstatus = '0;

    generate
        if (ROM) begin : g_rom_contents
            // Why: the stub target is fed the same Verilog-hex image the portable
            // backend reads, so one contract test covers both files. A Quartus
            // configuration passes a staged .mif here instead; the stub never
            // parses that format. A missing image name is already rejected by
            // g_rom_without_image; not loading it here keeps that rejection the
            // only diagnostic of the adverse probe.
            if ((init_file != "UNUSED") && (init_file != "")) begin : g_rom_image
                initial begin
                    $readmemh(init_file, memory);
                end
            end else begin : g_rom_no_image
                // Why: reached only in the rejected ROM-without-image shape, where
                // g_rom_without_image ends the simulation at time zero. This dummy
                // driver exists solely so `memory` is driven and lint cannot abort
                // the compile before that rejection; it is not a ROM zero-fill policy
                // and no accepted configuration elaborates it.
                initial begin
                    for (int n2a_i = 0; n2a_i < numwords_a; n2a_i++) begin
                        memory[n2a_i] = '0;
                    end
                end
            end
            always_ff @(posedge clock0) begin
                q_a <= memory[address_a];
            end
        end else begin : g_ram_port_a
            always_ff @(posedge clock0) begin
                q_a <= memory[address_a];
                if (wren_a) begin
                    memory[address_a] <= data_a;
                end
            end
        end
    endgenerate

    // Why: in dual-clock mode an overlapping same-word access resolves in
    // simulator delta order, which is exactly the unspecified value the
    // collision-undefined contract permits and no test may assert. In
    // BIDIR_DUAL_PORT mode two enabled writes to one address in one edge
    // likewise have no contract winner and no test asserts one.
    generate
        if (DUAL_CLOCK) begin : g_read_port_b_clock1
            always_ff @(posedge clock1) begin
                q_b <= memory[address_b];
            end
        end else if (BIDIR) begin : g_ram_port_b
            always_ff @(posedge clock0) begin
                q_b <= memory[address_b];
                if (wren_b) begin
                    memory[address_b] <= data_b;
                end
            end
        end else if (DUAL_PORT) begin : g_read_port_b
            always_ff @(posedge clock0) begin
                if (rden_b) q_b <= memory[address_b];
            end
        end else begin : g_no_port_b
            assign q_b = '0;
        end
    endgenerate
endmodule
/* verilator lint_on UNUSEDPARAM */
/* verilator lint_on UNUSEDSIGNAL */
