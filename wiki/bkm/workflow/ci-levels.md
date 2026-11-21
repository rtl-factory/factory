---
status: draft
---

# CI regression levels

Which checks run when. This BKM is the single source for the CI schedule. Levels are cumulative: running level N runs every level below it.

## Levels

| Level | Runs on | Purpose |
|---|---|---|
| L0 | on demand; every pull request into an epic branch; inside every L1 run | the fastest possible checks; run it locally while working |
| L1 | every pull request into `main` (pre-merge), and again after every merge to `main` (post-merge) | the minimal gate a pull request must pass |
| L2 | every hour on `main` | broader regression |
| L3 | every 6 hours on `main` | long regression |
| L4 | nightly on `main` | everything, including the longest runs |

These workflows run outside the levels; they are workflow rules, not regression checks:

- `issue-link` runs on every pull request.
- `label-sync` creates and updates labels from `.github/labels.json` on every merge to `main` that changes it ([labels.md](labels.md)).
- `status-clear` removes `status:` labels from an issue when it closes, and sets `status:triage` when it reopens ([labels.md](labels.md)).
- `pages` builds the wiki site (`mkdocs build --strict`) on every pull request that touches the wiki, and builds and deploys it on every merge to `main` that does ([wiki-site.md](wiki-site.md)).

## Jobs

| Level | Job | What it runs |
|---|---|---|
| L0 | `l0` (`.github/workflows/l0.yml`), on every pull request into `main` or an epic branch and every merge to `main` | the comment lint (`tools/lint/comment_lint.py`), the unit tests of every project's scripts and of the tools, and every L0 simulation target (`tools/sim/sim.py test --level L0`) |

L1 to L4 have no job yet: until the L1 job exists, a pull request into `main` runs only L0 in CI.

## Rules

- Put a check at the lowest level whose runtime fits that level. Why: the faster a failure shows up, the cheaper it is to fix.
- Keep L0 fast enough to run before every push. Why: L0 is the inner loop, for agents and humans alike.
- When L1–L4 fails on `main`, fix forward or revert at once, through an issue labelled `priority:P0`. Why: we merge fast because we recover fast (ADR 0005).
- Until a level has a CI job, treat it as passing and say so in the pull request. Why: the levels are defined before the runners exist; an honest note keeps that visible.

## Open

- Runtime budget per level is not yet set.
- L1 to L4 have no CI job yet; a self-hosted runner is planned for them.
