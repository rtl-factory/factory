---
name: bkm-router
description: The list of every BKM and principle, and when to follow each. Use whenever you start a piece of work or the kind of work changes.
---

BKMs define how work is done here. Find the rows that match what you are doing, read those BKMs, and follow them. If none fits, say so and propose a new BKM in an issue instead of improvising a method.

Each row names a file and, after it, the section (its `##` heading) to follow.

| When you are | Follow |
|---|---|
| doing any work on an issue, from alignment to landing, or deciding who may merge | `wiki/bkm/flow.md` Flow |
| adding a tool, script, block or folder, moving one, or deciding where code or its documents go (tool or script, script or software, which discipline) | `wiki/bkm/layout.md` Repository layout |
| the first mate: dispatching, supervising, pausing, handing over or landing crewmates' work, or running competing candidates on one issue | `wiki/bkm/first-mate.md` Running the crew |
| working out what plays first mate and crewmate on a given agent platform, or adding a platform to that list | `wiki/bkm/first-mate.md` Platforms |
| opening any issue or pull request: which template, and what each section holds | `wiki/bkm/issues.md` Templates |
| splitting a plan into issues or an epic, or triaging an issue | `wiki/bkm/issues.md` Issues and epics |
| opening, labelling or dispatching an issue | `wiki/bkm/issues.md` Labels |
| reviewing a pull request as the reviewer crewmate | `wiki/bkm/pull-requests.md` Review |
| writing a pull request or a GitHub comment, getting it landable, taking over a stalled one, or landing it | `wiki/bkm/pull-requests.md` Writing and landing |
| reporting to a captain, catching one up, resuming after a restart, or keeping a decision trail | `wiki/bkm/first-mate.md` Reporting to the captain |
| debugging a failing simulation, formal check, lint, synthesis run, test or tool | `wiki/bkm/debug.md` |
| writing or changing SystemVerilog RTL, or reviewing an RTL diff (names, flops, reset, structs, enums, parameters, packages, assertions) | `wiki/bkm/rtl.md` RTL coding |
| designing a block or where its interface goes, finding where structure should change, refactoring a block, or explaining how and why a block works | `wiki/bkm/rtl.md` Architecture of blocks |
| deciding where a check runs, or reading CI results | `wiki/bkm/flow.md` CI levels |
| verifying a block in simulation: test-first, DV plans, testbenches, seeds, coverage | `wiki/bkm/dv.md` |
| verifying a block formally: properties, assumptions, proofs, covers, counterexamples | `wiki/bkm/fv.md` |
| building an FPGA image with Quartus, handing it to a board host, or programming a board | `wiki/bkm/fpga.md` |
| writing or changing a wiki page, or the wiki site build | `wiki/bkm/layout.md` Wiki site |
| configuring or auditing GitHub repository settings | `wiki/bkm/flow.md` GitHub settings |
| stress-testing a plan or decision with a captain | `wiki/bkm/planning.md` Grilling, with Domain modeling |
| turning an aligned idea into a plan or a PRD, prototyping to answer a design question, or planning work too large for one session | `wiki/bkm/planning.md` Planning and specs |
| meeting a fuzzy term, or a decision that may deserve an ADR | `wiki/bkm/planning.md` Domain modeling |
| writing or changing a standard document (HAS, MAS, DV plan, FV plan, board spec), or deciding which documents a discipline keeps and their file names | `wiki/bkm/layout.md` Standard documents |
| writing a glossary entry | `wiki/bkm/planning.md` Glossary format |
| writing an ADR | `wiki/bkm/planning.md` ADR format |
| writing or changing a BKM | `wiki/bkm/writing.md` Writing BKMs |
| writing anything an agent reads (BKM, skill, `AGENTS.md`) | `wiki/bkm/writing.md` Writing for agents |
| writing, reviewing or cleaning up a comment in Python or SystemVerilog code, or fixing a comment lint failure | `wiki/bkm/code-comments.md` |
| writing or changing a skill | `wiki/bkm/writing.md` Skill mechanics |
| running a retro after a session or landed epic, or after a captain corrects an agent | `wiki/bkm/writing.md` Learning from mistakes |

## Principles

Engineering principles, one `##` section each in `wiki/bkm/principles.md`. Follow each one whose trigger matches what you are doing.

- Attack the Premise: Apply when two or more fixes that share one premise have failed the same gate.
- Boundary Discipline: Apply when wiring validation, error handling, or framework adapters.
- Build the Lever: Apply to any non-trivial work, not just bulk work: edits, migrations, analyses, checks.
- Encode Lessons in Structure: Apply when you catch yourself writing the same instruction a second time, or notice a recurring correction.
- Exhaust the Design Space: Apply when facing a novel UI interaction or architectural decision with no precedent in the codebase.
- Explain the Number: Apply before you measure, and before you trust, report, or act on a number you measured: a speedup, a regression, a throughput, a latency, or an eval result.
- Fix Root Causes: Apply when debugging.
- Foundational Thinking: Apply before writing logic: choosing core types and data structures, sequencing scaffold-vs-feature work, asking what concurrent actors share.
- Guard the Context Window: Apply when context is filling up: large outputs, long files, repeated reads, fan-out planning.
- Laziness Protocol: Apply when refactoring, evaluating diff size, or tempted to add abstractions, layers, or signal threading.
- Make Operations Idempotent: Apply when designing commands, lifecycle steps, or processing loops that run amid crashes, restarts, and retries.
- Migrate Callers Then Delete Legacy APIs: Apply when introducing a new internal API while old callers still exist.
- Minimize Reader Load: Apply when reviewing or shaping code that's hard to trace.
- Model the Domain: Apply when writing stateful logic, or when code branches a lot or repeats a shape assumption across files.
- Never Block on the Human: Apply when tempted to ask 'should I do X?' on reversible work.
- Outcome-Oriented Execution: Apply during planned rewrites and migrations with explicit phase boundaries.
- Prove It Works: Apply after completing a piece of work, before declaring done.
- Redesign From First Principles: Apply when integrating a new requirement into an existing design.
- Separate Before Serializing Shared State: Apply when concurrent actors might write to the same file, branch, key, or state object.
- Sequence work into verifiable units: Apply to multi-step work (sweeps, migrations, runs of similar edits) and to how you stack commits and PRs.
- Subtract Before You Add: Apply when sequencing an addition, refactor, or rewrite.
- Test Behavior, Not Implementation: Apply when you write, change, or keep a test.
- Type System Discipline: Apply when designing types, reviewing a function signature, or writing code in any statically-typed language.
