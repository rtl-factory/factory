---
status: draft
upstream: mattpocock/skills skills/engineering/to-spec/SKILL.md, skills/engineering/wayfinder/SKILL.md, skills/in-progress/loop-me/SKILL.md, skills/productivity/to-questionnaire/SKILL.md, skills/engineering/prototype/{SKILL,LOGIC}.md @ abeeaf95 (MIT, see ../LICENSE-mattpocock-skills); cursor/plugins pstack/skills/{architect,sharpen,in-your-own-words,figure-it-out}/SKILL.md, pstack/skills/poteto-mode/playbooks/{multi-phase-plan,prototype}.md @ aaeb0ea (MIT, see ../LICENSE-pstack)
---

# Planning and specs

How to turn an aligned idea into a written plan (a tool's PRD, an interface sketch for each block, the decisions behind them) before the build issues exist. Interview the captain per [grilling.md](grilling.md); split the finished plan into issues per [issues.md](../workflow/issues.md).

## Rules

- Plan on an issue: the captain's alignment issue, or a `type:spec` issue for the plan itself. A PRD reaches `main` through that issue's pull request ([flow.md](../workflow/flow.md)). Why: every change starts with an issue (ADR 0003), and the issue keeps the decisions behind the plan.
- Skip the written plan when the change fits one issue with an obvious approach; say so and go to [issues.md](../workflow/issues.md). Why: a plan for a one-issue change is overhead nobody reads.
- Before writing anything, restate in your own words the captain's goal and the problem behind it, and get the captain to confirm it. Why: a plan built on a misread goal is wrong in every section.
- Sharpen a vague request before planning it: say what a senior hardware engineer would do here that was not asked for, the rawest source they would start from (the standard, the datasheet, the reference implementation, measured traces, not a summary), the methods and tools they would use, and what other than the agent or the captain would show the result is right (a reference model, a compliance suite, a formal property, a synthesis report). Fold the answers into the plan. Why: this makes the work deeper, not bigger, and names its oracle up front.
- Settle by experiment every open question an experiment can settle (Steps: prototype); escalate to the captain only product or preference calls no run can settle. Why: a measured answer beats an opinion, and captain attention is the scarcest resource ([never-block-on-the-human](../principles/never-block-on-the-human.md)).
- Write a tool's, script's or software's PRD as `prd.md`, next to its `spec.md`, in the wiki folder that mirrors its code ([repo-layout.md](../workflow/repo-layout.md)), with these sections: **Problem** (who needs it and why), numbered testable **Requirements**, **Out of scope**, **Acceptance** (a check per requirement), and **Blocks** when hardware implements it (the blocks and their interface sketches). Write it in glossary terms and with no file paths or code, except a snippet from a prototype that states a decision better than prose (an FSM table, a register map). Why: paths go stale; behaviour and decisions do not.
- Name in the PRD, for each requirement, the highest place where it is verified (a block's interface, a subsystem testbench, a formal harness), reusing existing ones and adding as few new ones as possible. Why: checks at a high place survive refactors; each new one is a testbench to build and keep.
- Sketch each block's interfaces before any RTL (Steps: sketch). Keep the sketch in the PRD's Blocks section; the project's HAS and the block's MAS ([standard-docs.md](standard-docs.md)) are written in that block's first slice, with its code ([flow.md](../workflow/flow.md) step 5). Why: the spec and the code change together; a spec far ahead of the code misleads like one that lags it.
- A plan is done when a crewmate could build every slice from it without asking a question. Why: every question a crewmate must ask is a decision the plan left open.
- Record every planning decision as a comment on the issue it belongs to, with its evidence link ([reporting.md](../workflow/reporting.md) Decision trail). Why: the next session, or a reviewer, must find why the plan says what it says.

## Steps: write a plan

1. Read the alignment issue and its comments, the glossary, the ADRs for the area, the existing PRDs and the specs of the blocks it touches.
2. Restate the goal and sharpen it (Rules). Grill the captain on what is still unclear ([grilling.md](grilling.md)).
3. List the open questions. Settle each by prototype or research, or escalate it; tackle the riskiest unknown first.
4. Sketch the blocks (Steps: sketch) and name the verification seams.
5. Write the PRD: numbered, testable requirements; acceptance per requirement as a check with a pass predicate; out of scope stated. Open its pull request on the planning issue.
6. Split it into issues per [issues.md](../workflow/issues.md).

## Steps: sketch the blocks

1. Trace every block and interface the change touches, and the clock and reset domains it crosses.
2. Design the new blocks and interfaces per [architecture.md](../rtl/architecture.md) "Steps: design it twice".
3. Write the chosen sketch, and in one line why the others lost, where the rule above says.

## Steps: prototype to answer a design question

1. Write the one question it answers ("does this arbiter starve a port under back-to-back requests?", "does this FIFO depth meet the throughput target?", "does this pipeline close timing at the target clock?"). No question, no prototype.
2. Build the smallest thing that answers it, outside `src/` and never in a pull request: a Python or cycle-level behavioural model, a tiny RTL experiment run through simulation, lint, a formal check or synthesis. Keep the decision logic in a pure function or FSM that could be lifted into the real design; skip tests, polish and generality.
3. Print the full relevant state at each step, or the measured number with its baseline ([explain-the-number](../principles/explain-the-number.md)). Drive the hard cases: the normal path, the edge case, the case that should be impossible.
4. Post on the issue: the question, the verdict, the evidence (output, waveform, report line) and any decision-rich snippet. If the code is worth rerunning, push it to a branch `proto/<issue>-<slug>`, never merged, and link it; delete the branch when the issue it answers closes.
5. Fold the decision into the plan; the prototype itself is throwaway.

## Steps: ask someone who is not present

1. Settle the send with the captain, not the subject: who receives it (role, expertise, what they know that we do not), and the decisions or facts needed back.
2. Write a questionnaire as a comment on the issue: purpose and the decision riding on it; from, to, and how the answers will be used; one paragraph of context; how to answer (deadline, "I don't know" is a useful answer); questions grouped by theme, most important first, one idea each with an answer stub, and a one-line "why this matters" only where a question could be misread; a closing "anything else?".
3. Check that every item from step 1 has a question, hand it to the captain to send, and record the answers on the issue when they return.

## Steps: plan work too large for one session

1. Name the destination with the captain: the PRD, the decision or the change this effort is finding its way to. It fixes the scope.
2. Open a planning issue (`type:spec`): **Destination**; **Notes** (BKMs to follow, standing preferences); **Decisions so far**, one line per closed issue with its link; **Not yet specified**, the in-scope questions you cannot yet state sharply; **Out of scope**.
3. Open an issue for each question you can state sharply now, even if you cannot act on it yet; leave the rest in Not yet specified. Label it per [labels.md](../workflow/labels.md) (`type:research` for research and prototypes). Make the planning issue blocked by each, and link order between them with "blocked by". Do not make them sub-issues: the planning issue is not an epic, since decisions land nothing together (ADR 0004).
4. Resolve one issue per session (research issues may run in parallel): move it to `status:in-progress` per [labels.md](../workflow/labels.md), resolve it by grilling, prototype, research or the work it waits on, post the answer as a comment, close it, and add its line to Decisions so far.
5. After each, open issues for questions that became sharp, remove them from Not yet specified, and close any issue the answer moved past the destination, with a line in Out of scope.
6. When none of those issues is open, write the plan (Steps: write a plan) from the planning issue.

## Exit criteria

- The plan is merged on `main` (a PRD) or recorded on its issue, and a crewmate could build each slice from it without asking a question.
- Every open question is settled, with its decision and evidence on an issue, or escalated to the captain.
- The plan is split into issues per [issues.md](../workflow/issues.md).
