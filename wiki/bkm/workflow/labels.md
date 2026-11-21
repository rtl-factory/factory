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
| `size:` | exactly one |
| `complexity:` | exactly one |
| `status:` | exactly one while open; none once closed |
| `authority:` | optional |

## Rules

- Label every issue when you open it: one `type:`, at least one `area:`, `block:` labels for the blocks it touches, one `priority:`, one `size:`, one `complexity:` and `status:triage`. Why: an unlabelled issue is invisible on every dashboard.
- Set `size:` from the change one pull request will make: `minimal` (a one-line or wording fix), `small` (one block, document or tool, under about 100 changed lines), `medium` (one block or tool with its spec and tests, up to about 400 lines), `large` (several blocks or documents, still one crewmate and one pull request), `xlarge` (more than one crewmate or pull request). An `xlarge` issue must be split or made an epic before `status:ready`; an epic carries the size of the whole. Why: the first mate plans and batches by size, and an issue too big for one crewmate lands half done.
- Set `complexity:` from how much is unknown: `low` (mechanical; the method and the answer are known), `medium` (a known method with design choices inside existing patterns), `high` (new design, unknown risk or open questions; grill or research first and expect captain decisions). Re-set both when triage or the work shows they were wrong. Why: complexity, not size, says how much review and alignment the work needs.
- Move `status:` as the work moves: `ready` once the issue is clear, `in-progress` at dispatch, `in-review` when the pull request opens, `blocked` while waiting. The `status-clear` workflow removes it when the issue closes and sets `status:triage` when it reopens; never set one on a closed issue. Why: dashboards and the first mate read status from labels, and a closed issue left `in-progress` reads as live work.
- The first mate must check an issue's labels before dispatching on it, and fix them or escalate to the captain rather than dispatch blind. Why: labels tell the first mate what kind of work it is handing out.
- Give a pull request the same `type:`, `area:` and `block:` labels as its issue. Why: dashboards count pull requests too.
- Only a captain may add or remove `authority:needs-captain`. Why: it is the captain's control over merge authority; what it does is in [flow.md](flow.md) Merging.
- Add a `block:` label to `.github/labels.json` in the same pull request that creates the block. Why: the label set stays in one place.
- Never delete a label on GitHub as part of other work; remove it from `.github/labels.json` and delete it by hand. Why: deleting a label strips it from every issue.

## Dashboard

A GitHub Project board for the repo shows the work, with views filtered and grouped by `status:`, `area:`, `block:` and `priority:` labels. The tracker tool will publish generated status, regression and metric pages on the wiki site; it is not built yet ([its PRD](../../tools/tracker/prd.md)).

## Exit criteria

- Every open issue has one `type:`, at least one `area:`, one `priority:`, one `size:`, one `complexity:` and one `status:` label, and no closed issue has a `status:` label.
