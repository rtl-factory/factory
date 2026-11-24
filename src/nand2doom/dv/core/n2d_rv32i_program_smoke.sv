module n2d_rv32i_program_smoke (
    input  logic        clk_i,
    input  logic        reset_i,
    input  logic        run_i,
    input  logic        program_write_enable_i,
    input  logic [5:0]  program_write_address_i,
    input  logic [31:0] program_write_data_i,
    input  logic        data_write_enable_i,
    input  logic [5:0]  data_write_address_i,
    input  logic [31:0] data_write_data_i,
    input  logic [5:0]  debug_data_address_i,
    output logic [31:0] debug_data_o,
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
    logic        instruction_request_valid;
    logic [31:0] instruction_request_address;
    logic        instruction_response_valid_q;
    logic        instruction_response_error_q;
    logic [31:0] instruction_ram_read_data;
    logic [5:0]  instruction_ram_address;

    logic        data_request_valid;
    logic        data_request_write;
    logic [31:0] data_request_address;
    logic [31:0] data_request_write_data;
    logic [3:0]  data_request_byte_enable;
    logic        data_response_valid_q;
    logic        data_response_error_q;
    logic        data_response_write_q;
    logic [5:0]  data_response_word_address_q;
    logic [31:0] data_response_write_data_q;
    logic [3:0]  data_response_byte_enable_q;
    logic [31:0] data_ram_read_data;
    logic        data_ram_write_enable;
    logic [5:0]  data_ram_address;
    logic [31:0] data_ram_write_data;

    assign instruction_ram_address = program_write_enable_i
        ? program_write_address_i : instruction_request_address[7:2];

    n2a_sync_ram #(
        .DATA_WIDTH(32),
        .DEPTH(64)
    ) u_instruction_ram (
        .clk_i(clk_i),
        .write_enable_i(program_write_enable_i),
        .address_i(instruction_ram_address),
        .write_data_i(program_write_data_i),
        .read_data_o(instruction_ram_read_data)
    );

    always_comb begin
        if (data_write_enable_i) begin
            data_ram_write_enable = 1'b1;
            data_ram_address = data_write_address_i;
            data_ram_write_data = data_write_data_i;
        end else if (!run_i) begin
            data_ram_write_enable = 1'b0;
            data_ram_address = debug_data_address_i;
            data_ram_write_data = 32'h0000_0000;
        end else begin
            data_ram_write_enable = !reset_i && data_response_valid_q &&
                data_response_write_q && !data_response_error_q &&
                (&data_response_byte_enable_q);
            data_ram_address = data_response_valid_q
                ? data_response_word_address_q : data_request_address[7:2];
            data_ram_write_data = data_response_write_data_q;
        end
    end

    n2a_sync_ram #(
        .DATA_WIDTH(32),
        .DEPTH(64)
    ) u_data_ram (
        .clk_i(clk_i),
        .write_enable_i(data_ram_write_enable),
        .address_i(data_ram_address),
        .write_data_i(data_ram_write_data),
        .read_data_o(data_ram_read_data)
    );

    always_ff @(posedge clk_i) begin
        if (reset_i) begin
            instruction_response_valid_q <= 1'b0;
            instruction_response_error_q <= 1'b0;
            data_response_valid_q <= 1'b0;
            data_response_error_q <= 1'b0;
            data_response_write_q <= 1'b0;
            data_response_word_address_q <= 6'd0;
            data_response_write_data_q <= 32'h0000_0000;
            data_response_byte_enable_q <= 4'b0000;
        end else begin
            instruction_response_valid_q <= run_i && instruction_request_valid;
            instruction_response_error_q <= |instruction_request_address[31:8] ||
                (instruction_request_address[1:0] != 2'b00);
            data_response_valid_q <= run_i && data_request_valid;
            data_response_error_q <= |data_request_address[31:8] ||
                (data_request_address[1:0] != 2'b00) ||
                (data_request_write
                    ? (data_request_byte_enable != 4'b1111)
                    : (data_request_byte_enable != 4'b0000));
            if (run_i && data_request_valid) begin
                data_response_write_q <= data_request_write;
                data_response_word_address_q <= data_request_address[7:2];
                data_response_write_data_q <= data_request_write_data;
                data_response_byte_enable_q <= data_request_byte_enable;
            end
        end
    end

    assign debug_data_o = data_ram_read_data;

    n2d_rv32i_core_step u_core (
        .clk_i(clk_i),
        .reset_i(reset_i),
        .enable_i(run_i),
        .instruction_request_valid_o(instruction_request_valid),
        .instruction_request_ready_i(run_i),
        .instruction_request_address_o(instruction_request_address),
        .instruction_response_valid_i(instruction_response_valid_q),
        .instruction_response_data_i(instruction_ram_read_data),
        .instruction_response_error_i(instruction_response_error_q),
        .data_request_valid_o(data_request_valid),
        .data_request_ready_i(run_i),
        .data_request_write_o(data_request_write),
        .data_request_address_o(data_request_address),
        .data_request_write_data_o(data_request_write_data),
        .data_request_byte_enable_o(data_request_byte_enable),
        .data_response_valid_i(data_response_valid_q),
        .data_response_read_data_i(data_ram_read_data),
        .data_response_error_i(data_response_error_q),
        .observation_valid_o(observation_valid_o),
        .observation_order_o(observation_order_o),
        .observation_pc_o(observation_pc_o),
        .observation_instruction_o(observation_instruction_o),
        .observation_next_pc_o(observation_next_pc_o),
        .observation_rd_address_o(observation_rd_address_o),
        .observation_rd_write_o(observation_rd_write_o),
        .observation_rd_write_data_o(observation_rd_write_data_o),
        .observation_memory_valid_o(observation_memory_valid_o),
        .observation_memory_write_o(observation_memory_write_o),
        .observation_memory_address_o(observation_memory_address_o),
        .observation_memory_read_data_o(observation_memory_read_data_o),
        .observation_memory_write_data_o(observation_memory_write_data_o),
        .observation_memory_read_mask_o(observation_memory_read_mask_o),
        .observation_memory_write_mask_o(observation_memory_write_mask_o),
        .observation_trap_o(observation_trap_o),
        .observation_cause_o(observation_cause_o),
        .observation_trap_value_o(observation_trap_value_o),
        .halted_o(halted_o)
    );
endmodule
