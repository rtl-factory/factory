"""A scratch checkout for unit tests: a copy of the checkout's files with an empty workdir/ of its own.

Code that takes a checkout root (and confines its writes below `<root>/workdir/`)
runs against `checkout(self)` instead of the real root, so a test reads the real
sources, registries and pages but writes only into its own temporary folder, and
passes whatever the real `workdir/` holds.

Spec: wiki/tools/bazel/spec.md (Unit tests).
"""
from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
import unittest

CHECKOUT = Path(__file__).resolve().parents[2]
# Why: output and other checkouts, never sources; a copy of them would be slow and
# would hand the test the very state it must not depend on.
SKIPPED = {".git", "site", "workdir", "worktrees"}


def checkout(test: unittest.TestCase) -> Path:
    """A fresh checkout root with an empty workdir/, removed when the test ends.

    Every top-level entry of the real checkout except its output folders is copied,
    not linked, so the code under test's in-tree checks (a path that resolves below
    the root) hold, and nothing it writes reaches the real checkout.
    """
    root = Path(tempfile.mkdtemp(prefix="checkout-")).resolve()
    test.addCleanup(shutil.rmtree, root)
    for entry in CHECKOUT.iterdir():
        if entry.name in SKIPPED or entry.name.startswith("bazel-"):
            continue
        if entry.is_dir() and not entry.is_symlink():
            shutil.copytree(entry, root / entry.name, symlinks=True,
                            ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(entry, root / entry.name, follow_symlinks=False)
    (root / "workdir").mkdir()
    return root
