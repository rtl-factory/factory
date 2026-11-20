---
status: draft
---

# CI regression levels

Which checks run when. Levels are cumulative: running level N runs every level below it too.

## Levels

| Level | Runs on | Purpose |
|---|---|---|
| L0 | on demand, and inside every L1 run | the fastest possible checks; run it locally while working |
| L1 | every pull request into `main` (pre-merge), and again after every merge to `main` (post-merge) | the minimal gate a pull request must pass |
| L2 | every hour on `main` | broader regression |
| L3 | every 6 hours on `main` | long regression |
| L4 | nightly on `main` | everything, including the longest runs |

## Rules

- Put a check at the lowest level whose runtime fits that level. Why: the faster a failure shows up, the cheaper it is to fix.
- Keep L0 fast enough to run before every push. Why: L0 is the inner loop, for agents and humans alike.
- When L1–L4 fails on `main`, fix forward or revert at once, through an issue. Why: we merge fast because we recover fast (ADR 0005).
- Pull requests into an epic branch run L0; the epic's pull request into `main` runs L1 like any other.

## Open

- Runtime budget per level (minutes) is not yet set.
