#!/usr/bin/env python3
"""Build any project's board image with Quartus, driven only by its manifest, and gate it.

    python3 tools/quartus/quartus.py --manifest src/<project>/cfg/<manifest>.json [--board B]
        [--profile P] [--quartus PATH] [--output DIR] [--plan | --elaborate-only | --verify-only]

It stages workdir/quartus/<project>/ from the manifest and the board's pin map
(src/common/fpga/<board>/pins.json), compiles, runs a separate timing audit,
applies the report gate, writes evidence.json and binds the SOF to its commit
(<project>.json beside it). It knows no design. Spec: wiki/tools/quartus/spec.md.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.quartus import build, gate, image, manifest  # noqa: E402

SKIPPED = 77


def output_dir(root: Path, config: dict, output: Path | None, suffix: str = "") -> Path:
    return build.confined(root, output.resolve() if output else
                          root / "workdir" / "quartus" / (config["project"] + suffix))


def find_quartus(requested: str | None) -> str | None:
    return shutil.which(requested or os.environ.get("QUARTUS_SH", "quartus_sh"))


def elaborate(root: Path, output: Path, config: dict, executable: str) -> dict:
    """Stage and run analysis and elaboration only, then gate it."""
    with build.build_lock(root, output):
        if output.exists():
            shutil.rmtree(output)
        build.stage(root, output, config)
        try:
            build.run([str(Path(executable).with_name("quartus_map")), config["project"],
                       "--analysis_and_elaboration"], output, "elaborate.log", config["timeout_seconds"])
        except RuntimeError as error:
            # Why: the exit status alone does not say which construct Quartus rejected.
            log = gate.read(output / "elaborate.log").splitlines()
            raise RuntimeError("\n".join([str(error)] + [line.strip() for line in log
                                                         if gate.ERROR_LINE.match(line.strip())])) from error
        return gate.verify_elaboration(output, config)


def compile_image(root: Path, output: Path, config: dict, executable: str) -> dict:
    """Stage, compile, audit timing and gate; bind the passing SOF to its source."""
    with build.build_lock(root, output):
        source = build.source_identity(root, executable)
        if source["source_dirty"] and config["require_clean_source"]:
            raise ValueError("commit the source before a qualified full compile (require_clean_source)")
        # Why: an artifact from an earlier invocation must not satisfy this run.
        if output.exists():
            shutil.rmtree(output)
        build.stage(root, output, config)
        (output / "source.json").write_text(json.dumps(source, indent=2) + "\n")
        build.run([executable, "--flow", "compile", config["project"]], output,
                  "compile.log", config["timeout_seconds"])
        build.run([str(Path(executable).with_name("quartus_sta")), "-t", "audit.tcl"], output,
                  "audit.log", config["timeout_seconds"])
        result = gate.verify_reports(output, config)
        image.write_identity(output, config, source)
        return result


def verify_only(output: Path, config: dict) -> dict:
    """Rerun the report gate on an existing build and rebind its SOF."""
    result = gate.verify_reports(output, config)
    image.write_identity(output, config, json.loads((output / "source.json").read_text()))
    return result


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--manifest", required=True, help="the project manifest, relative to the checkout")
    p.add_argument("--board", help="a board override of the manifest (default: the manifest's own board)")
    p.add_argument("--profile", help="a profile that extends the manifest (another project of the same board)")
    p.add_argument("--quartus", help="quartus_sh executable (or QUARTUS_SH/PATH)")
    p.add_argument("--output", type=Path, help="output directory below workdir (default workdir/quartus/<project>)")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--plan", action="store_true", help="validate and show the inputs without writing")
    mode.add_argument("--elaborate-only", action="store_true",
                      help="run only analysis and elaboration (no fit, timing or image); exit 77 without Quartus")
    mode.add_argument("--verify-only", action="store_true", help="rerun the report gate on an existing build")
    return p


def main(argv=None, root: Path = ROOT) -> int:
    args = parser().parse_args(argv)
    try:
        config = manifest.load(root, args.manifest, args.board, args.profile)
        output = output_dir(root, config, args.output, "-elaborate" if args.elaborate_only else "")
        parameters = " ".join(f"{k}={v}" for k, v in config["parameters"].items()) or "default parameters"
        print(f'{config["board"]} {config["top"]}: {config["device"]}, {config["clock_hz"]} Hz, {parameters}, '
              f'{len(config["sources"])} sources, {len(config["pins"])} pins; timeout '
              f'{config["timeout_seconds"]} s per invocation; output {output.relative_to(root)}' +
              ("; analysis and elaboration only" if args.elaborate_only else ""))
        if args.plan:
            return 0
        if args.verify_only:
            result = verify_only(output, config)
        else:
            executable = find_quartus(args.quartus)
            if not executable and args.elaborate_only:
                # Why: exit 77 is an explicit skip, never a pass: no Quartus means no evidence.
                print("SKIPPED: Quartus not found; pass --quartus or set QUARTUS_SH", file=sys.stderr)
                return SKIPPED
            if not executable:
                raise ValueError("Quartus not found; pass --quartus or set QUARTUS_SH")
            if args.elaborate_only:
                result = elaborate(root, output, config, executable)
                print(f'Quartus analysis and elaboration passed for {config["top"]}; '
                      f"{gate.warning_summary_text(result)}; evidence {output.relative_to(root)}")
                return 0
            result = compile_image(root, output, config, executable)
        resources = result["resources"]
        logic, m9k = resources["Total logic elements"], resources["M9Ks"]
        print(f"Full hardware gate passed; {gate.warning_summary_text(result)}; "
              f'LEs {logic["used"]}/{logic["available"]}, M9Ks {m9k["used"]}/{m9k["available"]}, '
              f'minimum slack {result["minimum_slack_ns"]} ns; evidence {output.relative_to(root)}/evidence.json')
        return 0
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
