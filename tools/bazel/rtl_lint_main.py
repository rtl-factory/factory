"""Run the RTL rule lint as a Bazel test; the entry point of //tools:rtl_lint.

Spec: wiki/tools/bazel/spec.md (Unit tests).
"""
import sys

from tools.lint import rtl_lint

if __name__ == "__main__":
    sys.exit(rtl_lint.main(sys.argv[1:]))
