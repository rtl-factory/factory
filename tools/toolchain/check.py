#!/usr/bin/env python3
"""Toolchain readiness check: reads cfg/toolchain.json and reports PASS, WARN
or FAIL per tool (Verilator, Bazel, OSS CAD Suite, Quartus), with the fix. Never changes the host. Exit status is
nonzero on any FAIL; READY is printed when nothing fails.

    python3 tools/toolchain/check.py [--config cfg/toolchain.json] [--json] [--tool NAME]

Spec: wiki/tools/toolchain/spec.md.
"""
from __future__ import annotations

import argparse
import hashlib
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
FORMAL_SPEC = "wiki/tools/toolchain/spec.md (Install, OSS CAD Suite)"
BAZEL_SPEC = "wiki/tools/toolchain/spec.md (Install, Bazelisk)"
HOOK = "rerun .claude/hooks/session-start.sh in a cloud session"


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


def find_verilator(suite_dir: str, which=shutil.which, is_file=os.path.isfile) -> str | None:
    """verilator from VERILATOR, else the OSS CAD Suite's bin/: where Bazel looks (tools/bazel/extensions.bzl).
    Never PATH, so a host Verilator cannot stand in for the pinned one."""
    if os.environ.get("VERILATOR"):
        return which(os.environ["VERILATOR"])
    candidate = Path(os.environ.get("OSS_CAD_SUITE") or suite_dir).expanduser() / "bin" / "verilator"
    return str(candidate) if is_file(str(candidate)) else None


def check_verilator(pin: dict, suite_dir: str, *, runner=run, which=shutil.which, is_file=os.path.isfile,
                    read=None) -> dict:
    """The pinned Verilator (the OSS CAD Suite's), with verilator_coverage beside it for
    coverage reports and no compiler cache baked into its verilated.mk."""
    want = f'{pin["version"]} rev {pin["revision"]}'
    result = {"tool": "verilator", "want": want}
    fix = f"install the pinned OSS CAD Suite per {FORMAL_SPEC} (it carries Verilator), or {HOOK}"
    executable = find_verilator(suite_dir, which, is_file)
    if not executable:
        return dict(result, status="FAIL", found="verilator not found", fix=fix)
    result["path"] = executable
    status, text = runner([executable, "--version"])
    match = re.search(r"Verilator\s+(\S+)(?:\s+\S+)*?\s+rev\s+(\S+)", text)
    found = f"{match.group(1)} rev {match.group(2)}" if match else (text.strip().splitlines() or ["unknown"])[0]
    if status != 0 or found != want:
        return dict(result, status="FAIL", found=found, fix=fix)
    if not is_file(str(Path(executable).resolve().parent / "verilator_coverage")):
        return dict(result, status="FAIL", found=f"{found}; missing verilator_coverage", fix=fix)
    # Why: configure bakes a ccache found on PATH into verilated.mk (OBJCACHE ?= ccache);
    # a build where it is missing then fails.
    makefile = Path(executable).resolve().parent.parent / "share" / "verilator" / "include" / "verilated.mk"
    cache = re.search(r"^OBJCACHE\s*\?=[ \t]*(\S*)", (read or read_stamp)(makefile) or "", re.M)
    if cache and cache.group(1):
        return dict(result, status="FAIL", found=f"{found}; {makefile} needs {cache.group(1)}", fix=fix)
    return dict(result, status="PASS", found=found)


def read_stamp(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def check_oss_cad_suite(pin: dict, *, runner=run, is_file=os.path.isfile, read=read_stamp) -> dict:
    """The pinned OSS CAD Suite: its release stamp, Yosys version, formal tools
    and the SystemVerilog front-end plugin. Only the install directory is used,
    never PATH, because the suite also ships its own Verilator and Python."""
    result = {"tool": "oss-cad-suite", "want": f'{pin["version"]} (Yosys {pin["yosys"]}, slang)'}
    root = Path(pin["install_dir"]).expanduser()
    fix = (f'install OSS CAD Suite {pin["version"]} per {FORMAL_SPEC}, '
           "or rerun .claude/hooks/session-start.sh in a cloud session")
    stamp = read(root / "VERSION")
    if stamp is None:
        return dict(result, status="FAIL", found=f"not installed in {root}", fix=fix)
    if stamp != pin["version"].replace("-", ""):
        return dict(result, status="FAIL", found=f"release {stamp}", fix=fix)
    missing = [tool for tool in ["yosys"] + pin["tools"] if not is_file(str(root / "bin" / tool))]
    if missing:
        return dict(result, status="FAIL", found=f"release {stamp}; missing {', '.join(missing)}", fix=fix)
    status, text = runner([str(root / "bin" / "yosys"), "-V"])
    match = re.search(r"Yosys\s+(\S+)", text)
    found = match.group(1) if match else "unknown"
    if status != 0 or found != pin["yosys"]:
        return dict(result, status="FAIL", found=f"Yosys {found}", fix=fix)
    for plugin in pin["plugins"]:
        status, text = runner([str(root / "bin" / "yosys"), "-q", "-m", plugin, "-p", f"help read_{plugin}"])
        if status != 0:
            return dict(result, status="FAIL", found=f"Yosys {found}; plugin {plugin} does not load", fix=fix)
    return dict(result, status="PASS", found=f'{pin["version"]} (Yosys {found}, {", ".join(pin["plugins"])})')


def sha256(path: str) -> str | None:
    try:
        with open(path, "rb") as handle:
            return hashlib.file_digest(handle, "sha256").hexdigest()
    except OSError:
        return None


def check_bazel(pin: dict, *, which=shutil.which, digest=sha256) -> dict:
    """A bazel command: PASS for the pinned Bazelisk, WARN for another one (a
    CI runner's setup-bazel), FAIL for none or a changed pinned binary."""
    result = {"tool": "bazel", "want": f'Bazelisk {pin["version"]}'}
    pinned = Path(pin["install_dir"]).expanduser() / "bazelisk"
    fix = f'install Bazelisk {pin["version"]} as {pinned} per {BAZEL_SPEC}, or {HOOK}'
    found = which("bazel") or which(str(pinned.with_name("bazel")))
    if not found:
        return dict(result, status="FAIL", found="bazel not found", fix=fix)
    result["path"] = found
    if os.path.realpath(found) != os.path.realpath(pinned):
        return dict(result, status="WARN", found=f"{found}, not the pinned Bazelisk", fix=fix)
    if digest(str(pinned)) != pin["sha256"]:
        return dict(result, status="FAIL", found=f"{pinned} does not match the pinned SHA-256", fix=fix)
    return dict(result, status="PASS", found=f'Bazelisk {pin["version"]} at {found}')


TOOLS = ("verilator", "bazel", "oss-cad-suite", "quartus")


def check(config: dict, *, runner=run, which=shutil.which, tools=TOOLS) -> list[dict]:
    results = []
    if "verilator" in tools:
        results.append(check_verilator(config["verilator"], config["oss_cad_suite"]["install_dir"],
                                      runner=runner, which=which))
    if "bazel" in tools:
        results.append(check_bazel(config["bazelisk"], which=which))
    if "oss-cad-suite" in tools:
        results.append(check_oss_cad_suite(config["oss_cad_suite"], runner=runner))
    if "quartus" in tools:
        results.append(check_quartus(config["quartus"], runner=runner, which=which))
    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--json", action="store_true", help="print the results as JSON")
    parser.add_argument("--tool", choices=TOOLS, action="append",
                        help="check only this tool (repeatable); default every tool")
    args = parser.parse_args(argv)
    results = check(json.loads(args.config.read_text()), tools=args.tool or TOOLS)
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
