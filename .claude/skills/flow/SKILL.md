---
name: flow
description: How every change is driven, from alignment to landing on main. Use when starting, continuing or finishing work on an issue or epic, or when unsure what the next step is.
---

Every change runs this loop. The rules for each step are in `wiki/bkm/workflow/flow.md`; read it before acting, and follow its Merging section for who may merge.

1. **Align**: a captain and the first mate agree on the outcome (`/grill` if unclear).
2. **Issue**: the goal becomes labelled GitHub issues, or an epic of sub-issues (`to-issues`). No issue, no change.
3. **Branch**: `issue/<number>-<slug>` from `main`, or from the epic branch.
4. **Worktree**: `worktrees/issue-<number>/`, one crewmate each.
5. **Change loop**: change the code and its block spec together, run L0, fix, repeat until code, spec and issue agree and L0 passes (`debug` when stuck).
6. **Commit, push, pull request**: the pull request says `Closes #<number>` (`land` covers how to write it).
7. **Review**: a reviewer crewmate with a fresh context reviews it (`review`); fix and return to step 5 until it passes.
8. **Approve**: the reviewer's pass, plus a captain's word when the pull request needs captain.
9. **Land**: the first mate merges once checks are green: squash for an issue branch, merge commit for an epic branch (`land`).
10. **Clean up**: remove the worktree and the branch.
