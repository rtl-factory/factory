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
    sys.exit(pytest.main(["-q", "-p", "no:cacheprovider", *sys.argv[1:]]))
