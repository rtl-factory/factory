---
status: draft
upstream: mattpocock/skills skills/engineering/to-spec/SKILL.md, skills/engineering/wayfinder/SKILL.md, skills/in-progress/loop-me/SKILL.md, skills/productivity/to-questionnaire/SKILL.md, skills/engineering/prototype/{SKILL,LOGIC}.md @ abeeaf95 (MIT, see SOURCES.md); cursor/plugins pstack/skills/{architect,sharpen,in-your-own-words,figure-it-out}/SKILL.md, pstack/skills/poteto-mode/playbooks/{multi-phase-plan,prototype}.md @ aaeb0ea (MIT, see SOURCES.md); mattpocock/skills skills/productivity/grilling/SKILL.md @ abeeaf95 (MIT, see SOURCES.md); mattpocock/skills skills/engineering/domain-modeling/SKILL.md @ abeeaf95 (MIT, see SOURCES.md); mattpocock/skills skills/engineering/domain-modeling/GLOSSARY-FORMAT.md @ abeeaf95 (MIT, see SOURCES.md); mattpocock/skills skills/engineering/domain-modeling/ADR-FORMAT.md @ abeeaf95 (MIT, see SOURCES.md)
---

# Planning

Deciding what to build: planning a PRD, grilling a captain, sharpening the domain model, and the glossary and ADR formats.

## Planning and specs

How to turn an aligned idea into a written plan (a tool's PRD, an interface sketch for each block, the decisions behind them) before the build issues exist. Interview the captain per [grilling](#grilling); split the finished plan into issues per [issues and epics](issues.md#issues-and-epics).

### Rules

- Plan on an issue: the captain's alignment issue, or a `type:spec` issue for the plan itself. A PRD reaches `main` through that issue's pull request ([flow](flow.md#flow)). Why: every change starts with an issue (ADR 0003), and the issue keeps the decisions behind the plan.
- Skip the written plan when the change fits one issue with an obvious approach; say so and go to [issues and epics](issues.md#issues-and-epics). Why: a plan for a one-issue change is overhead nobody reads.
- Before writing anything, restate in your own words the captain's goal and the problem behind it, and get the captain to confirm it. Why: a plan built on a misread goal is wrong in every section.
- Sharpen a vague request before planning it: say what a senior hardware engineer would do here that was not asked for, the rawest source they would start from (the standard, the datasheet, the reference implementation, measured traces, not a summary), the methods and tools they would use, and what other than the agent or the captain would show the result is right (a reference model, a compliance suite, a formal property, a synthesis report). Fold the answers into the plan. Why: this makes the work deeper, not bigger, and names its oracle up front.
- Settle by experiment every open question an experiment can settle (Steps: prototype); escalate to the captain only product or preference calls no run can settle. Why: a measured answer beats an opinion, and captain attention is the scarcest resource ([Never Block on the Human](principles.md#never-block-on-the-human)).
- Write a tool's, script's or software's PRD as `prd.md`, next to its `spec.md`, in the wiki folder that mirrors its code ([repository layout](layout.md#repository-layout)), with these sections: **Problem** (who needs it and why), numbered testable **Requirements**, **Out of scope**, **Acceptance** (a check per requirement), and **Blocks** when hardware implements it (the blocks and their interface sketches). Write it in glossary terms and with no file paths or code, except a snippet from a prototype that states a decision better than prose (an FSM table, a register map). Why: paths go stale; behaviour and decisions do not.
- Name in the PRD, for each requirement, the highest place where it is verified (a block's interface, a subsystem testbench, a formal harness), reusing existing ones and adding as few new ones as possible. Why: checks at a high place survive refactors; each new one is a testbench to build and keep.
- Sketch each block's interfaces before any RTL (Steps: sketch). Keep the sketch in the PRD's Blocks section; the project's HAS and the block's MAS ([standard documents](layout.md#standard-documents)) are written in that block's first slice, with its code ([flow step 5](flow.md#steps)). Why: the spec and the code change together; a spec far ahead of the code misleads like one that lags it.
- A plan is done when a crewmate could build every slice from it without asking a question. Why: every question a crewmate must ask is a decision the plan left open.
- Record every planning decision as a comment on the issue it belongs to, with its evidence link ([Decision trail](first-mate.md#decision-trail)). Why: the next session, or a reviewer, must find why the plan says what it says.

### Steps: write a plan

1. Read the alignment issue and its comments, the glossary, the ADRs for the area, the existing PRDs and the specs of the blocks it touches.
2. Restate the goal and sharpen it (Rules). Grill the captain on what is still unclear ([grilling](#grilling)).
3. List the open questions. Settle each by prototype or research, or escalate it; tackle the riskiest unknown first.
4. Sketch the blocks (Steps: sketch) and name the verification seams.
5. Write the PRD: numbered, testable requirements; acceptance per requirement as a check with a pass predicate; out of scope stated. Open its pull request on the planning issue.
6. Split it into issues per [issues and epics](issues.md#issues-and-epics).

### Steps: sketch the blocks

1. Trace every block and interface the change touches, and the clock and reset domains it crosses.
2. Design the new blocks and interfaces per [design it twice](rtl.md#steps-design-it-twice).
3. Write the chosen sketch, and in one line why the others lost, where the rule above says.

### Steps: prototype to answer a design question

1. Write the one question it answers ("does this arbiter starve a port under back-to-back requests?", "does this FIFO depth meet the throughput target?", "does this pipeline close timing at the target clock?"). No question, no prototype.
2. Build the smallest thing that answers it, outside `src/` and never in a pull request: a Python or cycle-level behavioural model, a tiny RTL experiment run through simulation, lint, a formal check or synthesis. Keep the decision logic in a pure function or FSM that could be lifted into the real design; skip tests, polish and generality.
3. Print the full relevant state at each step, or the measured number with its baseline ([Explain the Number](principles.md#explain-the-number)). Drive the hard cases: the normal path, the edge case, the case that should be impossible.
4. Post on the issue: the question, the verdict, the evidence (output, waveform, report line) and any decision-rich snippet. If the code is worth rerunning, push it to a branch `proto/<issue>-<slug>`, never merged, and link it; delete the branch when the issue it answers closes.
5. Fold the decision into the plan; the prototype itself is throwaway.

### Steps: ask someone who is not present

1. Settle the send with the captain, not the subject: who receives it (role, expertise, what they know that we do not), and the decisions or facts needed back.
2. Write a questionnaire as a comment on the issue: purpose and the decision riding on it; from, to, and how the answers will be used; one paragraph of context; how to answer (deadline, "I don't know" is a useful answer); questions grouped by theme, most important first, one idea each with an answer stub, and a one-line "why this matters" only where a question could be misread; a closing "anything else?".
3. Check that every item from step 1 has a question, hand it to the captain to send, and record the answers on the issue when they return.

### Steps: plan work too large for one session

1. Name the destination with the captain: the PRD, the decision or the change this effort is finding its way to. It fixes the scope.
2. Open a planning issue (`type:spec`): **Spec references** ([templates](issues.md#templates)); **Destination**; **Notes** (BKMs to follow, standing preferences); **Decisions so far**, one line per closed issue with its link; **Not yet specified**, the in-scope questions you cannot yet state sharply; **Out of scope**.
3. Open an issue for each question you can state sharply now, even if you cannot act on it yet; leave the rest in Not yet specified. Label it per [labels](issues.md#labels) (`type:research` for research and prototypes). Make the planning issue blocked by each, and link order between them with "blocked by". Do not make them sub-issues: the planning issue is not an epic, since decisions land nothing together (ADR 0004).
4. Resolve one issue per session (research issues may run in parallel): move it to `status:in-progress` per [labels](issues.md#labels), resolve it by grilling, prototype, research or the work it waits on, post the answer as a comment, close it, and add its line to Decisions so far.
5. After each, open issues for questions that became sharp, remove them from Not yet specified, and close any issue the answer moved past the destination, with a line in Out of scope.
6. When none of those issues is open, write the plan (Steps: write a plan) from the planning issue.

### Exit criteria

- The plan is merged on `main` (a PRD) or recorded on its issue, and a crewmate could build each slice from it without asking a question.
- Every open question is settled, with its decision and evidence on an issue, or escalated to the captain.
- The plan is split into issues per [issues and epics](issues.md#issues-and-epics).

## Grilling

Interview the user relentlessly until you reach a shared understanding. Map this as a **design tree**: every decision branches into the decisions that hang off it.

Work the tree in **rounds**. The **frontier** is every decision whose prerequisites are already settled: the questions you can ask _now_ without guessing at answers you haven't heard yet. Ask the whole frontier in one round: number each question and give your recommended answer. Then wait for the user's answers before the next round.

Format a round like so:

```
❓ **Q1** - **<question title>**: <question body, might be multiple paragraphs, including multiple choices>

➡️ <your recommended answer>

---

❓ **Q2** - **<question title>**: <question body, might be multiple paragraphs, including multiple choices>

➡️ <your recommended answer>
```

Word each question so "yes" accepts your recommended answer.

**Factory-local:** in this repo "the user" is the captain, and a "sub-agent" sent to find facts is a read-only subagent inside the grilling session, not a crewmate: it takes no issue, writes nothing and reports only to the grilling agent. When the harness offers a multiple-choice question tool (Claude Code's `AskUserQuestion`), ask each round through it instead of as text, so the captain picks answers instead of typing them. Put your recommended answer first and mark it "(Recommended)". The tool takes at most four questions per call, so send a larger frontier as consecutive calls. Fall back to the text format above only where no such tool exists.

Each round the user answers reshapes the tree: settled decisions push the frontier outward and unblock questions that depended on them. Recompute the frontier and ask the next round. A question whose answer depends on another question still open in this round belongs to a _later_ round, not this one.

Finding _facts_ is your job, never the user's. When a frontier question needs a fact from the environment (filesystem, tools, etc.), dispatch a sub-agent with a fresh context window to find it; don't ask the user for anything you could look up yourself. Don't block on it: a running exploration is an unsettled prerequisite, so only the questions downstream of it wait for the sub-agent to report; ask the rest of the frontier now. The _decisions_ are the user's: put each to them and wait.

The session is done when the frontier is empty: every branch of the design tree visited, nothing left silently assumed. Do not act on it until the user confirms you have reached a shared understanding.

## Domain modeling

Actively build and sharpen the project's domain model as you design. This is the *active* discipline: challenging terms, inventing edge-case scenarios, and writing the glossary and decisions down the moment they crystallise. (Merely *reading* `GLOSSARY.md` for vocabulary is not this skill: that's a one-line habit every BKM already asks for. This BKM is for when you're changing the model, not just consuming it.)

### File structure

This repo keeps its domain docs under `wiki/project/` (factory-local change from upstream):

```
factory/
├── wiki/
│   └── project/
│       ├── GLOSSARY.md
│       └── adr/
│           ├── 0001-<slug>.md
│           └── 0002-<slug>.md
└── src/
```

If `wiki/project/GLOSSARY-MAP.md` exists, the repo has multiple contexts (e.g. a hardware block with its own vocabulary). The map points to where each one lives:

```
factory/
├── wiki/
│   ├── project/
│   │   ├── GLOSSARY-MAP.md
│   │   └── adr/                      ← factory-wide decisions
│   └── src/<project>/<discipline>/<block>/
│       ├── GLOSSARY.md
│       └── adr/                      ← block-specific decisions
```

Create files lazily: only when you have something to write. If no `wiki/project/GLOSSARY.md` exists, create one when the first term is resolved. If no `wiki/project/adr/` exists, create it when the first ADR is needed.

### During the session

#### Challenge against the glossary

When the user uses a term that conflicts with the existing language in the glossary, call it out immediately. "Your glossary defines 'cancellation' as X, but you seem to mean Y. Which is it?"

#### Sharpen fuzzy language

When the user uses vague or overloaded terms, propose a precise canonical term. "You're saying 'account': do you mean the Customer or the User? Those are different things."

#### Discuss concrete scenarios

When domain relationships are being discussed, stress-test them with specific scenarios. Invent scenarios that probe edge cases and force the user to be precise about the boundaries between concepts.

#### Cross-reference with code

When the user states how something works, check whether the code agrees. If you find a contradiction, surface it: "Your code cancels entire Orders, but you just said partial cancellation is possible. Which is right?"

#### Update GLOSSARY.md inline

When a term is resolved, update `GLOSSARY.md` right there. Don't batch these up: capture them as they happen. Use the format in [glossary format](#glossary-format).

`GLOSSARY.md` should be totally devoid of implementation details. Do not treat `GLOSSARY.md` as a spec, a scratch pad, or a repository for implementation decisions. It is a glossary and nothing else.

#### Offer ADRs sparingly

Offer an ADR only when it passes all three tests in [When to offer an ADR](#when-to-offer-an-adr), and write it in the [ADR format](#adr-format).

## Glossary format

### Structure

```md
# {Context Name}

{One or two sentence description of what this context is and why it exists.}

## Language

**Order**:
{A one or two sentence description of the term}
_Avoid_: Purchase, transaction

**Invoice**:
A request for payment sent to a customer after delivery.
_Avoid_: Bill, payment request

**Customer**:
A person or organization that places orders.
_Avoid_: Client, buyer, account
```

### Rules

- **Be opinionated.** When multiple words exist for the same concept, pick the best one and list the others under `_Avoid_`.
- **Keep definitions tight.** One or two sentences max. Define what it IS, not what it does.
- **Only include terms specific to this project's context.** General programming concepts (timeouts, error types, utility patterns) don't belong even if the project uses them extensively. Before adding a term, ask: is this a concept unique to this context, or a general programming concept? Only the former belongs.
- **Group terms under subheadings** when natural clusters emerge. If all terms belong to a single cohesive area, a flat list is fine.

### Single vs multi-context repos

**Single context (most repos):** One `GLOSSARY.md` at `wiki/project/GLOSSARY.md`.

**Multiple contexts:** A `GLOSSARY-MAP.md` at `wiki/project/GLOSSARY-MAP.md` lists the contexts, where they live, and how they relate to each other:

```md
# Glossary Map

## Contexts

- [Ordering](./src/ordering/GLOSSARY.md): receives and tracks customer orders
- [Billing](./src/billing/GLOSSARY.md): generates invoices and processes payments
- [Fulfillment](./src/fulfillment/GLOSSARY.md): manages warehouse picking and shipping

## Relationships

- **Ordering → Fulfillment**: Ordering emits `OrderPlaced` events; Fulfillment consumes them to start picking
- **Fulfillment → Billing**: Fulfillment emits `ShipmentDispatched` events; Billing consumes them to generate invoices
- **Ordering ↔ Billing**: Shared types for `CustomerId` and `Money`
```

Infer which structure applies:

- If `GLOSSARY-MAP.md` exists, read it to find contexts
- If only a root `GLOSSARY.md` exists, single context
- If neither exists, create `wiki/project/GLOSSARY.md` lazily when the first term is resolved

When multiple contexts exist, infer which one the current topic relates to. If unclear, ask.

## ADR format

ADRs live in `wiki/project/adr/` and use sequential numbering: `0001-slug.md`, `0002-slug.md`, etc.

Create the `wiki/project/adr/` directory lazily: only when the first ADR is needed.

### Template

```md
# {Short title of the decision}

{1-3 sentences: what's the context, what did we decide, and why.}
```

That's it. An ADR can be a single paragraph. The value is in recording *that* a decision was made and *why*, not in filling out sections.

### Optional sections

Only include these when they add genuine value. Most ADRs won't need them.

- **Status** frontmatter (`proposed | accepted | deprecated | superseded by ADR-NNNN`): useful when decisions are revisited
- **Considered Options**: only when the rejected alternatives are worth remembering
- **Consequences**: only when non-obvious downstream effects need to be called out

### Numbering

Scan `wiki/project/adr/` for the highest existing number and increment by one.

### When to offer an ADR

All three of these must be true:

1. **Hard to reverse**: the cost of changing your mind later is meaningful
2. **Surprising without context**: a future reader will look at the code and wonder "why on earth did they do it this way?"
3. **The result of a real trade-off**: there were genuine alternatives and you picked one for specific reasons

If a decision is easy to reverse, skip it: you'll just reverse it. If it's not surprising, nobody will wonder why. If there was no real alternative, there's nothing to record beyond "we did the obvious thing."

#### What qualifies

- **Architectural shape.** "We're using a monorepo." "The write model is event-sourced, the read model is projected into Postgres."
- **Integration patterns between contexts.** "Ordering and Billing communicate via domain events, not synchronous HTTP."
- **Technology choices that carry lock-in.** Database, message bus, auth provider, deployment target. Not every library: just the ones that would take a quarter to swap out.
- **Boundary and scope decisions.** "Customer data is owned by the Customer context; other contexts reference it by ID only." The explicit no-s are as valuable as the yes-s.
- **Deliberate deviations from the obvious path.** "We're using manual SQL instead of an ORM because X." Anything where a reasonable reader would assume the opposite. These stop the next engineer from "fixing" something that was deliberate.
- **Constraints not visible in the code.** "We can't use AWS because of compliance requirements." "Response times must be under 200ms because of the partner API contract."
- **Rejected alternatives when the rejection is non-obvious.** If you considered GraphQL and picked REST for subtle reasons, record it; otherwise someone will suggest GraphQL again in six months.
