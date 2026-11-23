"""Stage a Quartus project from a resolved config and run the Quartus tools on it.

The generated project, databases, logs and reports stay below workdir/; a lock
keeps two invocations off one project, and every tool runs in its own process
group so a timeout kills the fitter too. Spec: wiki/tools/quartus/spec.md.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys

@contextmanager
def build_lock(root: Path, output: Path):
    """Keep concurrent invocations from deleting a live project's database."""
    lock = confined(root, output.with_suffix(".lock"))
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("x") as stream:
        stream.write(f"{os.getpid()}\n")
    try:
        yield
    finally:
        lock.unlink()


def confined(root: Path, path: Path) -> Path:
    """The path, refused unless it is below workdir/ with no link component."""
    if not path.resolve().is_relative_to(root.resolve() / "workdir"):
        raise ValueError("output must remain below workdir")
    for component in (path, *path.parents):
        if component == root:
            break
        if component.is_symlink():
            raise ValueError(f"output symlink is not allowed: {component}")
    return path


def staged_files(config: dict) -> list[str]:
    return config["sources"] + config["headers"] + [config["settings"], config["constraints"]]


def qsf(config: dict) -> str:
    """The generated Quartus settings: device, top, macros, files, parameters and pins."""
    lines = [f'set_global_assignment -name FAMILY "{config["family"]}"',
             f'set_global_assignment -name DEVICE {config["device"]}',
             f'set_global_assignment -name TOP_LEVEL_ENTITY {config["top"]}',
             'set_global_assignment -name VERILOG_MACRO "SYNTHESIS=1"']
    lines += [f'set_global_assignment -name VERILOG_MACRO "{macro}"' for macro in config["verilog_macros"]]
    lines += [f'source "{config["settings"]}"',
              f'set_global_assignment -name SDC_FILE "{config["constraints"]}"']
    lines += [f'set_global_assignment -name SYSTEMVERILOG_FILE "{name}"' for name in config["sources"]]
    lines += [f"set_parameter -name {name} {value}" for name, value in config["parameters"].items()]
    for port, pin in config["pins"].items():
        lines.append(f"set_location_assignment PIN_{pin} -to {port}")
        standard = config["io_standards"][port]
        if standard:
            lines.append(f'set_instance_assignment -name IO_STANDARD "{standard}" -to {port}')
    return "\n".join(lines) + "\n"


def corner_label(corner: str) -> str:
    """The report-file label of a timing corner: "Slow 1200mV 85C" is slow_85."""
    return corner.split()[0].lower() + "_" + corner.split()[-1].removesuffix("C")


def audit_tcl(config: dict) -> str:
    """The timing audit: coverage reports, clocks, and per corner the Fmax and each path group."""
    groups = []
    for name, group in config["timing_paths"].items():
        select = (f'-from [get_ports {{{group["from_ports"]}}}]' if "from_ports" in group
                  else f'-to_clock [get_clocks {group["to_clock"]}]')
        groups.append(f"        report_timing -$check {select} -npaths 1 -detail full_path "
                      f"-file output_files/{name}_${{label}}_${{check}}.rpt")
    paths = "\n".join(groups)
    return f"""project_open {config["project"]}
create_timing_netlist
read_sdc
update_timing_netlist
report_ucp -file output_files/unconstrained.rpt
check_timing -file output_files/check_timing.rpt
report_sdc -ignored -file output_files/ignored.rpt
report_timing -setup -npaths 10 -detail full_path -file output_files/setup.rpt
report_timing -hold -npaths 10 -detail full_path -file output_files/hold.rpt
set clocks_file [open output_files/clocks.tsv w]
foreach_in_collection clock [get_clocks *] {{
    puts $clocks_file "[get_clock_info -name $clock]\t[get_clock_info -period $clock]"
}}
close $clocks_file
foreach_in_collection corner [get_available_operating_conditions] {{
    set_operating_conditions $corner
    update_timing_netlist
    set label "[get_operating_conditions_info -model $corner]_[get_operating_conditions_info -temperature $corner]"
    report_clock_fmax_summary -file output_files/fmax_${{label}}.rpt
    foreach check {{setup hold}} {{
{paths}
    }}
}}
project_close
"""


def stage(root: Path, output: Path, config: dict) -> None:
    """Copy every input below output and write the project, the input manifest and the audit."""
    confined(root, output)
    output.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for name in staged_files(config):
        data = (root / name).read_bytes()
        destination = confined(root, output / name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        hashes[name] = hashlib.sha256(data).hexdigest()
    project = config["project"]
    (output / f"{project}.qsf").write_text(qsf(config))
    (output / f"{project}.qpf").write_text(f'PROJECT_REVISION = "{project}"\n')
    (output / "inputs.json").write_text(json.dumps({"config": config, "sha256": hashes}, indent=2) + "\n")
    (output / "audit.tcl").write_text(audit_tcl(config))


def source_identity(root: Path, executable: str) -> dict:
    """The commit, whether the tree differs from it, and the Quartus version of a build."""
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=root, text=True)
    return {"source_sha": git("rev-parse", "HEAD").strip(),
            "source_dirty": bool(git("status", "--porcelain", "--untracked-files=normal").strip()),
            "quartus_version": subprocess.check_output([executable, "--version"], text=True).strip()}


def record_launcher_child(sidecar: Path, process, timeout: int) -> None:
    """Write the identity of the owned child process group to its own file."""
    sidecar.write_text(json.dumps({"launcher_child": {
        "pid": process.pid,
        "pgid": os.getpgid(process.pid),
        "started_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "timeout_seconds": timeout}}, indent=1) + "\n")


def run(command: list[str], output: Path, log: str, timeout: int) -> None:
    """Run one Quartus tool in output, its output to log; fail on a nonzero exit or the timeout."""
    print("$ " + shlex.join(command), flush=True)
    environment = os.environ.copy()
    # Ref: Quartus 25.1 TBB allocator interposition can fail before compilation;
    # this process-local setting does not change synthesis.
    environment["TBB_MALLOC_DISABLE_REPLACEMENT"] = "1"
    with (output / log).open("w") as stream:
        # Why: a new session lets a timeout kill the whole tool process group,
        # including fitter children of quartus_sh.
        process = subprocess.Popen(command, cwd=output, env=environment,
                                   stdout=stream, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        # Why: the child may already be writing to the log, so the launcher's identity
        # record goes to a sidecar it alone owns; a recording failure must not skip
        # the wait and cleanup of the launched child.
        try:
            record_launcher_child(output / f"{log}.owner.json", process, timeout)
        except OSError as error:  # pragma: no cover - environment specific
            print(f"warning: could not record launcher child identity: {error}",
                  file=sys.stderr, flush=True)
        try:
            status = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            raise
    if status:
        raise RuntimeError(f"Quartus exited {status}; see {output / log}")
