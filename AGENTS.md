# AGENTS.md

This repo is a hardware development factory: captains (humans) set direction and decide, a first mate (agent) orchestrates, crewmates (agents) do the work. How work is done lives in BKMs under `wiki/bkm/`; follow them, and tune them through issues when they are wrong.

- Every change starts with a GitHub issue. Do not change the repo without one.
- Open every issue and pull request with its template from `wiki/bkm/issues.md`, starting with Spec references, whether you use the web UI, `gh` or the API. Never write a body of your own shape.
- Before any piece of work, use the `bkm-router` skill to find the BKMs to follow. For the steps of a change and who may merge, use the `flow` skill.
- On every change, take the strategic fix over the tactical one: fix the cause so the next change gets easier, not only the symptom in front of you. The rule is the Program Strategically section of `wiki/bkm/principles.md`.
- Run every check through Bazel, per the Build section of `wiki/bkm/flow.md`.
- Use the terms in `wiki/project/GLOSSARY.md`.
- Respect the decisions in `wiki/project/adr/`; if your work contradicts one, say so instead of overriding it.
- Keep this file the only agent instructions file; never add a `CLAUDE.md`. Claude Code and Codex read `AGENTS.md` natively, and Claude Code stops reading it once a `CLAUDE.md` exists.
- Never change a BKM, skill, this file, the glossary, an ADR or `.github/` inside unrelated work; open an issue for it.
