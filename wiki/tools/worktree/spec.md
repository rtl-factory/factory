# Worktree clean-up: spec

The tool is `tools/worktree/worktree.py`, Python standard library only, with its unit tests in `tools/worktree/tests/` (`//tools:worktree.unit`). It is a tool, not a script ([repository layout](../../bkm/layout.md#repository-layout)): it knows git and Bazel, no project. [Requirements](prd.md).

## Usage

```
python3 tools/worktree/worktree.py remove worktrees/issue-<n>            # expunge, remove the worktree, delete its landed branch
python3 tools/worktree/worktree.py remove <path> --base origin/epic/<x>  # an issue of an epic: it landed on the epic branch
python3 tools/worktree/worktree.py orphans                               # list orphaned output bases
python3 tools/worktree/worktree.py orphans --remove                      # and delete them
```

| Option | Meaning |
|---|---|
| `--output-user-root <dir>` | Bazel's output user root (before the command). `orphans` scans it, by default `$XDG_CACHE_HOME/bazel/_bazel_$USER`, else `~/.cache/bazel/_bazel_$USER`. `remove` passes it to Bazel as `--output_user_root` when given, and otherwise leaves the choice to Bazel |
| `--base <ref>` | `remove`: the branch the work landed on, default `origin/main`; an `origin/` base is fetched first unless `--no-fetch` |
| `--bazel <cmd>` | `remove`: the Bazel command, default `bazel` (Bazelisk) |
| `--remove` | `orphans`: delete what is listed |

Run `remove` from the main checkout or any worktree of the same repository; the repository is the one owning the current folder.

## Output bases

Bazel keeps each workspace's state in an output base `<output user root>/<MD5 of the workspace's absolute path>/`, which holds a `DO_NOT_BUILD_HERE` file naming the workspace and, while its server runs, `server/server.pid.txt`. The output user root also holds `install/` (Bazel itself) and `cache/` (the repository cache); neither has `DO_NOT_BUILD_HERE`, so neither is ever an output base. The shared disk cache (`~/.cache/bazel-disk`) is outside the output user root.

## `remove`

In order, stopping at the first refusal or failure:

1. Refuse (exit 2) the main checkout, a path `git worktree list` does not show, a worktree folder that is missing, a worktree with any `git status --porcelain` output, and a detached worktree whose HEAD no ref holds (`git for-each-ref --contains HEAD` is empty), since removing it would leave its commits reachable from nothing. Nothing is changed.
2. Ask Bazel for the worktree's output base (`bazel info output_base`), then run `bazel clean --expunge` in the worktree; it deletes the output base and stops its server. If Bazel is missing or fails, or that output base is still there afterwards, exit 2 and keep the worktree, so the expunge can be retried.
3. `git worktree remove <path>`, never `--force`.
4. With an `origin/` base, `git fetch origin <branch>` (a failure only warns). Delete the branch when it has landed: it is an ancestor of the base (a merge commit), or a commit holding its tree on its fork point with the base is patch-identical, by `git cherry`, to a commit on the base (a squash merge). The tool deletes it with `git branch -D` only after that check, so no unmerged commit is lost ([git you must not run](../../bkm/pull-requests.md#git-you-must-not-run)). The squash probe is an unreferenced commit object, which git's garbage collection prunes. A branch that has not landed is kept and the command exits 1. A detached worktree has no branch to delete; step 1 already made sure a ref holds its commit.

Exit 0 means the output base, the worktree and the branch are gone.

## `orphans`

Lists every output base whose `DO_NOT_BUILD_HERE` workspace no longer exists, with its size in GiB. One whose server is running (the pid in `server/server.pid.txt` is alive) is printed as `skip` and never deleted: Bazel stops an idle server after three hours, and the next scan takes it. With `--remove`, each listed output base is deleted, making read-only folders writable first, and the total freed is printed. Always exits 0.
