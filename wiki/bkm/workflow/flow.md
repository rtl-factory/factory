---
status: draft
---

# Flow

The path every change takes from intent to `main`.

## Steps

1. **Align.** A captain and the first mate agree on the outcome. Either may start: a captain by opening an alignment or grilling session, the first mate by proposing work within a goal the captain already set. Grill (`wiki/bkm/design/grilling.md`) if the goal, scope or acceptance is unclear.
2. **Issue.** Open or update the GitHub issue: what to build, acceptance criteria, labels per [labels.md](labels.md). The first mate may open issues and update epics on its own as work goes, within the aligned goal. Mark it `status:ready` once it is clear enough to dispatch.
3. **Branch.** Create `issue/<number>-<slug>` from `main`, or from the epic branch if the issue belongs to an epic.
4. **Worktree.** Check the branch out in `worktrees/issue-<number>/`.
5. **Change.** Make the change. When it touches a block, a script or a tool, change its code and its documents at the mirrored wiki path ([repo-layout.md](repo-layout.md)) together, and loop until they agree. Run L0 until it passes.
6. **Pull request.** Commit, push, and open a pull request whose description says `Closes #<number>`.
7. **Review.** The first mate dispatches a reviewer crewmate; it reviews against the issue, the spec and the BKMs and posts its findings as a pull request review. Fix the findings and return to step 5 until the reviewer passes the pull request.
8. **Approve.** Treat the reviewer's pass as approval, unless the pull request needs captain (see Merging); then wait for a captain to say it is ready, and record that as a pull request comment.
9. **Land.** Once approved and the required checks are green, the first mate merges it (see Merging).
10. **Clean up.** Remove the worktree and delete the issue branch.

## Rules

- Every change must start with an issue. Why: the issue is the record of what was decided and why (ADR 0003).
- A crewmate must only be dispatched on an issue labelled `status:ready`. Why: a vague issue produces vague work.
- One issue → one issue branch → one pull request. Why: each pull request maps to exactly one decision.
- A change to a block, a script or a tool must change its code and its spec in the same pull request. Why: a spec that lags the code misleads the next reader.
- A pull request into `main` must close at least one issue. Why: nothing lands without a decision behind it; the `issue-link` check enforces it.
- Related issues should be linked ("blocked by", "relates to"), not made into an epic. Why: an epic is for landing together, not for grouping.

## Merging

This section is the single source for who may merge what.

- Only the first mate lands pull requests. Why: one agent keeps track of what is in flight.
- **Full send** is the default: land once the reviewer has passed it and the required checks are green. Why: most work is done by agents, so captains spend attention only where they ask to (ADR 0007).
- A pull request **needs captain**, and must wait for a captain's explicit go-ahead, when its issue has `authority:needs-captain` or when it changes the factory's controls: `AGENTS.md`, `CLAUDE.md`, `.claude/skills/`, `wiki/bkm/`, `wiki/project/` or `.github/`. Why: BKMs and settings are how captains steer agents; agents propose changes to them, captains accept them.
- Issue branch → `main` or epic branch: squash merge. Why: one issue, one commit.
- Epic branch → `main`: merge commit, never squash. Why: each issue stays its own commit on `main` (ADR 0008).

## Epics

Use an epic only when several issues cannot reach `main` one by one: each would break `main` until the others land.

1. Open the epic as a parent issue, with its issues as sub-issues linked by "blocked by" where order matters.
2. Create the epic branch `epic/<number>-<slug>` from `main`.
3. Run each sub-issue through steps 3–10 with the epic branch in place of `main`; pull requests into the epic branch run L0.
4. When a sub-issue's pull request lands in the epic branch, close the sub-issue with a comment linking the pull request. Why: GitHub only closes issues on merges into `main`, and an open sub-issue would keep its dependents blocked.
5. When every sub-issue is closed and the epic branch is healthy, open one pull request from the epic branch into `main` that closes the epic. It goes through review, approval and L1 like any other.

## Exit criteria

- The pull request is merged, every issue it closes is closed, and the worktree and branch are gone.
