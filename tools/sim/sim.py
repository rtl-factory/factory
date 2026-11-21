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

# Why: a Bazel action or test sets SIM_ROOT to its own tree, so the runner reads
# only the files Bazel declared, not the checkout behind the runfiles symlinks.
ROOT = Path(os.environ["SIM_ROOT"]).absolute() if os.environ.get("SIM_ROOT") else Path(__file__).resolve().parents[2]
DEFAULT_WORKDIR = ROOT / "workdir" / "sim"
COMPILE_ALLOWANCE_SECONDS = 900
LEVELS = ["L0", "L1", "L2", "L3", "L4"]


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


def load_targets(root: Path = ROOT) -> dict:
    targets = {}
    resolver = hdl_libraries(root)
    libraries = resolver.load(root)
    for registry in sorted(root.glob("src/*/cfg/targets.json")):
        for name, target in json.loads(registry.read_text())["targets"].items():
            if name in targets:
                raise ValueError(f"target {name} defined twice")
            target = dict(resolver.resolve_target(target, libraries), project=registry.parts[-3])
            native = target.get("kind") == "native"
            if target.get("regression_action", "test") not in ("test", "compile") or (
                    not native and ("regression_action" in target or "trace_fst" in target)):
                raise ValueError(f"target {name}: regression_action (test or compile) and "
                                 "trace_fst are for native targets only")
            targets[name] = target
    return targets


TARGET_DEFAULTS = {"kind": "cocotb", "parameters": {}, "defines": [], "test_module": None,
                   "testcase": None, "default_seed": None, "max_cycles": None}


def _frozen(value):
    if isinstance(value, dict):
        return tuple(sorted(value.items()))
    return tuple(value) if isinstance(value, list) else value


def target_views(root: Path = ROOT) -> dict:
    """Every target as a read-only object with the defaults filled in, for tests that check the registries."""
    from types import SimpleNamespace
    return {name: SimpleNamespace(**{k: _frozen(v) for k, v in {**TARGET_DEFAULTS, **target}.items()}, name=name)
            for name, target in load_targets(root).items()}


def fatal_matches(text: str, expected: dict) -> bool:
    options = expected.get("any_of", [expected])
    return any(all(str(option[key]) in text for key in ("file", "hierarchy", "message") if key in option)
               for option in options)


def build_model(name: str, target: dict, build_dir: Path, log_file: Path, waves: bool) -> int:
    """Build one target's simulation model into build_dir; 0 on success."""
    if target.get("kind") == "native":
        return build_native(name, target, build_dir, log_file)
    import cocotb_tools.runner
    from cocotb_tools.runner import get_runner

    cocotb_tools.runner.MAX_PARALLEL_BUILD_JOBS = int(os.environ.get("SIM_BUILD_JOBS", "2"))
    # Why: the runner hands its own sys.path to the build as PYTHONPATH, so the python3
    # that Verilator's makefile calls must be this interpreter, not another one on PATH.
    os.environ["PATH"] = os.pathsep.join([str(Path(sys.executable).parent), os.environ.get("PATH", "")])
    build_args = ["--timing", "--assert", "--Wall"]
    if target.get("public_depth"):
        build_args += ["--no-public-flat-rw", "--public-depth", str(target["public_depth"])]
    get_runner("verilator").build(
        sources=[ROOT / s for s in target["sources"]], hdl_toplevel=target["top"],
        build_args=build_args, parameters=target.get("parameters", {}),
        build_dir=build_dir, cwd=ROOT, always=waves, waves=waves, log_file=log_file)
    return 0


def model_binary(name: str, target: dict, build_dir: Path) -> Path:
    if target.get("kind") == "native":
        return build_dir / f"{name}_sim"
    return build_dir / target["top"]


def test_model(name: str, target: dict, build_dir: Path, test_dir: Path, seed: int, waves: bool,
               log_file: Path) -> int:
    """Run one target's test against its built model; 0 when it passes."""
    if target.get("kind") == "native":
        if target.get("regression_action") == "compile":
            # Why: a harness that needs inputs prepared elsewhere (a program image, a
            # script's plusargs): the regression only proves that it builds.
            return 0
        with open(log_file, "w") as log:
            run = subprocess.run([str(model_binary(name, target, build_dir)), *target.get("plusargs", [])],
                                 cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        return run.returncode
    import cocotb_tools.config
    from cocotb_tools.runner import get_runner, get_results

    test_dir.mkdir(parents=True, exist_ok=True)
    results = test_dir / "results.xml"
    get_runner("verilator").test(
        test_module=target["test_module"], hdl_toplevel=target["top"],
        testcase=target["testcase"], seed=seed, waves=waves, hdl_toplevel_lang="verilog",
        extra_env={"DV_CLOCK_HZ": str(target.get("clock_hz", 25_000_000)),
                   "DV_MAX_CYCLES": str(target.get("max_cycles", 0)),
                   "DV_SEED": str(seed),
                   "COCOTB_RESULTS_RELATIVE_TO": str(test_dir),
                   # Why: a model built elsewhere (a Bazel action) has its rpath pointing
                   # at that build's cocotb; this finds the libraries of the cocotb running now.
                   "LD_LIBRARY_PATH": os.pathsep.join(
                       filter(None, [str(cocotb_tools.config.libs_dir), os.environ.get("LD_LIBRARY_PATH")]))},
        build_dir=build_dir, test_dir=test_dir, results_xml=str(results), log_file=log_file)
    total, failed = get_results(results)
    return 0 if total and not failed else 1


def run_one(name: str, target: dict, workdir: Path, seed: int, waves: bool, model: Path | None = None) -> int:
    """Build (unless model names a built one) and test one target in this process (called in a child)."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    base = workdir / "cocotb" / name
    build_dir = model or (base / ("obj" if target.get("kind") == "native" else "build"))
    if model is None:
        build_dir.mkdir(parents=True, exist_ok=True)
        if build_model(name, target, build_dir, base / "build.log", waves):
            return 1
    return test_model(name, target, build_dir, base / "test", seed, waves, base / "test.log")


def build_native(name: str, target: dict, obj: Path, log_file: Path) -> int:
    """Build a self-checking SystemVerilog top into a binary."""
    build = ["verilator", "--binary", "--timing", "--assert", "--Wall",
             "-j", os.environ.get("SIM_BUILD_JOBS", "2"),
             "--top-module", target["top"], "--Mdir", str(obj), "-o", f"{name}_sim",
             *(["--trace-fst"] if target.get("trace_fst") else []),
             *(f"-D{d}" for d in target.get("defines", [])),
             *(str(ROOT / s) for s in target["sources"])]
    with open(log_file, "w") as log:
        return subprocess.run(build, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT).returncode


def prune_model(name: str, target: dict, build_dir: Path) -> None:
    """Keep only the model's executable, so a cached model holds no object files."""
    keep = model_binary(name, target, build_dir)
    for path in build_dir.iterdir():
        if path != keep:
            shutil.rmtree(path) if path.is_dir() and not path.is_symlink() else path.unlink()


def run_target(name: str, target: dict, workdir: Path, seed: int | None, waves: bool,
               direct: bool = False, spec: Path | None = None, model: Path | None = None) -> bool:
    native = target.get("kind") == "native"
    seed = target.get("default_seed", 1) if seed is None else seed
    wrapped = bool(target.get("wrapper")) and not direct
    env = dict(os.environ)
    base = workdir / ("wrapped" if wrapped else "cocotb") / name
    if base.exists():
        shutil.rmtree(base)
    base.mkdir(parents=True)
    timeout = target.get("timeout_seconds", 600) + (0 if model else COMPILE_ALLOWANCE_SECONDS)
    if wrapped:
        # Why: the wrapper prepares the program image before it runs this target
        # directly, so it needs a longer budget.
        timeout *= 4
        fields = {"output": str(base / "out"), "workdir": str(base / "lane")}
        argv = [sys.executable, "-B", "-m"] + [a.format(**fields) for a in target["wrapper"]]
        # Why: the wrapper's own `test --direct` call reads these from the environment.
        env.update(SIM_SEED=str(seed), SIM_WAVES="1" if waves else "")
    else:
        argv = [sys.executable, "-B", __file__, "--workdir", str(workdir), "_child", name, "--seed", str(seed)]
        if waves:
            argv.append("--waves")
        if model:
            argv += ["--spec", str(spec), "--model", str(model)]
    start = time.monotonic()
    log = base / "run.log"
    with log.open("w") as handle:
        # Why: own process group, so a timeout also stops the simulator and any wrapper
        # children; a --direct run stays in its wrapper's group so the outer kill reaches it.
        proc = subprocess.Popen(argv, cwd=ROOT, stdout=handle, stderr=subprocess.STDOUT,
                                env=env, start_new_session=not direct)
        try:
            code, verdict = proc.wait(timeout=timeout), None
        except subprocess.TimeoutExpired:
            if direct:
                proc.kill()  # Why: the wrapper's group kill covers its children
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
        if os.environ.get("TEST_TMPDIR"):
            # Why: under bazel test this output is the test log an agent reads first.
            print(text, flush=True)
    return passed


def prepend_tool_paths() -> None:
    """Put the directories of the SIM_PATH_PREPEND files (Bazel's Verilator) first on PATH."""
    dirs = []
    for entry in os.environ.get("SIM_PATH_PREPEND", "").split():
        path = Path(entry).absolute()
        dirs.append(str(path if path.is_dir() else path.parent))
    if dirs:
        os.environ["PATH"] = os.pathsep.join(dict.fromkeys(dirs + [os.environ.get("PATH", "")]))


def main(argv=None) -> int:
    prepend_tool_paths()
    # Why: rules_python sets this for its own interpreter; children that run a script
    # by path (a wrapper's program build) need their own folder on sys.path.
    os.environ.pop("PYTHONSAFEPATH", None)
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
    build = sub.add_parser("build-model", help="build one target's model (Bazel's model action)")
    build.add_argument("--spec", type=Path, required=True, help="JSON file: the target's name and fields")
    build.add_argument("--out", type=Path, required=True)
    build.add_argument("--waves", action="store_true")
    run = sub.add_parser("run-model", help="test one target against a built model (Bazel's test)")
    run.add_argument("--spec", type=Path, required=True)
    run.add_argument("--model", type=Path, required=True)
    run.add_argument("--seed", type=int)
    run.add_argument("--waves", action="store_true")
    child = sub.add_parser("_child")
    child.add_argument("target")
    child.add_argument("--seed", type=int, required=True)
    child.add_argument("--waves", action="store_true")
    child.add_argument("--spec", type=Path)
    child.add_argument("--model", type=Path)
    args = parser.parse_args(argv)
    # Why: a Bazel model build or test reads only its spec; the registries are not its inputs.
    targets = {} if getattr(args, "spec", None) else load_targets()
    workdir = args.workdir if args.workdir.is_absolute() else ROOT / args.workdir
    if args.command == "list":
        for name, target in sorted(targets.items()):
            print(f"{name:45s} {target['project']:12s} {target.get('level', ''):6s} {target.get('description', '')[:80]}")
        return 0
    if args.command in ("build-model", "run-model") or getattr(args, "spec", None):
        spec = json.loads(args.spec.read_text())
        name, target = spec["name"], spec["target"]
    if args.command == "build-model":
        out = args.out.absolute()
        out.mkdir(parents=True, exist_ok=True)
        log = out.parent / f"{out.name}.log"
        try:
            code = build_model(name, target, out, log, args.waves)
        except (RuntimeError, SystemExit, subprocess.CalledProcessError):
            code = 1
        if code:
            print(log.read_text(errors="replace") if log.exists() else "", file=sys.stderr)
            return code
        prune_model(name, target, out)
        return 0
    if args.command == "run-model":
        if args.seed is None and os.environ.get("SIM_SEED"):
            args.seed = int(os.environ["SIM_SEED"])
        outputs = Path(os.environ.get("TEST_UNDECLARED_OUTPUTS_DIR") or workdir)
        return 0 if run_target(name, target, outputs, args.seed, args.waves or bool(os.environ.get("SIM_WAVES")),
                               spec=args.spec.absolute(), model=args.model.absolute()) else 1
    if args.command == "_child":
        target = target if args.spec else targets[args.target]
        return run_one(args.target, target, workdir, args.seed, args.waves, args.model)
    # Why: a wrapper's own `test --direct` call and a Bazel wrapped test both pass the
    # seed and waves this way.
    if args.seed is None and os.environ.get("SIM_SEED"):
        args.seed = int(os.environ["SIM_SEED"])
    if os.environ.get("SIM_WAVES"):
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
