# Worktree clean-up: requirements

## Problem

Every crewmate and reviewer works in its own worktree, and Bazel gives each worktree its own output base (2-3 GB for an issue worktree, 50-300 MB for a review one) and its own server. Removing a worktree with git leaves both behind. On 2026-11-24 a session running about ten crewmates in parallel reached 95% disk: 19 orphaned output bases and 6 idle servers held about 22 GB and 3 GB of memory (#208). A clean-up step written only as prose is skipped or done half; one command that does all of it is not.

## Requirements

1. Clean up a finished worktree in one command: expunge its output base (stopping its server), then remove the worktree, then delete its branch.
2. Never lose work: refuse the main checkout, a path that is not a worktree, a worktree with uncommitted or untracked changes, and a detached worktree whose commit no branch or tag holds, before anything is changed; keep the worktree if the expunge fails.
3. Delete the branch only when it has landed on its base, by a merge or a squash merge; otherwise keep it and say so.
4. List the output bases whose workspace no longer exists and whose server is not running, with their size, and remove them on request.
5. Never touch the shared disk cache, the Bazel install, an output base whose workspace still exists, or one whose server is running.
6. Run with the Python standard library and Bazel alone, on any host.

## Out of scope

- Deleting the remote branch.
- The shared disk cache, which is meant to persist.
- Stopping a running server of a gone workspace: it stops itself when idle, and the next scan removes its output base.

## Acceptance

| Requirement | Check |
|---|---|
| 1 | Unit test with a fake Bazel: the expunge runs in the worktree before it is removed, and the output base, worktree and branch are gone; a manual run with real Bazel in a temporary repository |
| 2 | Unit tests: a dirty worktree, a detached worktree with an unreferenced commit, the main checkout, an unknown path and a failed expunge each exit 2 with the worktree kept; a detached worktree on a branch's commit is removed |
| 3 | Unit tests: a squash-merged and a merge-commit branch are deleted; an unlanded one is kept and exits 1 |
| 4, 5 | Unit test: of live, gone, gone-with-stale-pid and gone-with-running-server bases plus `install` and `cache`, only the two idle gone bases are listed and removed, read-only folders included |
| 6 | The tests run under `//tools:worktree.unit` with no extra dependency |
