---
status: draft
---

# Flow

The path every change takes from a captain's intent to `main`.

## Steps

1. **Align.** The captain tells the first mate what they want. If the goal, scope or acceptance is unclear, grill (`wiki/bkm/design/grilling.md`) before writing anything.
2. **Issue.** Write the work as a GitHub issue: what to build and acceptance criteria. The captain assigns it. Every change must start here (ADR 0003).
3. **Branch.** Create `issue/<number>-<slug>` from `main`, or from the epic branch if the issue belongs to an epic.
4. **Worktree.** Check the branch out in `worktrees/issue-<number>/`. One crewmate per worktree.
5. **Code and spec loop.** Change the code under `src/<type>/<block>/` and its spec under `wiki/src/<type>/<block>/` together. Run L0. Repeat until the code matches the spec, the spec matches the issue, and L0 passes.
6. **Pull request.** Commit, push, and open a pull request whose description says `Closes #<number>`.
7. **Review.** A reviewer crewmate, with a fresh context, reviews the pull request against the issue, the spec and the BKMs, and posts its findings as a pull request review. The implementing crewmate fixes them; return to step 5 until the reviewer passes it.
8. **Approval.** Wait for the captain's approval, which the first mate records as a pull request comment, unless the issue is labelled `full-send`.
9. **Merge.** Once L1 passes, merge (see Merging). The issue closes.
10. **Clean up.** Remove the worktree and delete the issue branch.

## Rules

- Change code and spec in the same pull request, never one without the other. Why: a spec that lags the code is wrong, and nobody notices until it misleads someone.
- One issue → one issue branch → one pull request. Why: each pull request maps to exactly one decision.
- Name branches `issue/<number>-<slug>` and `epic/<number>-<slug>`.
- A pull request into `main` must close at least one issue. Why: nothing lands without a decision behind it; the `issue-link` check enforces it.
- Link related issues ("blocked by", "relates to") without making them an epic. Why: an epic is for landing together, not for grouping.
- Approval is a step in this flow, not a GitHub review. Agents act under their captain's account and GitHub never lets an author approve their own pull request, so the reviewer crewmate's verdict plus the captain's word is the approval (ADR 0007).

## Merging

- **Issue branch → `main`:** squash merge. Why: one issue, one commit on `main`.
- **Issue branch → epic branch:** squash merge, once L0 passes. The first mate merges these on its own.
- **Epic branch → `main`:** merge commit, never squash. Why: the epic's issues stay as separate commits on `main` (ADR 0008).
- Merge into `main` without the captain's approval only when the issue is labelled `full-send`.

## Epics

Use an epic only when several issues cannot reach `main` one by one: each would break `main` until the others land.

1. Open the epic as a parent issue, with its issues as sub-issues.
2. Create the epic branch `epic/<number>-<slug>` from `main`.
3. Run each sub-issue through steps 3–10, with the epic branch in place of `main`. Pull requests into the epic branch run L0.
4. When every sub-issue is merged and the epic branch is healthy, open one pull request from the epic branch into `main` that closes the epic and every sub-issue. It goes through review, approval and L1 like any other.

## Exit criteria

- The pull request is merged, every issue it closes is closed, and the worktree and branch are gone.
