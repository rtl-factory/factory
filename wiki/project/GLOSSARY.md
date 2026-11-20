# Factory

The shared vocabulary for this repo: a hardware development factory where agents do the execution and humans make the decisions.

## Language

**Factory**:
This repository together with the BKMs, skills, tools and CI that turn hardware work (spec, RTL, verification, formal, synthesis) into reviewed, merged results.
_Avoid_: framework, platform, pipeline

**BKM**:
Best Known Method: a written, prescriptive document that defines how a recurring kind of work is done in the factory. It is written for agents to follow, and it is the knob humans turn to change how agents work.
_Avoid_: guide, how-to, process doc, playbook

**Skill**:
A thin, rarely-changing entry point under `.claude/skills/` that routes an agent to the BKM it should follow. Skills hold no method of their own.
_Avoid_: prompt, command, plugin

## Crew

**Captain**:
A human who directs the work: writes and assigns issues, makes the decisions only a human can make, and approves what lands on `main`. Captains talk to the first mate, not to crewmates.
_Avoid_: user, operator, owner (for this role)

**First mate**:
The agent a captain talks to. It turns the captain's intent into dispatched work, spawns and supervises crewmates, and reports outcomes and decisions back. It orchestrates; it does not do the work itself.
_Avoid_: orchestrator, lead agent, manager, chief of staff

**Crewmate**:
An agent spawned by the first mate to do one issue, in its own worktree on its own issue branch. It reports only to the first mate.
_Avoid_: worker, subagent, teammate

## Work flow

**Issue**:
A GitHub issue. Every change to the repo starts with one, and a human assigning it to an agent is what starts the work.
_Avoid_: ticket, task, story, request

**Epic**:
A parent issue whose sub-issues cannot reach `main` one by one and must land together through its epic branch. Every parent issue is an epic; issues that are merely related are linked, not grouped.
_Avoid_: feature, initiative, project, tracking issue

**Issue branch**:
The branch where one issue's work is done. It merges into `main`, or into its epic branch when the issue belongs to an epic.

**Epic branch**:
The integration branch of an epic. Its issue branches merge into it; it merges into `main` through one pull request once it is healthy.
_Avoid_: feature branch, integration branch

**Pull request**:
The GitHub request to merge one branch into another; the only way changes reach `main` or an epic branch.
_Avoid_: merge request, MR, diff

## CI

**Regression level**:
One of L0 to L4, a cumulative set of CI checks bound to one trigger: L0 on demand (the fastest checks), L1 on every pull request into `main` and again after the merge, L2 hourly, L3 every 6 hours, L4 nightly. Running a level runs every level below it.
_Avoid_: light CI, full CI, test tier, suite

**Code owner**:
A person or team listed in `.github/CODEOWNERS` whose approval a pull request into `main` needs.
_Avoid_: reviewer, maintainer (when the approval is required)
