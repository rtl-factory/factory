"""Run pytest on the folders given as arguments; the entry point of every unit-test target.

Spec: wiki/tools/bazel/spec.md (Unit tests).
"""
import os
import sys

import pytest

if __name__ == "__main__":
    # Why: rules_python sets this for its own interpreter; tests that run a script by
    # path need that script's folder on sys.path, as under a plain pytest run.
    os.environ.pop("PYTHONSAFEPATH", None)
    # Why: the strict action PATH has no Verilator; tests that lint with it find Bazel's.
    tools = [os.path.dirname(os.path.abspath(p)) for p in os.environ.get("UNIT_PATH_PREPEND", "").split()]
    os.environ["PATH"] = os.pathsep.join(dict.fromkeys(tools + [os.environ.get("PATH", "")]))
    # Why: an unsandboxed test inherits the host's TMPDIR; pointing it at the test's own
    # folder keeps every tempfile (and scratch.checkout) inside what Bazel cleans up.
    if os.environ.get("TEST_TMPDIR"):
        os.environ["TMPDIR"] = os.environ["TEST_TMPDIR"]
    sys.exit(pytest.main(["-q", "-p", "no:cacheprovider", *sys.argv[1:]]))
