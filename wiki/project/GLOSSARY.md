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

## Work flow

**Issue**:
A GitHub issue. Every change to the repo starts with one, and an issue is what triggers an agent to do the work it describes.
_Avoid_: ticket, task, story, request

**Epic**:
A parent issue whose sub-issues cannot reach `main` one by one and must land together through an epic branch. Opt-in: used only when issues depend on each other that way.
_Avoid_: feature, initiative, project

**Issue branch**:
The branch where one issue's work is done. It merges into `main`, or into its epic branch when the issue belongs to an epic.

**Epic branch**:
The integration branch of an epic. Its issue branches merge into it under light CI; it merges into `main` under full CI once it is healthy.
_Avoid_: feature branch, integration branch

**Pull request**:
The GitHub request to merge one branch into another; the only way changes reach `main` or an epic branch.
_Avoid_: merge request, MR, diff
