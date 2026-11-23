#!/usr/bin/env python3
"""Wiki reference check: every `wiki/...` path cited in a tracked file outside
`wiki/` must exist in the checkout.

    python3 tools/lint/wiki_refs.py [paths ...]

Spec: wiki/tools/lint/spec.md (Wiki reference check).
"""
from __future__ import annotations

import argparse
import fnmatch
from pathlib import Path
import re
import subprocess
import sys
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[2]
SKIPPED_PARTS = {"third_party"}

# Why: a match must start the path; a URL (".../blob/<sha>/wiki/...") or a
# longer path ("docs/wiki/...") names another tree and is not a citation.
REFERENCE = re.compile(r"(?<![\w./-])(?:\.{1,2}/)*wiki/[\w./<>*{}+-]*")
PATTERN = re.compile(r"<[^>]*>|\{[^}]*\}|\*+")
TRAILING = ".,:;"

FIX = "port the document, or cite the document that holds the content now"


class Missing(NamedTuple):
    path: str
    line: int
    reference: str


def references(text: str) -> list[tuple[int, str]]:
    """Every cited wiki path with its line, trailing punctuation removed."""
    found = []
    for number, line in enumerate(text.splitlines(), start=1):
        for match in REFERENCE.finditer(line):
            found.append((number, match.group(0).rstrip(TRAILING)))
    return found


def target(reference: str) -> str:
    """The path from the repository root: leading ./ and ../ dropped."""
    return re.sub(r"^(?:\.{1,2}/)+", "", reference).rstrip("/")


def exists(path: str, root: Path, tracked: list[str]) -> bool:
    """A plain path exists on disk; a placeholder (<board>, {name}, *) must
    match at least one tracked file or the folders above one."""
    if not PATTERN.search(path):
        return (root / path).exists()
    glob = PATTERN.sub("*", path)
    return any(fnmatch.fnmatchcase(f, glob) or fnmatch.fnmatchcase(f, glob + "/*") for f in tracked)


def scanned(path: str) -> bool:
    parts = path.split("/")
    return parts[0] != "wiki" and not SKIPPED_PARTS.intersection(parts)


def missing_in(path: Path, rel: str, root: Path, tracked: list[str]) -> list[Missing]:
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, IsADirectoryError):
        return []
    return [Missing(rel, line, ref) for line, ref in references(text)
            if not exists(target(ref), root, tracked)]


def tracked_files(root: Path) -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=root,
                         capture_output=True, text=True, check=True).stdout
    return [p for p in out.split("\0") if p]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="*", help="files to check (default: every tracked file outside wiki/)")
    args = parser.parse_args(argv)
    tracked = tracked_files(ROOT)
    files = [Path(p).resolve().relative_to(ROOT).as_posix() for p in args.paths] or tracked
    files = [f for f in files if scanned(f) and (ROOT / f).is_file()]
    found = [m for f in files for m in missing_in(ROOT / f, f, ROOT, tracked)]
    for m in found:
        print(f"{m.path}:{m.line}: {m.reference} does not exist")
    if found:
        print(f"\n{len(found)} cited wiki path(s) missing in {len({m.path for m in found})} file(s): {FIX}.")
        return 1
    print(f"wiki references: {len(files)} files checked, every cited wiki path exists")
    return 0


if __name__ == "__main__":
    sys.exit(main())
