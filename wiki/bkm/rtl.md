---
status: draft
upstream: mattpocock/skills skills/engineering/codebase-design/{SKILL,DEEPENING,DESIGN-IT-TWICE}.md @ abeeaf95, mattpocock/skills skills/engineering/improve-codebase-architecture/SKILL.md @ abeeaf95, mattpocock/skills skills/productivity/teach/SKILL.md @ abeeaf95 (MIT, see SOURCES.md); cursor/plugins pstack/skills/{how,why,teach}/SKILL.md @ aaeb0ea, cursor/plugins pstack/skills/poteto-mode/playbooks/refactoring.md @ aaeb0ea (MIT, see SOURCES.md)
---

# RTL

The `rtl` discipline: shaping blocks and their seams, and writing and reviewing SystemVerilog RTL. The RTL author and the reviewer follow the same rules.

## Architecture of blocks

How to shape blocks and the seams between them, find where the existing structure should change, refactor it safely, and explain how and why a block is built. RTL lives under `src/<project>/rtl/<block>/`; the design's architecture is its HAS, `wiki/src/<project>/rtl/has.md`, and each block's design its MAS, `wiki/src/<project>/rtl/<block>/mas.md` ([standard documents](layout.md#standard-documents)). There is no separate architecture discipline (ADR 0013): a behavioural model of a block is a DV reference model under `src/<project>/dv/<block>/`, or a throwaway prototype per [planning and specs](planning.md#planning-and-specs).

### Terms

**Interface**, **seam**, **deep block** (and a shallow one) and the **deletion test** are defined in the [glossary](../project/GLOSSARY.md).

### Rules

- Make blocks deep: shrink the port list, the protocol and the parameters before adding behaviour behind them; hide FSM state, internal flags and timing choices inside. Why: depth gives neighbours leverage and puts change, bugs and verification in one place.
- Use the spec's and the glossary's domain names for blocks and seams, and follow [Model the Domain](principles.md#model-the-domain) and [Foundational Thinking](principles.md#foundational-thinking) for what sits behind the seam. Why: a seam named after a domain concept stays put when the code moves.
- Put a seam only where something actually varies across it: two implementations of one interface (the RTL and its architecture model, two bus fabrics, a hard macro and its behavioural model). A seam with one side is indirection. Why: every seam is a protocol to specify, verify and time-close.
- Keep internal seams internal: sub-blocks, assertions and formal properties a block uses for itself do not appear in its interface. Why: exposing them to make a test easy makes every neighbour depend on them.
- If a test must reach past a block's interface to check it, treat the block as the wrong shape ([dv.md](dv.md) tests only through ports). Why: the interface is the test surface; needing to reach inside means the seam is in the wrong place.
- You **must** compare at least two radically different candidate structures before committing a new block, a new seam or a changed interface (Design it twice). Why: the first design is rarely the best, and an interface is the hardest thing to change later ([Exhaust the Design Space](principles.md#exhaust-the-design-space)).
- Judge structure by reader load: a change that does not make the block easier to trace is reverted, per [Minimize Reader Load](principles.md#minimize-reader-load). Why: that is the measure of whether a reshape was worth it.
- Respect the ADRs in `wiki/project/adr/`. Raise a candidate that contradicts one only when the friction is real, and say which ADR and why it is worth reopening. Why: settled decisions are not re-argued for free.
- Never mix a refactor with new behaviour. A bug or missing feature found while refactoring gets its own issue ([labels](issues.md#labels)). Why: a reviewer cannot tell a structure change from a behaviour change in one diff.

### Steps: design it twice

1. Frame the problem in the issue: the constraints any interface must meet (throughput, latency, area, clock domains, the protocols of the neighbours), and each dependency with how it is tested across the seam: pure logic (merge it in and test through the new interface); a memory or macro with a behavioural model (keep the seam internal, test with the model); another block of ours (specify the protocol at the seam, stand in a bus functional model in DV); vendor IP (wrap it at a seam, test against the vendor model or a stub).
2. Draft two or more candidates, each under a different constraint: fewest ports and entry points; the common transaction trivial; most flexible; or one interface shared by RTL and architecture model. A crewmate drafts them itself; the first mate may instead dispatch one crewmate per constraint.
3. For each, write: the interface (ports, protocol, invariants, ordering, latency, error modes), one transaction sketched cycle by cycle, what it hides, the dependency strategy, and where it is thin.
4. Compare them in prose on depth, locality (where a future change lands) and seam placement, and on any number you can estimate per [Explain the Number](principles.md#explain-the-number). Recommend one, or a hybrid. Be opinionated.
5. Record the choice and the rejected options in the spec (or, before the block exists, in the PRD's Blocks section per [planning and specs](planning.md#planning-and-specs)). If the reason would stop a future crewmate re-proposing a rejected option, write an ADR ([ADR format](planning.md#adr-format)). If it needs a captain's call, grill per [grilling](planning.md#grilling).

### Steps: find deepening opportunities

1. Scope first: take the direction a captain or the issue names; otherwise find hot spots with `git log --oneline -- src/<project>/rtl/` and look there. Why: deepening pays off where change keeps landing.
2. Read the glossary, the ADRs and the specs of the area, then the RTL. Note friction: shallow pass-through blocks (deletion test), the same control logic or FSM duplicated across blocks, leaky interfaces (sideband signals, exposed internal state, a protocol that only works with one neighbour's timing), one concept spread over many small blocks, blocks untestable through their ports.
3. For each candidate write: the blocks involved, the problem, the change in plain words, the benefit in locality, leverage and tests, and a strength (`Strong`, `Worth exploring`, `Speculative`). Do not propose interfaces yet.
4. Post the list as a comment on your issue, top recommendation first, and report it to the first mate. Open an issue for each candidate it or a captain picks, per [issues and epics](issues.md#issues-and-epics); a reshape that enables a feature is prefactoring and blocks it. When a captain rejects one for a load-bearing reason, offer an ADR.

### Steps: refactor a block

1. Pin the behaviour before moving anything: the block's DV regression green at a named commit, plus, where the reshape allows, a formal equivalence check of old against new RTL. Lint and elaboration are not a pin.
2. Name the missing structure and the target shape (block split, interfaces, FSMs), per [Redesign From First Principles](principles.md#redesign-from-first-principles).
3. Subtract first: delete dead logic, collapse one-neighbour pass-through blocks, drop unused ports, per [Subtract Before You Add](principles.md#subtract-before-you-add) and [Laziness Protocol](principles.md#laziness-protocol).
4. Move in small steps, each keeping the pin green. For an interface change, migrate every instance and delete the old ports in the same wave, with no shim, per [Migrate Callers Then Delete Legacy APIs](principles.md#migrate-callers-then-delete-legacy-apis); when the change is too wide for one pull request, split it expand–contract per [issues and epics](issues.md#issues-and-epics); for a planned multi-phase rewrite, follow [Outcome-Oriented Execution](principles.md#outcome-oriented-execution). Grep every renamed signal in RTL, testbench, constraints, waivers and specs.
5. Prove behaviour is unchanged on the real artifacts ([Prove It Works](principles.md#prove-it-works)): the pin passes, and synthesis area and timing moved only as explained. Change the spec in the same pull request if the interface changed.
6. Order the commits subtraction, reshape, cleanup, per [Sequence work into verifiable units](principles.md#sequence-work-into-verifiable-units).

### Steps: explain a block

Use this when a captain or a crewmate asks how a block works or why it is built that way. Change nothing.

1. **How**: read the spec, the RTL, the architecture model and the tests. Build the mental model someone new to the block needs: what it is, its interface, its data and control paths, and one transaction walked cycle by cycle.
2. **Why**: answer from the record, not the code: the issues, pull requests, decision trails and ADRs that introduced and later changed it (`git log -S <signal>`, `gh pr view`). Cite each claim, mark your inferences as inferences, and say what the record does not answer.
3. **Teach**: start with a plain definition and its common hardware name, then tie it to this block. Give the smallest complete answer first and go deeper when asked. For three or more moving parts, build the picture in a series of diagrams that each add one part. Keep the hedges from step 2.
4. If the spec lacks the reasons you had to dig for, open an issue to add them.

### Exit criteria

- A new block or changed interface has its candidates, comparison and choice in the spec or an ADR, and its tests reach it only through its interface.
- A refactor lands with its pin and equivalence evidence in the pull request, no behaviour change, and a stated reader-load gain.
- Deepening candidates are posted on the issue, and each one picked has its own issue.
- An explanation cites its sources and marks what is inference.

## RTL coding

How to write SystemVerilog RTL in this factory, and how a reviewer checks it. One document for both readers: the RTL author follows each rule, and the reviewer crewmate flags the same rule when the diff breaks it. Block shape and seams are in [architecture of blocks](#architecture-of-blocks); comments are in [code-comments.md](code-comments.md).

### How to read this

- Every rule below is a **must** unless it says **should** or **may**. Each rule ends with **Check:**, saying what enforces it: `lint` (the L0 RTL lint gate: Verilator `--lint-only -Wall` on every project top and common block, `//src/<project>:<top>.lint`, and the rule checker `//tools:rtl_lint`, whose rule names appear in backticks; [lint spec](../tools/lint/spec.md)), `sim`, `formal`, or `review`. Existing violations are frozen in `tools/lint/rtl_baseline.json`, which may only shrink. Which rules a reviewer skips because a tool already checks them is set in [review](pull-requests.md#review). Why: the author knows which rules bend, and the reviewer knows which ones a tool has already checked.
- In review, a broken **must** on a line the diff adds or changes is a **should** finding; a broken **should** with no stated reason is a **nit**. Why: one severity scale per rule keeps author and reviewer on the same page.
- Existing code that predates a rule is not a finding unless the diff touches that line. Fix it when you are in the file anyway, or open a cleanup issue. Why: style migrations get their own pull requests, never mixed into behaviour changes ([architecture of blocks](#architecture-of-blocks) refactor rules).

### Rules: files and names

- Put one module, package or interface in each file, named after it: `n2m_timer.sv` holds `module n2m_timer`. Memory wrappers with vendor variants may share a base name with a suffix (`n2a_sdp_ram_intel.sv`). Why: a reader and a tool both find a unit from its name. Check: lint (`DECLFILENAME`) and review (vendor-suffix variants).
- Prefix every module and package with its project: `n2a_` for `src/common`, `n2m_` for nand2mario, `n2d_` for nand2doom; a new project picks a short prefix in its HAS. Why: blocks from several projects elaborate together in one simulation and one FPGA image. Check: review.
- Name ports with a direction suffix: `_i`, `_o`, `_io`. Name the clock `clk_i` and the reset `reset_i`; a block with several clocks or resets adds the domain (`clk_a_i`, `reset_a_i`). A board top (`src/<project>/fpga/<board>/<prefix>_<board>.sv`) keeps the board's pin names on its ports instead (`CLOCK_50`, `LEDR`), as the board's pin assignments name them ([fpga.md](fpga.md)). Why: direction and domain are visible at every use; a pin name matches the board's manual and its Quartus settings. Check: lint (`port-suffix`, which skips board tops) and review (clock, reset and domain names).
- Name a flop's output `<name>_q` and its next-state value `<name>_d`. A pipeline register may use a second stage suffix (`rgb_qq`) or a pipeline stage tag (`slot_Q102H`, `next_pc_Q102H_i`) where the block's MAS defines its stages. Why: every reader can tell state from logic at a glance. Check: review.
- Use `lower_snake_case` for signals, variables, instances (`u_<role>`) and functions; `UPPER_SNAKE_CASE` for parameters, localparams, enum members and macros; `t_<name>` for every typedef. Why: one spelling per kind of name, across every project. Check: lint (`typedef-name`) and review (the other names).
- Use the spec's and the glossary's domain names, per [architecture of blocks](#architecture-of-blocks). Never encode a width or a type in a name (`addr32`, `state_enum`). Why: the name survives the next width change. Check: review.

### Rules: formatting

- Indent with 4 spaces, never tabs. Align a continuation line under the operand it continues. Why: one indent across projects keeps diffs small when code moves between them. Check: lint (`tab`) and review (indent width and alignment; a formatter check is a follow-up).
- Include a header by its path from the repo root: `` `include "src/common/rtl/pkg/macros.vh" ``, and include it in every file that uses a macro. Why: root-relative includes resolve the same in Verilator, Yosys and Quartus. Check: lint (`include-path`; Verilator fails a missing include) and review.
- Name shared macros and include guards without a project prefix (`` `DFF_RST ``, `MACROS_VH`), and define a new shared macro only in `src/common/rtl/pkg/`. Why: one vocabulary for every project; the macros live in one place, so a prefix adds nothing. Check: review.

### Rules: sequential logic

- You **must** write every flop through the flop macros in `src/common/rtl/pkg/macros.vh` (`` `DFF ``, `` `DFF_EN ``, `` `DFF_RST ``, `` `DFF_RST_VAL ``, `` `DFF_RST_EN ``, the `DFF_ARRAY_*` forms) and every inferred memory through the `MEM_*` macros. Never write `always_ff` in a block. Why: one reviewed pattern per kind of state makes reset, enable and inference identical everywhere. Check: lint (`always-ff`) and review (the `MEM_*` macros).
- Build every next-state value in `always_comb` or `assign`, with blocking assignments. Never use `always @`, `reg`, `always_latch`, non-blocking assignments outside the macros, or `#` delays. Why: the macros own the clock edge; everything else is combinational and simulates the way it synthesises. Check: lint (`COMBDLY`, a non-blocking assignment in combinational code; `always-at`, `reg`, `always-latch`, `delay`).
- In `always_comb`, you should assign every output a default at the top (often `x_d = x_q;`) before any branch. Why: a path that forgets an assignment infers a latch. Check: lint (`LATCH`).
- Never use `initial` in RTL except through `MEM_INIT_*` for memory contents and `ASSERT_PARAM` / `ASSERT_INITIAL` for elaboration checks. Why: FPGA and ASIC flows treat `initial` differently; reset is the only portable start state. Check: lint (`initial`).

### Rules: reset and clocks

- Reset is synchronous and active-high on `reset_i`: use `DFF_RST`, `DFF_RST_VAL` or `DFF_RST_EN`. Why: one reset style keeps timing, simulation and formal assumptions the same in every block. Check: review.
- Use an asynchronous reset (`DFF_ARST_*`) only with a `Why:` comment naming the reason, such as a reset synchronizer or logic that must reset while its clock is stopped. Why: async reset adds a recovery and removal path to time-close. Check: review.
- You should reset only the state that needs a known value (control, valid bits, FSM state); leave pure datapath flops without reset (`DFF`, `DFF_EN`). Why: fewer reset nets, and a datapath that depends on reset is a bug the valid bits should catch. Check: review.
- Cross a clock domain only through the synchronizers in `src/common/rtl/sync/`, and name the crossing in the block's MAS. Why: a hand-made crossing is invisible to lint and metastable in hardware. Check: review.

### Rules: combinational logic

- Size every literal and every arithmetic result: `14'd0`, `14'(tick)`, `{1'b0, a} + {1'b0, b}`. Why: unsized math widens or truncates silently. Check: lint (`WIDTHTRUNC`, `WIDTHEXPAND`) and review (unsized literals and missing carry bits, which `-Wall` passes).
- You should use `unique case` when exactly one item must match, and always give a `default`. Use `case` (not `casex`); use `casez` with `?` only for decode tables. Why: `unique` documents and checks intent; `casex` hides `x` from simulation. Check: lint (`casex`) and review (`unique`, `default`).
- You should prefer a `case` or a lookup over a long `if`/`else if` chain when the conditions are exclusive. Why: the structure states the exclusivity the hardware relies on. Check: review.
- Write a pure helper as `function automatic` with typed arguments; never use `task` in RTL outside the shared macros. Why: automatic functions are re-entrant and synthesise as plain logic. Check: lint (`task`) and review (`automatic`, typed arguments).

### Rules: structs

- Every bundle that crosses a port is a `typedef struct packed` named `t_<name>` in a package, with fields named in domain terms (`t_bus_request` with `valid`, `write`, `address`, `write_data`). Pass the struct through the port, not its fields. Why: one type keeps both ends of a seam in step, and adding a field changes one line. Check: review.
- A block may keep all its state in one record, `t_<block>_regs` held in `regs_q` / `regs_d` with one flop macro; it need not. Why: one record makes reset and the next-state default one line each. Check: review.
- Give every reset value a named constant built with an assignment pattern by field name: `localparam t_scanout_pipe PIPE_RESET = '{in_image_q: 1'b0, ...};`. Never reset a struct with a positional pattern. Why: a named pattern stays correct when fields are added or reordered. Check: review.
- Never use unpacked structs, unions or `interface`/`modport` in RTL. Why: Verilator, Yosys and Quartus differ on them; packed structs work in all three. Check: lint (`struct-packed`, `no-union-interface`).

### Rules: enums and state machines

- Declare every enum as `typedef enum logic [N-1:0]` with an explicit width, named `t_<name>`. Assign explicit encodings only when hardware or a protocol fixes them (`SDRAM_READ_CMD = 3'b101`). Why: the width is part of the interface; implicit `int` enums widen state and ports. Check: lint (`enum-width`, `typedef-name`) and review (encodings).
- Name enum members in `UPPER_SNAKE_CASE`, prefixed with their concept: `UART_IDLE`, `UART_START`, `KBD_DECODE`. Why: members share one namespace in a package; the prefix keeps them unique and greppable. Check: review.
- Give an FSM's state signal the enum type (`t_uart_state state_q, state_d;`), never bare `logic`, and assign it only enum members. Why: waveforms and lint show names, and illegal values become visible. Check: lint (`ENUMVALUE`) and review.
- Write each FSM as one `always_comb` with `unique case (state_q)`, one item per state, and a `default` that returns to the reset state. Beside it, at module scope, add an `` `ASSERT_NEVER `` that rejects the unused encodings, or, when the enum uses every encoding, an `` `ASSERT_KNOWN `` on the state. Why: the default is the safe recovery hardware needs after an upset; the assertion makes simulation and formal fail on the illegal state instead of recovering quietly. Check: review and formal.

### Rules: parameters

- Declare overridable parameters as `parameter int unsigned` for a size or count, `parameter int` for a signed value, or `parameter bit` for a flag, in `UPPER_SNAKE_CASE`, only on blocks that are genuinely reused or sized per instance: `src/common/`, memories, FIFOs. Project blocks take their sizes from package constants. Why: a parameter is part of the interface and must be verified at every value; a project block has one value. Check: review.
- Take a parameter's default from a package constant named `<NAME>_DEFAULT` when the package owns the size. Why: one source of truth for the value the rest of the design assumes. Check: review.
- Derive dependent values as `localparam` (`localparam int unsigned ADDR_WIDTH = $clog2(DEPTH);`); never override a `localparam` and never pass a derived value as a second parameter. The memory wrappers that take `ADDR_WIDTH` next to `DEPTH` predate this rule; #126 removes it, and a new block must not copy them. Why: two parameters that must agree eventually disagree. Check: review.
- Check every parameter's legal range with `` `ASSERT_PARAM(g_<label>, <condition>, "<block> <NAME> must ...") ``. Why: an illegal value fails at elaboration with a message instead of misbehaving in hardware. Check: sim (elaboration).
- Type every constant: `localparam logic [15:0] DIV_ADDRESS = 16'hFF04;`, `localparam int DEPTH = 8;`. Why: an untyped constant takes the width of whatever it touches. Check: review.

### Rules: packages

- Give a block that needs its own types or constants a package `<prefix>_<block>_pkg.sv` beside it, in the same folder. Put types shared by several blocks of a project in that project's interfaces package (`n2m_interfaces_pkg`), and types shared by projects in `src/common/rtl/pkg/`. Why: a type lives next to its owner, and a shared seam has one definition. Check: review.
- Name package types and constants with a qualified reference in port lists and parameter lists: `input n2m_interfaces_pkg::t_bus_request io_request_i`. Use `import <pkg>::*;` only inside the module body, never in the module header or at file scope. Why: the header stays readable without the package open, and no import leaks into other files. Check: lint (`header-import`) and review (qualified references).
- Put only `typedef`, `localparam` and pure `function automatic` helpers in a package: no state, no `parameter`, no `initial`, no DPI. Why: a package is compiled once and shared; anything stateful in it couples every user. Check: review.

### Rules: assertions and checks

- Write assertions through the macros in `src/common/rtl/pkg/assertion_macros.vh` (`ASSERT`, `ASSERT_NEVER`, `ASSERT_KNOWN`, `ASSERT_AFTER_RESET`, the `ASSERT_CLOCKED*` and `ASSERT_ERROR*` forms, and `ASSERT_PARAM` / `ASSERT_INITIAL` for elaboration checks), never raw `assert property` in a block. Why: the concurrent and clocked forms compile out under `DISABLE_ASSERTIONS` for Quartus and stay identical for simulation and formal, while `ASSERT_PARAM` fires in every flow. Check: review.
- Turn every invariant the block relies on (one-hot, mutual exclusion, handshake stability, an FSM never in an illegal state) into an assertion in the block, per [Encode Lessons in Structure](principles.md#encode-lessons-in-structure); a comment that claims a constraint is handled by [code-comments.md](code-comments.md). Why: a comment never fails; an assertion fails in every simulation and proof. Check: review.
- Waive a lint warning only inline, on the narrowest span (`/* verilator lint_off UNUSEDSIGNAL */ ... lint_on`), or with `` `UNUSED_BITS `` for deliberately unused fields, with a `Why:` comment when the reason is not obvious from the port's own comment. Never waive a warning file-wide or in the build command. Why: a broad waiver hides the next real warning. Check: lint (`waiver-scope`: every `lint_off` closed by a `lint_on`) and review (the span and the `Why:`).

### Steps: writing or changing RTL

1. Read the block's MAS and the project's HAS ([standard documents](layout.md#standard-documents)); for a new block or interface, follow [architecture of blocks](#architecture-of-blocks) first.
2. Write the package types and constants, then the port list, then the state declarations with their reset constants, then the `always_comb` next-state logic, then the flop macros, then the assertions.
3. Run lint (`bazel test //tools:rtl_lint //src/<project>:<top>.lint` for the tops that hold the block) and the block's simulation targets; fix every new warning or waive it per the rules above.
4. Update the MAS in the same pull request when a port, a type, a parameter or an FSM changed.

### Steps: reviewing RTL

Inside [review step 4](pull-requests.md#steps) (Standards), for every SystemVerilog line the diff adds or changes:

1. Walk the rule sections above in order (files and names, formatting, sequential, reset, combinational, structs, enums, parameters, packages, assertions) and record each broken rule as a finding with its `file:line`, the rule's heading and the smallest fix.
2. Give it the severity from "How to read this"; a broken rule that also causes a real failure takes the failure's severity instead, proven per [review step 6](pull-requests.md#steps).

### Exit criteria

- Every line the change adds or touches follows every **must** above, and every **should** it breaks has a stated reason.
- Lint and the block's simulation targets pass with no new warnings or waivers beyond those the rules allow.
- The MAS agrees with the ports, types, parameters and FSMs in the RTL.
