---
title: Glossary
---

# Factory

The shared vocabulary for this repo: a hardware development factory where agents do the execution and humans make the decisions.

## Language

**Factory**:
This repository together with the BKMs, skills, tools and CI that turn hardware work (spec, RTL, verification, formal, FPGA implementation) into reviewed, merged results.
_Avoid_: framework, platform, pipeline

**BKM**:
Best Known Method: a written, prescriptive document that defines how a recurring kind of work is done in the factory. It is written for agents to follow, and it is the knob captains turn to change how agents work.
_Avoid_: guide, how-to, process doc, playbook

**Principle**:
A BKM of one engineering rule with its trigger, in `wiki/bkm/principles/`, applied whenever the trigger matches.
_Avoid_: guideline, best practice

**Skill**:
A thin, rarely-changing entry point under `.claude/skills/` that routes an agent to the BKMs it should follow. Skills hold no method of their own.
_Avoid_: prompt, command, plugin

## Crew

**Captain**:
A human who sets direction: starts alignment, makes the decisions only a human can make, and holds back any merge they want to see first. Captains talk to the first mate, not to crewmates.
_Avoid_: user, operator, owner (for this role)

**First mate**:
The agent a captain talks to. It turns the captain's intent into issues, dispatches and supervises crewmates, lands their work, and reports outcomes and decisions back. It orchestrates; it does not change the repo itself.
_Avoid_: orchestrator, lead agent, conductor, coordinator, manager, chief of staff

**Crewmate**:
An agent the first mate dispatches to do one issue, in its own worktree on its own issue branch (a candidate crewmate works on a scratch branch instead). It reports only to the first mate.
_Avoid_: worker, subagent, implementer, teammate

**Candidate crewmate**:
One of several crewmates the first mate dispatches on the same issue as competing attempts, each on a scratch branch `candidate/<number>-<n>` with no pull request; only the chosen candidate's branch becomes the issue branch.
_Avoid_: arena entry, contestant

**Reviewer crewmate**:
A crewmate with a fresh context that reviews another crewmate's work (a pull request, or competing candidates) against the issue, the spec and the BKMs. It never reviews its own work.
_Avoid_: verifier, checker

**Brief**:
What the first mate hands a crewmate at dispatch: the captain's intent, the first mate's build instructions, where to work, which BKMs to follow, and what done means.
_Avoid_: prompt, task description, contract

**Dispatch**:
Start a crewmate on one issue with its brief.
_Avoid_: spawn, fan out, launch

**Escalate**:
Bring a decision that only a human can make to the captain.
_Avoid_: ask, interrupt, ping

**Land**:
Merge a crewmate's pull request into its base branch.
_Avoid_: ship, integrate

## Work flow

**Issue**:
A GitHub issue. Every change to the repo starts with one. A captain or the first mate opens it; a crewmate is dispatched on it once it is labelled `status:ready`.
_Avoid_: ticket, task, story, request

**Epic**:
A parent issue whose sub-issues cannot reach `main` one by one and must land together through its epic branch. Every parent issue is an epic; issues that are merely related are linked, not grouped.
_Avoid_: feature, initiative, project, tracking issue

**Issue branch**:
The branch where one issue's work is done. It merges into `main`, or into its epic branch when the issue belongs to an epic.

**Epic branch**:
The branch an epic's issue branches merge into; it merges into `main` through one pull request once it is healthy.
_Avoid_: feature branch, integration branch

**Project**:
A self-contained design the factory builds, with everything it needs under `src/<project>/` (for example nand2mario); shared building blocks live in the `common` project.
_Avoid_: repo, product (for this meaning)

**Discipline**:
The kind of work a project's code is, and the second level of its path `src/<project>/<discipline>/`: `rtl`, `dv`, `fv`, `fpga`, `scripts` or `sw`. Each has its standard documents in the wiki (`wiki/bkm/design/standard-docs.md`); `cfg/` is a project folder, not a discipline.
_Avoid_: type, work type, layer

**Block**:
One hardware design unit of a project (an IP or unit), with its code under `src/<project>/<discipline>/<block>/` and its MAS under `wiki/src/<project>/rtl/<block>/mas.md`.
_Avoid_: module (for this meaning), component

**Vertical slice**:
An issue that delivers one narrow but complete behaviour, ending in a state a reviewer crewmate can check; for a block, typically its MAS, RTL and a DV test for one feature.
_Avoid_: horizontal slice, layer, phase

**PRD**:
Product requirements document: what a tool, script or piece of software we build must do, for whom, and how we will know it does, kept as `prd.md` next to its `spec.md` in the wiki folder which mirrors its code folder (`wiki/bkm/workflow/repo-layout.md`).
_Avoid_: requirements spec, MRD

**Interface**:
Everything a neighbouring block or a test must know to use a block correctly: ports, parameters, handshake, ordering, latency and throughput, reset and error behaviour, clock domains and register map.
_Avoid_: API (for a block), pinout

**Seam**:
The place where a block's interface sits; where to put it is a design decision separate from what goes behind it.
_Avoid_: boundary, cut

**Deep block**:
A block whose small interface hides a lot of behaviour; a shallow block's interface is nearly as complex as what it contains.
_Avoid_: wrapper (for a deep block)

**Deletion test**:
Imagine removing a block and wiring its contents into its neighbours: if complexity vanishes it was a pass-through; if it reappears in several places, the block earns its keep.
_Avoid_: inline test

**Spec**:
The specification of one script, piece of software or tool, kept as `spec.md` next to its PRD in the wiki folder that mirrors its code and changed in the same pull request as the code; "the spec" of a block means its MAS, and of a whole design its HAS.
_Avoid_: design doc, documentation (for this meaning)

**HAS**:
High-level architecture specification: the architecture of a project's whole design (its blocks, the seams between them, the memory map, clocks and resets, and the trade-offs behind them), one per project at `wiki/src/<project>/rtl/has.md`.
_Avoid_: architecture doc, system spec, top-level spec

**MAS**:
Micro-architecture specification: how one block is built behind its interface (the interface itself, data and control paths, FSMs, pipelines, storage), one per block at `wiki/src/<project>/rtl/<block>/mas.md`.
_Avoid_: design spec, uarch doc

**Pull request**:
The GitHub request to merge one branch into another; the only way changes reach `main` or an epic branch.
_Avoid_: merge request, MR, diff

**Decision trail**:
The append-only comments on an issue or epic that record each decision point of long or unattended work: decision, why, evidence link, result.
_Avoid_: log, journal, notes

**Retro**:
A review of past sessions or a landed epic that traces each mistake or slowdown to its cause in the factory and proposes a fix for each as an issue.
_Avoid_: postmortem, reflection

**Label**:
A GitHub label named `kind:value`, defined in `.github/labels.json`. Labels are how work is tracked, grouped on dashboards and checked by agents.
_Avoid_: tag

## Merge authority

**Approval**:
The go-ahead to land a pull request: the reviewer crewmate's pass, plus a captain's explicit word when the issue needs captain. It is a step in the flow, not a GitHub review.
_Avoid_: LGTM, sign-off

**Full send**:
The default merge authority: the first mate lands the pull request once it is approved and its checks are green, without waiting for a captain.
_Avoid_: auto-merge, YOLO

**Needs captain**:
The merge authority of an issue labelled `authority:needs-captain`, and of any pull request that changes the factory's controls: its pull request lands only after a captain explicitly says it is ready.
_Avoid_: full stop, manual merge

## CI

**Regression level**:
One of L0 to L4: a cumulative set of CI checks bound to one trigger, from L0 (fastest, on demand) to L4 (nightly). Running a level runs every level below it.
_Avoid_: light CI, full CI, test tier, suite

**Code owner**:
A person or team listed in `.github/CODEOWNERS`; GitHub requests their review on every pull request.
_Avoid_: maintainer

## Verification

**DV plan**:
The verification plan of one block or testbench, `wiki/src/<project>/dv/<block>/dv-plan.md`, that maps each spec feature and acceptance criterion to its stimulus, checker, coverage points, tests and regression level.
_Avoid_: test plan, verification plan (say DV plan)

**FV plan**:
The formal verification plan of one block, `wiki/src/<project>/fv/<block>/fv-plan.md`, that records each property's statement, spec source, assumptions, result, command and sync commit.
_Avoid_: formal plan, property list

**Sync commit**:
The commit at which a formal property was last confirmed to describe the RTL it covers.

## Hosts and tools

**Tool**:
Code any project can use unchanged, such as the simulation runner or the toolchain; its code is in `tools/<tool>/` and its PRD and spec in `wiki/tools/<tool>/`. Only these are tools.
_Avoid_: repo tool (say tool), global tool

**Script**:
Code bound to one project, or to a block of the `common` project, that changes when that project or block changes; its code is in the project's `scripts` discipline, `src/<project>/scripts/<script>/`, and its PRD and spec in `wiki/src/<project>/scripts/<script>/`.
_Avoid_: project tool, helper

**Software**:
Code and data that run on a design's own CPU (C and assembly sources, firmware and runtimes, linker scripts and the target build setup, ROM and data images built for the design); it is the project's `sw` discipline, `src/<project>/sw/<name>/`, with its PRD and spec in `wiki/src/<project>/sw/<name>/`. Code that runs on the host to build, load or drive it is a script.
_Avoid_: firmware (for the whole discipline), target code, program (for this meaning)

**Board spec**:
The document of one board top, `wiki/src/<project>/fpga/<board>/board-spec.md`: the device, the pins and I/O standards, clocks and constraints, the resource and timing budget, and how the image is built and programmed.
_Avoid_: board doc, pinout (for the whole document)

**Board host**:
The Linux host a board is wired to; the only place that board is programmed and observed.
_Avoid_: lab machine, test PC

**Readiness check**:
The toolchain's check that reads the pinned versions and reports, per tool, pass, warning or fail with the fix, without changing the host.
_Avoid_: doctor, env check
