---
status: draft
---

# RTL coding

How to write SystemVerilog RTL in this factory, and how a reviewer checks it. One document for both readers: the RTL author follows each rule, and the reviewer crewmate flags the same rule when the diff breaks it. Block shape and seams are in [architecture.md](architecture.md); comments are in [code-comments.md](../writing/code-comments.md).

## How to read this

- Every rule below is a **must** unless it says **should** or **may**. Each rule ends with **Check:**, saying what enforces it: `lint` (Verilator `-Wall` as the simulation build runs it today; the L0 lint gate, #123, will add more), `sim`, `formal`, or `review`. Which rules a reviewer skips because a tool already checks them is set in [review.md](../workflow/review.md). Why: the author knows which rules bend, and the reviewer knows which ones a tool has already checked.
- In review, a broken **must** on a line the diff adds or changes is a **should** finding; a broken **should** with no stated reason is a **nit**. Why: one severity scale per rule keeps author and reviewer on the same page.
- Existing code that predates a rule is not a finding unless the diff touches that line. Fix it when you are in the file anyway, or open a cleanup issue. Why: style migrations get their own pull requests, never mixed into behaviour changes ([architecture.md](architecture.md) refactor rules).

## Rules: files and names

- Put one module, package or interface in each file, named after it: `n2m_timer.sv` holds `module n2m_timer`. Memory wrappers with vendor variants may share a base name with a suffix (`n2a_sdp_ram_intel.sv`). Why: a reader and a tool both find a unit from its name. Check: lint (`DECLFILENAME`) and review (vendor-suffix variants).
- Prefix every module and package with its project: `n2a_` for `src/common`, `n2m_` for nand2mario, `n2d_` for nand2doom; a new project picks a short prefix in its HAS. Why: blocks from several projects elaborate together in one simulation and one FPGA image. Check: review.
- Name ports with a direction suffix: `_i`, `_o`, `_io`. Name the clock `clk_i` and the reset `reset_i`; a block with several clocks or resets adds the domain (`clk_a_i`, `reset_a_i`). Why: direction and domain are visible at every use. Check: review.
- Name a flop's output `<name>_q` and its next-state value `<name>_d`. A pipeline register may use a second stage suffix (`rgb_qq`) or a pipeline stage tag (`slot_Q102H`, `next_pc_Q102H_i`) where the block's MAS defines its stages. Why: every reader can tell state from logic at a glance. Check: review.
- Use `lower_snake_case` for signals, variables, instances (`u_<role>`) and functions; `UPPER_SNAKE_CASE` for parameters, localparams, enum members and macros; `t_<name>` for every typedef. Why: one spelling per kind of name, across every project. Check: review.
- Use the spec's and the glossary's domain names, per [architecture.md](architecture.md). Never encode a width or a type in a name (`addr32`, `state_enum`). Why: the name survives the next width change. Check: review.

## Rules: formatting

- Indent with 4 spaces, never tabs. Align a continuation line under the operand it continues. Why: one indent across projects keeps diffs small when code moves between them. Check: review (a formatter check is a follow-up).
- Include a header by its path from the repo root: `` `include "src/common/rtl/pkg/macros.vh" ``, and include it in every file that uses a macro. Why: root-relative includes resolve the same in Verilator, Yosys and Quartus. Check: review (the L0 lint gate, #123, later).
- Name shared macros and include guards without a project prefix (`` `DFF_RST ``, `MACROS_VH`), and define a new shared macro only in `src/common/rtl/pkg/`. Why: one vocabulary for every project; the macros live in one place, so a prefix adds nothing. Check: review.

## Rules: sequential logic

- You **must** write every flop through the flop macros in `src/common/rtl/pkg/macros.vh` (`` `DFF ``, `` `DFF_EN ``, `` `DFF_RST ``, `` `DFF_RST_VAL ``, `` `DFF_RST_EN ``, the `DFF_ARRAY_*` forms) and every inferred memory through the `MEM_*` macros. Never write `always_ff` in a block. Why: one reviewed pattern per kind of state makes reset, enable and inference identical everywhere. Check: review (lint gate later).
- Build every next-state value in `always_comb` or `assign`, with blocking assignments. Never use `always @`, `reg`, `always_latch`, non-blocking assignments outside the macros, or `#` delays. Why: the macros own the clock edge; everything else is combinational and simulates the way it synthesises. Check: lint (`COMBDLY`, a non-blocking assignment in combinational code) and review (`always @`, `reg`, `always_latch`, `#` delays, which `-Wall` passes).
- In `always_comb`, you should assign every output a default at the top (often `x_d = x_q;`) before any branch. Why: a path that forgets an assignment infers a latch. Check: lint (`LATCH`).
- Never use `initial` in RTL except through `MEM_INIT_*` for memory contents and `ASSERT_PARAM` / `ASSERT_INITIAL` for elaboration checks. Why: FPGA and ASIC flows treat `initial` differently; reset is the only portable start state. Check: review.

## Rules: reset and clocks

- Reset is synchronous and active-high on `reset_i`: use `DFF_RST`, `DFF_RST_VAL` or `DFF_RST_EN`. Why: one reset style keeps timing, simulation and formal assumptions the same in every block. Check: review.
- Use an asynchronous reset (`DFF_ARST_*`) only with a `Why:` comment naming the reason, such as a reset synchronizer or logic that must reset while its clock is stopped. Why: async reset adds a recovery and removal path to time-close. Check: review.
- You should reset only the state that needs a known value (control, valid bits, FSM state); leave pure datapath flops without reset (`DFF`, `DFF_EN`). Why: fewer reset nets, and a datapath that depends on reset is a bug the valid bits should catch. Check: review.
- Cross a clock domain only through the synchronizers in `src/common/rtl/sync/`, and name the crossing in the block's MAS. Why: a hand-made crossing is invisible to lint and metastable in hardware. Check: review.

## Rules: combinational logic

- Size every literal and every arithmetic result: `14'd0`, `14'(tick)`, `{1'b0, a} + {1'b0, b}`. Why: unsized math widens or truncates silently. Check: lint (`WIDTHTRUNC`, `WIDTHEXPAND`) and review (unsized literals and missing carry bits, which `-Wall` passes).
- You should use `unique case` when exactly one item must match, and always give a `default`. Use `case` (not `casex`); use `casez` with `?` only for decode tables. Why: `unique` documents and checks intent; `casex` hides `x` from simulation. Check: lint and review.
- You should prefer a `case` or a lookup over a long `if`/`else if` chain when the conditions are exclusive. Why: the structure states the exclusivity the hardware relies on. Check: review.
- Write a pure helper as `function automatic` with typed arguments; never use `task` in RTL outside the shared macros. Why: automatic functions are re-entrant and synthesise as plain logic. Check: review.

## Rules: structs

- Every bundle that crosses a port is a `typedef struct packed` named `t_<name>` in a package, with fields named in domain terms (`t_bus_request` with `valid`, `write`, `address`, `write_data`). Pass the struct through the port, not its fields. Why: one type keeps both ends of a seam in step, and adding a field changes one line. Check: review.
- A block may keep all its state in one record, `t_<block>_regs` held in `regs_q` / `regs_d` with one flop macro; it need not. Why: one record makes reset and the next-state default one line each. Check: review.
- Give every reset value a named constant built with an assignment pattern by field name: `localparam t_scanout_pipe PIPE_RESET = '{in_image_q: 1'b0, ...};`. Never reset a struct with a positional pattern. Why: a named pattern stays correct when fields are added or reordered. Check: review.
- Never use unpacked structs, unions or `interface`/`modport` in RTL. Why: Verilator, Yosys and Quartus differ on them; packed structs work in all three. Check: review.

## Rules: enums and state machines

- Declare every enum as `typedef enum logic [N-1:0]` with an explicit width, named `t_<name>`. Assign explicit encodings only when hardware or a protocol fixes them (`SDRAM_READ_CMD = 3'b101`). Why: the width is part of the interface; implicit `int` enums widen state and ports. Check: review.
- Name enum members in `UPPER_SNAKE_CASE`, prefixed with their concept: `UART_IDLE`, `UART_START`, `KBD_DECODE`. Why: members share one namespace in a package; the prefix keeps them unique and greppable. Check: review.
- Give an FSM's state signal the enum type (`t_uart_state state_q, state_d;`), never bare `logic`, and assign it only enum members. Why: waveforms and lint show names, and illegal values become visible. Check: lint (`ENUMVALUE`) and review.
- Write each FSM as one `always_comb` with `unique case (state_q)`, one item per state, and a `default` that returns to the reset state. Beside it, at module scope, add an `` `ASSERT_NEVER `` that rejects the unused encodings, or, when the enum uses every encoding, an `` `ASSERT_KNOWN `` on the state. Why: the default is the safe recovery hardware needs after an upset; the assertion makes simulation and formal fail on the illegal state instead of recovering quietly. Check: review and formal.

## Rules: parameters

- Declare overridable parameters as `parameter int unsigned` for a size or count, `parameter int` for a signed value, or `parameter bit` for a flag, in `UPPER_SNAKE_CASE`, only on blocks that are genuinely reused or sized per instance: `src/common/`, memories, FIFOs. Project blocks take their sizes from package constants. Why: a parameter is part of the interface and must be verified at every value; a project block has one value. Check: review.
- Take a parameter's default from a package constant named `<NAME>_DEFAULT` when the package owns the size. Why: one source of truth for the value the rest of the design assumes. Check: review.
- Derive dependent values as `localparam` (`localparam int unsigned ADDR_WIDTH = $clog2(DEPTH);`); never override a `localparam` and never pass a derived value as a second parameter. The memory wrappers that take `ADDR_WIDTH` next to `DEPTH` predate this rule; #126 removes it, and a new block must not copy them. Why: two parameters that must agree eventually disagree. Check: review.
- Check every parameter's legal range with `` `ASSERT_PARAM(g_<label>, <condition>, "<block> <NAME> must ...") ``. Why: an illegal value fails at elaboration with a message instead of misbehaving in hardware. Check: sim (elaboration).
- Type every constant: `localparam logic [15:0] DIV_ADDRESS = 16'hFF04;`, `localparam int DEPTH = 8;`. Why: an untyped constant takes the width of whatever it touches. Check: review.

## Rules: packages

- Give a block that needs its own types or constants a package `<prefix>_<block>_pkg.sv` beside it, in the same folder. Put types shared by several blocks of a project in that project's interfaces package (`n2m_interfaces_pkg`), and types shared by projects in `src/common/rtl/pkg/`. Why: a type lives next to its owner, and a shared seam has one definition. Check: review.
- Name package types and constants with a qualified reference in port lists and parameter lists: `input n2m_interfaces_pkg::t_bus_request io_request_i`. Use `import <pkg>::*;` only inside the module body, never in the module header or at file scope. Why: the header stays readable without the package open, and no import leaks into other files. Check: review.
- Put only `typedef`, `localparam` and pure `function automatic` helpers in a package: no state, no `parameter`, no `initial`, no DPI. Why: a package is compiled once and shared; anything stateful in it couples every user. Check: review.

## Rules: assertions and checks

- Write assertions through the macros in `src/common/rtl/pkg/assertion_macros.vh` (`ASSERT`, `ASSERT_NEVER`, `ASSERT_KNOWN`, `ASSERT_AFTER_RESET`, the `ASSERT_CLOCKED*` and `ASSERT_ERROR*` forms, and `ASSERT_PARAM` / `ASSERT_INITIAL` for elaboration checks), never raw `assert property` in a block. Why: the concurrent and clocked forms compile out under `DISABLE_ASSERTIONS` for Quartus and stay identical for simulation and formal, while `ASSERT_PARAM` fires in every flow. Check: review.
- Turn every invariant the block relies on (one-hot, mutual exclusion, handshake stability, an FSM never in an illegal state) into an assertion in the block, per [encode-lessons-in-structure.md](../principles/encode-lessons-in-structure.md); a comment that claims a constraint is handled by [code-comments.md](../writing/code-comments.md). Why: a comment never fails; an assertion fails in every simulation and proof. Check: review.
- Waive a lint warning only inline, on the narrowest span (`/* verilator lint_off UNUSEDSIGNAL */ ... lint_on`), or with `` `UNUSED_BITS `` for deliberately unused fields, with a `Why:` comment when the reason is not obvious from the port's own comment. Never waive a warning file-wide or in the build command. Why: a broad waiver hides the next real warning. Check: review.

## Steps: writing or changing RTL

1. Read the block's MAS and the project's HAS ([standard-docs.md](../design/standard-docs.md)); for a new block or interface, follow [architecture.md](architecture.md) first.
2. Write the package types and constants, then the port list, then the state declarations with their reset constants, then the `always_comb` next-state logic, then the flop macros, then the assertions.
3. Run lint and the block's simulation targets; fix every new warning or waive it per the rules above.
4. Update the MAS in the same pull request when a port, a type, a parameter or an FSM changed.

## Steps: reviewing RTL

Inside [review.md](../workflow/review.md) step 4 (Standards), for every SystemVerilog line the diff adds or changes:

1. Walk the rule sections above in order (files and names, formatting, sequential, reset, combinational, structs, enums, parameters, packages, assertions) and record each broken rule as a finding with its `file:line`, the rule's heading and the smallest fix.
2. Give it the severity from "How to read this"; a broken rule that also causes a real failure takes the failure's severity instead, proven per review.md step 6.

## Exit criteria

- Every line the change adds or touches follows every **must** above, and every **should** it breaks has a stated reason.
- Lint and the block's simulation targets pass with no new warnings or waivers beyond those the rules allow.
- The MAS agrees with the ports, types, parameters and FSMs in the RTL.
