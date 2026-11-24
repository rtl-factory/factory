---
name: review
description: Review a pull request as the reviewer crewmate. Use when dispatched to review, or when asked to review a branch or pull request.
---

Follow the Review section of `wiki/bkm/pull-requests.md`: review against the issue, the spec and the BKMs, look past the diff for what the change could break, keep only findings you can prove, and post them as one pull request review. Never review your own work. When the review is posted, remove your review worktree with `python3 tools/worktree/worktree.py remove <worktree>` so its Bazel output base goes with it.
