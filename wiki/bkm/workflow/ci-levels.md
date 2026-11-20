---
status: draft
---

# CI regression levels

Which checks run when. Levels are cumulative: running level N runs every level below it too.

## Levels

| Level | Runs on | Purpose |
|---|---|---|
| L0 | every pull request (pre-merge) | fast gate: lint, build, issue link, smoke tests |
| L1 | every merge to `main` (post-merge) | confirm `main` right after a change lands |
| L2 | every hour on `main` | broader regression |
| L3 | every 6 hours on `main` | long regression |
| L4 | nightly on `main` | everything, including the longest runs |

## Rules

- Put a check at the lowest level whose runtime fits that level's trigger. Why: the faster a failure shows up, the cheaper it is to fix.
- When L1–L4 fails on `main`, fix forward or revert at once, through an issue. Why: we merge fast because we recover fast (ADR 0005).
- Pull requests into an epic branch run L0.

## Open

- Runtime budget per level (minutes) is not yet set.
