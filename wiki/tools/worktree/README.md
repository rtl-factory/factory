# Worktree clean-up

Cleans up a finished worktree in one command: expunges its Bazel output base (which also stops its Bazel server), removes the worktree and deletes its branch once the branch has landed. It also finds the output bases whose worktree is already gone and removes them. Code: `tools/worktree/`.

- [Requirements](prd.md)
- [Spec](spec.md)
