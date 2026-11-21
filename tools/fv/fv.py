#!/usr/bin/env python3
"""Run every project's formal targets with SymbiYosys (wiki/tools/fv/spec.md).

A target is one SymbiYosys file, src/<project>/fv/<block>/<target>.sby, whose
header names its regression level, time budget and the library it proves (its
files come from src/<project>/cfg/libraries.json, wiki/tools/bazel/spec.md):

    # level: L0
    # timeout: 600
    # library: <harness library>

The runner writes the file with `{sources}` in its script replaced by the
library's ordered sources and a [files] section that copies them and their
headers, and runs that.

Each run writes <workdir>/<target>_<task>/ for every task in the file, keeps
counterexample and cover traces there, and prints one line per task.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time

# Why: a Bazel test sets FV_ROOT to its runfiles and writes into its own outputs,
# so a run reads only the files Bazel declared and keeps traces with the test.
ROOT = Path(os.environ["FV_ROOT"]).absolute() if os.environ.get("FV_ROOT") else Path(__file__).resolve().parents[2]
DEFAULT_WORKDIR = Path(os.environ.get("TEST_UNDECLARED_OUTPUTS_DIR") or ROOT / "workdir" / "fv")
CONFIG = ROOT / "cfg" / "toolchain.json"
LEVELS = ["L0", "L1", "L2", "L3", "L4"]
DEFAULT_TIMEOUT_SECONDS = 600
HEADER = re.compile(r"^#\s*(level|timeout|library|flavors|defines)\s*:\s*(.*?)\s*$")


def parse_target(path: Path) -> dict:
    """Read a .sby file's header fields and task list."""
    fields = {"level": None, "timeout_seconds": DEFAULT_TIMEOUT_SECONDS, "libraries": [], "flavors": [], "defines": []}
    tasks, section = [], None
    for line in path.read_text().splitlines():
        match = HEADER.match(line)
        if match and section is None:
            key, value = match.groups()
            if key == "level":
                fields["level"] = value
            elif key == "timeout":
                fields["timeout_seconds"] = int(value)
            else:
                fields["libraries" if key == "library" else key] = value.split()
            continue
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1].strip()
        elif section == "tasks" and stripped and not stripped.startswith("#"):
            tasks.append(stripped.split()[0].rstrip(":"))
    if fields["timeout_seconds"] > DEFAULT_TIMEOUT_SECONDS:
        raise ValueError(f"{path}: timeout over {DEFAULT_TIMEOUT_SECONDS} s; every proof must finish in ten minutes")
    if fields["level"] not in LEVELS + ["manual"]:
        raise ValueError(f"{path}: header needs '# level: L0..L4' or 'manual'")
    if len(fields["libraries"]) != 1:
        raise ValueError(f"{path}: header needs '# library: <the harness library>'")
    return dict(fields, sby=path, tasks=tasks or [""])


def hdl_libraries(root: Path = ROOT):
    """The library resolver, tools/bazel/hdl_libraries.py, of the tree being run."""
    import importlib.util
    path = root / "tools" / "bazel" / "hdl_libraries.py"
    if not path.is_file():
        path = Path(__file__).resolve().parents[1] / "bazel" / "hdl_libraries.py"
    spec = importlib.util.spec_from_file_location("hdl_libraries", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def render(target: dict, root: Path = ROOT) -> str:
    """The .sby file with its library's sources in the script and a [files] section."""
    text = target["sby"].read_text()
    if "{sources}" not in text:
        raise ValueError(f"{target['sby']}: its script must name the library's files as {{sources}}")
    if re.search(r"^\s*\[files\]", text, re.M):
        raise ValueError(f"{target['sby']}: drop [files]; the runner writes it from the library")
    files = [f"{name} {root / name}" for name in target["headers"] + target["sources"]]
    return text.replace("{sources}", " ".join(target["sources"])).rstrip("\n") + "\n\n[files]\n" + "\n".join(files) + "\n"


def load_targets(root: Path = ROOT) -> dict:
    targets = {}
    resolver = hdl_libraries(root)
    libraries = resolver.load(root)
    for path in sorted(root.glob("src/*/fv/*/*.sby")):
        name = path.stem
        if name in targets:
            raise ValueError(f"formal target {name} defined twice")
        target = resolver.resolve_target(parse_target(path), libraries)
        target["project"] = path.relative_to(root).parts[1]
        targets[name] = target
    return targets


def find_sby(config_path: Path = CONFIG, which=shutil.which) -> str | None:
    """sby from $SBY, the pinned OSS CAD Suite install, then PATH."""
    if os.environ.get("SBY"):
        sby = os.environ["SBY"]
        # Why: the suite's wrapper finds its install through its own real folder, and a
        # relative path (Bazel's runfiles link) must survive the run's change of folder.
        return which(str(Path(sby).resolve()) if os.sep in sby else sby)
    pin = json.loads(config_path.read_text()).get("oss_cad_suite")
    if pin:
        candidate = Path(pin["install_dir"]).expanduser() / "bin" / "sby"
        if candidate.is_file():
            return str(candidate)
    return which("sby")


def task_status(task_dir: Path) -> str:
    for status in ("PASS", "FAIL", "ERROR", "UNKNOWN", "TIMEOUT"):
        if (task_dir / status).exists():
            return status
    return "ERROR"


def run_target(name: str, target: dict, workdir: Path, sby: str) -> list[dict]:
    """Run every task of one target under its time budget; one result per task."""
    workdir.mkdir(parents=True, exist_ok=True)
    prefix = workdir / name
    log = workdir / f"{name}.log"
    rendered = workdir / f"{name}.sby"
    rendered.write_text(render(target))
    start = time.monotonic()
    with open(log, "w") as out:
        process = subprocess.Popen([sby, "-f", "--prefix", str(prefix), rendered.name],
                                   cwd=workdir, stdout=out, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        try:
            process.wait(timeout=target["timeout_seconds"])
            timed_out = False
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            timed_out = True
    seconds = time.monotonic() - start
    results = []
    for task in target["tasks"]:
        task_dir = Path(f"{prefix}_{task}") if task else prefix
        status = "TIMEOUT" if timed_out else task_status(task_dir)
        results.append({"target": name, "task": task or "-", "status": status,
                        "seconds": round(seconds, 1), "dir": str(task_dir), "log": str(log)})
    return results


def select(targets: dict, names: list[str], project: str | None, level: str | None,
           everything: bool) -> list[str]:
    unknown = [n for n in names if n not in targets]
    if unknown:
        raise SystemExit(f"unknown formal targets: {unknown}")
    chosen = list(names) or [n for n, t in targets.items() if t["level"] != "manual"]
    if project:
        chosen = [n for n in chosen if targets[n]["project"] == project]
    if level:
        chosen = [n for n in chosen if targets[n]["level"] in LEVELS[:LEVELS.index(level) + 1]]
    if not (names or project or level or everything):
        raise SystemExit("name targets, or use --all, --project or --level")
    return chosen


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="list every formal target")
    run = sub.add_parser("run", help="run formal targets")
    run.add_argument("targets", nargs="*")
    run.add_argument("--all", action="store_true", help="every non-manual target")
    run.add_argument("--project")
    run.add_argument("--level", choices=LEVELS, help="targets at this level or below")
    run.add_argument("--workdir", type=Path, default=DEFAULT_WORKDIR)
    args = parser.parse_args(argv)

    targets = load_targets()
    if args.command == "list":
        for name, t in targets.items():
            print(f"{name:30s} {t['project']:12s} {t['level']:6s} {','.join(t['tasks']):14s} "
                  f"{t['sby'].relative_to(ROOT)}")
        return 0
    names = select(targets, args.targets, args.project, args.level, args.all)
    sby = find_sby()
    if not sby:
        print("sby not found: install the pinned OSS CAD Suite (python3 tools/toolchain/check.py)",
              file=sys.stderr)
        return 1
    failed = 0
    total = time.monotonic()
    for name in names:
        for r in run_target(name, targets[name], args.workdir.resolve(), sby):
            print(f"{r['target']:30s} {r['task']:8s} {r['seconds']:7.1f}s {r['status']}", flush=True)
            if r["status"] != "PASS":
                failed += 1
                outputs = os.environ.get("TEST_UNDECLARED_OUTPUTS_DIR")
                task_dir = Path(r["dir"]).resolve()
                if outputs and task_dir.is_relative_to(Path(outputs).resolve()):
                    # Why: the sandbox paths are gone once the test ends; Bazel keeps the outputs.
                    rel = task_dir.relative_to(Path(outputs).resolve())
                    print(f"    see bazel-testlogs/<package>/<target>.formal/test.outputs/{rel}")
                else:
                    print(f"    see {r['dir']} and {r['log']}")
                if os.environ.get("TEST_TMPDIR") and Path(r["log"]).exists():
                    # Why: under bazel test this output is the test log an agent reads first.
                    print(Path(r["log"]).read_text(errors="replace"))
    print(f"{len(names)} targets, {failed} failed tasks, {time.monotonic() - total:.1f}s")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
