# AGENTS.md

This repo is a hardware development factory: captains (humans) set direction and decide, a first mate (agent) orchestrates, crewmates (agents) do the work. How work is done lives in BKMs under `wiki/bkm/`; follow them, and tune them through issues when they are wrong.

- Every change starts with a GitHub issue. Do not change the repo without one.
- Before any piece of work, use the `bkm-router` skill to find the BKMs to follow. For the steps of a change and who may merge, use the `flow` skill.
- Use the terms in `wiki/project/GLOSSARY.md`.
- Respect the decisions in `wiki/project/adr/`; if your work contradicts one, say so instead of overriding it.
- Never change a BKM, skill, this file, the glossary, an ADR or `.github/` inside unrelated work; open an issue for it.
