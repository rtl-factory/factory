#!/usr/bin/env python3
"""Worktree clean-up: expunge a finished worktree's Bazel output base, remove the
worktree and delete its landed branch; find and remove orphaned output bases.

    python3 tools/worktree/worktree.py remove worktrees/issue-<n> [--base origin/main]
    python3 tools/worktree/worktree.py orphans [--remove]

Every worktree gets its own Bazel output base and server, which `git worktree
remove` leaves behind. `remove` runs `bazel clean --expunge` in the worktree
first (which also stops its server), then removes the worktree, then deletes
its branch only once the branch is merged or squash-merged into the base.
`orphans` lists the output bases whose workspace (named in DO_NOT_BUILD_HERE)
no longer exists and whose server is not running; `--remove` deletes them.
Standard library only.
Spec: wiki/tools/worktree/spec.md.
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
from typing import NamedTuple


class CleanupError(Exception):
    """A request the tool refuses, or a step that failed, before anything is lost."""


class OutputBase(NamedTuple):
    path: Path
    workspace: str
    server_pid: int | None


def default_output_user_root() -> Path:
    """Bazel's default output user root on Linux and macOS."""
    cache = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(cache) / "bazel" / f"_bazel_{getpass.getuser()}"


def output_base_for(workspace: Path, user_root: Path) -> Path:
    """Bazel names a workspace's output base by the MD5 of its absolute path."""
    digest = hashlib.md5(str(workspace).encode()).hexdigest()
    return user_root / digest


def git(*args: str, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise CleanupError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result


def worktrees(repo: Path) -> list[dict[str, str]]:
    """`git worktree list --porcelain` as one dict per worktree; the first is the main one."""
    entries: list[dict[str, str]] = []
    for line in git("worktree", "list", "--porcelain", cwd=repo).stdout.splitlines():
        if line.startswith("worktree "):
            entries.append({"worktree": line[len("worktree "):]})
        elif line and entries:
            key, _, value = line.partition(" ")
            entries[-1][key] = value
    return entries


def branch_landed(repo: Path, branch: str, base: str) -> bool:
    """True when the branch is merged into base, or its whole diff landed as one squash commit."""
    if git("merge-base", "--is-ancestor", branch, base, cwd=repo, check=False).returncode == 0:
        return True
    fork = git("merge-base", base, branch, cwd=repo, check=False).stdout.strip()
    if not fork:
        return False
    # Why: a squash merge leaves no ancestry, so fold the branch into one commit on
    # its fork point and ask git cherry whether base holds a patch-identical commit.
    identity = ("-c", "user.name=worktree", "-c", "user.email=worktree@localhost")
    squashed = git(*identity, "commit-tree", f"{branch}^{{tree}}", "-p", fork, "-m", "squash probe",
                   cwd=repo).stdout.strip()
    cherry = git("cherry", base, squashed, cwd=repo).stdout.strip()
    return cherry.startswith("-")


def remove(path: Path, repo: Path, base: str, bazel: str, user_root: Path | None, fetch: bool) -> int:
    path = path.resolve()
    entries = worktrees(repo)
    if entries and Path(entries[0]["worktree"]).resolve() == path:
        raise CleanupError(f"{path} is the main checkout, not a worktree")
    entry = next((e for e in entries if Path(e["worktree"]).resolve() == path), None)
    if entry is None:
        raise CleanupError(f"{path} is not a worktree of {repo} (git worktree list)")
    if not path.is_dir():
        raise CleanupError(f"{path} is missing; run `git worktree prune`, then `orphans --remove`")
    dirty = git("status", "--porcelain", cwd=path).stdout.strip()
    if dirty:
        raise CleanupError(f"{path} has uncommitted changes; commit and push them first:\n{dirty}")

    command = [bazel]
    if user_root is not None:
        command.append(f"--output_user_root={user_root}")
    command += ["clean", "--expunge"]
    print(f"expunging the output base: {' '.join(command)} (in {path})")
    try:
        expunged = subprocess.run(command, cwd=path)
    except FileNotFoundError:
        raise CleanupError(f"{bazel} not found; the worktree is kept so its output base can still be expunged")
    if expunged.returncode != 0:
        raise CleanupError(f"bazel clean --expunge failed ({expunged.returncode}); the worktree is kept")
    left = output_base_for(path, user_root or default_output_user_root())
    if left.exists():
        raise CleanupError(f"{left} is still there after the expunge; the worktree is kept")

    print(f"removing the worktree {path}")
    git("worktree", "remove", str(path), cwd=repo)

    branch = entry.get("branch", "").removeprefix("refs/heads/")
    if not branch:
        print("no branch to delete (detached HEAD)")
        return 0
    if fetch and base.startswith("origin/"):
        if git("fetch", "-q", "origin", base.removeprefix("origin/"), cwd=repo, check=False).returncode != 0:
            print(f"warning: could not fetch {base}; checking against the local copy")
    if not branch_landed(repo, branch, base):
        print(f"kept branch {branch}: it has not landed on {base}")
        return 1
    git("branch", "-D", branch, cwd=repo)
    print(f"deleted branch {branch} (landed on {base})")
    return 0


def server_pid(output_base: Path) -> int | None:
    """The pid of the output base's Bazel server, or None when it is not running."""
    try:
        pid = int((output_base / "server" / "server.pid.txt").read_text().strip())
    except (OSError, ValueError):
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    except PermissionError:
        return pid
    return pid


def output_bases(user_root: Path) -> list[OutputBase]:
    """Every output base under the user root; `install` and `cache` hold no DO_NOT_BUILD_HERE."""
    found = []
    if not user_root.is_dir():
        return found
    for path in sorted(user_root.iterdir()):
        marker = path / "DO_NOT_BUILD_HERE"
        if path.is_symlink() or not marker.is_file():
            continue
        found.append(OutputBase(path, marker.read_text().strip(), server_pid(path)))
    return found


def size_of(path: Path) -> int:
    total = 0
    for folder, _, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(folder, name)).st_size
            except OSError:
                pass
    return total


def force_rmtree(path: Path) -> None:
    """Delete a tree Bazel left read-only in places."""
    def writable(function, name, _):
        os.chmod(os.path.dirname(name), stat.S_IRWXU)
        if not os.path.islink(name) and os.path.exists(name):
            os.chmod(name, stat.S_IRWXU)
        function(name)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=writable)
    else:
        shutil.rmtree(path, onerror=writable)


def orphans(user_root: Path, delete: bool) -> int:
    bases = output_bases(user_root)
    freed = 0
    count = 0
    for base in bases:
        if Path(base.workspace).exists():
            continue
        size = size_of(base.path)
        if base.server_pid is not None:
            print(f"skip   {base.path} ({size / 2**30:.2f} GiB): workspace {base.workspace} is gone "
                  f"but its server (pid {base.server_pid}) is running")
            continue
        count += 1
        if delete:
            force_rmtree(base.path)
            freed += size
            print(f"remove {base.path} ({size / 2**30:.2f} GiB): workspace {base.workspace} is gone")
        else:
            print(f"orphan {base.path} ({size / 2**30:.2f} GiB): workspace {base.workspace} is gone")
    if delete:
        print(f"{count} orphaned output base(s) removed, {freed / 2**30:.2f} GiB freed")
    else:
        print(f"{count} orphaned output base(s) under {user_root}" + (" (remove with --remove)" if count else ""))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--output-user-root", type=Path,
                        help="Bazel's output user root (default: ~/.cache/bazel/_bazel_$USER)")
    commands = parser.add_subparsers(dest="command", required=True)
    rm = commands.add_parser("remove", help="expunge, remove the worktree, delete its landed branch")
    rm.add_argument("worktree", type=Path)
    rm.add_argument("--base", default="origin/main", help="the branch it landed on (default origin/main)")
    rm.add_argument("--bazel", default="bazel", help="the Bazel (Bazelisk) command")
    rm.add_argument("--no-fetch", action="store_true", help="do not fetch the base first")
    orphan = commands.add_parser("orphans", help="list output bases whose workspace is gone")
    orphan.add_argument("--remove", action="store_true", help="delete them")
    args = parser.parse_args(argv)
    try:
        if args.command == "remove":
            repo = Path(git("rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()).parent
            return remove(args.worktree, repo, args.base, args.bazel, args.output_user_root, not args.no_fetch)
        return orphans(args.output_user_root or default_output_user_root(), args.remove)
    except CleanupError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
