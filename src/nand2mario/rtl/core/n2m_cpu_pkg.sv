`default_nettype none

package n2m_cpu_pkg;
    typedef enum logic [4:0] {
        ALU_ADD = 5'd0,
        ALU_ADC = 5'd1,
        ALU_SUB = 5'd2,
        ALU_SBC = 5'd3,
        ALU_AND = 5'd4,
        ALU_XOR = 5'd5,
        ALU_OR = 5'd6,
        ALU_CP = 5'd7,
        ALU_INC = 5'd8,
        ALU_DEC = 5'd9,
        ALU_DAA = 5'd10,
        ALU_CPL = 5'd11,
        ALU_SCF = 5'd12,
        ALU_CCF = 5'd13,
        ALU_RLCA = 5'd14,
        ALU_RRCA = 5'd15,
        ALU_RLA = 5'd16,
        ALU_RRA = 5'd17,
        ALU_RLC = 5'd18,
        ALU_RRC = 5'd19,
        ALU_RL = 5'd20,
        ALU_RR = 5'd21,
        ALU_SLA = 5'd22,
        ALU_SRA = 5'd23,
        ALU_SWAP = 5'd24,
        ALU_SRL = 5'd25,
        ALU_BIT = 5'd26,
        ALU_RES = 5'd27,
        ALU_SET = 5'd28
    } t_alu_operation;
    typedef struct packed {
        logic [7:0] a;
        logic [7:0] f;
        logic [7:0] b;
        logic [7:0] c;
        logic [7:0] d;
        logic [7:0] e;
        logic [7:0] h;
        logic [7:0] l;
        logic [15:0] sp;
    } t_cpu_registers;

    typedef enum logic [2:0] {
        ACCESS_IDLE = 3'd0,
        ACCESS_OPCODE = 3'd1,
        ACCESS_OPERAND = 3'd2,
        ACCESS_DATA = 3'd3,
        ACCESS_STACK = 3'd4
    } t_access_kind;

    // Prepared transaction; the bus owner acknowledges effects once.
    typedef struct packed {
        logic [15:0] address;
        logic [7:0] write_data;
        logic write_enable;
        t_access_kind access_kind;
    } t_cpu_bus_plan;

    typedef struct packed {
        t_cpu_registers registers;
        logic [7:0] opcode;
        logic cb_bank;
        logic [2:0] step;
        logic [15:0] pc;
        logic [15:0] temporary;
        logic [7:0] data;
    } t_cpu_execute_request;

    typedef struct packed {
        t_cpu_registers registers_after;
        logic [15:0] pc_after;
        logic [15:0] temporary_after;
        t_cpu_bus_plan plan;
        logic finish;
        logic prefix;
        logic halt_request;
        logic stop_request;
        logic illegal;
        logic enable_interrupts;
        logic disable_interrupts;
        logic return_interrupt;
    } t_cpu_execute_result;

    // Architectural payload captured on a completing machine enable.
    typedef struct packed {
        logic valid;
        logic is_interrupt;
        t_cpu_registers registers_after;
        logic [15:0] pc_before;
        logic [15:0] pc_after;
        logic [23:0] fetched_bytes;
        logic [1:0] fetched_length;
        logic ime_after;
        logic halted_after;
        logic stopped_after;
    } t_cpu_retire_capture;

    typedef enum logic [1:0] {
        STOP_CONTINUE = 2'd0,
        STOP_HALT = 2'd1,
        STOP_OSCILLATOR = 2'd2
    } t_cpu_stop_action;

    function automatic logic [7:0] read_byte(input t_cpu_registers r, input logic [2:0] index);
        case (index)
            0: read_byte = r.b;
            1: read_byte = r.c;
            2: read_byte = r.d;
            3: read_byte = r.e;
            4: read_byte = r.h;
            5: read_byte = r.l;
            7: read_byte = r.a;
            default: read_byte = 0;  // Why: (HL) is a separate bus access, so there is no register to return here.
        endcase
    endfunction

    function automatic t_cpu_registers write_byte(input t_cpu_registers r, input logic [2:0] index, input logic [7:0] data);
        t_cpu_registers result;
        result = r;
        case (index)
            0: result.b = data;
            1: result.c = data;
            2: result.d = data;
            3: result.e = data;
            4: result.h = data;
            5: result.l = data;
            7: result.a = data;
            default: begin end
        endcase
        return result;
    endfunction

    function automatic logic [15:0] read_pair(input t_cpu_registers r, input logic [1:0] index, input logic stack_pair);
        case (index)
            0: read_pair = {r.b, r.c};
            1: read_pair = {r.d, r.e};
            2: read_pair = {r.h, r.l};
            default: read_pair = stack_pair ? {r.a, r.f} : r.sp;
        endcase
    endfunction

    function automatic t_cpu_registers write_pair(input t_cpu_registers r, input logic [1:0] index, input logic stack_pair, input logic [15:0] data);
        t_cpu_registers result;
        result = r;
        case (index)
            0: {result.b, result.c} = data;
            1: {result.d, result.e} = data;
            2: {result.h, result.l} = data;
            default: begin
                if (stack_pair) begin
                    result.a = data[15:8];
                    result.f = {data[7:4], 4'b0};
                end else result.sp = data;
            end
        endcase
        return result;
    endfunction

    function automatic logic condition_true(input logic [1:0] condition_code, input logic [7:0] f);
        case (condition_code)
            0: condition_true = !f[7];
            1: condition_true = f[7];
            2: condition_true = !f[4];
            default: condition_true = f[4];
        endcase
    endfunction
    typedef enum logic [2:0] {MODE_FETCH, MODE_EXECUTE, MODE_HALT, MODE_STOP, MODE_INTERRUPT, MODE_LOCK} t_cpu_mode;
    typedef struct packed {
        t_cpu_mode mode;
        logic [15:0] pc;
        logic [15:0] instruction_pc;
        logic [15:0] temporary;
        logic [15:0] irq_pc;
        logic irq_select_pending;
        logic [7:0] opcode;
        logic [23:0] fetched;
        logic [1:0] length;
        logic [2:0] step;
        logic cb_bank;
        logic ime;
        logic ime_delay;
    } t_cpu_control;

    // Architectural reset values: the DMG ABC post-boot state (Pan Docs),
    // since there is no boot ROM. Consumed by n2m_cpu_control.
    function automatic t_cpu_registers reset_registers;
        t_cpu_registers r;
        r.a = 8'h01;
        r.f = 8'hb0;
        r.b = 8'h00;
        r.c = 8'h13;
        r.d = 8'h00;
        r.e = 8'hd8;
        r.h = 8'h01;
        r.l = 8'h4d;
        r.sp = 16'hfffe;
        return r;
    endfunction

    function automatic t_cpu_control reset_control;
        t_cpu_control c;
        c = '0;
        c.mode = MODE_FETCH;
        c.pc = 16'h0100;
        c.instruction_pc = 16'h0100;
        return c;
    endfunction
endpackage
