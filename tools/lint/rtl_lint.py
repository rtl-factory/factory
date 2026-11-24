#!/usr/bin/env python3
"""RTL lint: the RTL coding rules a script can check (wiki/bkm/rtl.md, RTL
coding) on every design file, and Verilator --lint-only -Wall on every lint
top. Existing violations are frozen in a baseline that may only shrink: per
file for the rules, per top for Verilator.

    python3 tools/lint/rtl_lint.py [--all] [paths ...]      # the rules
    python3 tools/lint/rtl_lint.py verilator <top> <files>  # one top, as //src/<project>:<top>.lint runs it
    python3 tools/lint/rtl_lint.py --update-baseline        # rewrite the baseline (Verilator on PATH)

Spec: wiki/tools/lint/spec.md. Rules: wiki/bkm/rtl.md.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import NamedTuple

try:
    from tools.lint import baseline as baseline_counts
except ImportError:  # Why: run as a script or a Bazel test main, tools/ is not a package on the path.
    import baseline as baseline_counts  # type: ignore[no-redef]

ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / "tools" / "lint" / "rtl_baseline.json"
UPDATE = "python3 tools/lint/rtl_lint.py --update-baseline"
SV_SUFFIXES = {".sv", ".svh", ".v", ".vh"}
DESIGN_AREAS = {"rtl", "fpga"}
SKIPPED_PARTS = {"third_party"}
# Why: the shared macros are where flops, memory init, elaboration checks and their tasks live.
MACRO_FILES = {"src/common/rtl/pkg/macros.vh", "src/common/rtl/pkg/assertion_macros.vh"}
VERILATOR_ARGS = ["--lint-only", "-Wall", "--assert", "-Wno-fatal"]
RULES_DOC = "wiki/bkm/rtl.md"
FIX = f"fix it, or for a Verilator warning waive it inline on the narrowest span ({RULES_DOC}, RTL coding)"


class Violation(NamedTuple):
    path: str
    line: int
    rule: str
    text: str


class KeywordRule(NamedTuple):
    rule: str
    pattern: str
    message: str
    exempt: set[str]


KEYWORD_RULES = [KeywordRule(*rule) for rule in [
    ("always-ff", r"\balways_ff\b", "write flops through the macros in macros.vh, never always_ff", MACRO_FILES),
    ("always-at", r"\balways\s*@", "use always_comb or assign, never always @", set()),
    ("reg", r"\breg\b", "declare logic, never reg", set()),
    ("always-latch", r"\balways_latch\b", "never always_latch", set()),
    ("initial", r"\binitial\b", "no initial in RTL outside the MEM_INIT_* and ASSERT_* macros", MACRO_FILES),
    ("task", r"\btask\b", "write a function automatic, never a task", MACRO_FILES),
    ("casex", r"\bcasex\b", "use case, or casez for a decode table, never casex", set()),
    ("delay", r"(?<![#\w])#(?!#)(?!\s*\()", "no # delays in RTL", set()),
    ("struct-packed", r"\bstruct\b(?!\s+packed\b)", "every struct is a struct packed", set()),
    ("no-union-interface", r"\b(union|interface|modport)\b", "no unions, interfaces or modports in RTL", set()),
    ("enum-width", r"\benum\b(?!\s+logic\s*\[)", "declare enums as typedef enum logic [N-1:0] with an explicit width",
     set()),
]]
TYPEDEF_NAME = re.compile(r"^t_\w+$")
PORT_NAME = re.compile(r"\w+_(i|o|io)$")
INCLUDE = re.compile(r'`include\s+"([^"]*)"')
WAIVER = re.compile(r"\bverilator\s+lint_(off|on)\b\s*(\w*)")
ARST = re.compile(r"`DFF_ARST_\w+")
COMMENT_LINE = re.compile(r"^\s*(//|/\*|\*)")


def split(source: str) -> tuple[str, str, list[tuple[int, str]]]:
    """The source with comments blanked (strings kept), with comments and strings
    blanked, and its comments with their lines; newlines are kept so offsets map to lines."""
    keep, code, comments = [], [], []
    i, line, n = 0, 1, len(source)
    while i < n:
        if source.startswith("//", i) or source.startswith("/*", i):
            block = source.startswith("/*", i)
            end = source.find("*/", i + 2) if block else source.find("\n", i)
            end = n if end < 0 else end + (2 if block else 0)
            text = source[i:end]
            comments.append((line, text))
            blank = re.sub(r"[^\n]", " ", text)
            keep.append(blank)
            code.append(blank)
            line += text.count("\n")
            i = end
        elif source[i] == '"':
            j = i + 1
            while j < n and source[j] not in '"\n':
                j += 2 if source[j] == "\\" else 1
            text = source[i:j + 1]
            keep.append(text)
            code.append('"' + re.sub(r"[^\n]", " ", text[1:-1]) + '"' if len(text) > 1 else text)
            line += text.count("\n")
            i = j + 1
        else:
            keep.append(source[i])
            code.append(source[i])
            line += source[i] == "\n"
            i += 1
    return "".join(keep), "".join(code), comments


def line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def nesting(c: str) -> int:
    """How a character changes the bracket depth."""
    return (c in "([{") - (c in ")]}")


def top_level(text: str, char: str) -> int:
    """The offset of the first char outside brackets, or the text's length."""
    depth = 0
    for i, c in enumerate(text):
        depth += nesting(c)
        if c == char and depth <= 0:
            return i
    return len(text)


def statement_end(code: str, start: int) -> int:
    """The offset of the `;` that ends the statement at start."""
    return start + top_level(code[start:], ";")


def balanced(code: str, start: int) -> int:
    """The offset just past the bracket that closes the one at start."""
    depth = 0
    for i in range(start, len(code)):
        depth += (code[i] == code[start]) - (code[i] == {"(": ")", "[": "]", "{": "}"}[code[start]])
        if depth == 0:
            return i + 1
    return len(code)


def declared_name(text: str) -> str:
    """The name a declaration ends with, past unpacked dimensions and a default value."""
    text = re.sub(r"\(\*.*?\*\)", " ", text, flags=re.S)
    text = text[:top_level(text, "=")].strip()
    while text.endswith("]"):
        depth, i = 0, len(text) - 1
        while i >= 0:
            depth += (text[i] == "]") - (text[i] == "[")
            if depth == 0:
                break
            i -= 1
        text = text[:i].rstrip()
    match = re.search(r"(\w+)$", text)
    return match.group(1) if match else ""


def board_top(rel: str) -> bool:
    """A board top, src/<project>/fpga/<board>/<prefix>_<board>.sv, whose ports keep the board's pin names."""
    parts = Path(rel).parts
    return len(parts) == 5 and parts[2] == "fpga" and Path(rel).stem.endswith("_" + parts[3])


def header_violations(code: str, pins: bool = False) -> list[tuple[int, str, str]]:
    """Port names without a direction suffix (unless the ports are board pins), and
    imports in a module header or at file scope."""
    found = []
    for match in re.finditer(r"\b(?:module|macromodule)\s+(?:automatic\s+|static\s+)?(\w+)", code):
        i = match.end()
        while imp := re.match(r"\s*import\b[^;]*;", code[i:]):
            found.append((line_of(code, i + len(imp.group()) - len(imp.group().lstrip())), "header-import",
                          "import a package inside the module body, never in its header"))
            i += imp.end()
        end = statement_end(code, i)
        while i < end:
            if code[i] == "#":
                i = code.find("(", i)
                i = balanced(code, i) if 0 <= i < end else end
            elif code[i] == "(":
                ports = code[i + 1:balanced(code, i) - 1]
                offset = i + 1
                depth, start = 0, 0
                for j, c in enumerate(ports + ","):
                    depth += nesting(c)
                    if c == "," and depth == 0:
                        item = re.sub(r"^\s*`.*$", "", ports[start:j], flags=re.M)
                        name = declared_name(item)
                        if name and not pins and not PORT_NAME.match(name):
                            line = line_of(code, offset + start + len(ports[start:j]) - len(ports[start:j].lstrip()))
                            found.append((line, "port-suffix", f"port {name} needs a direction suffix _i, _o or _io"))
                        start = j + 1
                break
            else:
                i += 1
    depth = 0
    for match in re.finditer(r"\b(module|macromodule|package|interface|program|class|"
                             r"endmodule|endpackage|endinterface|endprogram|endclass|import)\b", code):
        word = match.group(1)
        if word == "import":
            if depth == 0:
                found.append((line_of(code, match.start()), "header-import",
                              "import a package inside the module body, never at file scope"))
        elif word.startswith("end"):
            depth = max(0, depth - 1)
        else:
            depth += 1
    return found


def typedef_violations(code: str) -> list[tuple[int, str, str]]:
    found = []
    for match in re.finditer(r"\btypedef\b", code):
        name = declared_name(code[match.end():statement_end(code, match.end())])
        if name and not TYPEDEF_NAME.match(name):
            found.append((line_of(code, match.start()), "typedef-name", f"typedef {name} must be named t_<name>"))
    return found


def waiver_violations(comments: list[tuple[int, str]]) -> list[tuple[int, str, str]]:
    """lint_off waivers that no later lint_on of the same rule (or of every rule) closes."""
    found, open_ = [], {}
    for line, text in comments:
        for match in WAIVER.finditer(text):
            kind, rule = match.groups()
            if kind == "off":
                open_.setdefault(rule, line)
            elif rule:
                open_.pop(rule, None)
            else:
                open_.clear()
    for rule, line in sorted(open_.items(), key=lambda item: item[1]):
        found.append((line, "waiver-scope", f"lint_off {rule or '(every rule)'} has no lint_on: "
                                            "waive on the narrowest span, never file-wide"))
    return found


def arst_violations(source: str, code: str) -> list[tuple[int, str, str]]:
    """Asynchronous-reset flops without a `Why:` in the comment directly above them,
    or on the same line; a run of such flops shares one comment."""
    lines, code_lines, found = source.splitlines(), code.splitlines(), []
    for number, text in enumerate(code_lines, start=1):
        if not ARST.search(text):
            continue
        i, why = number - 1, "Why:" in lines[number - 1]
        while not why and i > 0 and (COMMENT_LINE.match(lines[i - 1]) or ARST.search(code_lines[i - 1])):
            i -= 1
            why = COMMENT_LINE.match(lines[i]) is not None and "Why:" in lines[i]
        if not why:
            found.append((number, "arst-why", "reset synchronously, or say in a Why: comment directly above "
                          "why this flop needs an asynchronous reset"))
    return found


def violations(path: Path, rel: str) -> list[Violation]:
    source = path.read_text(encoding="utf-8", errors="replace")
    kept, code, comments = split(source)
    found: list[tuple[int, str, str]] = []
    for rule, pattern, message, exempt in KEYWORD_RULES:
        if rel in exempt:
            continue
        for match in re.finditer(pattern, code):
            found.append((line_of(code, match.start()), rule, message))
    for match in INCLUDE.finditer(kept):
        if not match.group(1).startswith("src/"):
            found.append((line_of(kept, match.start()), "include-path",
                          f"include {match.group(1)} by its path from the repository root (src/...)"))
    for number, text in enumerate(source.splitlines(), start=1):
        if "\t" in text:
            found.append((number, "tab", "indent with spaces, never tabs"))
    found += header_violations(code, board_top(rel)) + typedef_violations(code) + waiver_violations(comments)
    if rel not in MACRO_FILES:
        found += arst_violations(source, code)
    return [Violation(rel, line, rule, text) for line, rule, text in sorted(found)]


def scanned(path: str) -> bool:
    p = Path(path)
    return (len(p.parts) > 3 and p.parts[0] == "src" and p.parts[2] in DESIGN_AREAS
            and p.suffix in SV_SUFFIXES and not SKIPPED_PARTS.intersection(p.parts))


def tracked_files(root: Path) -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z", "src/"], cwd=root,
                         capture_output=True, text=True, check=True).stdout
    return sorted(p for p in out.split("\0") if p and scanned(p))


def hdl_libraries(root: Path):
    """The library resolver, tools/bazel/hdl_libraries.py."""
    spec = importlib.util.spec_from_file_location("hdl_libraries", root / "tools" / "bazel" / "hdl_libraries.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def lint_tops(root: Path) -> dict[str, dict]:
    """Every lint top, with its sources and headers."""
    return {top: files for tops in hdl_libraries(root).lint_tops(root).values() for top, files in tops.items()}


def run_verilator(top: str, sources: list[str], cwd: Path) -> tuple[list[str], list[str]]:
    """Verilator's warnings (one line each) and errors for one top."""
    if not shutil.which("verilator"):
        raise SystemExit("verilator not found on PATH: install the version pinned in cfg/toolchain.json")
    run = subprocess.run(["verilator", *VERILATOR_ARGS, "--top-module", top, *sources], cwd=cwd,
                         capture_output=True, text=True)
    lines = (run.stdout + run.stderr).splitlines()
    warnings = [line for line in lines if line.startswith("%Warning-")]
    errors = [line for line in lines if line.startswith("%Error")]
    if run.returncode and not errors:
        errors = [f"%Error: verilator exited with {run.returncode}"] + lines[-20:]
    return warnings, errors


def prepend_path() -> None:
    """Put the directories of the LINT_PATH_PREPEND files (Bazel's Verilator) first on PATH."""
    dirs = [str(Path(p).absolute().parent) for p in os.environ.get("LINT_PATH_PREPEND", "").split()]
    if dirs:
        os.environ["PATH"] = os.pathsep.join(dict.fromkeys(dirs + [os.environ.get("PATH", "")]))


def verilator_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="rtl_lint.py verilator",
                                     description="Lint one top with Verilator against its baseline entry.")
    parser.add_argument("top")
    parser.add_argument("files", nargs="+", help="the top's sources, needed ones first, relative to the working folder")
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    args = parser.parse_args(argv)
    prepend_path()
    sources = [f for f in args.files if Path(f).suffix in {".sv", ".v"}]
    warnings, errors = run_verilator(args.top, sources, Path.cwd())
    allowed = load_baseline(args.baseline)["verilator"]
    for line in errors:
        print(line)
    problems = baseline_counts.check({args.top: len(warnings)}, allowed, [args.top], "Verilator warnings", FIX,
                                     UPDATE, {args.top: ["  " + w for w in warnings]})
    for line in problems:
        print(line)
    if errors or problems:
        return 1
    for line in warnings:
        print("  baselined: " + line)
    print(f"verilator lint {args.top}: PASS ({len(sources)} files, {len(warnings)} baselined warnings)")
    return 0


def load_baseline(path: Path) -> dict[str, dict[str, int]]:
    data = json.loads(path.read_text()) if path.exists() else {}
    return {"rules": data.get("rules", {}), "verilator": data.get("verilator", {})}


def write_baseline(rules: dict[str, int], verilator: dict[str, int]) -> None:
    BASELINE.write_text(json.dumps({"rules": dict(sorted(rules.items())),
                                    "verilator": dict(sorted(verilator.items()))}, indent=1) + "\n")


def relative(arg: str) -> str:
    path = Path(arg)
    path = path if path.is_absolute() else Path.cwd() / path
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return arg


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["verilator"]:
        return verilator_main(argv[1:])
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="*", help="design files to check (default: every tracked one)")
    parser.add_argument("--update-baseline", action="store_true",
                        help="rewrite the baseline: the rules on every file, Verilator on every lint top")
    parser.add_argument("--all", action="store_true", help="list every rule violation, ignoring the baseline")
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
    counts = baseline_counts.count([v.path for v in found])

    if args.update_baseline:
        if args.paths:
            parser.error("--update-baseline scans the whole tree; pass no paths")
        tops = lint_tops(ROOT)
        verilator = {}
        for top, top_files in sorted(tops.items()):
            warnings, errors = run_verilator(top, top_files["sources"], ROOT)
            if errors:
                print("\n".join([f"{top}: Verilator fails, which no baseline can hold:"] + errors))
                return 1
            if warnings:
                verilator[top] = len(warnings)
        write_baseline(counts, verilator)
        print(f"baseline: {len(found)} rule violations in {len(counts)} files, "
              f"{sum(verilator.values())} Verilator warnings in {len(verilator)} of {len(tops)} tops")
        return 0
    if args.all:
        for v in found:
            print(f"{v.path}:{v.line}: [{v.rule}] {v.text}")
        print(f"{len(found)} violations")
        return 1 if found else 0

    baseline = load_baseline(BASELINE)
    if not args.paths:
        files = files + sorted(set(baseline["rules"]) - set(files))
    details = {path: [f"  {v.path}:{v.line}: [{v.rule}] {v.text}" for v in found if v.path == path] for path in files}
    errors = baseline_counts.check(counts, baseline["rules"], files, "RTL rule violations", FIX, UPDATE, details)
    if not args.paths:
        tops = lint_tops(ROOT)
        errors += [f"{top}: baseline entry for a top Verilator no longer lints; run {UPDATE} and commit it"
                   for top in sorted(set(baseline["verilator"]) - set(tops))]
    base = args.base_baseline and ROOT / args.base_baseline
    if base and base.exists():
        old = load_baseline(base)
        errors += baseline_counts.raised(baseline["rules"], old["rules"], "RTL rule violations",
                                         "fix the new violations")
        errors += baseline_counts.raised(baseline["verilator"], old["verilator"], "Verilator warnings",
                                         "fix or waive the new warnings inline", "new tops")
    for e in errors:
        print(e)
    if errors:
        return 1
    print(f"rtl lint: PASS ({len(files)} files, {sum(baseline['rules'].values())} baselined violations)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
