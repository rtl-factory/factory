---
status: draft
---

# Labels

Which labels issues and pull requests carry, and when agents set, change and check them.

## Kinds

`.github/labels.json` is the single source for label names, colours and descriptions; the `label-sync` workflow creates them on GitHub. Names are `kind:value`.

| Kind | Per issue |
|---|---|
| `type:` | exactly one |
| `area:` | at least one |
| `block:` | one per block the issue touches; none for factory work |
| `priority:` | exactly one |
| `status:` | exactly one while open; none once closed |
| `authority:` | optional |

## Rules

- Label every issue when you open it: one `type:`, at least one `area:`, `block:` labels for the blocks it touches, one `priority:` and `status:triage`. Why: an unlabelled issue is invisible on every dashboard.
- Move `status:` as the work moves: `ready` once the issue is clear, `in-progress` at dispatch, `in-review` when the pull request opens, `blocked` while waiting. The `status-clear` workflow removes it when the issue closes; never set one on a closed issue. Why: dashboards and the first mate read status from labels, and a closed issue left `in-progress` reads as live work.
- The first mate must check an issue's labels before dispatching on it, and fix them or escalate to the captain rather than dispatch blind. Why: labels tell the first mate what kind of work it is handing out.
- Give a pull request the same `type:`, `area:` and `block:` labels as its issue. Why: dashboards count pull requests too.
- Only a captain may add or remove `authority:needs-captain`. Why: it is the captain's control over merge authority; what it does is in [flow.md](flow.md) Merging.
- Add a `block:` label to `.github/labels.json` in the same pull request that creates the block. Why: the label set stays in one place.
- Never delete a label on GitHub as part of other work; remove it from `.github/labels.json` and delete it by hand. Why: deleting a label strips it from every issue.

## Dashboard

A GitHub Project board for the repo shows the work, with views filtered and grouped by `status:`, `area:`, `block:` and `priority:` labels. The tracker tool will publish generated status, regression and metric pages on the wiki site; it is not built yet ([its PRD](../../tools/tracker/prd.md)).

## Exit criteria

- Every open issue has one `type:`, at least one `area:`, one `priority:` and one `status:` label, and no closed issue has a `status:` label.
