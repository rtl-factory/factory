`default_nettype none
`include "src/common/rtl/pkg/macros.vh"

// Single owner of architectural steering. Temporal bus and recorder state
// belong to their separate modules in the composition wrapper.
module n2m_cpu_control (
    input var logic clk_sys,
    input var logic reset_sys,
    input var logic machine_cycle_enable,
    input var logic [4:0] ie,
    input var logic [4:0] iflags,
    input var logic [7:0] read_data,
    input var logic wake_request,
    input var logic cycle_end,
    input var logic bus_fault,
    input var n2m_cpu_pkg::cpu_execute_result_t execute_result,
    input var n2m_cpu_pkg::cpu_stop_action_t stop_action,
    input var logic stop_padding,
    output n2m_cpu_pkg::cpu_execute_request_t execute_request,
    output n2m_cpu_pkg::cpu_bus_plan_t bus_plan,
    output n2m_cpu_pkg::cpu_retire_capture_t retire_capture,
    output logic active,
    output logic complete_enable,
    output logic pending_irq,
    output logic [4:0] irq_ack,
    output logic halted,
    output logic stopped,
    output logic locked,
    output logic fault,
    output logic stop_execute
);

    n2m_cpu_pkg::cpu_control_t control;
    n2m_cpu_pkg::cpu_control_t control_next;
    n2m_cpu_pkg::cpu_registers_t registers;
    n2m_cpu_pkg::cpu_registers_t registers_next;
    logic [4:0] dispatch;
    logic [15:0] selected_vector;

    always_comb begin
        execute_request.registers = registers;
        execute_request.opcode = control.opcode;
        execute_request.cb_bank = control.cb_bank;
        execute_request.step = control.step;
        execute_request.pc = control.pc;
        execute_request.temporary = control.temporary;
        execute_request.data = read_data;
    end

    assign fault = bus_fault;
    assign active = !fault && (control.mode == n2m_cpu_pkg::MODE_FETCH || control.mode == n2m_cpu_pkg::MODE_EXECUTE || control.mode == n2m_cpu_pkg::MODE_INTERRUPT || control.mode == n2m_cpu_pkg::MODE_HALT);
    // Admit a new HALT wake only at the sampling boundary. Once admitted,
    // the bus owns retention through acknowledgement even if the IRQ clears.
    assign complete_enable = control.mode != n2m_cpu_pkg::MODE_HALT ||
                             (machine_cycle_enable && pending_irq);
    assign halted = control.mode == n2m_cpu_pkg::MODE_HALT;
    assign stopped = control.mode == n2m_cpu_pkg::MODE_STOP;
    assign locked = control.mode == n2m_cpu_pkg::MODE_LOCK;
    assign pending_irq = |(ie[4:0] & iflags);
    assign dispatch = ie[4:0] & iflags;
    assign stop_execute = control.mode == n2m_cpu_pkg::MODE_EXECUTE && execute_result.stop_request && cycle_end;

    always_comb begin
        bus_plan.address = control.pc;
        bus_plan.write_data = 0;
        bus_plan.write_enable = 0;
        bus_plan.access_kind = n2m_cpu_pkg::ACCESS_IDLE;
        // An IME-enabled HALT wake dispatches at the sampling boundary;
        // a discarded opcode read must not add a responder-dependent cycle.
        if (control.mode == n2m_cpu_pkg::MODE_FETCH ||
            (control.mode == n2m_cpu_pkg::MODE_HALT && !control.ime))
            bus_plan.access_kind = n2m_cpu_pkg::ACCESS_OPCODE;
        else if (control.mode == n2m_cpu_pkg::MODE_EXECUTE) begin
            bus_plan.address = execute_result.plan.address;
            bus_plan.write_data = execute_result.plan.write_data;
            bus_plan.write_enable = execute_result.plan.write_enable;
            bus_plan.access_kind = execute_result.plan.access_kind;
        end else if (control.mode == n2m_cpu_pkg::MODE_INTERRUPT) begin
            if (control.step == 2 || control.step == 3) begin
                bus_plan.address = registers.sp;
                bus_plan.write_enable = 1;
                bus_plan.write_data = control.step == 2 ? control.irq_pc[15:8] : control.irq_pc[7:0];
                bus_plan.access_kind = n2m_cpu_pkg::ACCESS_STACK;
            end else if (control.step == 4) bus_plan.access_kind = n2m_cpu_pkg::ACCESS_OPCODE;
        end
    end

    always_comb begin
        selected_vector = 0;
        if (dispatch[0]) selected_vector = 16'h0040;
        else if (dispatch[1]) selected_vector = 16'h0048;
        else if (dispatch[2]) selected_vector = 16'h0050;
        else if (dispatch[3]) selected_vector = 16'h0058;
        else if (dispatch[4]) selected_vector = 16'h0060;
    end

    always_comb begin
        control_next = control;
        registers_next = registers;
        retire_capture = '0;
        retire_capture.valid = 0;
        retire_capture.is_interrupt = 0;
        retire_capture.pc_before = control.instruction_pc;
        retire_capture.pc_after = control.pc;
        retire_capture.fetched_bytes = control.fetched;
        retire_capture.fetched_length = control.length;
        retire_capture.halted_after = halted;
        retire_capture.stopped_after = stopped;
        irq_ack = 0;
        // Select on the first system edge after the upper-byte push completes.
        // Its synchronous IE write is now visible, while the lower-byte push
        // cannot have changed IE yet. Retain this selection through bus waits.
        if (control.mode == n2m_cpu_pkg::MODE_INTERRUPT && control.irq_select_pending) begin
            control_next.pc = selected_vector;
            control_next.irq_select_pending = 0;
        end
        if (control.mode == n2m_cpu_pkg::MODE_STOP && wake_request) begin
            control_next.mode = n2m_cpu_pkg::MODE_FETCH;
        end
        if (cycle_end) begin
            case (control.mode)
                // HALT prepares an address but issues no bus request until wake.
                // Once issued, the bus owner completes it even if IRQ clears.
                n2m_cpu_pkg::MODE_FETCH, n2m_cpu_pkg::MODE_HALT: begin
                    control_next.opcode = read_data;
                    control_next.fetched = {16'b0, read_data};
                    control_next.length = 1;
                    control_next.instruction_pc = control.pc;
                    control_next.pc = control.pc + 16'd1;
                    control_next.step = 0;
                    control_next.cb_bank = 0;
                    control_next.mode = n2m_cpu_pkg::MODE_EXECUTE;
                    if (control.ime && pending_irq) begin
                        control_next.mode = n2m_cpu_pkg::MODE_INTERRUPT;
                        control_next.irq_pc = control.pc;
                    end
                end
                n2m_cpu_pkg::MODE_EXECUTE: begin
                    registers_next = execute_result.registers_after;
                    control_next.pc = execute_result.pc_after;
                    control_next.temporary = execute_result.temporary_after;
                    control_next.step = control.step + 3'd1;
                    if (execute_result.plan.access_kind == n2m_cpu_pkg::ACCESS_OPERAND) begin
                        case (control.length)
                            1: control_next.fetched[15:8] = read_data;
                            2: control_next.fetched[23:16] = read_data;
                            default: begin end
                        endcase
                        control_next.length = control.length + 2'd1;
                    end
                    if (execute_result.prefix) begin
                        control_next.opcode = read_data;
                        control_next.cb_bank = 1;
                        control_next.step = 0;
                    end
                    if (execute_result.illegal) control_next.mode = n2m_cpu_pkg::MODE_LOCK;
                    if (execute_result.finish) begin
                        retire_capture.valid = 1;
                        retire_capture.pc_after = execute_result.pc_after;
                        control_next.step = 0;
                        control_next.cb_bank = 0;
                        control_next.ime = control.ime || control.ime_delay || execute_result.return_interrupt;
                        control_next.ime_delay = 0;
                        if (execute_result.disable_interrupts) control_next.ime = 0;
                        else if (execute_result.enable_interrupts && !control_next.ime) control_next.ime_delay = 1;
                        control_next.opcode = read_data;
                        control_next.fetched = {16'b0, read_data};
                        control_next.length = 1;
                        control_next.instruction_pc = execute_result.pc_after;
                        control_next.pc = execute_result.pc_after + 16'd1;
                        if (execute_result.halt_request) begin
                            control_next.pc = execute_result.pc_after;
                            if (pending_irq) begin
                                if (control_next.ime) begin
                                    // A request recognized while HALT executes
                                    // with IME set returns to this HALT. This also
                                    // covers delayed EI maturation; late arrivals
                                    // after this machine boundary instead enter sleep.
                                    retire_capture.pc_after = control.instruction_pc;
                                    control_next.pc = control.instruction_pc + 16'd1;
                                end
                            end else begin
                                control_next.mode = n2m_cpu_pkg::MODE_HALT;
                                retire_capture.halted_after = 1;
                            end
                        end
                        if (execute_result.stop_request) begin
                            if (stop_padding) begin
                                retire_capture.fetched_bytes[15:8] = read_data;
                                retire_capture.fetched_length = 2;
                                retire_capture.pc_after = execute_result.pc_after + 16'd1;
                            end
                            if (stop_action != n2m_cpu_pkg::STOP_CONTINUE) begin
                                control_next.pc = retire_capture.pc_after;
                                control_next.mode = stop_action == n2m_cpu_pkg::STOP_HALT ? n2m_cpu_pkg::MODE_HALT : n2m_cpu_pkg::MODE_STOP;
                                retire_capture.halted_after = stop_action == n2m_cpu_pkg::STOP_HALT;
                                retire_capture.stopped_after = stop_action == n2m_cpu_pkg::STOP_OSCILLATOR;
                            end
                        end
                        if (control_next.ime && pending_irq && !retire_capture.stopped_after) begin
                            control_next.mode = n2m_cpu_pkg::MODE_INTERRUPT;
                            control_next.irq_pc = retire_capture.pc_after;
                            retire_capture.halted_after = 0;
                        end
                    end
                end
                n2m_cpu_pkg::MODE_INTERRUPT: begin
                    control_next.step = control.step + 3'd1;
                    control_next.ime = 0;
                    control_next.ime_delay = 0;
                    if (control.step == 0) control_next.pc = control.irq_pc;
                    if (control.step == 1 || control.step == 2)
                        registers_next.sp = registers.sp - 16'd1;
                    if (control.step == 2) control_next.irq_select_pending = 1;
                    if (control.step == 3) begin
                        // The held vector PC already owns the selection; do
                        // not duplicate it in another five state bits. Use
                        // next PC when selection and completion share an edge.
                        case (control_next.pc)
                            16'h0040: irq_ack = 5'b00001;
                            16'h0048: irq_ack = 5'b00010;
                            16'h0050: irq_ack = 5'b00100;
                            16'h0058: irq_ack = 5'b01000;
                            16'h0060: irq_ack = 5'b10000;
                            default: irq_ack = 0;
                        endcase
                    end
                    if (control.step == 4) begin
                        retire_capture.valid = 1;
                        retire_capture.is_interrupt = 1;
                        retire_capture.pc_before = control.irq_pc;
                        retire_capture.pc_after = control.pc;
                        control_next.mode = n2m_cpu_pkg::MODE_EXECUTE;
                        control_next.step = 0;
                        control_next.cb_bank = 0;
                        control_next.instruction_pc = control.pc;
                        control_next.pc = control.pc + 16'd1;
                        control_next.opcode = read_data;
                        control_next.fetched = {16'b0, read_data};
                        control_next.length = 1;
                    end
                end
                default: begin end
            endcase
        end
        retire_capture.registers_after = registers_next;
        retire_capture.ime_after = control_next.ime;
    end

    localparam n2m_cpu_pkg::cpu_control_t CONTROL_RESET = n2m_cpu_pkg::reset_control();
    `N2A_DFF_RST_VAL(control, control_next, clk_sys, reset_sys, CONTROL_RESET)
    localparam n2m_cpu_pkg::cpu_registers_t REGISTERS_RESET = n2m_cpu_pkg::reset_registers();
    `N2A_DFF_RST_VAL(registers, registers_next, clk_sys, reset_sys, REGISTERS_RESET)

    `N2A_ASSERT(CPU_IRQ_ACK_ONEHOT, clk_sys, reset_sys, $onehot0(irq_ack))
    `N2A_ASSERT(CPU_F_LOW_ZERO, clk_sys, reset_sys, registers.f[3:0] == 0)
endmodule
