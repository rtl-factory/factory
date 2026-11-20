`include "src/common/rtl/pkg/macros.vh"

module n2d_rv32i_core_step (
    input  logic        clk_i,
    input  logic        rst_i,
    input  logic        enable_i,

    output logic        instruction_request_valid_o,
    input  logic        instruction_request_ready_i,
    output logic [31:0] instruction_request_address_o,
    input  logic        instruction_response_valid_i,
    input  logic [31:0] instruction_response_data_i,
    input  logic        instruction_response_error_i,

    output logic        data_request_valid_o,
    input  logic        data_request_ready_i,
    output logic        data_request_write_o,
    output logic [31:0] data_request_address_o,
    output logic [31:0] data_request_write_data_o,
    output logic [3:0]  data_request_byte_enable_o,
    input  logic        data_response_valid_i,
    input  logic [31:0] data_response_read_data_i,
    input  logic        data_response_error_i,

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
    output logic [31:0] observation_trap_value_o,
    output logic        halted_o
);
    import n2d_core_types_pkg::*;

    localparam step_regs_t REGS_RESET = '{
        state: STEP_FETCH_REQUEST,
        pc: 32'h0000_0000,
        instruction: 32'h0000_0013,
        instruction_pc: 32'h0000_0000,
        order: 64'd0,
        memory_is_load: 1'b0,
        memory_rd: 5'd0,
        memory_address: 32'h0000_0000,
        memory_write_data: 32'h0000_0000,
        memory_next_pc: 32'h0000_0000,
        observation_valid: 1'b0,
        observation_order: 64'd0,
        observation_pc: 32'h0000_0000,
        observation_instruction: 32'h0000_0000,
        observation_next_pc: 32'h0000_0000,
        observation_rd_address: 5'd0,
        observation_rd_write: 1'b0,
        observation_rd_write_data: 32'h0000_0000,
        observation_memory_valid: 1'b0,
        observation_memory_write: 1'b0,
        observation_memory_address: 32'h0000_0000,
        observation_memory_read_data: 32'h0000_0000,
        observation_memory_write_data: 32'h0000_0000,
        observation_memory_read_mask: 4'b0000,
        observation_memory_write_mask: 4'b0000,
        observation_trap: 1'b0,
        observation_cause: 32'h0000_0000,
        observation_trap_value: 32'h0000_0000};
    step_regs_t regs_q, regs_d;

    localparam logic [6:0] OPCODE_BRANCH = 7'b1100011;
    localparam logic [6:0] OPCODE_LOAD   = 7'b0000011;
    localparam logic [6:0] OPCODE_OP     = 7'b0110011;
    localparam logic [6:0] OPCODE_OP_IMM = 7'b0010011;
    localparam logic [6:0] OPCODE_STORE  = 7'b0100011;

    logic [4:0]  rs1_index;
    logic [4:0]  rs2_index;
    logic [4:0]  rd_index;
    logic        alu_rs1_read;
    logic        alu_rs2_read;
    logic        alu_rd_write;
    logic [31:0] alu_immediate;
    logic [31:0] alu_result;
    logic        alu_illegal;
    logic        alu_contract_valid;
    logic [31:0] rs1_data;
    logic [31:0] rs2_data;
    logic        register_write_enable;
    logic [4:0]  register_write_index;
    logic [31:0] register_write_data;

    logic [6:0]  opcode;
    logic [2:0]  funct3;
    logic [31:0] branch_immediate;
    logic [31:0] load_immediate;
    logic [31:0] store_immediate;
    logic [31:0] effective_address;
    logic        branch_legal;
    logic        branch_taken;
    logic        load_legal;
    logic        store_legal;
    logic        memory_aligned;

    n2a_rv32_regfile u_register_file (
        .clk_i             (clk_i),
        .rst_i             (rst_i),
        .read_a_index_i    (rs1_index),
        .read_a_data_o     (rs1_data),
        .read_b_index_i    (rs2_index),
        .read_b_data_o     (rs2_data),
        .write_enable_i    (register_write_enable),
        .write_index_i     (register_write_index),
        .write_data_i      (register_write_data)
    );

    n2d_rv32i_decode_alu u_decode_alu (
        .instruction_i(regs_q.instruction),
        .rs1_data_i    (rs1_data),
        .rs2_data_i    (rs2_data),
        .rs1_index_o   (rs1_index),
        .rs2_index_o   (rs2_index),
        .rd_index_o    (rd_index),
        .rs1_read_o    (alu_rs1_read),
        .rs2_read_o    (alu_rs2_read),
        .rd_write_o    (alu_rd_write),
        .immediate_o   (alu_immediate),
        .alu_result_o  (alu_result),
        .illegal_o     (alu_illegal)
    );

    always_comb begin
        opcode = regs_q.instruction[6:0];
        funct3 = regs_q.instruction[14:12];
        branch_immediate = {
            {19{regs_q.instruction[31]}}, regs_q.instruction[31], regs_q.instruction[7],
            regs_q.instruction[30:25], regs_q.instruction[11:8], 1'b0
        };
        load_immediate = {{20{regs_q.instruction[31]}}, regs_q.instruction[31:20]};
        store_immediate = {
            {20{regs_q.instruction[31]}}, regs_q.instruction[31:25], regs_q.instruction[11:7]
        };

        branch_legal = 1'b0;
        branch_taken = 1'b0;
        if (opcode == OPCODE_BRANCH) begin
            case (funct3)
                3'b000: begin branch_legal = 1'b1; branch_taken = rs1_data == rs2_data; end
                3'b001: begin branch_legal = 1'b1; branch_taken = rs1_data != rs2_data; end
                3'b100: begin
                    branch_legal = 1'b1;
                    branch_taken = $signed(rs1_data) < $signed(rs2_data);
                end
                3'b101: begin
                    branch_legal = 1'b1;
                    branch_taken = $signed(rs1_data) >= $signed(rs2_data);
                end
                3'b110: begin branch_legal = 1'b1; branch_taken = rs1_data < rs2_data; end
                3'b111: begin branch_legal = 1'b1; branch_taken = rs1_data >= rs2_data; end
                default: begin branch_legal = 1'b0; branch_taken = 1'b0; end
            endcase
        end

        load_legal = (opcode == OPCODE_LOAD) && (funct3 == 3'b010);
        store_legal = (opcode == OPCODE_STORE) && (funct3 == 3'b010);
        effective_address = rs1_data + (load_legal ? load_immediate : store_immediate);
        memory_aligned = effective_address[1:0] == 2'b00;
        alu_contract_valid = !alu_illegal && alu_rs1_read &&
            ((opcode == OPCODE_OP) ? alu_rs2_read : !alu_rs2_read) &&
            ((opcode != OPCODE_OP_IMM) || (alu_immediate == load_immediate));

        instruction_request_valid_o = enable_i && (regs_q.state == STEP_FETCH_REQUEST);
        instruction_request_address_o = regs_q.pc;

        data_request_valid_o = enable_i && (regs_q.state == STEP_EXECUTE) &&
            (load_legal || store_legal) && memory_aligned;
        data_request_write_o = store_legal;
        data_request_address_o = effective_address;
        data_request_write_data_o = rs2_data;
        data_request_byte_enable_o = store_legal ? 4'b1111 : 4'b0000;

        register_write_enable = 1'b0;
        register_write_index = 5'd0;
        register_write_data = 32'h0000_0000;
        if ((regs_q.state == STEP_EXECUTE) && alu_contract_valid) begin
            register_write_enable = alu_rd_write;
            register_write_index = rd_index;
            register_write_data = alu_result;
        end else if ((regs_q.state == STEP_DATA_RESPONSE) &&
                     data_response_valid_i && regs_q.memory_is_load &&
                     !data_response_error_i) begin
            register_write_enable = regs_q.memory_rd != 5'd0;
            register_write_index = regs_q.memory_rd;
            register_write_data = data_response_read_data_i;
        end
    end

    always_comb begin
        regs_d = regs_q;
        regs_d.observation_valid = 1'b0;
        regs_d.observation_rd_address = 5'd0;
        regs_d.observation_rd_write = 1'b0;
        regs_d.observation_rd_write_data = 32'h0000_0000;
        regs_d.observation_memory_valid = 1'b0;
        regs_d.observation_memory_write = 1'b0;
        regs_d.observation_memory_address = 32'h0000_0000;
        regs_d.observation_memory_read_data = 32'h0000_0000;
        regs_d.observation_memory_write_data = 32'h0000_0000;
        regs_d.observation_memory_read_mask = 4'b0000;
        regs_d.observation_memory_write_mask = 4'b0000;
        regs_d.observation_trap = 1'b0;
        regs_d.observation_cause = 32'h0000_0000;
        regs_d.observation_trap_value = 32'h0000_0000;

        case (regs_q.state)
            STEP_FETCH_REQUEST: begin
                if (enable_i && instruction_request_ready_i) begin
                    regs_d.state = STEP_FETCH_RESPONSE;
                end
            end

            STEP_FETCH_RESPONSE: begin
                if (instruction_response_valid_i) begin
                    if (instruction_response_error_i) begin
                        regs_d.observation_valid = 1'b1;
                        regs_d.observation_order = regs_q.order;
                        regs_d.observation_pc = regs_q.pc;
                        regs_d.observation_instruction = 32'h0000_0000;
                        regs_d.observation_next_pc = regs_q.pc;
                        regs_d.observation_trap = 1'b1;
                        regs_d.observation_cause = 32'd1;
                        regs_d.observation_trap_value = regs_q.pc;
                        regs_d.order = regs_q.order + 64'd1;
                        regs_d.state = STEP_HALTED;
                    end else begin
                        regs_d.instruction = instruction_response_data_i;
                        regs_d.instruction_pc = regs_q.pc;
                        regs_d.state = STEP_EXECUTE;
                    end
                end
            end

            STEP_EXECUTE: begin
                if (alu_contract_valid) begin
                    regs_d.observation_valid = 1'b1;
                    regs_d.observation_order = regs_q.order;
                    regs_d.observation_pc = regs_q.instruction_pc;
                    regs_d.observation_instruction = regs_q.instruction;
                    regs_d.observation_next_pc = regs_q.instruction_pc + 32'd4;
                    regs_d.observation_rd_address = rd_index;
                    regs_d.observation_rd_write = alu_rd_write;
                    regs_d.observation_rd_write_data = alu_result;
                    regs_d.pc = regs_q.instruction_pc + 32'd4;
                    regs_d.order = regs_q.order + 64'd1;
                    regs_d.state = STEP_FETCH_REQUEST;
                end else if (branch_legal) begin
                    if (branch_taken &&
                        ((regs_q.instruction_pc + branch_immediate) & 32'h0000_0003) != 32'd0) begin
                        regs_d.observation_valid = 1'b1;
                        regs_d.observation_order = regs_q.order;
                        regs_d.observation_pc = regs_q.instruction_pc;
                        regs_d.observation_instruction = regs_q.instruction;
                        regs_d.observation_next_pc = regs_q.instruction_pc;
                        regs_d.observation_trap = 1'b1;
                        regs_d.observation_cause = 32'd0;
                        regs_d.observation_trap_value = regs_q.instruction_pc + branch_immediate;
                        regs_d.order = regs_q.order + 64'd1;
                        regs_d.state = STEP_HALTED;
                    end else begin
                        regs_d.observation_valid = 1'b1;
                        regs_d.observation_order = regs_q.order;
                        regs_d.observation_pc = regs_q.instruction_pc;
                        regs_d.observation_instruction = regs_q.instruction;
                        regs_d.observation_next_pc = branch_taken
                            ? regs_q.instruction_pc + branch_immediate
                            : regs_q.instruction_pc + 32'd4;
                        regs_d.pc = branch_taken
                            ? regs_q.instruction_pc + branch_immediate
                            : regs_q.instruction_pc + 32'd4;
                        regs_d.order = regs_q.order + 64'd1;
                        regs_d.state = STEP_FETCH_REQUEST;
                    end
                end else if (load_legal || store_legal) begin
                    if (!memory_aligned) begin
                        regs_d.observation_valid = 1'b1;
                        regs_d.observation_order = regs_q.order;
                        regs_d.observation_pc = regs_q.instruction_pc;
                        regs_d.observation_instruction = regs_q.instruction;
                        regs_d.observation_next_pc = regs_q.instruction_pc;
                        regs_d.observation_trap = 1'b1;
                        regs_d.observation_cause = load_legal ? 32'd4 : 32'd6;
                        regs_d.observation_trap_value = effective_address;
                        regs_d.order = regs_q.order + 64'd1;
                        regs_d.state = STEP_HALTED;
                    end else if (enable_i && data_request_ready_i) begin
                        regs_d.memory_is_load = load_legal;
                        regs_d.memory_rd = rd_index;
                        regs_d.memory_address = effective_address;
                        regs_d.memory_write_data = rs2_data;
                        regs_d.memory_next_pc = regs_q.instruction_pc + 32'd4;
                        regs_d.state = STEP_DATA_RESPONSE;
                    end
                end else begin
                    regs_d.observation_valid = 1'b1;
                    regs_d.observation_order = regs_q.order;
                    regs_d.observation_pc = regs_q.instruction_pc;
                    regs_d.observation_instruction = regs_q.instruction;
                    regs_d.observation_next_pc = regs_q.instruction_pc;
                    regs_d.observation_trap = 1'b1;
                    regs_d.observation_cause = 32'd2;
                    regs_d.observation_trap_value = regs_q.instruction;
                    regs_d.order = regs_q.order + 64'd1;
                    regs_d.state = STEP_HALTED;
                end
            end

            STEP_DATA_RESPONSE: begin
                if (data_response_valid_i) begin
                    regs_d.observation_valid = 1'b1;
                    regs_d.observation_order = regs_q.order;
                    regs_d.observation_pc = regs_q.instruction_pc;
                    regs_d.observation_instruction = regs_q.instruction;
                    regs_d.observation_next_pc = data_response_error_i
                        ? regs_q.instruction_pc : regs_q.memory_next_pc;
                    if (data_response_error_i) begin
                        regs_d.observation_trap = 1'b1;
                        regs_d.observation_cause = regs_q.memory_is_load ? 32'd5 : 32'd7;
                        regs_d.observation_trap_value = regs_q.memory_address;
                        regs_d.state = STEP_HALTED;
                    end else begin
                        regs_d.observation_rd_address = regs_q.memory_is_load
                            ? regs_q.memory_rd : 5'd0;
                        regs_d.observation_rd_write = regs_q.memory_is_load && (regs_q.memory_rd != 5'd0);
                        regs_d.observation_rd_write_data = regs_q.memory_is_load &&
                            (regs_q.memory_rd != 5'd0)
                            ? data_response_read_data_i : 32'h0000_0000;
                        regs_d.observation_memory_valid = 1'b1;
                        regs_d.observation_memory_write = !regs_q.memory_is_load;
                        regs_d.observation_memory_address = regs_q.memory_address;
                        regs_d.observation_memory_read_data = regs_q.memory_is_load
                            ? data_response_read_data_i : 32'h0000_0000;
                        regs_d.observation_memory_write_data = regs_q.memory_is_load
                            ? 32'h0000_0000 : regs_q.memory_write_data;
                        regs_d.observation_memory_read_mask = regs_q.memory_is_load
                            ? 4'b1111 : 4'b0000;
                        regs_d.observation_memory_write_mask = regs_q.memory_is_load
                            ? 4'b0000 : 4'b1111;
                        regs_d.pc = regs_q.memory_next_pc;
                        regs_d.state = STEP_FETCH_REQUEST;
                    end
                    regs_d.order = regs_q.order + 64'd1;
                end
            end

            default: regs_d.state = STEP_HALTED;
        endcase
    end

    `N2A_DFF_RST_VAL(regs_q, regs_d, clk_i, rst_i, REGS_RESET)
    assign observation_valid_o = regs_q.observation_valid;
    assign observation_order_o = regs_q.observation_order;
    assign observation_pc_o = regs_q.observation_pc;
    assign observation_instruction_o = regs_q.observation_instruction;
    assign observation_next_pc_o = regs_q.observation_next_pc;
    assign observation_rd_address_o = regs_q.observation_rd_address;
    assign observation_rd_write_o = regs_q.observation_rd_write;
    assign observation_rd_write_data_o = regs_q.observation_rd_write_data;
    assign observation_memory_valid_o = regs_q.observation_memory_valid;
    assign observation_memory_write_o = regs_q.observation_memory_write;
    assign observation_memory_address_o = regs_q.observation_memory_address;
    assign observation_memory_read_data_o = regs_q.observation_memory_read_data;
    assign observation_memory_write_data_o = regs_q.observation_memory_write_data;
    assign observation_memory_read_mask_o = regs_q.observation_memory_read_mask;
    assign observation_memory_write_mask_o = regs_q.observation_memory_write_mask;
    assign observation_trap_o = regs_q.observation_trap;
    assign observation_cause_o = regs_q.observation_cause;
    assign observation_trap_value_o = regs_q.observation_trap_value;

    assign halted_o = regs_q.state == STEP_HALTED;
endmodule
