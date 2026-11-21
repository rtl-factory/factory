`default_nettype none
`include "src/common/rtl/pkg/macros.vh"

// Game Boy interrupt-flag register IF ($FF0F) in the 25 MHz domain. Five
// flag bits (0 VBlank, 1 STAT, 2 timer, 3 serial, 4 joypad) collect
// one-cycle set requests from their sources, are written by the CPU through
// the peripheral port (bits [4:0]; bits [7:5] read as 1) and are cleared by
// the CPU's one-hot acknowledge. On one edge an acknowledge clear wins over a
// CPU write, which wins over a source set. The stored flags drive the CPU's
// interrupt_flags_i directly; IE lives in the memory map.
module n2m_interrupts (
    input  logic                               clk_i,
    input  logic                               reset_i,
    // Peripheral port from the memory map; kind is not consulted.
    /* verilator lint_off UNUSEDSIGNAL */
    input  n2m_interfaces_pkg::t_bus_request   io_request_i,
    /* verilator lint_on UNUSEDSIGNAL */
    output n2m_interfaces_pkg::t_bus_response  io_response_o,
    output logic                               io_selected_o,
    // Per-source set requests: a high sample sets that flag on this edge.
    input  logic [4:0]                         source_set_i,
    // CPU acknowledge (one-hot or zero) of the interrupt being entered.
    input  logic [4:0]                         interrupt_ack_i,
    output logic [4:0]                         interrupt_flags_o
);
    localparam logic [15:0] IF_ADDRESS = 16'hFF0F;

    logic [4:0] flags_q, flags_d;
    logic       write_if;

    assign io_selected_o = io_request_i.address == IF_ADDRESS;
    assign write_if = io_request_i.valid && io_selected_o && io_request_i.write;

    always_comb begin
        flags_d = flags_q | source_set_i;
        if (write_if) flags_d = io_request_i.write_data[4:0];
        flags_d = flags_d & ~interrupt_ack_i;
    end
    // Ref: Pan Docs "Power Up Sequence": reset leaves VBlank pending, the DMG
    // boot-ROM handoff value (IF $E1).
    `DFF_RST_VAL(flags_q, flags_d, clk_i, reset_i, 5'h01)
    assign interrupt_flags_o = flags_q;

    always_comb begin
        io_response_o.valid = io_request_i.valid && io_selected_o;
        io_response_o.fault = 1'b0;
        io_response_o.read_data = {3'b111, flags_q};
    end

    `ASSERT_KNOWN(interrupts_controls_known, clk_i, reset_i,
        {io_request_i.valid, source_set_i, interrupt_ack_i})
    `ASSERT(interrupts_access_known, clk_i, reset_i,
        !io_request_i.valid || !$isunknown({io_request_i.write, io_request_i.address}))
    `ASSERT(interrupts_write_known, clk_i, reset_i,
        !write_if || !$isunknown(io_request_i.write_data))
    `ASSERT(interrupts_ack_onehot, clk_i, reset_i, $onehot0(interrupt_ack_i))
endmodule
