`default_nettype none
`include "src/common/rtl/pkg/macros.vh"

// Adapted donor bus owner: system-edge acknowledgement, machine-enable advance.
module n2m_cpu_bus (
    input logic clk_i, reset_i, machine_cycle_enable_i,
    input logic active_i, complete_enable_i, halted_i,
    input n2m_cpu_pkg::cpu_bus_plan_t plan_i,
    input n2m_interfaces_pkg::bus_response_t response_i,
    output n2m_interfaces_pkg::bus_request_t request_o,
    output logic [7:0] read_data_o,
    output logic cycle_end_o, fault_o
);
    logic accepted, accepted_next, fault_next;
    logic [7:0] data_q, data_next;
    logic transfer, needs_bus, outstanding, outstanding_next;
    assign needs_bus = plan_i.access_kind != n2m_cpu_pkg::ACCESS_IDLE;
    always_comb begin
        request_o = '0;
        request_o.valid = active_i && (complete_enable_i || outstanding) && needs_bus &&
                          !accepted && !fault_o && !reset_i;
        request_o.write = plan_i.write_enable;
        request_o.address = plan_i.address;
        request_o.write_data = plan_i.write_data;
        case (plan_i.access_kind)
            n2m_cpu_pkg::ACCESS_OPCODE, n2m_cpu_pkg::ACCESS_OPERAND:
                request_o.kind = n2m_interfaces_pkg::ACCESS_FETCH;
            n2m_cpu_pkg::ACCESS_DATA: request_o.kind = n2m_interfaces_pkg::ACCESS_DATA;
            n2m_cpu_pkg::ACCESS_STACK: request_o.kind = n2m_interfaces_pkg::ACCESS_STACK;
            default: request_o.kind = n2m_interfaces_pkg::ACCESS_IDLE;
        endcase
    end
    assign transfer = request_o.valid && response_i.valid;
    assign read_data_o = accepted ? data_q : response_i.read_data;
    // Why: HALT admits a fresh wake fetch only on a qualified machine boundary.
    // Once admitted, decode its response on the system edge so execution can
    // resume at the next boundary rather than charging another fetch cycle.
    // The IME-enabled idle plan and every executing phase remain synchronous
    // to the machine enable; a fault never completes either path.
    assign cycle_end_o = (machine_cycle_enable_i ||
        (halted_i && plan_i.access_kind == n2m_cpu_pkg::ACCESS_OPCODE && outstanding)) && active_i &&
        (complete_enable_i || outstanding || accepted) &&
        !fault_o && !reset_i && (!needs_bus || accepted || (transfer && !response_i.fault));
    always_comb begin
        accepted_next = accepted;
        data_next = data_q;
        fault_next = fault_o;
        outstanding_next = outstanding || request_o.valid;
        if (transfer) begin
            outstanding_next = 0;
            accepted_next = 1;
            data_next = response_i.read_data;
            if (response_i.fault) fault_next = 1;
        end
        if (cycle_end_o) accepted_next = 0;
    end
    `N2A_DFF_RST(accepted, accepted_next, clk_i, reset_i)
    `N2A_DFF_RST(data_q, data_next, clk_i, reset_i)
    `N2A_DFF_RST(fault_o, fault_next, clk_i, reset_i)
    `N2A_DFF_RST(outstanding, outstanding_next, clk_i, reset_i)
    `N2A_ASSERT(CPU_REQUEST_HELD, clk_i, reset_i,
        request_o.valid && !response_i.valid |=> $stable(request_o))
endmodule
