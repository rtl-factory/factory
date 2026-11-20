`default_nettype none

// Canonical LR35902 composition in the repository 25 MHz domain.
module n2m_cpu (
    input logic clk_i, reset_i, machine_cycle_enable_i,
    input logic [4:0] interrupt_enable_i, interrupt_flags_i,
    input logic joyp_selected_active_i, wake_i,
    input n2m_interfaces_pkg::bus_response_t response_i,
    output n2m_interfaces_pkg::bus_request_t request_o,
    output n2m_interfaces_pkg::retirement_t retirement_o,
    output logic [4:0] interrupt_ack_o,
    output logic halted_o, stopped_o, locked_o, fault_o, divider_reset_o
);
    n2m_cpu_pkg::cpu_execute_request_t execute_request;
    n2m_cpu_pkg::cpu_execute_result_t execute_result;
    n2m_cpu_pkg::cpu_bus_plan_t bus_plan;
    n2m_cpu_pkg::cpu_retire_capture_t capture;
    n2m_cpu_pkg::cpu_stop_action_t stop_action;
    logic stop_padding, active, complete_enable, pending_irq, cycle_end, bus_fault;
    logic stop_execute;
    logic [7:0] read_data;
    n2m_cpu_control u_control (
        .clk_sys(clk_i), .reset_sys(reset_i), .machine_cycle_enable(machine_cycle_enable_i),
        .ie(interrupt_enable_i), .iflags(interrupt_flags_i),
        .read_data(read_data), .wake_request(wake_i), .cycle_end(cycle_end),
        .bus_fault(bus_fault), .execute_result(execute_result),
        .stop_action(stop_action), .stop_padding(stop_padding),
        .execute_request(execute_request), .bus_plan(bus_plan), .retire_capture(capture),
        .active(active), .complete_enable(complete_enable), .pending_irq(pending_irq),
        .irq_ack(interrupt_ack_o), .halted(halted_o), .stopped(stopped_o),
        .locked(locked_o), .fault(fault_o), .stop_execute(stop_execute)
    );
    n2m_cpu_execute u_execute (.request(execute_request), .result(execute_result));
    n2m_cpu_bus u_bus (
        .clk_i(clk_i), .reset_i(reset_i), .machine_cycle_enable_i(machine_cycle_enable_i),
        .active_i(active), .complete_enable_i(complete_enable), .halted_i(halted_o), .plan_i(bus_plan),
        .response_i(response_i), .request_o(request_o), .read_data_o(read_data),
        .cycle_end_o(cycle_end), .fault_o(bus_fault)
    );
    n2m_cpu_retire u_retire (.clk_i(clk_i), .reset_i(reset_i),
        .capture_i(capture), .retirement_o(retirement_o));
    n2m_cpu_stop_policy u_stop_policy (.selected_active(joyp_selected_active_i),
        .enabled_pending(pending_irq), .execute(stop_execute), .action(stop_action),
        .padding(stop_padding), .divider_reset(divider_reset_o));
endmodule
