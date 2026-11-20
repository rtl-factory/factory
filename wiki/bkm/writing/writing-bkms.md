---
status: draft
---

# Writing BKMs

How to write and change a BKM so an agent follows it predictably and a captain can tune it.

## Rules

- Write for an agent to follow. A human reading it should be able to predict what the agent will do.
- Write in English. Everything committed to the repo is in English.
- Use the imperative mood, and put rules before explanation. ("Run lint before review.")
- Follow each rule with one line of *why*.
- Use **must** for gates (enforced by CI or review) and **should** for defaults (deviate only with a stated reason).
- Use the terms in `wiki/project/GLOSSARY.md`.
- Put the BKM in the `wiki/bkm/` subdirectory for its kind of work, never directly in `wiki/bkm/`, and add a line for it to that directory's `README.md` index. The index line is what routes an agent to the BKM, so word it as the situation that calls for it.
- Keep method in BKMs, never in skills. Skills only route here (ADR 0002).
- Start the file with front matter: `status: draft | active`, plus `upstream:` (source path and commit) when the BKM is adapted from outside.
- Give every BKM this shape: a title, a one-line purpose, then **Rules** and/or **Steps**, then **Exit criteria** when the BKM ends in finished work. Add other sections only when they earn their place. BKMs copied from upstream keep their own shape.
- Change a BKM like any other change: issue, pull request, code-owner approval. An agent that finds a BKM wrong opens an issue proposing the change; it never edits a BKM inside unrelated work. Why: BKMs are the captains' controls.
- Follow [writing-for-agents.md](writing-for-agents.md) for how to structure the content.
