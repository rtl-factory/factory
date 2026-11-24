"""The scratch checkout copies the checkout's tracked files only."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools.bazel import scratch


class ScratchCheckoutTests(unittest.TestCase):
    def test_copies_tracked_files_only(self):
        source = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        subprocess.run(["git", "init", "-q", str(source)], check=True)
        (source / ".gitignore").write_text("private/\n")
        (source / "src").mkdir()
        (source / "src/tracked.txt").write_text("tracked")
        (source / "src/untracked.txt").write_text("untracked")
        (source / "private").mkdir()
        (source / "private/rom.gb").write_text("ignored")
        (source / "workdir").mkdir()
        (source / "workdir/stale").write_text("stale")
        subprocess.run(["git", "-C", str(source), "add", ".gitignore", "src/tracked.txt"], check=True)
        with patch.object(scratch, "CHECKOUT", source):
            root = scratch.checkout(self)
        copied = sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())
        self.assertEqual([".gitignore", "src/tracked.txt"], copied)
        self.assertEqual([], list((root / "workdir").iterdir()))

    def test_without_git_checkout_fails_with_the_reason(self):
        source = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        with patch.object(scratch, "CHECKOUT", source), \
                self.assertRaisesRegex(RuntimeError, "needs git and the git checkout"):
            scratch.checkout(self)


if __name__ == "__main__":
    unittest.main()
