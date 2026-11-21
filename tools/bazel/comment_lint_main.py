"""Run the comment lint as a Bazel test; the entry point of //tools:comment_lint.

Spec: wiki/tools/bazel/spec.md (Unit tests).
"""
import sys

from tools.lint import comment_lint

if __name__ == "__main__":
    sys.exit(comment_lint.main(sys.argv[1:]))
