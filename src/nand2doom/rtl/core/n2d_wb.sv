`include "src/common/rtl/pkg/macros.vh"

// Q105H writeback and retirement observation
// 1. Write the committed destination register from the Q105H record.
// 2. Publish the Q105H record as the registered observation port: one
//    valid pulse per ordinary retirement or precise trap, in program order.
module n2d_wb (
    input  n2d_core_pkg::t_retire q105_i,

    output logic        rf_write_enable_o,
    output logic [4:0]  rf_write_index_o,
    output logic [31:0] rf_write_data_o,

    output logic        observation_valid_o,
    output logic [63:0] observation_order_o,
    output logic [31:0] observation_pc_o,
    output logic [31:0] observation_instruction_o,
    output logic [31:0] observation_next_pc_o,
    output logic [4:0]  observation_rd_address_o,
    output logic        observation_rd_write_o,
    output logic [31:0] observation_rd_write_data_o,
    output logic        observation_memory_valid_o,
    output logic        observation_memory_write_o,
    output logic [31:0] observation_memory_address_o,
    output logic [31:0] observation_memory_read_data_o,
    output logic [31:0] observation_memory_write_data_o,
    output logic [3:0]  observation_memory_read_mask_o,
    output logic [3:0]  observation_memory_write_mask_o,
    output logic        observation_trap_o,
    output logic [31:0] observation_cause_o,
    output logic [31:0] observation_trap_value_o
);
    n2d_core_pkg::t_retire record;

    // Why: every observation field is zero outside a valid pulse, so consumers
    // that sample a field without valid see no stale or bubble payload.
    always_comb begin
        record = q105_i.valid ? q105_i : '0;

        rf_write_enable_o = record.valid && record.rd_write;
        rf_write_index_o = record.rd_address;
        rf_write_data_o = record.rd_write_data;

        observation_valid_o = record.valid;
        observation_order_o = record.order;
        observation_pc_o = record.pc;
        observation_instruction_o = record.instruction;
        observation_next_pc_o = record.next_pc;
        observation_rd_address_o = record.rd_address;
        observation_rd_write_o = record.rd_write;
        observation_rd_write_data_o = record.rd_write_data;
        observation_memory_valid_o = record.memory_valid;
        observation_memory_write_o = record.memory_write;
        observation_memory_address_o = record.memory_address;
        observation_memory_read_data_o = record.memory_read_data;
        observation_memory_write_data_o = record.memory_write_data;
        observation_memory_read_mask_o = record.memory_read_mask;
        observation_memory_write_mask_o = record.memory_write_mask;
        observation_trap_o = record.trap;
        observation_cause_o = record.cause;
        observation_trap_value_o = record.trap_value;
    end

    `UNUSED_BITS(unused_wb, {record.counted_at_commit, record.is_mul})
endmodule
