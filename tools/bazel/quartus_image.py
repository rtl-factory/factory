"""Run a project's Quartus build script and keep its image and gate evidence as Bazel outputs.

Spec: wiki/tools/bazel/spec.md (Board images). Called by the quartus_image rule
as a local action:

    quartus_image.py --script <script> --project <name> --out <dir> -- <script args>

The script builds in the checkout's workdir/quartus/<name>/ (it refuses any
other place) and runs its timing and warning gate; this copies the SOF, the
gate's evidence and the logs and reports into <dir>.
"""
import argparse
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import sys

KEEP = ["*.sof", "*.json", "*.log", "output_files/*.rpt", "output_files/*.tsv", "output_files/*.summary"]


def default_quartus(root: Path) -> str | None:
    """quartus_sh of the install that cfg/toolchain.json pins, when it is there."""
    pin = json.loads((root / "cfg" / "toolchain.json").read_text())["quartus"]
    home = pwd.getpwuid(os.getuid()).pw_dir
    candidate = Path(pin["install_dir"].replace("~", home, 1)) / "quartus" / "bin" / "quartus_sh"
    return str(candidate) if candidate.is_file() else None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--script", type=Path, required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("args", nargs="*")
    args = parser.parse_args(argv)
    script = args.script.resolve()
    # Why: the build scripts stage from and write into the checkout, never Bazel's tree.
    root = next(p for p in script.parents if (p / "MODULE.bazel").is_file())
    env = dict(os.environ)
    env.setdefault("QUARTUS_SH", default_quartus(root) or "quartus_sh")
    env.setdefault("LC_ALL", "C.UTF-8")
    status = subprocess.run([sys.executable, "-B", str(script), *args.args], cwd=root, env=env).returncode
    out = args.out.absolute()
    out.mkdir(parents=True, exist_ok=True)
    build = root / "workdir" / "quartus" / args.project
    for pattern in KEEP:
        for path in sorted(build.glob(pattern)):
            target = out / path.relative_to(build)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
