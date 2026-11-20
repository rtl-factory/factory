---
status: draft
upstream: mattpocock/skills skills/engineering/codebase-design/{SKILL,DEEPENING,DESIGN-IT-TWICE}.md @ abeeaf95, mattpocock/skills skills/engineering/improve-codebase-architecture/SKILL.md @ abeeaf95, mattpocock/skills skills/productivity/teach/SKILL.md @ abeeaf95 (MIT, see ../LICENSE-mattpocock-skills); cursor/plugins pstack/skills/{how,why,teach}/SKILL.md @ aaeb0ea, cursor/plugins pstack/skills/poteto-mode/playbooks/refactoring.md @ aaeb0ea (MIT, see ../LICENSE-pstack)
---

# Architecture of blocks

How to shape blocks and the seams between them, find where the existing structure should change, refactor it safely, and explain how and why a block is built. RTL lives under `src/<project>/rtl/<block>/`, architecture models under `src/<project>/arch/<block>/`, specs under `wiki/src/<project>/<discipline>/<block>/`.

## Terms

**Interface**, **seam**, **deep block** (and a shallow one) and the **deletion test** are defined in the [glossary](../../project/GLOSSARY.md).

## Rules

- Make blocks deep: shrink the port list, the protocol and the parameters before adding behaviour behind them; hide FSM state, internal flags and timing choices inside. Why: depth gives neighbours leverage and puts change, bugs and verification in one place.
- Use the spec's and the glossary's domain names for blocks and seams, and follow [model-the-domain](../principles/model-the-domain.md) and [foundational-thinking](../principles/foundational-thinking.md) for what sits behind the seam. Why: a seam named after a domain concept stays put when the code moves.
- Put a seam only where something actually varies across it: two implementations of one interface (the RTL and its architecture model, two bus fabrics, a hard macro and its behavioural model). A seam with one side is indirection. Why: every seam is a protocol to specify, verify and time-close.
- Keep internal seams internal: sub-blocks, assertions and formal properties a block uses for itself do not appear in its interface. Why: exposing them to make a test easy makes every neighbour depend on them.
- If a test must reach past a block's interface to check it, treat the block as the wrong shape ([verification.md](../dv/verification.md) tests only through ports). Why: the interface is the test surface; needing to reach inside means the seam is in the wrong place.
- You **must** compare at least two radically different candidate structures before committing a new block, a new seam or a changed interface (Design it twice). Why: the first design is rarely the best, and an interface is the hardest thing to change later ([exhaust-the-design-space](../principles/exhaust-the-design-space.md)).
- Judge structure by reader load: a change that does not make the block easier to trace is reverted, per [minimize-reader-load](../principles/minimize-reader-load.md). Why: that is the measure of whether a reshape was worth it.
- Respect the ADRs in `wiki/project/adr/`. Raise a candidate that contradicts one only when the friction is real, and say which ADR and why it is worth reopening. Why: settled decisions are not re-argued for free.
- Never mix a refactor with new behaviour. A bug or missing feature found while refactoring gets its own issue ([labels.md](../workflow/labels.md)). Why: a reviewer cannot tell a structure change from a behaviour change in one diff.

## Steps: design it twice

1. Frame the problem in the issue: the constraints any interface must meet (throughput, latency, area, clock domains, the protocols of the neighbours), and each dependency with how it is tested across the seam: pure logic (merge it in and test through the new interface); a memory or macro with a behavioural model (keep the seam internal, test with the model); another block of ours (specify the protocol at the seam, stand in a bus functional model in DV); vendor IP (wrap it at a seam, test against the vendor model or a stub).
2. Draft two or more candidates, each under a different constraint: fewest ports and entry points; the common transaction trivial; most flexible; or one interface shared by RTL and architecture model. A crewmate drafts them itself; the first mate may instead dispatch one crewmate per constraint.
3. For each, write: the interface (ports, protocol, invariants, ordering, latency, error modes), one transaction sketched cycle by cycle, what it hides, the dependency strategy, and where it is thin.
4. Compare them in prose on depth, locality (where a future change lands) and seam placement, and on any number you can estimate per [explain-the-number](../principles/explain-the-number.md). Recommend one, or a hybrid. Be opinionated.
5. Record the choice and the rejected options in the spec (or, before the block exists, in the PRD's Blocks section per [planning.md](../design/planning.md)). If the reason would stop a future crewmate re-proposing a rejected option, write an ADR ([adr-format](../design/adr-format.md)). If it needs a captain's call, grill per [grilling](../design/grilling.md).

## Steps: find deepening opportunities

1. Scope first: take the direction a captain or the issue names; otherwise find hot spots with `git log --oneline -- src/<project>/rtl/ src/<project>/arch/` and look there. Why: deepening pays off where change keeps landing.
2. Read the glossary, the ADRs and the specs of the area, then the RTL. Note friction: shallow pass-through blocks (deletion test), the same control logic or FSM duplicated across blocks, leaky interfaces (sideband signals, exposed internal state, a protocol that only works with one neighbour's timing), one concept spread over many small blocks, blocks untestable through their ports.
3. For each candidate write: the blocks involved, the problem, the change in plain words, the benefit in locality, leverage and tests, and a strength (`Strong`, `Worth exploring`, `Speculative`). Do not propose interfaces yet.
4. Post the list as a comment on your issue, top recommendation first, and report it to the first mate. Open an issue for each candidate it or a captain picks, per [issues.md](../workflow/issues.md); a reshape that enables a feature is prefactoring and blocks it. When a captain rejects one for a load-bearing reason, offer an ADR.

## Steps: refactor a block

1. Pin the behaviour before moving anything: the block's DV regression green at a named commit, plus, where the reshape allows, a formal equivalence check of old against new RTL. Lint and elaboration are not a pin.
2. Name the missing structure and the target shape (block split, interfaces, FSMs), per [redesign-from-first-principles](../principles/redesign-from-first-principles.md).
3. Subtract first: delete dead logic, collapse one-neighbour pass-through blocks, drop unused ports, per [subtract-before-you-add](../principles/subtract-before-you-add.md) and [laziness-protocol](../principles/laziness-protocol.md).
4. Move in small steps, each keeping the pin green. For an interface change, migrate every instance and delete the old ports in the same wave, with no shim, per [migrate-callers-then-delete-legacy-apis](../principles/migrate-callers-then-delete-legacy-apis.md); when the change is too wide for one pull request, split it expand–contract per [issues.md](../workflow/issues.md); for a planned multi-phase rewrite, follow [outcome-oriented-execution](../principles/outcome-oriented-execution.md). Grep every renamed signal in RTL, testbench, constraints, waivers and specs.
5. Prove behaviour is unchanged on the real artifacts ([prove-it-works](../principles/prove-it-works.md)): the pin passes, and synthesis area and timing moved only as explained. Change the spec in the same pull request if the interface changed.
6. Order the commits subtraction, reshape, cleanup, per [sequence-verifiable-units](../principles/sequence-verifiable-units.md).

## Steps: explain a block

Use this when a captain or a crewmate asks how a block works or why it is built that way. Change nothing.

1. **How**: read the spec, the RTL, the architecture model and the tests. Build the mental model someone new to the block needs: what it is, its interface, its data and control paths, and one transaction walked cycle by cycle.
2. **Why**: answer from the record, not the code: the issues, pull requests, decision trails and ADRs that introduced and later changed it (`git log -S <signal>`, `gh pr view`). Cite each claim, mark your inferences as inferences, and say what the record does not answer.
3. **Teach**: start with a plain definition and its common hardware name, then tie it to this block. Give the smallest complete answer first and go deeper when asked. For three or more moving parts, build the picture in a series of diagrams that each add one part. Keep the hedges from step 2.
4. If the spec lacks the reasons you had to dig for, open an issue to add them.

## Exit criteria

- A new block or changed interface has its candidates, comparison and choice in the spec or an ADR, and its tests reach it only through its interface.
- A refactor lands with its pin and equivalence evidence in the pull request, no behaviour change, and a stated reader-load gain.
- Deepening candidates are posted on the issue, and each one picked has its own issue.
- An explanation cites its sources and marks what is inference.
