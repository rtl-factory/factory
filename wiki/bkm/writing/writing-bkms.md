---
status: draft
---

# Writing BKMs

How to write and change a BKM so an agent follows it predictably and a captain can tune it.

## Rules

- Write for an agent to follow. Why: agents are the readers; a human should still be able to predict what the agent will do.
- Write in English. Why: everything committed to the repo is in English, whatever language we talk in.
- Use the imperative mood and put rules before explanation ("Run lint before review."). Why: an agent acts on the first thing it reads.
- Follow each rule with one line starting "Why:". Why: a rule without its reason gets misapplied at the edges.
- Use **must** for gates (checked by CI, review or the first mate) and **should** for defaults (deviate only with a stated reason). Why: the agent needs to know which rules bend.
- Use the terms in `wiki/project/GLOSSARY.md`, and add a term there before using a new one. Why: one word per concept keeps BKMs unambiguous.
- State each rule in one BKM only; elsewhere, point to it. Why: a rule written twice drifts.
- Put the BKM in the `wiki/bkm/` subdirectory for its kind of work, never directly in `wiki/bkm/`, and add a row for it to the `bkm-router` skill worded as the situation that calls for it. Why: the router row is what makes an agent reach the BKM.
- Keep method in BKMs, never in skills or `AGENTS.md`. Why: BKMs are the one place captains tune (ADR 0002).
- Start the file with front matter: `status: draft | active`, plus `upstream:` (source path and commit) when adapted from outside. Why: status shows what is settled; upstream lets us pull updates by hand.
- Give every BKM this shape: a title, a one-line purpose, then **Rules** and/or **Steps**, then **Exit criteria** when the BKM ends in finished work. Add other sections only when they earn their place. Upstream copies keep their own shape. Why: a fixed shape lets an agent find the part it needs.
- Change a BKM through its own issue and pull request, never inside unrelated work. An agent that finds a BKM wrong opens an issue proposing the change. Why: BKMs are the captains' controls, so their pull requests need captain ([flow.md](../workflow/flow.md) Merging).
- Follow [writing-for-agents.md](writing-for-agents.md) for how to structure the content. Why: it covers pointers, load and hierarchy in depth.
