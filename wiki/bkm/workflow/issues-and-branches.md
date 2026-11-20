---
status: draft
---

# Issues and branches

How a change moves from an issue to `main`.

## Rules

- Every change must start with a GitHub issue. Why: the issue is the factory's record of what humans decided (ADR 0003).
- An agent starts work when a human assigns the issue to it. Don't start on an unassigned issue. Why: assignment is the human's approval.
- One issue → one issue branch → one pull request. Why: each pull request maps to exactly one decision.
- Name branches `issue/<number>-<slug>` and `epic/<number>-<slug>`, e.g. `issue/12-rtl-lint-bkm`.
- A pull request into `main` must close at least one issue (`Closes #<number>` in the body). Why: nothing lands without a decision behind it; CI enforces this.
- Link related issues to each other ("blocked by", "relates to") without making them an epic. Why: an epic is for landing together, not for grouping.

## Epics

Use an epic only when several issues cannot reach `main` one by one: each would break `main` until the others land.

1. Open the epic as a parent issue, with its issues as sub-issues.
2. Create the epic branch `epic/<number>-<slug>` from `main`.
3. Branch each issue from the epic branch and open its pull request into the epic branch. These pull requests run L0.
4. When every sub-issue is merged and the epic branch is healthy, open one pull request from the epic branch into `main`. It closes the epic and every sub-issue, and runs L1 like any pull request into `main`.

## Exit criteria

- The pull request is merged, and every issue it closes is closed.
