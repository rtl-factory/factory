"""A scratch checkout for unit tests: a copy of the checkout's files with an empty workdir/ of its own.

Code that takes a checkout root (and confines its writes below `<root>/workdir/`)
runs against `checkout(self)` instead of the real root, so a test reads the real
sources, registries and pages but writes only into its own temporary folder, and
passes whatever the real `workdir/` holds.

Spec: wiki/tools/bazel/spec.md (Unit tests).
"""
from __future__ import annotations

import atexit
from collections.abc import Callable
import hashlib
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

CHECKOUT = Path(__file__).resolve().parents[2]
# Why: output and other checkouts, never sources; a copy of them would hand the test
# the very state it must not depend on.
SKIPPED = {"site", "workdir", "worktrees"}


def files() -> list[str]:
    """The checkout's files git would commit: tracked ones, and untracked ones it does not ignore.

    Why: ignored files (private ROMs, local settings, build output) are never inputs a
    test may depend on, and a new file not yet added is still the change under test.
    """
    listed = subprocess.run(
        ["git", "-C", str(CHECKOUT), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        check=True, capture_output=True, text=True).stdout
    return sorted({name for name in listed.split("\0")
                   if name and name.split("/", 1)[0] not in SKIPPED})


def checkout(test: unittest.TestCase) -> Path:
    """A fresh checkout root with an empty workdir/, removed when the test ends.

    Every file `files()` lists is copied, not linked, so the code under test's in-tree
    checks (a path that resolves below the root) hold, and nothing it writes reaches
    the real checkout.
    """
    root = Path(tempfile.mkdtemp(prefix="checkout-")).resolve()
    test.addCleanup(shutil.rmtree, root)
    for name in files():
        source = CHECKOUT / name
        # Why: a tracked file deleted in the working tree is not part of the change.
        if not source.is_symlink() and not source.exists():
            continue
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target, follow_symlinks=False)
    (root / "workdir").mkdir()
    return root


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


_FETCHED: dict[str, Path] = {}


def _fetched(name: str, sha256: str, fetch: Callable[[Path], None]) -> Path:
    if sha256 not in _FETCHED:
        folder = Path(tempfile.mkdtemp(prefix="pinned-")).resolve()
        atexit.register(shutil.rmtree, folder, True)
        fetch(folder / name)
        if _sha256(folder / name) != sha256:
            raise RuntimeError(f"fetched {name} does not match its pinned SHA-256")
        _FETCHED[sha256] = folder / name
    return _FETCHED[sha256]


def pinned(cached: Path, sha256: str, fetch: Callable[[Path], None]) -> Path:
    """A pinned download a test reads, without writing the checkout's download cache.

    Returns `cached` (a file in the real `workdir/` cache) when it holds exactly the
    pinned bytes; otherwise calls `fetch(path)` once per test process to download it
    to a path below the test's temporary folder, and returns that path.
    """
    if cached.is_file() and not cached.is_symlink() and _sha256(cached) == sha256:
        return cached
    return _fetched(cached.name, sha256, fetch)
