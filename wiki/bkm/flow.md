---
status: draft
---

# Flow and CI

The path every change takes from intent to `main`, the CI levels that gate it, and the GitHub settings that enforce it.

## Flow

### Steps

1. **Align.** A captain and the first mate agree on the outcome. Either may start: a captain by opening an alignment or grilling session, the first mate by proposing work within a goal the captain already set. Grill ([grilling](planning.md#grilling)) if the goal, scope or acceptance is unclear.
2. **Issue.** Open or update the GitHub issue with its template per [templates](issues.md#templates) and labels per [labels](issues.md#labels). The first mate may open issues and update epics on its own as work goes, within the aligned goal. Mark it `status:ready` once it is clear enough to dispatch.
3. **Branch.** Create `issue/<number>-<slug>` from `main`, or from the epic branch if the issue belongs to an epic.
4. **Worktree.** Check the branch out in `worktrees/issue-<number>/`.
5. **Change.** Make the change. When it touches a block, a script or a tool, change its code and its documents at the mirrored wiki path ([repository layout](layout.md#repository-layout)) together, and loop until they agree. Run L0 until it passes.
6. **Pull request.** Commit, push, and open a pull request whose description says `Closes #<number>`.
7. **Review.** The first mate dispatches a reviewer crewmate; it reviews against the issue, the spec and the BKMs and posts its findings as a pull request review. Fix the findings and return to step 5 until the reviewer passes the pull request.
8. **Approve.** Treat the reviewer's pass as approval, unless the pull request needs captain (see Merging); then wait for a captain to say it is ready, and record that as a pull request comment.
9. **Land.** Once approved and the required checks are green, the first mate merges it (see Merging).
10. **Clean up.** Remove the worktree and delete the issue branch.

### Rules

- Every change must start with an issue. Why: the issue is the record of what was decided and why (ADR 0003).
- A crewmate must only be dispatched on an issue labelled `status:ready`. Why: a vague issue produces vague work.
- One issue → one issue branch → one pull request. Why: each pull request maps to exactly one decision.
- A change to a block, a script or a tool must change its code and its spec in the same pull request. Why: a spec that lags the code misleads the next reader.
- A pull request into `main` must close at least one issue. Why: nothing lands without a decision behind it; the `issue-link` check enforces it.
- Related issues should be linked ("blocked by", "relates to"), not made into an epic. Why: an epic is for landing together, not for grouping.

### Merging

This section is the single source for who may merge what.

- Only the first mate lands pull requests. Why: one agent keeps track of what is in flight.
- **Full send** is the default: land once the reviewer has passed it and the required checks are green. Why: most work is done by agents, so captains spend attention only where they ask to (ADR 0007).
- A pull request **needs captain**, and must wait for a captain's explicit go-ahead, when its issue has `authority:needs-captain` or when it changes the factory's controls: `AGENTS.md`, `.claude/skills/`, `wiki/bkm/`, `wiki/project/` or `.github/`. Why: BKMs and settings are how captains steer agents; agents propose changes to them, captains accept them.
- Issue branch → `main` or epic branch: squash merge. Why: one issue, one commit.
- Epic branch → `main`: merge commit, never squash. Why: each issue stays its own commit on `main` (ADR 0008).

### Epics

Use an epic only when several issues cannot reach `main` one by one: each would break `main` until the others land.

1. Open the epic as a parent issue, with its issues as sub-issues linked by "blocked by" where order matters.
2. Create the epic branch `epic/<number>-<slug>` from `main`.
3. Run each sub-issue through steps 3–10 with the epic branch in place of `main`; pull requests into the epic branch run L0.
4. When a sub-issue's pull request lands in the epic branch, close the sub-issue with a comment linking the pull request. Why: GitHub only closes issues on merges into `main`, and an open sub-issue would keep its dependents blocked.
5. When every sub-issue is closed and the epic branch is healthy, open one pull request from the epic branch into `main` that closes the epic. It goes through review, approval and L1 like any other.

### Exit criteria

- The pull request is merged, every issue it closes is closed, and the worktree and branch are gone.

## CI levels

Which checks run when. This section is the single source for the CI schedule. Levels are cumulative: running level N runs every level below it.

### Levels

| Level | Runs on | Purpose |
|---|---|---|
| L0 | on demand; every pull request into an epic branch; inside every L1 run | the fastest possible checks; run it locally while working |
| L1 | every pull request into `main` (pre-merge), and again after every merge to `main` (post-merge) | the minimal gate a pull request must pass |
| L2 | every hour on `main` | broader regression |
| L3 | every 6 hours on `main` | long regression |
| L4 | nightly on `main` | everything, including the longest runs |

These workflows run outside the levels; they are workflow rules, not regression checks:

- `issue-link` runs on every pull request.
- `label-sync` creates and updates labels from `.github/labels.json` on every merge to `main` that changes it ([labels](issues.md#labels)).
- `status-clear` removes `status:` labels from an issue when it closes, and sets `status:triage` when it reopens ([labels](issues.md#labels)).
- `pages` builds the wiki site (`mkdocs build --strict`) on every pull request that touches the wiki, and builds and deploys it on every merge to `main` that does ([wiki site](layout.md#wiki-site)).

### Jobs

| Level | Job | What it runs |
|---|---|---|
| L0 | `l0` (`.github/workflows/l0.yml`), on every pull request into `main` or an epic branch and every merge to `main` | the comment lint (`tools/lint/comment_lint.py`), the unit tests of every project's scripts and of the tools, every L0 simulation and formal target (one `bazel test --config=ci --config=l0 //src/...`) |

L1 to L4 have no job yet: until the L1 job exists, a pull request into `main` runs only L0 in CI.

### Rules

- Put a check at the lowest level whose runtime fits that level. Why: the faster a failure shows up, the cheaper it is to fix.
- Keep L0 fast enough to run before every push. Why: L0 is the inner loop, for agents and humans alike.
- When L1–L4 fails on `main`, fix forward or revert at once, through an issue labelled `priority:P0`. Why: we merge fast because we recover fast (ADR 0005).
- Until a level has a CI job, treat it as passing and say so in the pull request. Why: the levels are defined before the runners exist; an honest note keeps that visible.

### Open

- Runtime budget per level is not yet set.
- L1 to L4 have no CI job yet; a self-hosted runner is planned for them.

## GitHub settings

The repository settings that enforce the flow. A repo admin applies them; agents cannot.

### Steps

1. **Ruleset for `main`.** Settings → Rules → Rulesets → New branch ruleset; target the default branch; enforcement **Active**; no bypass list:
   - **Restrict deletions** and **Block force pushes**.
   - **Require a pull request before merging**, with required approvals **0** and allowed merge methods **Squash** and **Merge** (not Rebase). Why: approval is a flow step, not a GitHub review (ADR 0007); merge methods follow ADR 0008.
   - **Require status checks to pass**: `issue-link`, and the L1 job once it exists. Require branches to be up to date.
2. **Ruleset for epic branches.** Target `epic/*`; enforcement **Active**; require a pull request, allowed merge method **Squash** only, and require `issue-link` and `l0` (the L0 job, [CI levels](#ci-levels)).
3. **Labels.** Delete labels that `.github/labels.json` no longer lists (renamed `area:syn` → `area:fpga`: move issues to the new label first). Run the `label-sync` workflow once (Actions → label-sync → Run workflow). It reruns on every change to `.github/labels.json` merged into `main`.
4. **Wiki site.** Settings → Pages → Build and deployment → Source: **GitHub Actions**. Then run the `pages` workflow once (Actions → pages → Run workflow); it reruns on every merge to `main` that touches the wiki. The site is at `https://rtl-factory.github.io/factory/`.
5. **Dashboard.** Create a GitHub Project for the repo with views per [labels](issues.md#labels).

`.github/CODEOWNERS` lists the repo admins so GitHub requests their review; their review is not required.

### Exit criteria

- Both rulesets are active, the labels exist, the wiki site is live, and the Project board shows open issues.
