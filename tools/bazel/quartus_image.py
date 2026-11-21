"""Run a project's Quartus build script and keep its image and gate evidence as Bazel outputs.

Spec: wiki/tools/bazel/spec.md (Board images). Called by the quartus_image rule
as a local action:

    quartus_image.py --script <script> --project <name> --out <dir> -- <script args>

The script builds in the checkout's workdir/quartus/<name>/ (it refuses any
other place) and runs its timing and warning gate; this copies the SOF, its
identity files, the gate's evidence and the logs and reports into <dir>, and
fails when the script passed but left no SOF.
"""
import argparse
import json
import os
from pathlib import Path
import pwd
import shutil
import signal
import subprocess
import sys

KEEP = ["*.json", "*.log", "output_files/*.sof", "output_files/*.json", "output_files/*.rpt",
        "output_files/*.tsv", "output_files/*.summary"]


def default_quartus(root: Path) -> str | None:
    """quartus_sh of the install that cfg/toolchain.json pins, when it is there."""
    pin = json.loads((root / "cfg" / "toolchain.json").read_text())["quartus"]
    home = pwd.getpwuid(os.getuid()).pw_dir
    candidate = Path(pin["install_dir"].replace("~", home, 1)) / "quartus" / "bin" / "quartus_sh"
    return str(candidate) if candidate.is_file() else None


def clear_stale_lock(lock: Path) -> None:
    """Remove the build lock of a script that was killed before it could remove it."""
    try:
        pid = int(lock.read_text().split()[0])
    except (FileNotFoundError, ValueError, IndexError):
        return
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        print(f"removing stale build lock {lock} (process {pid} is gone)", file=sys.stderr)
        lock.unlink(missing_ok=True)
    except PermissionError:
        pass


def run_script(command: list[str], root: Path, env: dict) -> int:
    """Run the build script; turn Bazel's SIGTERM into SIGINT so its cleanup runs."""
    # Why: the script removes its build lock only when it exits normally or on
    # KeyboardInterrupt; a SIGTERM sent to this wrapper alone would leave the lock.
    # A cancelled Bazel action kills the whole tree; clear_stale_lock covers that.
    child = subprocess.Popen(command, cwd=root, env=env, start_new_session=True)
    signal.signal(signal.SIGTERM, lambda *_: child.send_signal(signal.SIGINT))
    signal.signal(signal.SIGINT, lambda *_: child.send_signal(signal.SIGINT))
    return child.wait()


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
    build = root / "workdir" / "quartus" / args.project
    clear_stale_lock(build.with_suffix(".lock"))
    status = run_script([sys.executable, "-B", str(script), *args.args], root, env)
    out = args.out.absolute()
    out.mkdir(parents=True, exist_ok=True)
    for pattern in KEEP:
        for path in sorted(build.glob(pattern)):
            target = out / path.relative_to(build)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    sof = out / "output_files" / f"{args.project}.sof"
    if status == 0 and not sof.is_file():
        print(f"error: the build passed but left no {sof.relative_to(out)}; is --project right?", file=sys.stderr)
        return 1
    return status


if __name__ == "__main__":
    raise SystemExit(main())
