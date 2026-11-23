"""Run the wiki reference check as a Bazel test; the entry point of //tools:wiki_refs.

Spec: wiki/tools/bazel/spec.md (Unit tests).
"""
import sys

from tools.lint import wiki_refs

if __name__ == "__main__":
    sys.exit(wiki_refs.main(sys.argv[1:]))
