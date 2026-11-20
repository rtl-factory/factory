#!/usr/bin/env python3
"""Toolchain readiness check: reads cfg/toolchain.json and reports PASS, WARN
or FAIL per tool, with the fix. Never changes the host. Exit status is
nonzero on any FAIL; READY is printed when nothing fails.

    python3 tools/toolchain/check.py [--config cfg/toolchain.json] [--json]

Spec: wiki/tools/toolchain/spec.md.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "cfg" / "toolchain.json"
SPEC = "wiki/tools/toolchain/spec.md (Install, Quartus)"


def run(command: list[str], timeout: int = 120) -> tuple[int, str]:
    """Run one read-only tool query and return (status, combined output)."""
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=timeout,
                              env=dict(os.environ, LC_ALL=os.environ.get("LC_ALL", "C.UTF-8")))
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, f"{type(exc).__name__}: {exc}"
    return done.returncode, done.stdout + done.stderr


def find_quartus_sh(pin: dict, which=shutil.which) -> str | None:
    """quartus_sh from QUARTUS_SH, PATH, then the pinned install directory."""
    if os.environ.get("QUARTUS_SH"):
        return which(os.environ["QUARTUS_SH"])
    found = which("quartus_sh")
    if found:
        return found
    candidate = Path(pin["install_dir"]).expanduser() / "quartus" / "bin" / "quartus_sh"
    return str(candidate) if candidate.is_file() else None


def check_quartus(pin: dict, *, runner=run, which=shutil.which) -> dict:
    result = {"tool": "quartus", "want": f'{pin["version"]} with {", ".join(pin["families"])}'}
    executable = find_quartus_sh(pin, which)
    if not executable:
        hint = (f'set {pin["private_storage_env"]} and start a new cloud session, or install per {SPEC}'
                if not os.environ.get(pin["private_storage_env"]) else
                f'rerun .claude/hooks/session-start.sh, or install per {SPEC}')
        return dict(result, status="FAIL", found="quartus_sh not found", fix=hint)
    result["path"] = executable
    status, text = runner([executable, "--version"])
    match = re.search(r"Version\s+(\S+\s+Build\s+\d+)", text)
    found = match.group(1) if match else "unknown"
    if status != 0 or found != pin["version"]:
        return dict(result, status="FAIL", found=found,
                    fix=f'install Quartus Prime Lite {pin["version"]} per {SPEC}')
    status, text = runner([executable, "--tcl_eval", "get_family_list"])
    families = set(re.findall(r"\{([^}]*)\}", text))
    missing = [family for family in pin["families"] if family not in families]
    if status != 0 or missing:
        return dict(result, status="FAIL", found=f"{found}; missing {', '.join(missing) or 'family list'}",
                    fix=f"install the missing device files per {SPEC} (Cyclone IV: unzip the .qdz into the install directory)")
    return dict(result, status="PASS", found=f'{found} with {", ".join(pin["families"])}')


def check(config: dict, *, runner=run, which=shutil.which) -> list[dict]:
    return [check_quartus(config["quartus"], runner=runner, which=which)]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--json", action="store_true", help="print the results as JSON")
    args = parser.parse_args(argv)
    results = check(json.loads(args.config.read_text()))
    failed = any(r["status"] == "FAIL" for r in results)
    if args.json:
        print(json.dumps({"ready": not failed, "tools": results}, indent=2))
    else:
        for r in results:
            line = f'{r["status"]:4} {r["tool"]}: {r["found"]} (want {r["want"]})'
            print(line + (f'\n     fix: {r["fix"]}' if "fix" in r else ""))
        print("NOT READY" if failed else "READY")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
