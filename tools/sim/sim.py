#!/usr/bin/env python3
"""Run a project's cocotb simulation targets on Verilator (wiki/tools/sim/spec.md).

Targets live in src/<project>/cfg/targets.json. Each run builds and tests one
target under <workdir>/cocotb/<target>/{build,test}, prints its seed, and
fails on a failing test, a timeout, or a negative test whose expected fatal
did not fire.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WORKDIR = ROOT / "workdir" / "sim"
COMPILE_ALLOWANCE_SECONDS = 900
LEVELS = ["L0", "L1", "L2", "L3", "L4"]


def load_targets(root: Path = ROOT) -> dict:
    targets = {}
    for registry in sorted(root.glob("src/*/cfg/targets.json")):
        for name, target in json.loads(registry.read_text())["targets"].items():
            if name in targets:
                raise ValueError(f"target {name} defined twice")
            target = dict(target, project=registry.parts[-3])
            targets[name] = target
    return targets


def fatal_matches(text: str, expected: dict) -> bool:
    options = expected.get("any_of", [expected])
    return any(all(str(option[key]) in text for key in ("file", "hierarchy", "message") if key in option)
               for option in options)


def run_one(name: str, target: dict, workdir: Path, seed: int, waves: bool) -> int:
    """Build and test one target in this process (called in a child)."""
    import cocotb_tools.runner
    from cocotb_tools.runner import get_runner, get_results

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    base = workdir / "cocotb" / name
    if target.get("kind") == "native":
        return run_native(name, target, base)
    build_dir, test_dir = base / "build", base / "test"
    test_dir.mkdir(parents=True, exist_ok=True)
    cocotb_tools.runner.MAX_PARALLEL_BUILD_JOBS = int(os.environ.get("SIM_BUILD_JOBS", "2"))
    runner = get_runner("verilator")
    build_args = ["--timing", "--assert", "--Wall"]
    if target.get("public_depth"):
        build_args += ["--no-public-flat-rw", "--public-depth", str(target["public_depth"])]
    runner.build(sources=[ROOT / s for s in target["sources"]], hdl_toplevel=target["top"],
                 build_args=build_args, parameters=target.get("parameters", {}),
                 build_dir=build_dir, cwd=ROOT, always=waves, waves=waves,
                 log_file=base / "build.log")
    results = test_dir / "results.xml"
    runner.test(test_module=target["test_module"], hdl_toplevel=target["top"],
                testcase=target["testcase"], seed=seed, waves=waves,
                extra_env={"DV_CLOCK_HZ": str(target.get("clock_hz", 25_000_000)),
                           "DV_MAX_CYCLES": str(target.get("max_cycles", 0)),
                           "DV_SEED": str(seed),
                           "COCOTB_RESULTS_RELATIVE_TO": str(test_dir)},
                build_dir=build_dir, test_dir=test_dir, results_xml=str(results),
                log_file=base / "test.log")
    total, failed = get_results(results)
    return 0 if total and not failed else 1


def run_native(name: str, target: dict, base: Path) -> int:
    """Build a self-checking SystemVerilog top into a binary and run it."""
    obj = base / "obj"
    build = ["verilator", "--binary", "--timing", "--assert", "--Wall",
             "-j", os.environ.get("SIM_BUILD_JOBS", "2"),
             "--top-module", target["top"], "--Mdir", str(obj), "-o", f"{name}_sim",
             *(f"-D{d}" for d in target.get("defines", [])),
             *(str(ROOT / s) for s in target["sources"])]
    with open(base / "build.log", "w") as log:
        if subprocess.run(build, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT).returncode:
            return 1
    with open(base / "test.log", "w") as log:
        run = subprocess.run([str(obj / f"{name}_sim"), *target.get("plusargs", [])],
                             cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
    return run.returncode


def run_target(name: str, target: dict, workdir: Path, seed: int | None, waves: bool,
               direct: bool = False) -> bool:
    native = target.get("kind") == "native"
    seed = target.get("default_seed", 1) if seed is None else seed
    wrapped = bool(target.get("wrapper")) and not direct
    env = dict(os.environ)
    base = workdir / ("wrapped" if wrapped else "cocotb") / name
    if base.exists():
        shutil.rmtree(base)
    base.mkdir(parents=True)
    timeout = target.get("timeout_seconds", 600) + COMPILE_ALLOWANCE_SECONDS
    if wrapped:
        # The wrapper prepares the program image, then runs this target directly.
        timeout *= 4
        fields = {"output": str(base / "out"), "workdir": str(base / "lane")}
        argv = [sys.executable, "-B", "-m"] + [a.format(**fields) for a in target["wrapper"]]
        # The wrapper's own `test --direct` call picks these up.
        env.update(SIM_SEED=str(seed), SIM_WAVES="1" if waves else "")
    else:
        argv = [sys.executable, "-B", __file__, "--workdir", str(workdir), "_child", name, "--seed", str(seed)]
        if waves:
            argv.append("--waves")
    start = time.monotonic()
    log = base / "run.log"
    with log.open("w") as handle:
        # Own process group, so a timeout also stops the simulator and any wrapper
        # children; a --direct run stays in its wrapper's group so the outer kill reaches it.
        proc = subprocess.Popen(argv, cwd=ROOT, stdout=handle, stderr=subprocess.STDOUT,
                                env=env, start_new_session=not direct)
        try:
            code, verdict = proc.wait(timeout=timeout), None
        except subprocess.TimeoutExpired:
            if direct:
                proc.kill()  # the wrapper's group kill covers its children
            else:
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            code, verdict = 124, f"TIMEOUT after {timeout}s"
    text = "".join(p.read_text(errors="replace") for p in (log, base / "build.log", base / "test.log") if p.exists())
    expected = target.get("expected_fatal")
    if expected:
        passed = code != 0 and verdict is None and fatal_matches(text, expected)
        verdict = verdict or ("PASS (expected fatal fired)" if passed else "FAIL (expected fatal did not fire)")
    else:
        passed = code == 0
        verdict = verdict or ("PASS" if passed else f"FAIL (exit {code})")
    shown = "-" if native else seed
    print(f"{name:45s} seed={shown!s:<10} {time.monotonic() - start:7.1f}s  {verdict}", flush=True)
    if not passed:
        print(f"    log: {log}", flush=True)
    return passed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workdir", type=Path, default=DEFAULT_WORKDIR)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="list targets")
    test = sub.add_parser("test", help="run targets")
    test.add_argument("targets", nargs="*")
    test.add_argument("--all", action="store_true", help="run every target")
    test.add_argument("--project", help="only targets of this project (src/<project>)")
    test.add_argument("--seed", type=int, help="override the target's default seed")
    test.add_argument("--waves", action="store_true", help="write waveforms")
    test.add_argument("--level", choices=LEVELS,
                      help="targets at this regression level or below; alone, all such targets")
    test.add_argument("--direct", action="store_true",
                      help="run the target itself, not its wrapper (wrappers call this)")
    child = sub.add_parser("_child")
    child.add_argument("target")
    child.add_argument("--seed", type=int, required=True)
    child.add_argument("--waves", action="store_true")
    args = parser.parse_args(argv)
    targets = load_targets()
    workdir = args.workdir if args.workdir.is_absolute() else ROOT / args.workdir
    if args.command == "list":
        for name, target in sorted(targets.items()):
            print(f"{name:45s} {target['project']:12s} {target.get('level', ''):6s} {target.get('description', '')[:80]}")
        return 0
    if args.command == "_child":
        return run_one(args.target, targets[args.target], workdir, args.seed, args.waves)
    if args.direct and args.seed is None and os.environ.get("SIM_SEED"):
        args.seed = int(os.environ["SIM_SEED"])
    if args.direct and os.environ.get("SIM_WAVES"):
        args.waves = True
    selection = args.all or args.project or args.level
    names = args.targets or (sorted(targets) if selection else [])
    if args.project:
        names = [n for n in names if targets[n]["project"] == args.project]
    if selection and not args.targets:
        names = [n for n in names if targets[n].get("level") != "manual"]
    if args.level:
        names = [n for n in names if targets[n].get("level") in LEVELS[:LEVELS.index(args.level) + 1]]
    unknown = [n for n in names if n not in targets]
    if not names or unknown:
        parser.error(f"unknown targets: {unknown}" if unknown else "name targets, or use --all, --project or --level")
    if (args.seed is not None or args.waves) and any(targets[n].get("kind") == "native" for n in names):
        print("note: native targets take no seed and write no waveforms", file=sys.stderr)
    if not shutil.which("verilator"):
        parser.error("verilator not on PATH")
    failed = [n for n in names if not run_target(n, targets[n], workdir, args.seed, args.waves, args.direct)]
    print(f"\n{len(names) - len(failed)}/{len(names)} passed" + (f"; failed: {' '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
