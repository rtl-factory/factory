// Q103H memory access completion
//----------------------------------------------------------
// 1. Hold Q100H..Q103H while the Q103H load/store response is still owed.
// 2. On the response edge, select and sign/zero-extend the addressed byte or
//    halfword of a load, or record the completed store.
// 3. Turn an error response into the precise access trap (cause 5 or 7)
//    whose record carries no register or memory effect.
// 4. Build the Q104H retirement record. A multiply's product is registered
//    in n2d_muldiv on the same edge; the record only marks it.
//----------------------------------------------------------

module n2d_ma (
    input  n2d_core_pkg::t_q103   q103_i,
    input  logic                  data_response_valid_i,
    input  logic [31:0]           data_response_read_data_i,
    input  logic                  data_response_error_i,
    input  logic [31:0]           mtvec_i,

    // The owed response has not arrived: Q103H and older stages hold
    output logic                  hold_Q103H_o,
    // The Q103H access completes with an error this cycle
    output logic                  trap_Q103H_o,
    output logic [31:0]           trap_cause_o,
    output n2d_core_pkg::t_retire retire_Q104H_o
);
    import n2d_core_pkg::*;

    logic        memory_op;
    logic [15:0] shifted_read_data;
    logic [31:0] loaded_value;

    always_comb begin
        memory_op = q103_i.valid && (q103_i.is_load || q103_i.is_store);
        hold_Q103H_o = memory_op && !data_response_valid_i;
        trap_Q103H_o = memory_op && data_response_valid_i && data_response_error_i;
        trap_cause_o = q103_i.is_load ? CAUSE_LOAD_ACCESS : CAUSE_STORE_ACCESS;

        case (q103_i.mem_address[1:0])
            2'd0: shifted_read_data = data_response_read_data_i[15:0];
            2'd1: shifted_read_data = data_response_read_data_i[23:8];
            2'd2: shifted_read_data = data_response_read_data_i[31:16];
            default: shifted_read_data = {8'b0, data_response_read_data_i[31:24]};
        endcase
        case (q103_i.funct3)
            3'b000: loaded_value = {{24{shifted_read_data[7]}}, shifted_read_data[7:0]};
            3'b001: loaded_value = {{16{shifted_read_data[15]}}, shifted_read_data[15:0]};
            3'b100: loaded_value = {24'b0, shifted_read_data[7:0]};
            3'b101: loaded_value = {16'b0, shifted_read_data[15:0]};
            default: loaded_value = data_response_read_data_i;
        endcase

        retire_Q104H_o = '0;
        retire_Q104H_o.valid = q103_i.valid && !hold_Q103H_o;
        retire_Q104H_o.pc = q103_i.pc;
        retire_Q104H_o.instruction = q103_i.instruction;
        if (trap_Q103H_o) begin
            retire_Q104H_o.next_pc = mtvec_i;
            retire_Q104H_o.trap = 1'b1;
            retire_Q104H_o.cause = trap_cause_o;
            retire_Q104H_o.trap_value = q103_i.mem_address;
        end else begin
            retire_Q104H_o.next_pc = q103_i.next_pc;
            retire_Q104H_o.trap = q103_i.trap;
            retire_Q104H_o.cause = q103_i.cause;
            retire_Q104H_o.trap_value = q103_i.trap_value;
            retire_Q104H_o.counted_at_commit = q103_i.counted_at_commit;
            retire_Q104H_o.rd_write = q103_i.rd_write;
            retire_Q104H_o.is_mul = q103_i.is_mul;
            retire_Q104H_o.rd_address = q103_i.rd_write ? q103_i.rd : 5'd0;
            if (q103_i.rd_write) begin
                if (q103_i.is_load) retire_Q104H_o.rd_write_data = loaded_value;
                else if (!q103_i.is_mul) retire_Q104H_o.rd_write_data = q103_i.result;
            end
            if (memory_op) begin
                retire_Q104H_o.memory_valid = 1'b1;
                retire_Q104H_o.memory_write = q103_i.is_store;
                retire_Q104H_o.memory_address = q103_i.mem_address;
                if (q103_i.is_load) begin
                    retire_Q104H_o.memory_read_data = data_response_read_data_i;
                    retire_Q104H_o.memory_read_mask = q103_i.mem_mask;
                end else begin
                    retire_Q104H_o.memory_write_data = q103_i.mem_write_data;
                    retire_Q104H_o.memory_write_mask = q103_i.mem_mask;
                end
            end
        end
    end
endmodule
