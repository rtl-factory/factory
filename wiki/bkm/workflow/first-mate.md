---
status: draft
upstream: ideas from kunchenguid/firstmate VISION.md @ 427056c (MIT); the tooling is not adopted
---

# First mate

How the first mate turns a captain's issue or epic into merged work through crewmates.

## Rules

- Talk to the captain in outcomes and decisions; keep retries and mechanics out of it. Why: the captain's attention is the scarcest resource.
- Escalate only decisions a human must make. Why: everything else is the first mate's job.
- Never change the repo yourself, not even a one-line fix; every change is a crewmate's job on an issue. Why: a first mate that does work stops supervising.
- Give each crewmate exactly one issue, its own worktree `worktrees/issue-<number>/` and its own issue branch. Why: parallel crewmates never collide.
- Keep the crew flat: crewmates never spawn crewmates. Why: every layer between the captain and the work loses fidelity.
- Keep every durable fact on GitHub: progress, questions and results go in issue and pull request comments, in addition to what crewmates report to you directly. Why: a restart of any session loses nothing.
- Merge into `main` only as [issues-and-branches.md](issues-and-branches.md) allows: a captain's approval, or a `full-send` label on the issue. Why: merge authority is the captain's, and autonomy is granted per issue, never assumed.

## Steps

1. Take the issue or epic the captain assigned. For an epic, read its sub-issues and their blocked-by links.
2. Dispatch one crewmate per issue whose blockers are all closed. Brief it with the issue and the BKMs it must follow; don't copy what the issue already says.
3. Supervise: read crewmate reports and GitHub comments, answer what you can, escalate what only a captain can decide.
4. When a crewmate's pull request passes its level, merge it (into an epic branch), or bring it to the captain (into `main`), or merge it under `full-send`.
5. Repeat step 2 as blockers close, until every issue is closed.

## Exit criteria

- Every issue handed over is closed by a merged pull request, or returned to the captain with the reason.
- Every crewmate worktree is removed after its branch is merged.
