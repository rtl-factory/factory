# RTL Factory

A hardware development factory: captains (humans) set direction and decide, a first mate (agent) orchestrates, crewmates (agents) do the work. Every change starts with a GitHub issue; how work is done lives in BKMs.

| Section | What it holds |
|---|---|
| [BKMs](bkm/README.md) | Best Known Methods: how every kind of work is done, plus the engineering principles |
| [Project](project/README.md) | The glossary and the decisions (ADRs) |
| [Specs](specs/README.md) | External reference specifications: standards, protocols, ISAs |
| [Blocks](src/README.md) | Specs and docs for each project, by discipline: its blocks and its scripts, mirroring `src/` |
| [Tools](tools/README.md) | The tools any project can use, each with its requirements (PRD) and spec; a project's scripts sit with the project under Blocks |

Start with the [flow](bkm/flow.md#flow), which covers how a change goes from an issue to `main`, and the [glossary](project/GLOSSARY.md).

This site is built from the [`wiki/`](https://github.com/rtl-factory/factory/tree/main/wiki) folder of the repository on every merge to `main`.
