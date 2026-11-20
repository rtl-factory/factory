---
status: draft
upstream: mattpocock/skills skills/engineering/to-tickets/SKILL.md @ abeeaf95, mattpocock/skills skills/engineering/triage/SKILL.md @ abeeaf95, mattpocock/skills skills/engineering/setup-matt-pocock-skills/issue-tracker-github.md @ abeeaf95 (MIT, see ../LICENSE-mattpocock-skills); cursor/plugins pstack/skills/poteto-mode/playbooks/multi-phase-plan.md @ aaeb0ea (MIT, see ../LICENSE-pstack)
---

# Issues and epics

How to turn an aligned plan, spec or conversation into well-formed issues, when they become an epic, and how to triage an issue until it is `status:ready`. The steps of a change and the epic branch mechanics are in [flow.md](flow.md); label names and moves are in [labels.md](labels.md).

## Rules

- Cut work into vertical slices: each issue delivers one narrow, complete behaviour that ends in a state a reviewer crewmate can check, per [sequence-verifiable-units](../principles/sequence-verifiable-units.md). For a block, a slice is typically spec + RTL + DV test for one feature of that block. Why: a horizontal slice ("all the RTL", "all the tests") cannot be verified until its siblings land.
- Size each issue for one crewmate in one fresh context. Why: a crewmate that runs out of context lands half a change.
- Put prefactoring (a refactor that makes the feature easy) in its own issue, first, blocking the feature. Why: the refactor then lands green on its own and the feature diff stays small.
- Write what to build as behaviour, in glossary terms: what the block does on its interfaces, not a list of files or signals to edit. Name the block; leave file paths and code out unless a prototype produced a snippet (an FSM, a register map) that encodes a decision better than prose. Why: paths and code go stale; behaviour is what review checks.
- Write each acceptance criterion as a check with a pass predicate: a named test or seed passing, a formal property proven (or a counterexample gone), lint and CDC clean, a synthesis report meeting a stated number. For a timing, area or power criterion, name the metric, the baseline measured on `main` first and the number that fails ([explain-the-number](../principles/explain-the-number.md)). Why: "works" is not checkable; a predicate is.
- Add a "blocked by" link only where one issue truly gates another, using GitHub's native issue dependencies (`gh issue edit <n> --add-blocked-by <m>`). Open blockers first so links point at real numbers. Why: the first mate dispatches every ready issue whose blockers are closed, so a false edge serializes work for nothing.
- Open an epic only when the issues cannot reach `main` one by one: each would break `main` until the others land (ADR 0004). Being related, large or ordered is not enough; link those issues instead. Why: an epic branch drifts from `main` and delays real regression.
- For a wide mechanical change (rename a shared interface, retype a bus) prefer expand–contract over an epic: an issue that adds the new form beside the old, migrate issues per block each blocked by it, and a contract issue that removes the old form, blocked by every migrate issue. Use an epic only when even the batches cannot stay green alone. Why: expand–contract keeps every step landable on `main`.
- Make an issue a sub-issue only of an epic. Why: every parent issue is an epic (glossary).
- Label every issue you open per [labels.md](labels.md); an epic carries `type:epic`, and each sub-issue carries its own labels. Why: the first mate reads kind and status from labels.
- An issue **must** pass the ready test below before it moves to `status:ready`. Why: a crewmate is dispatched only on `status:ready` ([flow.md](flow.md)).

## Ready test

An issue is ready when all hold:

- What to build is stated as behaviour, with no decision left that only a captain can make.
- Every acceptance criterion is a check with a pass predicate.
- Its labels are complete per [labels.md](labels.md), and its "blocked by" links are set.
- It fits one crewmate, one issue branch and one pull request.
- For a bug, the failure is reproduced: a failing test, seed, waveform or counterexample is named in the issue.

## Steps: splitting a plan into issues

1. Read the source in full: the alignment notes, spec or issue, with its comments. Read the glossary and the ADRs for the area.
2. Look at the current blocks and specs for prefactoring that would make the slices easier.
3. Draft the slices as a numbered list, each with a title, its blockers and what it delivers. Decide whether the set is an epic (Rules).
4. If the split came out of an alignment session, show the list to the captain and adjust granularity and blockers until they agree. Within an already-aligned goal, go on and report the split afterwards ([never-block-on-the-human](../principles/never-block-on-the-human.md)).
5. Open the issues blockers first, each with the body below. For an epic, open the parent with `type:epic` and attach each issue as a sub-issue (`gh issue edit <epic> --add-sub-issue <n>`).
6. If the source was an existing issue and the set is not an epic, narrow that issue to one slice and link the others with "relates to".
7. Triage each new issue (next steps).

Issue body:

```markdown
## What to build
<the behaviour this issue makes work>

## Acceptance criteria
- [ ] <check with a pass predicate>

## Blocked by
<#n, or "None"; omit when set as native dependencies>
```

## Steps: triaging an issue

1. Read the issue, its comments and labels, and any earlier triage notes so you do not repeat a question already answered.
2. Search open and closed issues and the repo, by domain concept rather than wording, for the same request or an existing implementation. Note where you looked.
3. Choose one outcome:
   - **Duplicate**: comment with the link to the original and close it as duplicate.
   - **Already done**: comment with where it lives (pull request or block) and close it as completed.
   - **Bug**: reproduce it at L0 or in simulation before going further; if you cannot, it needs info.
   - **Needs info**: comment with triage notes, "What is established" and "What is still needed", each question specific; keep `status:triage` and escalate to the captain if only they can answer. Grill ([grilling.md](../design/grilling.md)) when the request itself is unclear.
   - **Reject**: escalate with your reason; close as not planned only on a captain's word, giving the reason in the closing comment. Why: rejecting changes the goal, which is the captain's call.
   - **Clear**: fix labels and links, split it if it fails the size rule, then move it to `status:ready`.

## Exit criteria

- Every issue from the source is open, labelled, linked by its real blockers, and either `status:ready` or waiting in `status:triage` with its open questions written on it.
- An epic exists only where ADR 0004 requires one, with every issue attached as a sub-issue.
