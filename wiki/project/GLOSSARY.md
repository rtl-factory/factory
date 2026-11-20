---
title: Glossary
---

# Factory

The shared vocabulary for this repo: a hardware development factory where agents do the execution and humans make the decisions.

## Language

**Factory**:
This repository together with the BKMs, skills, tools and CI that turn hardware work (spec, RTL, verification, formal, synthesis) into reviewed, merged results.
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
An agent the first mate dispatches to do one issue, in its own worktree on its own issue branch. It reports only to the first mate.
_Avoid_: worker, subagent, implementer, teammate

**Reviewer crewmate**:
A crewmate with a fresh context that reviews another crewmate's pull request against the issue, the spec and the BKMs. It never reviews its own work.
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

**Block**:
One hardware design unit, with its code under `src/<type>/<block>/` and its spec under `wiki/src/<type>/<block>/`.
_Avoid_: module (for this meaning), IP, component

**Vertical slice**:
An issue that delivers one narrow but complete behaviour, ending in a state a reviewer crewmate can check; for a block, typically spec, RTL and a DV test for one feature.
_Avoid_: horizontal slice, layer, phase

**PRD**:
Product requirements document: what a piece of hardware must do, for whom, and how we will know it does, in `wiki/prd/`. Blocks and their specs follow from it.
_Avoid_: requirements spec, MRD

**Spec**:
The specification of one block, in `wiki/src/<type>/<block>/`. It changes in the same pull request as the block's code.
_Avoid_: design doc, documentation (for this meaning)

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

**Verification plan**:
The per-block document in `wiki/src/dv/<block>/` that maps each spec feature and acceptance criterion to its stimulus, checker, coverage points, tests and regression level.
_Avoid_: test plan (for this meaning)

**Formal plan**:
The per-block document in `wiki/src/fv/<block>/` that records each property's statement, spec source, assumptions, result, command and sync commit.
_Avoid_: property list

**Sync commit**:
The commit at which a formal property was last confirmed to describe the RTL it covers.
