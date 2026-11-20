---
name: flow
description: The path every change takes, from aligning with the first mate to merging into main. Use when starting, continuing or finishing work on an issue or epic, or when unsure what the next step is.
---

Every change follows these steps. The rules for each are in `wiki/bkm/workflow/flow.md`; read it before acting.

1. **Align**: the captain and the first mate agree on what to build. Grill if it is unclear.
2. **Issue**: the work is written as a labelled GitHub issue and assigned.
3. **Branch**: `issue/<number>-<slug>` from `main`, or from the epic branch.
4. **Worktree**: `worktrees/issue-<number>/`, one per crewmate.
5. **Code and spec loop**: change the code and the block spec together, run L0, repeat until they agree and L0 passes.
6. **Pull request**: commit, push, open a pull request that closes the issue.
7. **Review**: a reviewer crewmate reviews; fix and loop back to step 5 until it passes.
8. **Approval**: the reviewer's pass (full send, the default); plus the captain's word if the issue has `authority:needs-captain`.
9. **Merge**: squash an issue branch; merge-commit an epic branch into `main`.
