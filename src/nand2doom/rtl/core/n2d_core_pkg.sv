// nand2doom pipelined RV32IM core package
//----------------------------------------------------------
// Opcode constants, the Q102H decode record, the stage payload records
// of the six-stage n2d_rv32im_core (Q100H fetch .. Q105H writeback), and the
// state records of the core blocks built on them (n2d_ctrl, n2d_if and the
// core's stage registers). Stage naming follows the nand2linux pipeline; the
// records carry only machine-mode RV32IM state. Core blocks compiled without
// this package keep their state records in n2d_core_types_pkg.
//----------------------------------------------------------

package n2d_core_pkg;

    localparam logic [6:0] OPCODE_LOAD     = 7'b0000011;
    localparam logic [6:0] OPCODE_MISC_MEM = 7'b0001111;
    localparam logic [6:0] OPCODE_OP_IMM   = 7'b0010011;
    localparam logic [6:0] OPCODE_AUIPC    = 7'b0010111;
    localparam logic [6:0] OPCODE_STORE    = 7'b0100011;
    localparam logic [6:0] OPCODE_OP       = 7'b0110011;
    localparam logic [6:0] OPCODE_LUI      = 7'b0110111;
    localparam logic [6:0] OPCODE_BRANCH   = 7'b1100011;
    localparam logic [6:0] OPCODE_JALR     = 7'b1100111;
    localparam logic [6:0] OPCODE_JAL      = 7'b1101111;
    localparam logic [6:0] OPCODE_SYSTEM   = 7'b1110011;

    localparam logic [31:0] INSN_ECALL  = 32'h0000_0073;
    localparam logic [31:0] INSN_EBREAK = 32'h0010_0073;
    localparam logic [31:0] INSN_MRET   = 32'h3020_0073;
    // JALR x0, 0(x1): the function return the return-address stack predicts
    localparam logic [31:0] INSN_RET    = 32'h0000_8067;
    localparam logic [4:0]  REG_RA      = 5'd1;

    // Fetch predictor sizes (wiki/src/nand2doom/rtl/core/mas.md "Return-address stack" and
    // "Fetch branch target buffer"); n2d_rv32im_core parameters default to
    // these, and 0 removes either predictor.
    localparam int RAS_DEPTH_DEFAULT = 8;
    localparam int BTB_ENTRIES_DEFAULT = 1024;
    // Fetch BTB entry: one 36-bit word (256 per M9K). The partial tag is the PC
    // bits above the index; the target is a word address in the 64 MiB
    // SDRAM, where all code lives. Q101H and Q102H verify every prediction,
    // so an alias or a stale entry costs a refetch, never correctness.
    localparam int BTB_TAG_BITS = 8;
    localparam int BTB_TARGET_BITS = 24;
    typedef struct packed {
        logic                       valid;
        logic [1:0]                 counter;     // taken when counter[1]
        logic                       is_return;   // predict from the RAS
        logic [BTB_TAG_BITS-1:0]    tag;
        logic [BTB_TARGET_BITS-1:0] target;      // target[25:2]
    } t_btb_entry;

    localparam logic [6:0] FUNCT7_MULDIV = 7'b0000001;

    // Architectural trap causes used by the machine-mode adapter.
    localparam logic [31:0] CAUSE_INSN_MISALIGNED  = 32'd0;
    localparam logic [31:0] CAUSE_INSN_ACCESS      = 32'd1;
    localparam logic [31:0] CAUSE_ILLEGAL          = 32'd2;
    localparam logic [31:0] CAUSE_BREAKPOINT       = 32'd3;
    localparam logic [31:0] CAUSE_LOAD_MISALIGNED  = 32'd4;
    localparam logic [31:0] CAUSE_LOAD_ACCESS      = 32'd5;
    localparam logic [31:0] CAUSE_STORE_MISALIGNED = 32'd6;
    localparam logic [31:0] CAUSE_STORE_ACCESS     = 32'd7;
    localparam logic [31:0] CAUSE_ECALL_M          = 32'd11;

    // Debug run state: RUN fetches; DRAIN stops fetch and lets Q102H..Q105H
    // and any owed fetch response finish; HALTED acknowledges the halt.
    typedef enum logic [1:0] {
        RUN_STATE_RUN    = 2'd0,
        RUN_STATE_DRAIN  = 2'd1,
        RUN_STATE_HALTED = 2'd2
    } t_run_state;

    // PMON-only record of why fetch was flushed on the previous edge, so the
    // refill clocks that follow are attributed to it (wiki/src/nand2doom/rtl/core/mas.md
    // "Performance monitor", IDs 83..86). It never feeds control.
    typedef enum logic [1:0] {
        FETCH_REFILL_NONE       = 2'd0,
        FETCH_REFILL_PREDICT    = 2'd1,
        FETCH_REFILL_MISPREDICT = 2'd2,
        FETCH_REFILL_REDIRECT   = 2'd3
    } t_fetch_refill;

    // Q101H instruction slot (the instruction-queue head or the bypassed
    // fetch response) and the Q102H instruction register. predicted_taken
    // records that fetch follows predicted_target after the slot: the fetch
    // BTB's target accepted by Q101H (no refill clock), or a Q101H
    // prediction that redirected fetch as the slot entered Q102H (a JAL's or
    // backward branch's target, or a return's return-address-stack entry).
    // Q102H redirects when the resolved direction differs or a predicted
    // transfer resolves to a different target. In the queue, fetch_taken
    // says fetch already continued at predicted_target; btb_hit and
    // btb_counter are the BTB entry seen at fetch, for training in Q102H.
    typedef struct packed {
        logic        valid;
        logic [31:0] pc;
        logic [31:0] instruction;
        logic        fetch_error;
        logic        fetch_taken;
        logic        btb_hit;
        logic [1:0]  btb_counter;
        logic        predicted_taken;
        logic [31:0] predicted_target;
    } t_fetch_slot;

    // Sign-extended B- and J-format immediates, shared by the Q102H decode
    // and the Q101H static branch predictor so both compute one target.
    // Each format reads only its own instruction bits.
    /* verilator lint_off UNUSEDSIGNAL */
    function automatic logic [31:0] imm_b(input logic [31:0] instruction);
        return {{19{instruction[31]}}, instruction[31], instruction[7],
                instruction[30:25], instruction[11:8], 1'b0};
    endfunction

    function automatic logic [31:0] imm_j(input logic [31:0] instruction);
        return {{11{instruction[31]}}, instruction[31], instruction[19:12],
                instruction[20], instruction[30:21], 1'b0};
    endfunction
    /* verilator lint_on UNUSEDSIGNAL */

    // Q102H decode of the instruction register. Base OP/OP-IMM legality comes
    // from n2d_rv32i_decode_alu in n2d_exe; every other class is decided here.
    typedef struct packed {
        logic [4:0]  rs1;
        logic [4:0]  rs2;
        logic [4:0]  rd;
        logic        uses_rs1;
        logic        uses_rs2;
        logic        rd_write;       // writes a nonzero rd when it retires
        logic        is_lui;
        logic        is_auipc;
        logic        is_jal;
        logic        is_jalr;
        logic        is_branch;
        logic        is_load;
        logic        is_store;
        logic        is_base_alu;    // OP/OP-IMM through n2d_rv32i_decode_alu
        logic        is_mul;         // MUL/MULH/MULHSU/MULHU
        logic        is_div;         // DIV/DIVU/REM/REMU
        logic        is_fence;
        logic        is_csr;
        logic        is_ecall;
        logic        is_ebreak;
        logic        is_mret;
        logic        class_legal;    // legal class; base ALU legality is separate
        logic [2:0]  funct3;
        logic [11:0] csr_address;
        logic        csr_write;      // CSR write intent from the encoded operand
        logic [31:0] imm_i;
        logic [31:0] imm_s;
        logic [31:0] imm_b;
        logic [31:0] imm_u;
        logic [31:0] imm_j;
    } t_dec;

    // Q103H record: an instruction that left Q102H. A memory operation's
    // request was accepted on the Q102H-to-Q103H edge and its response is
    // owed to this stage; a multiply's operands are registered in n2d_muldiv
    // and its product is registered on the Q103H-to-Q104H edge.
    typedef struct packed {
        logic        valid;
        logic [31:0] pc;
        logic [31:0] instruction;
        logic [31:0] next_pc;
        logic [4:0]  rd;
        logic        rd_write;
        logic [31:0] result;           // ALU, link, CSR or divide result
        logic        is_load;
        logic        is_store;
        logic        is_mul;
        logic [2:0]  funct3;
        logic [31:0] mem_address;
        logic [31:0] mem_write_data;   // aligned bus word
        logic [3:0]  mem_mask;
        logic        trap;
        logic [31:0] cause;
        logic [31:0] trap_value;
        logic        counted_at_commit; // CSR/MRET: minstret and INSTRET counted in Q102H
    } t_q103;

    // Q104H/Q105H retirement record; field meanings follow the shared
    // observation contract and every field without an effect is zero. In
    // the registered Q104H record a multiply's rd_write_data is zero; the
    // core substitutes the n2d_muldiv product register before forwarding
    // and before the record moves to Q105H.
    typedef struct packed {
        logic        valid;
        logic [63:0] order;
        logic [31:0] pc;
        logic [31:0] instruction;
        logic [31:0] next_pc;
        logic [4:0]  rd_address;
        logic        rd_write;
        logic [31:0] rd_write_data;
        logic        memory_valid;
        logic        memory_write;
        logic [31:0] memory_address;
        logic [31:0] memory_read_data;
        logic [31:0] memory_write_data;
        logic [3:0]  memory_read_mask;
        logic [3:0]  memory_write_mask;
        logic        trap;
        logic [31:0] cause;
        logic [31:0] trap_value;
        logic        counted_at_commit;
        logic        is_mul;           // Q104H: rd_write_data is the muldiv product
    } t_retire;

    // n2d_ctrl control state: run/halt state and the architectural PC.
    typedef struct packed {
        t_run_state  state;
        logic [31:0] arch_pc;
    } t_ctrl_regs;

    // n2d_if fetch state: the next fetch PC, the one outstanding word and
    // the two-entry fetch queue.
    typedef struct packed {
        logic [31:0] pc;
        logic        outstanding;
        logic        outstanding_stale;
        logic [31:0] outstanding_pc;
        // The fetch BTB entry seen when the outstanding word was requested
        logic        outstanding_fetch_taken;
        logic        outstanding_btb_hit;
        logic [1:0]  outstanding_btb_counter;
        logic [31:0] outstanding_target;
        t_fetch_slot entry0;
        t_fetch_slot entry1;
    } t_if_regs;

    // n2d_rv32im_core stage registers: Q102H slot, Q103H record, Q104H and
    // Q105H retirement records and the retirement order counter.
    typedef struct packed {
        t_fetch_slot slot_Q102H;
        t_q103       q103;
        t_retire     q104;
        t_retire     q105;
        logic [63:0] order;
    } t_pipe_regs;

endpackage
