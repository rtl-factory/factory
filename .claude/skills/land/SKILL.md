---
name: land
description: Write a pull request, watch its CI and review findings, fix what is real, and land it. Use when opening a pull request, when a pull request is waiting on checks or findings, or when taking over a stalled one.
---

Write the description with the pull request template in the Templates section of `wiki/bkm/issues.md`. Follow the Writing and landing section of `wiki/bkm/pull-requests.md` to write the pull request and get it landable, and the Merging section of `wiki/bkm/flow.md` for whether you may merge it. After the merge, clean up with `python3 tools/worktree/worktree.py remove worktrees/issue-<number>` (flow step 10), never `git worktree remove` alone.
