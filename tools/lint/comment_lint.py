#!/usr/bin/env python3
"""Comment lint: every comment in Python and SystemVerilog must be a header,
a license line, a tool directive or a tagged comment (Why:, Ref:,
TODO(#<issue>):). Existing violations are frozen per file in a baseline that
may only shrink.

    python3 tools/lint/comment_lint.py [--update-baseline] [--all] [paths ...]

Spec: wiki/tools/lint/spec.md. Method: wiki/bkm/code-comments.md.
"""
from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import tokenize
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / "tools" / "lint" / "comment_baseline.json"
SCANNED_DIRS = ("src/", "tools/")
PY_SUFFIXES = {".py"}
SV_SUFFIXES = {".sv", ".svh", ".v", ".vh"}
SKIPPED_PARTS = {"third_party"}

TAG = re.compile(r"^(Why|Ref):\s*\S|^TODO\(#\d+\):\s*\S")
LICENSE = re.compile(r"^(SPDX-License-Identifier:|Copyright\b)", re.IGNORECASE)
PY_DIRECTIVE = re.compile(r"^(!|-\*-|noqa\b|type:|pragma\b|pylint:|fmt:|isort:|mypy:|coding[:=])", re.IGNORECASE)
SV_DIRECTIVE = re.compile(
    r"^(verilator\b|synthesis\b|synopsys\b|pragma\b|altera\b|lint_off\b|lint_on\b|"
    r"translate_off\b|translate_on\b|spyglass\b|cadence\b|coverage\b|verilog_lint:)",
    re.IGNORECASE)
SV_DECL = re.compile(r"^\s*(?:virtual\s+|static\s+|automatic\s+|pure\s+|extern\s+)*"
                     r"(module|macromodule|interface|package|class|program|function|task|checker|"
                     r"typedef|localparam|parameter|`define)\b")
SV_PORT_HOST = re.compile(r"^\s*(module|macromodule|interface)\b")
SV_IMPORT = re.compile(r"^\s*import\b")
SV_TYPE_BODY = re.compile(r"^\s*typedef\s+(enum|struct|union)\b")

FIX = ("delete it, encode it in a name, type, assertion or test, or tag it "
       "'Why:', 'Ref:' or 'TODO(#<issue>):' (wiki/bkm/code-comments.md)")


class Comment(NamedTuple):
    line: int
    text: str
    own_line: bool


class Violation(NamedTuple):
    path: str
    line: int
    text: str


def body(text: str) -> str:
    """The comment's text without markers, from its first non-empty line."""
    stripped = text.strip().lstrip("/#")
    if stripped.endswith("*/"):
        stripped = stripped[:-2]
    for line in stripped.splitlines():
        line = line.strip().lstrip("*").strip()
        if line:
            return line
    return ""


def allowed(text: str, directive: re.Pattern[str]) -> bool:
    b = body(text)
    return not b or bool(TAG.match(b) or LICENSE.match(b) or directive.match(b))


def python_comments(source: str) -> tuple[list[Comment], set[int]]:
    """Comments found by the tokenizer, and the lines that hold code."""
    comments: list[Comment] = []
    code_lines: set[int] = set()
    skip = {tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT,
            tokenize.DEDENT, tokenize.ENDMARKER, tokenize.ENCODING}
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.COMMENT:
            own = not tok.line[:tok.start[1]].strip()
            comments.append(Comment(tok.start[0], tok.string, own))
        elif tok.type not in skip:
            code_lines.update(range(tok.start[0], tok.end[0] + 1))
    return comments, code_lines


def sv_comments(source: str) -> tuple[list[Comment], set[int]]:
    """Comments outside string literals, and the lines that hold code.
    A block comment is reported once, at its first line, with all its text."""
    comments: list[Comment] = []
    code_lines: set[int] = set()
    i, line, n = 0, 1, len(source)
    line_has_code = False
    while i < n:
        c = source[i]
        if c == "\n":
            line += 1
            line_has_code = False
            i += 1
        elif source.startswith("//", i):
            end = source.find("\n", i)
            end = n if end < 0 else end
            comments.append(Comment(line, source[i:end], not line_has_code))
            i = end
        elif source.startswith("/*", i):
            end = source.find("*/", i + 2)
            end = n if end < 0 else end + 2
            text = source[i:end]
            comments.append(Comment(line, text, not line_has_code))
            line += text.count("\n")
            i = end
        elif c == '"':
            j = i + 1
            while j < n and source[j] not in '"\n':
                if source[j] == "\\" and j + 1 < n and source[j + 1] == "\n":
                    line += 1
                j += 2 if source[j] == "\\" else 1
            code_lines.add(line)
            line_has_code = True
            i = j + 1
        else:
            if not c.isspace():
                code_lines.add(line)
                line_has_code = True
            i += 1
    return comments, code_lines


def sv_allowed_lines(lines: list[str], code_lines: set[int]) -> set[int]:
    """Lines whose comments are API documentation: comment blocks directly
    above a declaration, and comments inside a module or interface header or
    a typedef enum, struct or union body."""
    ok: set[int] = set()
    for number, text in enumerate(lines, start=1):
        if SV_DECL.match(text):
            above = number - 1
            while above >= 1 and above not in code_lines and lines[above - 1].strip():
                ok.add(above)
                above -= 1
        if SV_PORT_HOST.match(text):
            depth, end = 0, number
            while end <= len(lines):
                ok.add(end)
                code = lines[end - 1].split("//")[0]
                if SV_IMPORT.match(code):
                    code = ""
                ended = False
                for ch in code:
                    depth += (ch == "(") - (ch == ")")
                    if ch == ";" and depth <= 0:
                        ended = True
                        break
                if ended:
                    break
                end += 1
        if SV_TYPE_BODY.match(text):
            depth, end = 0, number
            while end <= len(lines):
                ok.add(end)
                code = lines[end - 1].split("//")[0]
                depth += code.count("{") - code.count("}")
                if depth <= 0 and "}" in code:
                    break
                end += 1
    return ok


def violations(path: Path, rel: str) -> list[Violation]:
    source = path.read_text(encoding="utf-8", errors="replace")
    is_py = path.suffix in PY_SUFFIXES
    if is_py:
        try:
            comments, code_lines = python_comments(source)
        except (tokenize.TokenError, SyntaxError, IndentationError):
            return [Violation(rel, 1, "file does not tokenize")]
        doc_lines: set[int] = set()
        directive = PY_DIRECTIVE
    else:
        comments, code_lines = sv_comments(source)
        doc_lines = sv_allowed_lines(source.splitlines(), code_lines)
        directive = SV_DIRECTIVE
    first_code = min(code_lines, default=10**9)
    found: list[Violation] = []
    covered_until = 0
    for comment in comments:
        if comment.line < first_code or comment.line in doc_lines:
            continue
        if comment.own_line and comment.line == covered_until + 1:
            covered_until = comment.line + comment.text.count("\n")
            continue
        if allowed(comment.text, directive):
            if comment.own_line:
                covered_until = comment.line + comment.text.count("\n")
            continue
        found.append(Violation(rel, comment.line, comment.text.strip().splitlines()[0][:100]))
        covered_until = comment.line + comment.text.count("\n") if comment.own_line else 0
    return found


def scanned(path: str) -> bool:
    p = Path(path)
    return (path.startswith(SCANNED_DIRS) and p.suffix in PY_SUFFIXES | SV_SUFFIXES
            and not SKIPPED_PARTS.intersection(p.parts))


def tracked_files(root: Path) -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z", *SCANNED_DIRS], cwd=root,
                         capture_output=True, text=True, check=True).stdout
    return sorted(p for p in out.split("\0") if p and scanned(p))


def count(found: list[Violation]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for v in found:
        counts[v.path] = counts.get(v.path, 0) + 1
    return dict(sorted(counts.items()))


def check(found: list[Violation], baseline: dict[str, int], files: list[str]) -> list[str]:
    """Error lines: files over their baseline, and baseline entries that are
    now too high (stale) for a file in this run."""
    errors: list[str] = []
    counts = count(found)
    for path in files:
        have, allowed_count = counts.get(path, 0), baseline.get(path, 0)
        if have > allowed_count:
            errors.append(f"{path}: {have} comment violations, baseline allows {allowed_count}; {FIX}")
            errors.extend(f"  {v.path}:{v.line}: {v.text}" for v in found if v.path == path)
        elif have < allowed_count:
            errors.append(f"{path}: {have} comment violations, below its baseline of {allowed_count}; "
                          "run python3 tools/lint/comment_lint.py --update-baseline and commit it")
    return errors


def raised(baseline: dict[str, int], base: dict[str, int]) -> list[str]:
    """Error lines for a baseline that grew against the base branch's: any
    entry above its base value, a higher total, or new entries holding more
    than the entries that disappeared (a rename moves counts; a new file
    brings none)."""
    errors = [f"{path}: baseline raised from {base[path]} to {n}; clean the new comments instead"
              for path, n in sorted(baseline.items()) if path in base and n > base[path]]
    if sum(baseline.values()) > sum(base.values()):
        errors.append(f"baseline total raised from {sum(base.values())} to {sum(baseline.values())}; "
                      "new files must have no comment violations")
    added = sum(n for path, n in baseline.items() if path not in base)
    moved = sum(n for path, n in base.items() if path not in baseline)
    if added > moved:
        errors.append(f"new baseline entries hold {added} violations but only {moved} moved from removed "
                      "files; new files must have no comment violations")
    return errors


def relative(arg: str) -> str:
    path = Path(arg)
    path = path if path.is_absolute() else Path.cwd() / path
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return arg


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="*", help="files to check (default: every tracked file in scope)")
    parser.add_argument("--update-baseline", action="store_true", help="rewrite the baseline to the current counts")
    parser.add_argument("--all", action="store_true", help="list every violation, ignoring the baseline")
    parser.add_argument("--base-baseline", type=Path,
                        help="the base branch's baseline file, relative to the checkout or absolute; "
                             "fail if this baseline is higher (skipped when it does not exist)")
    args = parser.parse_args(argv)

    if args.paths:
        rel = [relative(p) for p in args.paths]
        missing = [p for p in rel if not (ROOT / p).is_file()]
        if missing:
            parser.error("no such file: " + ", ".join(missing))
        files = [p for p in rel if scanned(p)]
    else:
        files = tracked_files(ROOT)
    found = [v for f in files for v in violations(ROOT / f, f)]

    if args.update_baseline:
        if args.paths:
            parser.error("--update-baseline scans the whole tree; pass no paths")
        BASELINE.write_text(json.dumps(count(found), indent=1) + "\n")
        print(f"baseline: {len(found)} violations in {len(count(found))} files")
        return 0
    if args.all:
        for v in found:
            print(f"{v.path}:{v.line}: {v.text}")
        print(f"{len(found)} violations")
        return 1 if found else 0

    baseline = json.loads(BASELINE.read_text()) if BASELINE.exists() else {}
    if not args.paths:
        gone = sorted(set(baseline) - set(files))
        files = files + gone
    errors = check(found, baseline, files)
    base = args.base_baseline and ROOT / args.base_baseline
    if base and base.exists():
        errors += raised(baseline, json.loads(base.read_text()))
    for e in errors:
        print(e)
    if errors:
        return 1
    print(f"comment lint: PASS ({len(files)} files, {sum(baseline.values())} baselined violations)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
