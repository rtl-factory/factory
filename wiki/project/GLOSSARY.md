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
A generic, rarely-changing agent capability (under `.claude/skills/`) that defines *how an agent works*, and defers project-specific choices to BKMs.
_Avoid_: prompt, command, plugin
