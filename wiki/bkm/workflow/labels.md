---
status: draft
---

# Labels

Which labels issues and pull requests carry, and when agents set, change and check them. Labels are how work is tracked and how dashboards group it.

## The set

The labels are defined in `.github/labels.json`; the `label-sync` workflow creates them on GitHub. Names are `kind:value`.

| Kind | Values | How many per issue |
|---|---|---|
| `type:` | `feature`, `bug`, `spec`, `bkm`, `infra`, `research`, `epic` | exactly one |
| `area:` | `arch`, `rtl`, `dv`, `fv`, `syn`, `workflow` | at least one |
| `block:` | one per hardware block, named like `src/<type>/<block>` | one per block the issue touches; none for factory work |
| `priority:` | `P0`, `P1`, `P2`, `P3` | exactly one |
| `status:` | `triage`, `ready`, `in-progress`, `in-review`, `blocked` | exactly one |
| `authority:` | `needs-captain` | optional |

## Rules

- Label every issue when you open it: one `type:`, at least one `area:`, `block:` labels for the blocks it touches, one `priority:` and `status:triage`. Why: an unlabelled issue is invisible on every dashboard.
- Before dispatching a crewmate, check the issue has its labels and `status:ready`; fix the labels or ask the captain, rather than dispatching blind. Why: labels are how the first mate knows what kind of work it is handing out.
- Move `status:` as the work moves: `in-progress` at dispatch, `in-review` when the pull request opens, `blocked` while waiting. The issue closes on merge; no status label is needed after that. Why: the dashboard reads status from labels.
- Give the pull request the same `type:`, `area:` and `block:` labels as its issue. Why: dashboards count pull requests too.
- Merge on your own by default (full send). If the issue has `authority:needs-captain`, wait until a captain says the pull request is ready to merge, and record that as a pull request comment. Why: autonomy is the default; a captain opts an issue out (ADR 0007).
- Add a `block:` label to `.github/labels.json` in the same pull request that creates the block. Why: the label set stays the single source.
- Only a captain adds or removes `authority:needs-captain`. Why: it is the captain's control over merge authority.
- Never delete a label from GitHub; remove it from `.github/labels.json` and delete it by hand if it must go. Why: deleting a label strips it from every issue.

## Dashboard

A GitHub Project board for the repo shows the work, with views grouped or filtered by `status:`, `area:`, `block:` and `priority:` labels. Metrics generated from labels and CI go in `wiki/tracker/`.

## Exit criteria

- Every open issue has one `type:`, at least one `area:`, one `priority:` and one `status:` label.
