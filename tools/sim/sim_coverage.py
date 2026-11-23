"""Code and functional coverage of simulation targets (wiki/tools/sim/spec.md, Coverage).

Testbench side: a cocotb test declares its coverage points with cocotb-coverage
(`CoverPoint`, `CoverCross` decorating a sampling function) and calls
`write_functional()` at its end, which saves the coverage database where the
runner looks for it and logs every bin.

Runner side: `summarize(test_dir, root)` reads what a run left in its test
folder, Verilator's coverage.dat (a model built with --coverage) and the
functional database, writes the lcov file and annotated sources of the code
coverage, and returns the summary lines it also writes to coverage.txt.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
import re
import shutil
import subprocess

CODE = "coverage.dat"
FUNCTIONAL = "functional_coverage.yml"
SUMMARY = "coverage.txt"
# Why: the runner points this at the test folder, so the database lands beside results.xml.
DIR_ENV = "SIM_COVERAGE_DIR"

_POINT = re.compile(r"^C '(?P<key>[^']*)' (?P<count>\d+)$")


def write_functional(log: logging.Logger | None = None) -> Path:
    """Save this run's functional coverage database and log its bins; call it at the end of a test."""
    from cocotb_coverage.coverage import coverage_db

    path = Path(os.environ.get(DIR_ENV, ".")) / FUNCTIONAL
    path.parent.mkdir(parents=True, exist_ok=True)
    coverage_db.export_to_yaml(str(path))
    coverage_db.report_coverage((log or logging.getLogger("cocotb.coverage")).info, bins=True)
    return path


def code_points(dat: Path) -> dict[str, tuple[int, int]]:
    """Points hit and total per coverage type (line, toggle, branch, expr, user) of a coverage.dat."""
    totals: dict[str, list[int]] = {}
    for line in dat.read_text(errors="replace").splitlines():
        match = _POINT.match(line)
        if not match:
            continue
        # Why: a key is \x01-separated name\x02value fields; "page" is v_<type>/<module>.
        fields = dict(f.split("\x02", 1) for f in match["key"].split("\x01") if "\x02" in f)
        kind = fields.get("page", "v_other/").split("/", 1)[0].removeprefix("v_")
        hit, total = totals.setdefault(kind, [0, 0])
        totals[kind] = [hit + (int(match["count"]) > 0), total + 1]
    return {kind: (hit, total) for kind, (hit, total) in sorted(totals.items())}


def root_relative(path: str, root: Path) -> str:
    """path as a path under root: the longest tail of it that names a file there, else path unchanged.

    Why: Verilator records each source by the absolute path it resolved at build
    time, which for a cached model is another checkout's (or a gone sandbox's).
    """
    parts = Path(path).parts
    for start in range(1, len(parts)):
        tail = Path(*parts[start:])
        if (root / tail).is_file():
            return tail.as_posix()
    return path


def relocate(dat: Path, out: Path, root: Path) -> None:
    """Copy a coverage.dat to out with every source path ("f" field) made root-relative."""
    cache: dict[str, str] = {}

    def fix(match: re.Match) -> str:
        old = match[1]
        if old not in cache:
            cache[old] = root_relative(old, root)
        return "\x01f\x02" + cache[old]

    field = re.compile("\x01f\x02([^\x01']*)")
    out.write_text("".join(field.sub(fix, line) if line.startswith("C '") else line
                           for line in dat.read_text(errors="replace").splitlines(keepends=True)))


def functional_groups(path: Path) -> dict[str, tuple[int, int]]:
    """Bins covered and total per top-level covergroup of a functional database."""
    import yaml

    database = yaml.safe_load(path.read_text()) or {}
    return {name: (int(entry["coverage"]), int(entry["size"]))
            for name, entry in sorted(database.items()) if "." not in name}


def _percent(hit: int, total: int) -> str:
    return f"{100.0 * hit / total:.1f}% ({hit}/{total})" if total else "no points"


def summarize(test_dir: Path, root: Path, expect_code: bool = False) -> list[str]:
    """The coverage summary of one run's test folder; empty when it holds no coverage."""
    lines = []
    dat = test_dir / CODE
    if dat.is_file():
        points = code_points(dat)
        lines.append("code: " + ", ".join(f"{kind} {_percent(*counts)}" for kind, counts in points.items()))
        tool = shutil.which("verilator_coverage")
        if tool:
            report = test_dir / "coverage"
            shutil.rmtree(report, ignore_errors=True)
            report.mkdir()
            relocated = report / CODE
            relocate(dat, relocated, root)
            # Why: annotation reads each source by its relocated, root-relative path, so
            # it reads this checkout's files whichever checkout built the model.
            run = subprocess.run([tool, "--annotate", str(report / "annotated"), "--annotate-min", "1",
                                  "--write-info", str(report / "coverage.info"), str(relocated.absolute())],
                                 cwd=root, capture_output=True, text=True)
            if run.returncode:
                lines.append(f"code report failed: {(run.stderr or run.stdout).strip()[-300:]}")
        else:
            lines.append("code report skipped: verilator_coverage not on PATH")
    elif expect_code:
        lines.append(f"code: no {CODE} (the model was not built with --coverage, or the run ended early)")
    functional = test_dir / FUNCTIONAL
    if functional.is_file():
        groups = functional_groups(functional)
        lines.append("functional: " + (", ".join(f"{name} {_percent(*counts)}" for name, counts in groups.items())
                                       or "no covergroups"))
    if lines:
        (test_dir / SUMMARY).write_text("".join(line + "\n" for line in lines))
    return lines
