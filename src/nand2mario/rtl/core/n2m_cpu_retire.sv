`default_nettype none
`include "src/common/rtl/pkg/macros.vh"

// Map donor architectural capture onto the compact retirement observation.
module n2m_cpu_retire (
    input logic clk_i, reset_i,
    input n2m_cpu_pkg::t_cpu_retire_capture capture_i,
    output n2m_interfaces_pkg::t_retirement retirement_o
);
    n2m_interfaces_pkg::t_retirement next_record;
    always_comb begin
        next_record = '0;
        next_record.valid = capture_i.valid;
        next_record.is_interrupt = capture_i.is_interrupt;
        next_record.pc_before = capture_i.pc_before;
        next_record.pc_after = capture_i.pc_after;
        if (!capture_i.is_interrupt) begin
            next_record.opcode_length = capture_i.fetched_length;
            case (capture_i.fetched_length)
                1: next_record.opcode = {16'b0, capture_i.fetched_bytes[7:0]};
                2: next_record.opcode = {8'b0, capture_i.fetched_bytes[15:0]};
                3: next_record.opcode = capture_i.fetched_bytes;
                default: next_record.opcode = 0;
            endcase
        end
        {next_record.a, next_record.f, next_record.b, next_record.c,
         next_record.d, next_record.e, next_record.h, next_record.l,
         next_record.sp} = capture_i.registers_after;
        next_record.ime = capture_i.ime_after;
        next_record.halted = capture_i.halted_after;
        next_record.stopped = capture_i.stopped_after;
    end
    `DFF_RST(retirement_o, next_record, clk_i, reset_i)
    `ASSERT(CPU_RETIRE_FLAGS, clk_i, reset_i,
        capture_i.valid |-> capture_i.registers_after.f[3:0] == 0)
endmodule
