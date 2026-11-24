`ifndef MACROS_VH
`define MACROS_VH

`include "src/common/rtl/pkg/assertion_macros.vh"

`ifdef VERILATOR
// Explicit lint-only observation of deliberately unused fields/bits. Keep the
// exact slices visible at each call site; no functional path may read NAME.
// The simulator defines VERILATOR even with SYNTHESIS, so both simulation modes
// retain the lint evidence. Hardware tools see neither a sink nor any logic.
`define UNUSED_BITS(NAME, BITS) \
    logic NAME; \
    assign NAME = ^(BITS);
`else
`define UNUSED_BITS(NAME, BITS)
`endif

// Canonical sequential vocabulary. Every form below expands to exactly one
// state-owning always_ff process; callers keep next-state logic in blocking
// combinational code and never write a clocked process of their own.
// Argument order is normative: Q, D, CLK, then ENABLE, then RESET, then VALUE.
`define DFF(Q, D, CLK) \
    always_ff @(posedge (CLK)) begin \
        Q <= D; \
    end

`define DFF_EN(Q, D, CLK, ENABLE) \
    always_ff @(posedge (CLK)) begin \
        if (ENABLE) begin \
            Q <= D; \
        end \
    end

`define DFF_RST(Q, D, CLK, RESET) \
    always_ff @(posedge (CLK)) begin \
        if (RESET) begin \
            Q <= '0; \
        end else begin \
            Q <= D; \
        end \
    end

`define DFF_RST_VAL(Q, D, CLK, RESET, VALUE) \
    always_ff @(posedge (CLK)) begin \
        if (RESET) begin \
            Q <= VALUE; \
        end else begin \
            Q <= D; \
        end \
    end

`define DFF_RST_EN(Q, D, CLK, ENABLE, RESET, VALUE) \
    always_ff @(posedge (CLK)) begin \
        if (RESET) begin \
            Q <= VALUE; \
        end else if (ENABLE) begin \
            Q <= D; \
        end \
    end

// Asynchronous reset: only with a Why: at the call site naming the reason,
// such as a reset synchronizer or a clock that may be stopped (wiki/bkm/rtl.md,
// Rules: reset and clocks). Every other flop resets synchronously.
`define DFF_ARST_VAL(Q, D, CLK, RESET, VALUE) \
    always_ff @(posedge (CLK) or posedge (RESET)) begin \
        if (RESET) begin \
            Q <= VALUE; \
        end else begin \
            Q <= D; \
        end \
    end

// Register arrays: every element is copied from the next-state array. These
// are flop arrays (palettes, small buffers, tables), never a RAM inference
// shape. Synchronous reset clears every element and wins over the enable.
`define DFF_ARRAY_EN(MEM, NEXT_MEM, CLK, ENABLE) \
    always_ff @(posedge (CLK)) begin \
        if (ENABLE) begin \
            for (int n2a_i = $low(MEM); n2a_i <= $high(MEM); n2a_i++) begin \
                MEM[n2a_i] <= NEXT_MEM[n2a_i]; \
            end \
        end \
    end

`define DFF_ARRAY_RST_EN(MEM, NEXT_MEM, CLK, ENABLE, RESET) \
    always_ff @(posedge (CLK)) begin \
        if (RESET) begin \
            for (int n2a_i = $low(MEM); n2a_i <= $high(MEM); n2a_i++) begin \
                MEM[n2a_i] <= '0; \
            end \
        end else if (ENABLE) begin \
            for (int n2a_i = $low(MEM); n2a_i <= $high(MEM); n2a_i++) begin \
                MEM[n2a_i] <= NEXT_MEM[n2a_i]; \
            end \
        end \
    end

// Explicit-bound register array forms for an array that is itself an indexed
// slice of a larger array (for example one bank of `palette[0:1][0:255]`):
// Quartus rejects $low/$high on an indexed expression, so the caller names
// the element range. Same body, reset priority, enable, and ordering as the
// $low/$high forms; no whole-array copy is added.
`define DFF_ARRAY_EN_RANGE(MEM, NEXT_MEM, CLK, ENABLE, LOW, HIGH) \
    always_ff @(posedge (CLK)) begin \
        if (ENABLE) begin \
            for (int n2a_i = (LOW); n2a_i <= (HIGH); n2a_i++) begin \
                MEM[n2a_i] <= NEXT_MEM[n2a_i]; \
            end \
        end \
    end

`define DFF_ARRAY_RST_EN_RANGE(MEM, NEXT_MEM, CLK, ENABLE, RESET, LOW, HIGH) \
    always_ff @(posedge (CLK)) begin \
        if (RESET) begin \
            for (int n2a_i = (LOW); n2a_i <= (HIGH); n2a_i++) begin \
                MEM[n2a_i] <= '0; \
            end \
        end else if (ENABLE) begin \
            for (int n2a_i = (LOW); n2a_i <= (HIGH); n2a_i++) begin \
                MEM[n2a_i] <= NEXT_MEM[n2a_i]; \
            end \
        end \
    end

`define DFF_ARRAY_RST_RANGE(MEM, NEXT_MEM, CLK, RESET, LOW, HIGH) \
    always_ff @(posedge (CLK)) begin \
        if (RESET) begin \
            for (int n2a_i = (LOW); n2a_i <= (HIGH); n2a_i++) begin \
                MEM[n2a_i] <= '0; \
            end \
        end else begin \
            for (int n2a_i = (LOW); n2a_i <= (HIGH); n2a_i++) begin \
                MEM[n2a_i] <= NEXT_MEM[n2a_i]; \
            end \
        end \
    end

// Memory ports: one process per port, one indexed element per edge. Reads and
// writes of one array live in separate processes; nonblocking semantics make a
// same-edge same-address collision read-first (old data), and the shape stays
// a conventional inferred RAM template. No form resets or copies the array.
`define MEM_WRITE(MEM, CLK, WRITE_ENABLE, ADDRESS, DATA) \
    always_ff @(posedge (CLK)) begin \
        if (WRITE_ENABLE) begin \
            MEM[ADDRESS] <= DATA; \
        end \
    end

`define MEM_WRITE_LANES(MEM, CLK, WRITE_ENABLE, ADDRESS, DATA, LANE_ENABLE, LANE_WIDTH) \
    always_ff @(posedge (CLK)) begin \
        if (WRITE_ENABLE) begin \
            for (int n2a_l = 0; n2a_l < $bits(LANE_ENABLE); n2a_l++) begin \
                if (LANE_ENABLE[n2a_l]) begin \
                    MEM[ADDRESS][n2a_l*(LANE_WIDTH) +: (LANE_WIDTH)] <= \
                        DATA[n2a_l*(LANE_WIDTH) +: (LANE_WIDTH)]; \
                end \
            end \
        end \
    end

`define MEM_READ(Q, MEM, CLK, ADDRESS) \
    always_ff @(posedge (CLK)) begin \
        Q <= MEM[ADDRESS]; \
    end

`define MEM_READ_EN(Q, MEM, CLK, READ_ENABLE, ADDRESS) \
    always_ff @(posedge (CLK)) begin \
        if (READ_ENABLE) begin \
            Q <= MEM[ADDRESS]; \
        end \
    end

// Register file: a synchronous reset sweeps every element to zero and wins
// over the indexed write. This is a flop array, not an inferred RAM.
`define MEM_WRITE_RST(MEM, CLK, WRITE_ENABLE, ADDRESS, DATA, RESET) \
    always_ff @(posedge (CLK)) begin \
        if (RESET) begin \
            for (int n2a_i = $low(MEM); n2a_i <= $high(MEM); n2a_i++) begin \
                MEM[n2a_i] <= '0; \
            end \
        end else if (WRITE_ENABLE) begin \
            MEM[ADDRESS] <= DATA; \
        end \
    end

// Behavioral ROM/RAM initialization from a hex image, present in simulation
// and synthesis alike (Quartus infers an initialized memory). This is a
// functional contract owned by the block's specification, never a diagnostic;
// it adds no reset and never replaces a vendor backend boundary.
`define MEM_INIT_FILE(MEM, FILE) \
    initial begin \
        $readmemh(FILE, MEM); \
    end

// Simulation-selected image and knobs, present in every configuration exactly
// like the parameter-selected generate branches that use them. Each form owns
// its own time-zero initial and declared support name; the string lives inside
// a macro-owned automatic task because synthesis tools accept string variables
// only there. Nothing here changes stored data, reset, or a backend boundary.
`define MEM_INIT_PLUSARG(MEM, PLUSARG_FORMAT, PATH_BITS, MESSAGE) \
    logic [(PATH_BITS)-1:0] MEM``_init_path; \
    initial begin \
        if ($value$plusargs(PLUSARG_FORMAT, MEM``_init_path)) begin \
            $readmemh(MEM``_init_path, MEM); \
        end else begin \
            $fatal(1, MESSAGE); \
        end \
    end

`define PLUSARG_INT(VAR, FORMAT, DEFAULT) \
    initial begin \
        VAR = DEFAULT; \
        if (!$value$plusargs(FORMAT, VAR)) begin \
            VAR = DEFAULT; \
        end \
    end

`define PLUSARG_CHOICE2(VAR, FORMAT, DEFAULT, KEY_A, VALUE_A, KEY_B, VALUE_B, MESSAGE) \
    task automatic VAR``_plusarg_read(); \
        string VAR``_plusarg; \
        VAR = DEFAULT; \
        if ($value$plusargs(FORMAT, VAR``_plusarg)) begin \
            if (VAR``_plusarg == KEY_A) begin \
                VAR = VALUE_A; \
            end else if (VAR``_plusarg == KEY_B) begin \
                VAR = VALUE_B; \
            end else begin \
                $fatal(1, MESSAGE, VAR``_plusarg); \
            end \
        end \
    endtask \
    initial VAR``_plusarg_read();

`endif
