# Principles

Engineering principles, one per file. Follow each one whose trigger matches what you are doing; the `bkm-router` skill lists the same triggers for agents.

- [Attack the Premise](attack-the-premise.md): Apply when two or more fixes that share one premise have failed the same gate.
- [Boundary Discipline](boundary-discipline.md): Apply when wiring validation, error handling, or framework adapters.
- [Build the Lever](build-the-lever.md): Apply to any non-trivial work, not just bulk work: edits, migrations, analyses, checks.
- [Encode Lessons in Structure](encode-lessons-in-structure.md): Apply when you catch yourself writing the same instruction a second time, or notice a recurring correction.
- [Exhaust the Design Space](exhaust-the-design-space.md): Apply when facing a novel UI interaction or architectural decision with no precedent in the codebase.
- [Explain the Number](explain-the-number.md): Apply before you measure, and before you trust, report, or act on a number you measured: a speedup, a regression, a throughput, a latency, or an eval result.
- [Fix Root Causes](fix-root-causes.md): Apply when debugging.
- [Foundational Thinking](foundational-thinking.md): Apply before writing logic: choosing core types and data structures, sequencing scaffold-vs-feature work, asking what concurrent actors share.
- [Guard the Context Window](guard-the-context-window.md): Apply when context is filling up: large outputs, long files, repeated reads, fan-out planning.
- [Laziness Protocol](laziness-protocol.md): Apply when refactoring, evaluating diff size, or tempted to add abstractions, layers, or signal threading.
- [Make Operations Idempotent](make-operations-idempotent.md): Apply when designing commands, lifecycle steps, or processing loops that run amid crashes, restarts, and retries.
- [Migrate Callers Then Delete Legacy APIs](migrate-callers-then-delete-legacy-apis.md): Apply when introducing a new internal API while old callers still exist.
- [Minimize Reader Load](minimize-reader-load.md): Apply when reviewing or shaping code that's hard to trace.
- [Model the Domain](model-the-domain.md): Apply when writing stateful logic, or when code branches a lot or repeats a shape assumption across files.
- [Never Block on the Human](never-block-on-the-human.md): Apply when tempted to ask 'should I do X?' on reversible work.
- [Outcome-Oriented Execution](outcome-oriented-execution.md): Apply during planned rewrites and migrations with explicit phase boundaries.
- [Prove It Works](prove-it-works.md): Apply after completing a piece of work, before declaring done.
- [Redesign From First Principles](redesign-from-first-principles.md): Apply when integrating a new requirement into an existing design.
- [Separate Before Serializing Shared State](separate-before-serializing-shared-state.md): Apply when concurrent actors might write to the same file, branch, key, or state object.
- [Sequence work into verifiable units](sequence-verifiable-units.md): Apply to multi-step work (sweeps, migrations, runs of similar edits) and to how you stack commits and PRs.
- [Subtract Before You Add](subtract-before-you-add.md): Apply when sequencing an addition, refactor, or rewrite.
- [Test Behavior, Not Implementation](test-behavior-not-implementation.md): Apply when you write, change, or keep a test.
- [Type System Discipline](type-system-discipline.md): Apply when designing types, reviewing a function signature, or writing code in any statically-typed language.
