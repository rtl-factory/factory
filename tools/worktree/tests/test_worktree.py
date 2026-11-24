"""Worktree clean-up: expunge before removal, landed-branch detection, refusals
that keep the worktree, and the orphaned output base scan."""
from pathlib import Path
import os
import stat
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import worktree  # noqa: E402

FAKE_BAZEL = """#!/usr/bin/env python3
import hashlib, os, shutil, sys
from pathlib import Path
log = Path(os.environ["FAKE_BAZEL_LOG"])
log.write_text(log.read_text() + " ".join(sys.argv[1:]) + "\\n" if log.exists() else " ".join(sys.argv[1:]) + "\\n")
if os.environ.get("FAKE_BAZEL_FAIL"):
    sys.exit(37)
root = Path(sys.argv[1].split("=", 1)[1])
shutil.rmtree(root / hashlib.md5(os.getcwd().encode()).hexdigest(), ignore_errors=True)
"""


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def setup(tmp_path, monkeypatch):
    for key, value in {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                       "GIT_COMMITTER_EMAIL": "t@t", "FAKE_BAZEL_LOG": str(tmp_path / "bazel.log")}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("FAKE_BAZEL_FAIL", raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "a.txt").write_text("a\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "base")
    wt = repo / "worktrees" / "issue-1"
    git(repo, "worktree", "add", "-q", "-b", "issue/1-x", str(wt))
    (wt / "b.txt").write_text("b\n")
    git(wt, "add", ".")
    git(wt, "commit", "-q", "-m", "change")
    bazel = tmp_path / "bazel"
    bazel.write_text(FAKE_BAZEL)
    bazel.chmod(0o755)
    root = tmp_path / "user_root"
    base = worktree.output_base_for(wt.resolve(), root)
    base.mkdir(parents=True)
    (base / "DO_NOT_BUILD_HERE").write_text(str(wt.resolve()))
    monkeypatch.chdir(repo)
    return repo, wt, bazel, root, base, tmp_path / "bazel.log"


def run_remove(setup):
    repo, wt, bazel, root, _, _ = setup
    return worktree.main(["--output-user-root", str(root), "remove", str(wt), "--base", "main",
                          "--bazel", str(bazel), "--no-fetch"])


def branches(repo):
    return git(repo, "branch", "--format=%(refname:short)").split()


def test_squash_merged_worktree_is_expunged_removed_and_its_branch_deleted(setup):
    repo, wt, _, root, base, log = setup
    git(repo, "merge", "-q", "--squash", "issue/1-x")
    git(repo, "commit", "-q", "-m", "squash (#1)")
    (repo / "c.txt").write_text("later\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "later work on main")
    assert run_remove(setup) == 0
    assert log.read_text() == f"--output_user_root={root} clean --expunge\n"
    assert not base.exists() and not wt.exists()
    assert branches(repo) == ["main"]


def test_merge_commit_branch_is_deleted(setup):
    repo, *_ = setup
    git(repo, "merge", "-q", "--no-ff", "-m", "merge", "issue/1-x")
    assert run_remove(setup) == 0
    assert branches(repo) == ["main"]


def test_unlanded_branch_is_kept_after_the_worktree_goes(setup, capsys):
    repo, wt, *_ = setup
    assert run_remove(setup) == 1
    assert not wt.exists()
    assert "issue/1-x" in branches(repo)
    assert "has not landed" in capsys.readouterr().out


def test_dirty_worktree_is_refused_before_the_expunge(setup):
    repo, wt, _, _, base, log = setup
    (wt / "b.txt").write_text("edited\n")
    assert run_remove(setup) == 2
    assert wt.exists() and base.exists() and not log.exists()


def test_failed_expunge_keeps_the_worktree(setup, monkeypatch):
    repo, wt, _, _, base, _ = setup
    monkeypatch.setenv("FAKE_BAZEL_FAIL", "1")
    assert run_remove(setup) == 2
    assert wt.exists() and base.exists()


def test_main_checkout_and_unknown_paths_are_refused(setup, tmp_path):
    repo, _, bazel, root, _, log = setup
    for path in (repo, tmp_path):
        assert worktree.main(["--output-user-root", str(root), "remove", str(path), "--bazel", str(bazel),
                              "--no-fetch"]) == 2
    assert not log.exists()


def make_base(root, name, workspace, pid=None):
    base = root / name
    (base / "server").mkdir(parents=True)
    (base / "DO_NOT_BUILD_HERE").write_text(str(workspace))
    if pid is not None:
        (base / "server" / "server.pid.txt").write_text(str(pid))
    locked = base / "external" / "repo"
    locked.mkdir(parents=True)
    (locked / "file").write_text("x" * 100)
    locked.chmod(stat.S_IRUSR | stat.S_IXUSR)
    return base


def dead_pid():
    process = subprocess.Popen(["true"])
    process.wait()
    return process.pid


def test_orphans_lists_then_removes_only_idle_bases_of_gone_workspaces(tmp_path, capsys):
    root = tmp_path / "user_root"
    live = make_base(root, "live", tmp_path)
    gone = make_base(root, "gone", tmp_path / "missing")
    stale = make_base(root, "stale", tmp_path / "missing2", pid=dead_pid())
    serving = make_base(root, "serving", tmp_path / "missing3", pid=os.getpid())
    (root / "install").mkdir()
    (root / "cache").mkdir()

    assert worktree.main(["--output-user-root", str(root), "orphans"]) == 0
    out = capsys.readouterr().out
    assert "2 orphaned output base(s)" in out
    assert gone.exists() and stale.exists()

    assert worktree.main(["--output-user-root", str(root), "orphans", "--remove"]) == 0
    out = capsys.readouterr().out
    assert not gone.exists() and not stale.exists()
    assert live.exists() and serving.exists() and (root / "install").exists() and (root / "cache").exists()
    assert f"server (pid {os.getpid()}) is running" in out
    assert "2 orphaned output base(s) removed" in out


def test_output_base_name_is_the_md5_of_the_workspace_path(tmp_path):
    # Ref: Bazel's output directory layout, outputBase = <outputUserRoot>/<MD5(workspace path)>.
    assert worktree.output_base_for(Path("/home/user/factory"), tmp_path).name == "01f11d999973e0bf89c316ed52497148"
