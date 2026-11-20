// Type-only package of nand2doom core blocks' module-private state records
// (RTL BKM "Types and shared constants live in packages"). It holds no
// constants or functions, so every closure that compiles one of these blocks
// lists it without unused-parameter lint, including the standalone
// n2d_muldiv and n2d_rv32i_core_step targets that do not list n2d_core_pkg.
// Records that use n2d_core_pkg types (n2d_ctrl, n2d_if, n2d_rv32im_core)
// live in n2d_core_pkg instead.
package n2d_core_types_pkg;
    // n2d_csr machine-mode CSR state.
    typedef struct packed {
        logic [31:0] mstatus;
        logic [31:0] mtvec;
        logic [31:0] mscratch;
        logic [31:0] mepc;
        logic [31:0] mcause;
        logic [31:0] mtval;
        logic [63:0] mcycle;
        logic [63:0] minstret;
    } t_csr_regs;

    // n2d_muldiv multiply issue registers (Q103H operands).
    typedef struct packed {
        logic signed [32:0] a;
        logic signed [32:0] b;
        logic               high;   // select the high product word
    } t_mul_issue;

    // n2d_muldiv Q104H product register.
    typedef struct packed {
        logic [63:0] product;
        logic        high;
    } t_mul_product;

    // n2d_muldiv divider phase.
    typedef enum logic [1:0] {
        DIV_IDLE  = 2'd0,
        DIV_SETUP = 2'd1,
        DIV_ITER  = 2'd2,
        DIV_DONE  = 2'd3
    } t_div_state;

    // n2d_muldiv restoring divider state and DIV/REM result reuse.
    typedef struct packed {
        t_div_state  state;
        logic        remainder_op;   // REM/REMU selects the remainder
        logic        signed_op;      // DIV/REM
        logic [31:0] rs1;            // raw operands of the current/last divide
        logic [31:0] rs2;
        logic        reusable;       // quotient/remainder belong to rs1/rs2/signed_op
        logic [5:0]  count;          // remaining restoring iterations
        logic [31:0] remainder;      // partial remainder, then |remainder|
        logic [31:0] quotient;       // dividend bits / quotient, then |quotient|
        logic [31:0] divisor;        // |divisor|
        logic        negative_quotient;
        logic        negative_remainder;
    } t_div_regs;

    // n2d_pmon counters, selectors, pending writes, inhibit and event state.
    typedef struct packed {
        logic [3:0][63:0] count;
        logic [3:0][6:0]  select;
        logic [3:0]       pending;
        logic [31:0]      inhibit;
        logic [127:0]     events;
    } pmon_regs_t;

    // n2d_rv32i_core_step sequencing state.
    typedef enum logic [2:0] {
        STEP_FETCH_REQUEST,
        STEP_FETCH_RESPONSE,
        STEP_EXECUTE,
        STEP_DATA_RESPONSE,
        STEP_HALTED
    } step_state_t;

    // n2d_rv32i_core_step state, in-flight memory operation and the
    // registered retirement observation record (observation pulses reset low).
    typedef struct packed {
        step_state_t state;
        logic [31:0] pc;
        logic [31:0] instruction;
        logic [31:0] instruction_pc;
        logic [63:0] order;
        logic        memory_is_load;
        logic [4:0]  memory_rd;
        logic [31:0] memory_address;
        logic [31:0] memory_write_data;
        logic [31:0] memory_next_pc;
        logic        observation_valid;
        logic [63:0] observation_order;
        logic [31:0] observation_pc;
        logic [31:0] observation_instruction;
        logic [31:0] observation_next_pc;
        logic [4:0]  observation_rd_address;
        logic        observation_rd_write;
        logic [31:0] observation_rd_write_data;
        logic        observation_memory_valid;
        logic        observation_memory_write;
        logic [31:0] observation_memory_address;
        logic [31:0] observation_memory_read_data;
        logic [31:0] observation_memory_write_data;
        logic [3:0]  observation_memory_read_mask;
        logic [3:0]  observation_memory_write_mask;
        logic        observation_trap;
        logic [31:0] observation_cause;
        logic [31:0] observation_trap_value;
    } step_regs_t;
endpackage
