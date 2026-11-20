---
name: bkm-router
description: The list of every BKM and principle, and when to follow each. Use whenever you start a piece of work or the kind of work changes.
---

BKMs define how work is done here. Find the rows that match what you are doing, read those BKMs, and follow them. If none fits, say so and propose a new BKM in an issue instead of improvising a method.

| When you are | Follow |
|---|---|
| doing any work on an issue, from alignment to landing, or deciding who may merge | `wiki/bkm/workflow/flow.md` |
| the first mate: dispatching, supervising and landing crewmates' work | `wiki/bkm/workflow/first-mate.md` |
| splitting a plan into issues or an epic, or triaging an issue | `wiki/bkm/workflow/issues.md` |
| opening, labelling or dispatching an issue | `wiki/bkm/workflow/labels.md` |
| reviewing a pull request as the reviewer crewmate | `wiki/bkm/workflow/review.md` |
| writing a pull request or a GitHub comment, getting it landable, taking over a stalled one, or landing it | `wiki/bkm/workflow/landing.md` |
| reporting to a captain, catching one up, resuming after a restart, or keeping a decision trail | `wiki/bkm/workflow/reporting.md` |
| debugging a failing simulation, formal check, lint, synthesis run, test or tool | `wiki/bkm/debug/debugging.md` |
| deciding where a check runs, or reading CI results | `wiki/bkm/workflow/ci-levels.md` |
| verifying a block in simulation: test-first, verification plans, testbenches, seeds, coverage | `wiki/bkm/dv/verification.md` |
| verifying a block formally: properties, assumptions, proofs, covers, counterexamples | `wiki/bkm/fv/formal.md` |
| writing or changing a wiki page, or the wiki site build | `wiki/bkm/workflow/wiki-site.md` |
| configuring or auditing GitHub repository settings | `wiki/bkm/workflow/github-settings.md` |
| stress-testing a plan or decision with a captain | `wiki/bkm/design/grilling.md`, with `wiki/bkm/design/domain-modeling.md` |
| meeting a fuzzy term, or a decision that may deserve an ADR | `wiki/bkm/design/domain-modeling.md` |
| writing a glossary entry | `wiki/bkm/design/glossary-format.md` |
| writing an ADR | `wiki/bkm/design/adr-format.md` |
| writing or changing a BKM | `wiki/bkm/writing/writing-bkms.md` |
| writing anything an agent reads (BKM, skill, `AGENTS.md`) | `wiki/bkm/writing/writing-for-agents.md` |
| writing or changing a skill | `wiki/bkm/writing/skill-mechanics.md` |
| running a retro after a session or landed epic, or after a captain corrects an agent | `wiki/bkm/writing/improving-bkms.md` |

## Principles

Engineering principles, one per file. Follow each one whose trigger matches what you are doing.

- `wiki/bkm/principles/attack-the-premise.md`: Apply when two or more fixes that share one premise have failed the same gate.
- `wiki/bkm/principles/boundary-discipline.md`: Apply when wiring validation, error handling, or framework adapters.
- `wiki/bkm/principles/build-the-lever.md`: Apply to any non-trivial work, not just bulk work: edits, migrations, analyses, checks.
- `wiki/bkm/principles/encode-lessons-in-structure.md`: Apply when you catch yourself writing the same instruction a second time, or notice a recurring correction.
- `wiki/bkm/principles/exhaust-the-design-space.md`: Apply when facing a novel UI interaction or architectural decision with no precedent in the codebase.
- `wiki/bkm/principles/explain-the-number.md`: Apply before you measure, and before you trust, report, or act on a number you measured: a speedup, a regression, a throughput, a latency, or an eval result.
- `wiki/bkm/principles/fix-root-causes.md`: Apply when debugging.
- `wiki/bkm/principles/foundational-thinking.md`: Apply before writing logic: choosing core types and data structures, sequencing scaffold-vs-feature work, asking what concurrent actors share.
- `wiki/bkm/principles/guard-the-context-window.md`: Apply when context is filling up: large outputs, long files, repeated reads, fan-out planning.
- `wiki/bkm/principles/laziness-protocol.md`: Apply when refactoring, evaluating diff size, or tempted to add abstractions, layers, or signal threading.
- `wiki/bkm/principles/make-operations-idempotent.md`: Apply when designing commands, lifecycle steps, or processing loops that run amid crashes, restarts, and retries.
- `wiki/bkm/principles/migrate-callers-then-delete-legacy-apis.md`: Apply when introducing a new internal API while old callers still exist.
- `wiki/bkm/principles/minimize-reader-load.md`: Apply when reviewing or shaping code that's hard to trace.
- `wiki/bkm/principles/model-the-domain.md`: Apply when writing stateful logic, or when code branches a lot or repeats a shape assumption across files.
- `wiki/bkm/principles/never-block-on-the-human.md`: Apply when tempted to ask 'should I do X?' on reversible work.
- `wiki/bkm/principles/outcome-oriented-execution.md`: Apply during planned rewrites and migrations with explicit phase boundaries.
- `wiki/bkm/principles/prove-it-works.md`: Apply after completing a piece of work, before declaring done.
- `wiki/bkm/principles/redesign-from-first-principles.md`: Apply when integrating a new requirement into an existing design.
- `wiki/bkm/principles/separate-before-serializing-shared-state.md`: Apply when concurrent actors might write to the same file, branch, key, or state object.
- `wiki/bkm/principles/sequence-verifiable-units.md`: Apply to multi-step work (sweeps, migrations, runs of similar edits) and to how you stack commits and PRs.
- `wiki/bkm/principles/subtract-before-you-add.md`: Apply when sequencing an addition, refactor, or rewrite.
- `wiki/bkm/principles/test-behavior-not-implementation.md`: Apply when you write, change, or keep a test.
- `wiki/bkm/principles/type-system-discipline.md`: Apply when designing types, reviewing a function signature, or writing code in any statically-typed language.

## Not yet written

No BKMs yet for writing RTL or for synthesis. They will live in `wiki/bkm/rtl/` and `wiki/bkm/syn/`.
