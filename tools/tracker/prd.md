# Tracker: requirements

## Problem

Captains need to see, at a glance and without asking an agent, what the factory is doing and how healthy the design is: what work is moving or stuck, what waits on them, whether `main` is passing its regressions, how fast work flows, and how each block's quality and implementation numbers are trending. Today this lives scattered across issues, labels, pull requests and CI runs.

## Requirements

**Work status**

1. Show open issues and pull requests grouped by status, area, block and priority, taken from their labels.
2. Show each open epic with its sub-issues and how many are closed.
3. Show what is blocked or waiting on a captain, from labels: open issues and pull requests that need captain (`authority:needs-captain`), and open issues marked blocked (`status:blocked`) with the reason from their latest comment.

**Regression results**

4. Show the latest result of every regression level that runs on `main` (L1 to L4), per block where the level reports per block, and whether `main` is currently healthy.
5. Show each level's pass/fail history over time, with a link to every failing run.

**Flow metrics**

6. Show issue lead time (opened to closed), review rounds per pull request, time pull requests wait on a captain, and merges per week, as trends over time.

**Design metrics**

7. Show, per block and over time: functional coverage, formal proof status (proven, bounded with its depth, or failing), and synthesis area and timing.

**Publishing**

8. Publish the tracker as pages on the wiki site; no server to run.
9. Regenerate it after every merge to `main` and after each scheduled regression level finishes.
10. Show on every page when its data was collected, and say plainly when a source has no data yet instead of showing an empty or zero value.
11. Read only what the factory already produces (issues, labels, pull requests, CI results and reports); never require an agent or a captain to enter data by hand.

## Out of scope

- Editing issues, labels or pull requests from the tracker; it only reads.
- Live updates between regenerations.
- Data from outside the repository and its CI (for example, licence servers or compute usage), until a later requirement adds it.

## Acceptance

Each requirement is verified at the highest place it can be: the generated pages, compared against GitHub and the CI reports they were built from.

| Requirement | Check |
|---|---|
| 1 | For a sample of open issues and pull requests, the grouping on the page matches their labels on GitHub |
| 2 | For an epic with some closed sub-issues, the page shows the same closed and total counts as GitHub |
| 3 | Adding `authority:needs-captain` or `status:blocked` to a test issue makes it appear after the next regeneration; removing the label makes it disappear |
| 4 | After a failing and then a passing run of a level, the page shows the latest result, and the health of `main` changes accordingly |
| 5 | The history of a level lists both runs in order, each failing run linking to its CI run |
| 6 | For a closed issue and a merged pull request, the shown lead time, review rounds and waiting time match their GitHub history; merges per week match the merge count on `main` for that week; each metric shows at least two points in time |
| 7 | For a block with coverage, proof and synthesis results from two runs, the page shows both points and their numbers match the reports CI produced |
| 8 | The published pages are static files on the wiki site; nothing else is deployed |
| 9 | After a merge to `main` and after a scheduled level, the pages show a new collection time |
| 10 | Every tracker page shows its collection time; with a source that has no data (a level with no job yet), the page says so instead of showing zero |
| 11 | Regenerating runs with no manual input |
