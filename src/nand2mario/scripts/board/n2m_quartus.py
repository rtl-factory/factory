#!/usr/bin/env python3
"""Build the nand2mario board image in workdir with the native Linux Quartus tools.

    python3 src/nand2mario/scripts/board/n2m_quartus.py --plan
    python3 src/nand2mario/scripts/board/n2m_quartus.py --board de2_115 --library --plan
    python3 src/nand2mario/scripts/board/n2m_quartus.py [--quartus /path/to/quartus_sh]

The maintained board/profile manifests own the device, top, parameters,
sources, pins, settings and constraints; the generated project, databases,
reports and SOF stay under workdir/quartus/<project>/. After
compile and a separate timing audit, the report gate checks fit, timing at
every corner, clocks, constraints coverage, fitted RAM geometry and warning
ownership and writes evidence.json (wiki/src/nand2mario/scripts/board/spec.md).
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import shlex
import signal
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[4]
DEFAULT_CONFIG = "src/nand2mario/cfg/n2m_quartus.json"
BOARD_CONFIGS = {"de10_lite": DEFAULT_CONFIG, "de2_115": "src/nand2mario/cfg/n2m_de2_115_quartus.json"}
LIBRARY_PROFILE = "src/nand2mario/cfg/n2m_library_quartus_profile.json"
BOARD_DEVICES = {"de10_lite": "10M50DAF484C7G", "de2_115": "EP4CE115F29C7"}

REQUIRED_KEYS = {"project", "top", "family", "device", "clock_hz", "timeout_seconds", "timing_corners",
                 "parameters", "reference_port", "clock_module", "jtag_hierarchy", "jtag_unused_outputs",
                 "ram_requirements", "reviewed_warnings", "headers", "sources", "settings", "constraints",
                 "pins", "io_standards", "sdram_capture", "sdram_controller"}
SDRAM_CLOCK, SDRAM_CLOCK_PORT = "sdram_clock", "DRAM_CLK"
# Why: the common controller drives CKE as !reset and CS_n as reset, so the board
# reset synchronizer launches those two pins.
SDRAM_RESET_SOURCE, SDRAM_RESET_PINS = "n2a_reset_sync:u_reset|", {"DRAM_CKE", "DRAM_CS_N"}


def sdram_write_source(config: dict, source: str, pin: str) -> bool:
    """Whether a register may launch a write path to this SDRAM pin."""
    return (source.startswith(config["sdram_controller"]) or
            (pin in SDRAM_RESET_PINS and source.startswith(SDRAM_RESET_SOURCE)))
DEFAULT_IO_STANDARD = "3.3-V LVTTL"
CONNECTIVITY_DETAIL = ("Declared by entity but not connected by instance. "
                       "Logic that only feeds a dangling port will be removed.")
CONNECTIVITY_AGGREGATE = ("Warning (12241): 1 hierarchies have connectivity warnings - "
                          "see the Connectivity Checks report folder")
# Ref: every check_timing category of Quartus 25.1std; only the listed ones may be nonzero.
CHECK_TIMING_CATEGORIES = set(
    "no_clock multiple_clock pos_neg_clock_domain generated_clock virtual_clock no_input_delay "
    "no_output_delay partial_input_delay partial_output_delay io_min_max_delay_consistency reference_pin "
    "generated_io_delay latency_override partial_multicycle multicycle_consistency loops latches "
    "pll_cross_check uncertainty partial_min_max_delay clock_assignments_on_output_ports "
    "input_delay_assigned_to_clock".split())
# Why: the forwarded DRAM_CLK pin is its generated clock's target, not a data
# output requiring set_output_delay.
ALLOWED_CHECK_TIMING = {"no_clock": 1, "virtual_clock": 1, "no_output_delay": 1}


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
    if not path.resolve().is_relative_to(root / "workdir"):
        raise ValueError("output must remain below workdir")
    for component in (path, *path.parents):
        if component == root:
            break
        if component.is_symlink():
            raise ValueError(f"output symlink is not allowed: {component}")
    return path


def io_standard(config: dict, port: str):
    """A pin's generated IO_STANDARD; None when the settings fragment owns it."""
    standards = config["io_standards"]
    return standards.get(port, standards.get(port.split("[")[0], DEFAULT_IO_STANDARD))


def hdl_libraries(root: Path):
    """The library resolver, tools/bazel/hdl_libraries.py (wiki/tools/bazel/spec.md, Libraries)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("hdl_libraries", root / "tools" / "bazel" / "hdl_libraries.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def resolve_sources(config: dict, root: Path) -> dict:
    """The manifest with its libraries resolved into the ordered sources and headers."""
    resolver = hdl_libraries(root)
    config = resolver.resolve_target(config, resolver.load(root))
    config.pop("libraries", None)
    config.pop("flavors", None)
    return config


def load_config(root: Path, filename: str = DEFAULT_CONFIG) -> dict:
    return validate_config(resolve_sources(json.loads((root / filename).read_text()), root), root)


def validate_config(config: dict, root: Path) -> dict:
    if not isinstance(config, dict) or set(config) != REQUIRED_KEYS:
        raise ValueError(f"manifest keys must be exactly {sorted(REQUIRED_KEYS)}")
    for key in ("project", "top", "device", "clock_module", "reference_port"):
        if not re.fullmatch(r"[A-Za-z0-9_]+", config[key]):
            raise ValueError(f"invalid {key}")
    if type(config["timeout_seconds"]) is not int or config["timeout_seconds"] <= 0:
        raise ValueError("timeout_seconds must be a positive integer")
    if config["clock_hz"] != 25_000_000:
        raise ValueError("this board contract requires 25 MHz")
    if not config["timing_corners"] or any(not re.fullmatch(r"(Slow|Fast) \d+mV -?\d+C", corner)
                                           for corner in config["timing_corners"]):
        raise ValueError("timing_corners must name Quartus operating conditions")
    for name, value in config["parameters"].items():
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", name) or type(value) is not int or value < 0:
            raise ValueError(f"invalid top-level parameter: {name}")
    for name in config["sources"] + config["headers"] + [config["settings"], config["constraints"]]:
        if Path(name).is_absolute() or ".." in Path(name).parts or not re.fullmatch(r"[\w./-]+", name):
            raise ValueError(f"invalid source path: {name}")
        path = (root / name).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError(f"missing or out-of-tree source: {name}")
    if len(set(config["sources"])) != len(config["sources"]):
        raise ValueError("duplicate source")
    pins = config["pins"]
    if len(set(pins.values())) != len(pins):
        raise ValueError("duplicate physical pin")
    for port, pin in pins.items():
        if not re.fullmatch(r"\w+(?:\[\d+\])?", port) or not re.fullmatch(r"[A-Z]+\d+", pin):
            raise ValueError("invalid pin assignment")
    if config["reference_port"] not in pins or SDRAM_CLOCK_PORT not in pins:
        raise ValueError("the reference and SDRAM clocks need pins")
    for key in ("sdram_capture", "sdram_controller"):
        if not re.fullmatch(r"[\w:|.\[\]]+", config[key]):
            raise ValueError(f"invalid {key}")
    buses = {port.split("[")[0] for port in pins}
    for port, standard in config["io_standards"].items():
        if (port not in pins and port not in buses) or not (
                standard is None or re.fullmatch(r"[\w .-]+", standard)):
            raise ValueError(f"invalid I/O standard assignment: {port}")
    if not re.fullmatch(r"[\w:|]+", config["jtag_hierarchy"]) or not config["jtag_unused_outputs"]:
        raise ValueError("invalid JTAG connectivity contract")
    for requirement in config["ram_requirements"]:
        if (set(requirement) != {"name", "count", "depth", "width", "m9ks_each", "mode"} or
                any(type(requirement[k]) is not int or requirement[k] <= 0
                    for k in ("count", "depth", "width", "m9ks_each"))):
            raise ValueError(f"invalid RAM requirement: {requirement}")
    return config


def selected_config(root: Path, board: str, *, library: bool = False) -> dict:
    """Resolve one maintained profile; no arbitrary overlay or package inference."""
    config = load_config(root, BOARD_CONFIGS[board])
    if (config["device"] != BOARD_DEVICES[board] or config["project"] != f"n2m_{board}" or
            config["top"] != f"n2m_{board}"):
        raise ValueError("manifest does not match selected board")
    if library:
        if board != "de2_115":
            raise ValueError("the maintained library synthesis profile requires --board de2_115")
        profile = json.loads((root / LIBRARY_PROFILE).read_text())
        if (not isinstance(profile, dict) or set(profile) != {
                "schema_version", "project", "parameters", "ram_requirements", "reviewed_warnings"} or
                type(profile["schema_version"]) is not int or profile["schema_version"] != 1 or
                profile["project"] != config["project"] + "_library" or
                profile["parameters"] != {"CART_LIBRARY": 1} or
                type(profile["parameters"]["CART_LIBRARY"]) is not int or
                not isinstance(profile["ram_requirements"], list) or not profile["ram_requirements"] or
                not isinstance(profile["reviewed_warnings"], list) or
                any(not isinstance(warning, str) for warning in profile["reviewed_warnings"])):
            raise ValueError("invalid maintained library profile")
        config["project"] = profile["project"]
        config["parameters"].update(profile["parameters"])
        config["ram_requirements"].extend(profile["ram_requirements"])
        config["reviewed_warnings"].extend(profile["reviewed_warnings"])
    return validate_config(config, root)


def stage(root: Path, output: Path, config: dict) -> None:
    confined(root, output)
    output.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for name in config["sources"] + config["headers"] + [config["settings"], config["constraints"]]:
        data = (root / name).read_bytes()
        destination = output / name
        confined(root, destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        hashes[name] = hashlib.sha256(data).hexdigest()
    qsf = [f'set_global_assignment -name FAMILY "{config["family"]}"',
           f'set_global_assignment -name DEVICE {config["device"]}',
           f'set_global_assignment -name TOP_LEVEL_ENTITY {config["top"]}',
           'set_global_assignment -name VERILOG_MACRO "SYNTHESIS=1"',
           f'source "{config["settings"]}"',
           f'set_global_assignment -name SDC_FILE "{config["constraints"]}"']
    qsf += [f'set_global_assignment -name SYSTEMVERILOG_FILE "{name}"' for name in config["sources"]]
    qsf += [f'set_parameter -name {name} {value}' for name, value in config["parameters"].items()]
    for port, pin in config["pins"].items():
        qsf.append(f'set_location_assignment PIN_{pin} -to {port}')
        standard = io_standard(config, port)
        if standard:
            qsf.append(f'set_instance_assignment -name IO_STANDARD "{standard}" -to {port}')
    project = config["project"]
    (output / f"{project}.qsf").write_text("\n".join(qsf) + "\n")
    (output / f"{project}.qpf").write_text(f'PROJECT_REVISION = "{project}"\n')
    (output / "inputs.json").write_text(json.dumps({"config": config, "sha256": hashes}, indent=2) + "\n")
    dac_audit = ""
    if config["top"] == "n2m_de2_115":
        dac_audit = "report_timing -$check -to_clock [get_clocks vga_dac_clock] -npaths 1 -detail full_path -file output_files/dac_${label}_${check}.rpt"
    (output / "audit.tcl").write_text(f"""project_open {project}
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
        report_timing -$check -from [get_ports {{DRAM_DQ[*]}}] -npaths 1 -detail full_path -file output_files/sdram_read_${{label}}_${{check}}.rpt
        report_timing -$check -to_clock [get_clocks {SDRAM_CLOCK}] -npaths 1 -detail full_path -file output_files/sdram_write_${{label}}_${{check}}.rpt
        {dac_audit}
    }}
}}
project_close
""")


def fitted_resources(report: str, config: dict) -> dict:
    resources = {}
    for label in ("Total logic elements", "Total registers", "M9Ks", "Total PLLs",
                  "Embedded Multiplier 9-bit elements"):
        # Ref: the Quartus fitter's resource table marks "Total registers*" (registers in
        # LEs and I/O); the summary's unstarred row has no device total.
        match = re.search(r";\s*" + re.escape(label) + r"\*?\s*;\s*([\d,]+)\s*/\s*([\d,]+)", report)
        if not match:
            raise RuntimeError(f"missing fitted resource: {label}")
        used, available = (int(value.replace(",", "")) for value in match.groups())
        if used > available or (label == "Total PLLs" and used != 1):
            raise RuntimeError(f"invalid fitted resource: {label}={used}/{available}")
        resources[label] = {"used": used, "available": available, "remaining": available - used}
    header, memories = None, []
    for line in report.splitlines():
        cells = [value.strip() for value in line.split(";")[1:-1]]
        if cells[:3] == ["Name", "Type", "Mode"] and "M9Ks" in cells:
            header = cells
        elif header and len(cells) == len(header):
            row = dict(zip(header, cells))
            # Why: the fitted M9Ks column, not the requested Type, proves physical
            # block allocation.
            if row["M9Ks"].isdigit() and int(row["M9Ks"]) > 0:
                memories.append(row)
    return validate_resources(resources, memories, config)


def validate_resources(resources: dict, memories: list, config: dict) -> dict:
    accounted = 0
    for requirement in config["ram_requirements"]:
        matches = [row for row in memories if requirement["name"] in row["Name"]]
        if len(matches) != requirement["count"]:
            raise RuntimeError(f'expected {requirement["count"]} fitted RAM(s) named {requirement["name"]}, '
                               f"found {len(matches)}")
        for ram in matches:
            for column, key in (("Port A Depth", "depth"), ("Port A Width", "width"), ("M9Ks", "m9ks_each")):
                if int(ram[column]) != requirement[key]:
                    raise RuntimeError(f'fitted RAM geometry mismatch: {ram["Name"]} {column}={ram[column]}')
            if ram.get("Mode") != requirement["mode"] or ram.get("Clock Mode") != "Single Clock":
                raise RuntimeError(f'fitted RAM mode mismatch: {ram["Name"]} {ram.get("Mode")}/{ram.get("Clock Mode")}')
            accounted += int(ram["M9Ks"])
    if sum(int(row["M9Ks"]) for row in memories) != resources["M9Ks"]["used"]:
        raise RuntimeError("fitted RAM table does not account for every M9K")
    if accounted != resources["M9Ks"]["used"]:
        raise RuntimeError(f'RAM requirements account for {accounted} of {resources["M9Ks"]["used"]} fitted M9Ks')
    resources["ram_summary"] = [{key: row.get(key, "") for key in (
        "Name", "Type", "Mode", "Clock Mode", "Port A Depth", "Port A Width", "M9Ks",
        "Port B Depth", "Port B Width", "Port A RDW Mode", "Mixed Width RDW Mode")}
        for row in memories]
    return resources


def fitted_clocks(report: str, system_clock: str, config: dict) -> dict:
    clocks, header = {}, None
    for line in report.splitlines():
        cells = [value.strip() for value in line.split(";")[1:-1]]
        if cells[:2] == ["Clock Name", "Type"] and "Targets" in cells:
            header = cells
        elif header and len(cells) == len(header) and cells[1] in ("Base", "Generated", "Virtual"):
            row = dict(zip(header, cells))
            if row["Clock Name"] in clocks:
                raise RuntimeError("duplicate fitted clock")
            clocks[row["Clock Name"]] = row
    return validate_clocks(clocks, system_clock, config)


def validate_clocks(clocks: dict, system_clock: str, config: dict) -> dict:
    extra = {"vga_dac_clock"} if config["top"] == "n2m_de2_115" else set()
    if set(clocks) != {"reference", system_clock, "jtag_tck", SDRAM_CLOCK} | extra:
        raise RuntimeError(f"missing or unexpected fitted clocks: {sorted(clocks)}")
    expected = {
        "reference": {"Type": "Base", "Period": 20, "Rise": 0, "Fall": 10,
                      "Targets": config["reference_port"], "Master": "", "Source": ""},
        "jtag_tck": {"Type": "Base", "Period": 100, "Rise": 0, "Fall": 50,
                     "Targets": "altera_reserved_tck", "Master": "", "Source": ""},
        system_clock: {"Type": "Generated", "Period": 40, "Rise": 0, "Fall": 20,
                       "Divide by": 2, "Multiply by": 1, "Inverted": "false", "Master": "reference",
                       "Source": system_clock.rsplit("|", 1)[0] + "|inclk[0]", "Targets": system_clock},
        SDRAM_CLOCK: {"Type": "Generated", "Period": 40, "Rise": 20, "Fall": 40,
                      "Divide by": 1, "Multiply by": 1, "Inverted": "true", "Master": system_clock,
                      "Source": system_clock, "Targets": SDRAM_CLOCK_PORT},
    }
    if extra:
        expected["vga_dac_clock"] = dict(expected[SDRAM_CLOCK], Targets="VGA_CLK")
    for name, fields in expected.items():
        for key, value in fields.items():
            actual = clocks[name].get(key, "")
            if key == "Targets":
                actual = actual.strip("{} ")
            if isinstance(value, int):
                try:
                    actual = float(actual)
                except ValueError:
                    actual = None
            if actual != value:
                raise RuntimeError(f"fitted clock relationship mismatch: {name} {key}")
    return clocks


def connectivity_warnings(report: str, config: dict) -> list[dict]:
    """Validate every diagnostic row in the actual map connectivity tables:
    only the JTAG hub's listed unused outputs may warn."""
    expected = {(config["jtag_hierarchy"], port, "Output", "Warning", CONNECTIVITY_DETAIL)
                for port in config["jtag_unused_outputs"]}
    observed, hierarchy, state = [], None, None
    border = re.compile(r"\+-+\+-+\+-+\+-+\+")
    for line in report.splitlines():
        if hierarchy is None:
            if not line.startswith(";") or "Port Connectivity Checks:" not in line:
                continue
            title = re.fullmatch(r';\s*Port Connectivity Checks: "([^"]+)"\s*;\s*', line)
            if not title:
                raise RuntimeError("malformed connectivity table title")
            hierarchy, state = title[1], "header_border"
            continue
        if state in ("header_border", "row_border"):
            if not border.fullmatch(line):
                raise RuntimeError("malformed connectivity table border")
            state = "header" if state == "header_border" else "rows"
            continue
        if state == "rows" and border.fullmatch(line):
            hierarchy, state = None, None
            continue
        if not re.fullmatch(r';[^\r\n]*;\s*', line):
            raise RuntimeError("malformed connectivity row framing")
        cells = [cell.strip() for cell in line.strip().split(";")[1:-1]]
        if state == "header":
            if cells != ["Port", "Type", "Severity", "Details"]:
                raise RuntimeError("malformed connectivity table header")
            state = "row_border"
            continue
        if len(cells) != 4 or not all(cells):
            raise RuntimeError("malformed connectivity table row")
        port, kind, severity, details = cells
        if kind not in ("Input", "Output", "Inout") or severity not in ("Info", "Warning"):
            raise RuntimeError("unreviewed connectivity type/severity")
        if severity == "Warning":
            row = (hierarchy, port, kind, severity, details)
            if row not in expected or row in observed:
                raise RuntimeError("unreviewed or duplicate connectivity warning: " + repr(row))
            observed.append(row)
    if hierarchy is not None:
        raise RuntimeError("unterminated connectivity table")
    if observed and set(observed) != expected:
        raise RuntimeError("incomplete reviewed connectivity warning set")
    return [dict(zip(("hierarchy", "port", "type", "severity", "details"), row)) for row in observed]


def sdram_timing(reports: Path, config: dict, system_clock: str) -> dict:
    """Require actual external SDRAM read/write paths at every configured corner."""
    paths = []
    capture = re.compile(re.escape(config["sdram_capture"]) + r"\[(\d+)\]")
    for corner in config["timing_corners"]:
        label = corner.split()[0].lower() + "_" + corner.split()[-1].removesuffix("C")
        for direction in ("read", "write"):
            for kind in ("setup", "hold"):
                name = f"sdram_{direction}_{label}_{kind}.rpt"
                file = reports / name
                if not file.is_file() or file.is_symlink():
                    raise RuntimeError(f"missing SDRAM timing report: {name}")
                report = file.read_text(encoding="latin-1")
                summary = re.search(r"Report Timing: Found 1 " + kind +
                                    r" paths \(0 violated\)\.\s+Worst case slack is (-?\d+(?:\.\d+)?)", report)
                detail = re.findall(r"^Path #1: " + kind.title() +
                                    r" slack is (-?\d+(?:\.\d+)?)\s*$", report, re.M)
                rows = re.findall(r"^;\s*(From Node|To Node|Launch Clock|Latch Clock|Slack)\s*;"
                                  r"\s*([^;\n]+?)\s*;\s*$", report, re.M)
                values = dict(rows)
                if (not summary or len(detail) != 1 or len(rows) != 5 or len(values) != 5 or
                        not re.search(r"Delay Model:\s*" + re.escape(corner) + r" Model", report)):
                    raise RuntimeError(f"missing or malformed SDRAM timing path: {name}")
                try:
                    slack = float(values["Slack"])
                except ValueError as error:
                    raise RuntimeError(f"invalid SDRAM timing margin: {name}") from error
                if slack < 0 or slack != float(summary[1]) or slack != float(detail[0]):
                    raise RuntimeError(f"failing or inconsistent SDRAM timing margin: {name}")
                if direction == "read":
                    target = capture.fullmatch(values["To Node"])
                    valid = (values["From Node"] in {f"DRAM_DQ[{i}]" for i in range(16)} and
                             target is not None and int(target[1]) < 16 and
                             values["Launch Clock"] == SDRAM_CLOCK and values["Latch Clock"] == system_clock)
                else:
                    valid = (sdram_write_source(config, values["From Node"], values["To Node"]) and
                             values["To Node"] in {pin for pin in config["pins"]
                                                   if pin.startswith("DRAM_") and pin != SDRAM_CLOCK_PORT} and
                             values["Launch Clock"] == system_clock and values["Latch Clock"] == SDRAM_CLOCK)
                if not valid:
                    raise RuntimeError(f"unexpected SDRAM timing endpoints/clocks: {name} {values}")
                paths.append({"report": name, "corner": corner, "direction": direction,
                              "check": kind, "slack_ns": slack, **values})
    worst = {f"{direction}_{kind}": min(path["slack_ns"] for path in paths
                                      if path["direction"] == direction and path["check"] == kind)
             for direction in ("read", "write") for kind in ("setup", "hold")}
    return {"paths": paths, "worst_margin_ns": worst}


def dac_timing(reports: Path, config: dict, system_clock: str) -> list[dict]:
    """Require real constrained DAC paths, independently of a clock summary."""
    if config["top"] != "n2m_de2_115":
        return []
    paths = []
    for corner in config["timing_corners"]:
        label = corner.split()[0].lower() + "_" + corner.split()[-1].removesuffix("C")
        for kind in ("setup", "hold"):
            name = f"dac_{label}_{kind}.rpt"
            report = (reports / name).read_text(encoding="latin-1")
            summary = re.search(r"Report Timing: Found 1 " + kind +
                                r" paths \(0 violated\)\.\s+Worst case slack is (-?\d+(?:\.\d+)?)", report)
            detail = re.findall(r"^Path #1: " + kind.title() +
                                r" slack is (-?\d+(?:\.\d+)?)\s*$", report, re.M)
            rows = re.findall(r"^;\s*(From Node|To Node|Launch Clock|Latch Clock|Slack)\s*;"
                              r"\s*([^;\n]+?)\s*;\s*$", report, re.M)
            values = dict(rows)
            if (not summary or len(detail) != 1 or len(rows) != 5 or len(values) != 5 or
                    not re.search(r"Delay Model:\s*" + re.escape(corner) + r" Model", report)):
                raise RuntimeError(f"missing or malformed DAC timing path: {name}")
            slack = float(values["Slack"])
            if (slack < 0 or slack != float(summary[1]) or slack != float(detail[0]) or
                    values["Launch Clock"] != system_clock or values["Latch Clock"] != "vga_dac_clock" or
                    not re.fullmatch(r"VGA_(?:[RGB]\[[0-7]\]|[HV]S)", values["To Node"])):
                raise RuntimeError(f"failing DAC timing margin/endpoints: {name}")
            paths.append({"report": name, "corner": corner, "check": kind, "slack_ns": slack, **values})
    return paths


def fmax_summary(reports: Path, config: dict, system_clock: str) -> dict:
    """The system clock's restricted Fmax at every configured corner."""
    fmax = {}
    for corner in config["timing_corners"]:
        label = corner.split()[0].lower() + "_" + corner.split()[-1].removesuffix("C")
        file = reports / f"fmax_{label}.rpt"
        if not file.is_file():
            raise RuntimeError(f"missing Fmax report: {file.name}")
        text = file.read_text(encoding="latin-1")
        rows = re.findall(r";\s*(-?\d+(?:\.\d+)?) MHz\s*;\s*(-?\d+(?:\.\d+)?) MHz\s*;\s*([^;]+?)\s*;", text)
        match = [row for row in rows if row[2] == system_clock]
        if len(match) != 1:
            raise RuntimeError(f"missing system clock Fmax at {corner}")
        value, restricted, _ = match[0]
        if float(restricted) < 25.0:
            raise RuntimeError(f"restricted Fmax below 25 MHz at {corner}: {restricted}")
        fmax[corner] = {"fmax_mhz": float(value), "restricted_fmax_mhz": float(restricted)}
    return fmax


def verify_reports(output: Path, config: dict) -> dict:
    staged = json.loads((output / "inputs.json").read_text())
    if staged["config"] != config:
        raise RuntimeError("report configuration differs from staged inputs; fresh compile required")
    for name, digest in staged["sha256"].items():
        if hashlib.sha256((output / name).read_bytes()).hexdigest() != digest:
            raise RuntimeError(f"staged input changed after compilation: {name}")
    reports = output / "output_files"
    project = config["project"]
    for name in ("map.rpt", "fit.rpt", "sta.rpt", "sta.summary", "asm.rpt", "sof"):
        artifact = reports / f"{project}.{name}"
        if not artifact.is_file() or artifact.is_symlink() or artifact.stat().st_size == 0:
            raise RuntimeError(f"missing Quartus artifact: {name}")
    for name in ("unconstrained.rpt", "check_timing.rpt", "ignored.rpt", "setup.rpt", "hold.rpt", "clocks.tsv"):
        if not (reports / name).is_file():
            raise RuntimeError(f"missing timing audit: {name}")
    timing = (reports / f"{project}.sta.summary").read_text(encoding="latin-1")
    entries = re.findall(r"^Type\s*:\s*(.+?) Model (.+?) '([^']+)'\s*"
                         r"Slack\s*:\s*(-?\d+(?:\.\d+)?)\s*"
                         r"TNS\s*:\s*(-?\d+(?:\.\d+)?)", timing, re.M)
    if (not entries or len(entries) != len(re.findall(r"^Type\s*:", timing, re.M)) or
            any(float(slack) < 0 or float(tns) != 0 for _, _, _, slack, tns in entries)):
        raise RuntimeError("missing or failing all-corner timing slack")
    keys = {(corner, kind, clock) for corner, kind, clock, _, _ in entries}
    system_clocks = {clock for _, _, clock, _, _ in entries
                     if re.fullmatch(r"u_clock\|u_pll\|.+\|clk\[0\]", clock)}
    if len(keys) != len(entries) or len(system_clocks) != 1:
        raise RuntimeError("ambiguous timing summary")
    system_clock = next(iter(system_clocks))
    clock_relationships = fitted_clocks((reports / f"{project}.sta.rpt").read_text(encoding="latin-1"),
                                        system_clock, config)
    required = {(corner, kind, system_clock) for corner in config["timing_corners"]
                for kind in ("Setup", "Hold", "Minimum Pulse Width")}
    # Why: the JTAG scan domain holds the only asynchronous resets
    # (wiki/bkm/rtl.md, Rules: reset and clocks), so recovery and removal are timed on TCK.
    required |= {(corner, kind, "jtag_tck") for corner in config["timing_corners"]
                 for kind in ("Setup", "Hold", "Recovery", "Removal", "Minimum Pulse Width")}
    required |= {(corner, "Minimum Pulse Width", "reference") for corner in config["timing_corners"]}
    required |= {(corner, kind, SDRAM_CLOCK) for corner in config["timing_corners"] for kind in ("Setup", "Hold")}
    if not required <= keys:
        raise RuntimeError(f"incomplete timing summary: {sorted(required - keys)}")
    de2 = config["top"] == "n2m_de2_115"
    if de2:
        required |= {(corner, kind, "vga_dac_clock") for corner in config["timing_corners"] for kind in ("Setup", "Hold")}
        if not required <= keys:
            raise RuntimeError("incomplete DAC timing summary")
    allowed_check = dict(ALLOWED_CHECK_TIMING)
    if de2:
        allowed_check["no_output_delay"] = 2  # Why: the DE2-115 forwards two generated clocks to output ports, each a no_output_delay
    check = (reports / "check_timing.rpt").read_text(encoding="latin-1")
    counts = dict(re.findall(r";\s*([a-z_]+)\s*;\s*(\d+)\s*;", check))
    if (set(counts) != CHECK_TIMING_CATEGORIES or
            any(int(count) != allowed_check.get(name, 0) for name, count in counts.items()) or
            not re.search(r";\s*" + SDRAM_CLOCK_PORT + r"\s*;\s*No output delay was set on output port\. "
                          r"This port has clock assignments\.\s*;", check) or
            not re.search(r";\s*" + re.escape(config["clock_module"]) + r":u_clock\|altpll:u_pll\|altpll_[A-Za-z0-9]+:"
                          r"auto_generated\|pll_lock_sync\s*;\s*No clock feeds this register's clock port\.\s*;", check) or
            not re.search(r";\s*;\s*No virtual clock was found\.\s*;", check)):
        raise RuntimeError("unreviewed check_timing findings")
    if de2 and not re.search(r";\s*VGA_CLK\s*;\s*No output delay was set on output port\. This port has clock assignments\.\s*;", check):
        raise RuntimeError("missing forwarded DAC clock diagnostic")
    ignored = (reports / "ignored.rpt").read_text(encoding="latin-1")
    if not re.fullmatch(r"\s*-+\s*;\s*SDC Assignments\s*;\s*-+\s*"
                        r"No constraints were ignored\.\s*", ignored):
        raise RuntimeError("ignored or incomplete SDC constraints report")
    resources = fitted_resources((reports / f"{project}.fit.rpt").read_text(encoding="latin-1"), config)
    ucp = (reports / "unconstrained.rpt").read_text(encoding="latin-1")
    for category in UCP_CATEGORIES:
        values = re.search(r";\s*" + category + r"\s*;\s*(\d+)\s*;\s*(\d+)\s*;", ucp)
        if not values or values.groups() != ("0", "0"):
            raise RuntimeError(f"timing audit failed: {category}")
    clocks = dict(line.split("\t") for line in (reports / "clocks.tsv").read_text().splitlines())
    if (set(clocks) != set(clock_relationships) or
            any(float(clocks[name]) != float(row["Period"]) for name, row in clock_relationships.items())):
        raise RuntimeError("missing required 50/25 MHz clocks")
    fmax = fmax_summary(reports, config, system_clock)
    sdram = sdram_timing(reports, config, system_clock)
    warnings = []
    for log in ("compile.log", "audit.log"):
        for line in (output / log).read_text(encoding="latin-1").splitlines():
            line = line.strip()
            if re.match(r"(?:Critical )?Warning \(", line):
                warnings.append(line)
    connectivity = connectivity_warnings(
        (reports / f"{project}.map.rpt").read_text(encoding="latin-1"), config)
    aggregates = [line for line in warnings if line == CONNECTIVITY_AGGREGATE]
    if aggregates and (not connectivity or len(aggregates) != 1):
        raise RuntimeError("missing or duplicate reviewed connectivity evidence")
    if connectivity and not aggregates:
        raise RuntimeError("connectivity warnings without their aggregate diagnostic")
    validate_library_ram_warnings(warnings, config)  # Why: before deduplication, since duplicates are a failure.
    unreviewed = [line for line in warnings if line != CONNECTIVITY_AGGREGATE and not any(
        re.fullmatch(pattern, line) for pattern in config["reviewed_warnings"])]
    if unreviewed:
        raise RuntimeError("unreviewed Quartus warnings:\n" + "\n".join(unreviewed))
    result = {"schema": "n2m-fit-evidence/1", "project": project, "device": config["device"],
              "ignored_constraints": 0, "unconstrained": {name: 0 for name in UCP_CATEGORIES},
              "connectivity_warnings": connectivity,
              "minimum_slack_ns": min(float(entry[3]) for entry in entries),
              "clocks_ns": clocks,
              "timing": [{"corner": corner, "check": kind, "clock": clock, "slack_ns": float(slack), "tns_ns": float(tns)}
                         for corner, kind, clock, slack, tns in entries],
              "fmax": fmax,
              "sdram_timing": sdram,
              "dac_timing": dac_timing(reports, config, system_clock),
              "check_timing": counts,
              "clock_relationships": clock_relationships,
              "resources": resources,
              "reviewed_warnings": sorted(set(warnings))}
    validate_fit_evidence(result, config)
    (output / "evidence.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


UCP_CATEGORIES = ("Illegal Clocks", "Unconstrained Clocks", "Unconstrained Input Ports",
                  "Unconstrained Input Port Paths", "Unconstrained Output Ports", "Unconstrained Output Port Paths")
EVIDENCE_FIELDS = {"schema", "project", "device", "ignored_constraints", "unconstrained",
                   "connectivity_warnings", "minimum_slack_ns", "clocks_ns", "timing", "fmax",
                   "sdram_timing", "dac_timing", "check_timing", "clock_relationships", "resources", "reviewed_warnings"}


def require(condition: bool, detail: str) -> None:
    if not condition:
        raise ValueError("invalid transferred evidence: " + detail)


def nonnegative(value) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def validate_library_ram_warnings(warnings: list[str], config: dict) -> None:
    """Removal summaries authorize only the complete, unique library save-RAM set."""
    group = [w for w in warnings if re.match(r"Warning \((?:14284|14285|14320)\):", w)]
    if not group:
        return
    require(config["top"] == "n2m_de2_115" and config["project"] == "n2m_de2_115_library" and
            config["parameters"].get("CART_LIBRARY") == 1, "removed RAM outside library profile")
    headers = ["Warning (14284): Synthesized away the following node(s):",
               "Warning (14285): Synthesized away the following RAM node(s):"]
    patterns = [p for p in config["reviewed_warnings"] if p.startswith(r"Warning \(14320\):")]
    require(len(patterns) == 8 and len(group) == 10 and len(set(group)) == 10 and
            all(group.count(header) == 1 for header in headers), "incomplete/duplicate removed RAM warnings")
    details = [w for w in group if w not in headers]
    # Why: one pattern per node index, so an extra or a missing removed RAM node fails
    # instead of matching an arbitrary count; each pattern also binds the generated
    # type to its file.
    require(all(sum(re.fullmatch(pattern, w) is not None for w in details) == 1 for pattern in patterns) and
            all(sum(re.fullmatch(pattern, w) is not None for pattern in patterns) == 1 for w in details),
            "unreviewed removed RAM node")


def validate_fit_evidence(e: dict, config: dict) -> None:
    """Check the existing report-gate result, not authenticity or reviewer approval."""
    require(isinstance(e, dict) and set(e) == EVIDENCE_FIELDS, "schema fields")
    require(e["schema"] == "n2m-fit-evidence/1" and e["project"] == config["project"] and
            e["device"] == config["device"], "schema/board identity")
    require(type(e["ignored_constraints"]) is int and e["ignored_constraints"] == 0, "ignored constraints")
    require(e["unconstrained"] == dict.fromkeys(UCP_CATEGORIES, 0) and
            all(type(v) is int for v in e["unconstrained"].values()), "unconstrained paths")
    de2 = config["top"] == "n2m_de2_115"
    expected_checks = {name: str(ALLOWED_CHECK_TIMING.get(name, 0)) for name in CHECK_TIMING_CATEGORIES}
    if de2:
        expected_checks["no_output_delay"] = "2"
    require(e["check_timing"] == expected_checks, "check_timing findings")
    clocks = e["clock_relationships"]
    require(isinstance(clocks, dict) and all(isinstance(row, dict) and all(isinstance(v, str) for v in row.values())
                                            for row in clocks.values()), "clock relationship fields")
    system = [name for name in clocks if re.fullmatch(r"u_clock\|u_pll\|.+\|clk\[0\]", name)]
    require(len(system) == 1, "system clock")
    system = system[0]
    validate_clocks(clocks, system, config)
    require(set(e["clocks_ns"]) == set(clocks) and all(
        math.isfinite(float(e["clocks_ns"][name])) and float(e["clocks_ns"][name]) == float(row["Period"])
        for name, row in clocks.items()), "clock periods")
    corners = set(config["timing_corners"])
    required = {(c, k, system) for c in corners for k in ("Setup", "Hold", "Minimum Pulse Width")}
    required |= {(c, k, "jtag_tck") for c in corners for k in ("Setup", "Hold", "Recovery", "Removal", "Minimum Pulse Width")}
    required |= {(c, "Minimum Pulse Width", "reference") for c in corners}
    required |= {(c, k, clock) for c in corners for k in ("Setup", "Hold")
                 for clock in ({SDRAM_CLOCK, "vga_dac_clock"} if de2 else {SDRAM_CLOCK})}
    observed = set()
    require(isinstance(e["timing"], list) and bool(e["timing"]), "timing rows")
    for row in e["timing"]:
        require(set(row) == {"corner", "check", "clock", "slack_ns", "tns_ns"}, "timing row fields")
        key = (row["corner"], row["check"], row["clock"])
        require(key not in observed and key[0] in corners and key[2] in clocks and key[1] in
                {"Setup", "Hold", "Recovery", "Removal", "Minimum Pulse Width"}, "timing coverage")
        require(nonnegative(row["slack_ns"]) and type(row["tns_ns"]) in (int, float) and row["tns_ns"] == 0,
                "negative/nonfinite slack or nonzero TNS")
        observed.add(key)
    require(required <= observed and nonnegative(e["minimum_slack_ns"]) and
            e["minimum_slack_ns"] == min(row["slack_ns"] for row in e["timing"]), "timing minimum/coverage")
    require(set(e["fmax"]) == corners, "Fmax corners")
    for values in e["fmax"].values():
        require(set(values) == {"fmax_mhz", "restricted_fmax_mhz"} and all(nonnegative(v) for v in values.values()) and
                25 <= values["restricted_fmax_mhz"] <= values["fmax_mhz"], "Fmax below 25 MHz")
    resources = e["resources"]
    resource_names = {"Total logic elements", "Total registers", "M9Ks", "Total PLLs", "Embedded Multiplier 9-bit elements"}
    require(set(resources) == resource_names | {"ram_summary"}, "resource fields")
    for name in resource_names:
        values = resources[name]
        require(set(values) == {"used", "available", "remaining"} and all(type(v) is int and v >= 0 for v in values.values()) and
                values["available"] > 0 and values["used"] + values["remaining"] == values["available"], "resource capacity")
    require(resources["Total PLLs"]["used"] == 1, "PLL count")
    memory_fields = {"Name", "Type", "Mode", "Clock Mode", "Port A Depth", "Port A Width", "M9Ks",
                     "Port B Depth", "Port B Width", "Port A RDW Mode", "Mixed Width RDW Mode"}
    require(isinstance(resources["ram_summary"], list) and all(isinstance(row, dict) and set(row) == memory_fields and
            all(isinstance(v, str) for v in row.values()) for row in resources["ram_summary"]), "RAM summary fields")
    validate_resources(dict(resources), resources["ram_summary"], config)
    require(set(e["sdram_timing"]) == {"paths", "worst_margin_ns"}, "SDRAM fields")
    expected_paths = {(c, d, k) for c in corners for d in ("read", "write") for k in ("setup", "hold")}
    seen = set()
    require(isinstance(e["sdram_timing"]["paths"], list), "SDRAM path list")
    for row in e["sdram_timing"]["paths"]:
        key = (row["corner"], row["direction"], row["check"])
        require(key in expected_paths and key not in seen, "SDRAM coverage")
        validate_path_summary(row)
        read = row["direction"] == "read"
        require(row["Launch Clock"] == (SDRAM_CLOCK if read else system) and
                row["Latch Clock"] == (system if read else SDRAM_CLOCK), "SDRAM path clocks")
        if read:
            require(row["From Node"] in {f"DRAM_DQ[{i}]" for i in range(16)} and
                    row["To Node"] in {config["sdram_capture"] + f"[{i}]" for i in range(16)}, "SDRAM read endpoints")
        else:
            require(sdram_write_source(config, row["From Node"], row["To Node"]) and row["To Node"] in config["pins"] and
                    row["To Node"].startswith("DRAM_") and row["To Node"] != SDRAM_CLOCK_PORT, "SDRAM write endpoints")
        seen.add(key)
    require(seen == expected_paths, "missing SDRAM paths")
    worst = {f"{d}_{k}": min(p["slack_ns"] for p in e["sdram_timing"]["paths"] if p["direction"] == d and p["check"] == k)
             for d in ("read", "write") for k in ("setup", "hold")}
    require(e["sdram_timing"]["worst_margin_ns"] == worst, "SDRAM worst margins")
    seen = set()
    require(isinstance(e["dac_timing"], list), "DAC path list")
    for row in e["dac_timing"]:
        key = (row["corner"], row["check"])
        require(de2 and key not in seen and key[0] in corners and key[1] in ("setup", "hold"), "DAC coverage")
        validate_path_summary(row)
        require(row["Launch Clock"] == system and row["Latch Clock"] == "vga_dac_clock" and
                re.fullmatch(r"VGA_(?:[RGB]\[[0-7]\]|[HV]S)", row["To Node"]), "DAC path clocks/endpoints")
        seen.add(key)
    require(seen == ({(c, k) for c in corners for k in ("setup", "hold")} if de2 else set()), "missing DAC paths")
    warnings = e["reviewed_warnings"]
    require(isinstance(warnings, list) and all(isinstance(w, str) and (w == CONNECTIVITY_AGGREGATE or any(
        re.fullmatch(pattern, w) for pattern in config["reviewed_warnings"])) for w in warnings), "unreviewed warning")
    validate_library_ram_warnings(warnings, config)
    require(warnings == sorted(set(warnings)), "duplicate/unsorted warnings")
    expected_connectivity = [dict(hierarchy=config["jtag_hierarchy"], port=p, type="Output", severity="Warning", details=CONNECTIVITY_DETAIL)
                             for p in config["jtag_unused_outputs"]]
    connectivity = e["connectivity_warnings"]
    require(isinstance(connectivity, list) and
            (not connectivity or sorted(connectivity, key=lambda r: r["port"]) == sorted(expected_connectivity, key=lambda r: r["port"])),
            "connectivity warnings")
    require(bool(connectivity) == (CONNECTIVITY_AGGREGATE in warnings), "connectivity aggregate")


def validate_path_summary(row: dict) -> None:
    expected = {"report", "corner", "check", "slack_ns", "From Node", "To Node", "Launch Clock", "Latch Clock", "Slack"}
    require(set(row) == expected | ({"direction"} if "direction" in row else set()), "path fields")
    require(isinstance(row["Slack"], str) and nonnegative(row["slack_ns"]) and float(row["Slack"]) == row["slack_ns"] and
            all(isinstance(row[k], str) and row[k] for k in ("report", "From Node", "To Node", "Launch Clock", "Latch Clock")),
            "path summary/margin")


IDENTITY_FIELDS = {"schema", "project", "device", "source_sha", "quartus_version",
                   "sha256", "inputs_sha256", "evidence_sha256"}


def validate_image_identity(sof: Path, *, project: str, device: str, library: bool = False) -> dict:
    """Validate the existing SOF receipt and its two transferred evidence files."""
    receipt = sof.with_suffix(".json")
    if not receipt.is_file() or receipt.is_symlink():
        raise ValueError("SOF needs its source-bound .json identity receipt before programming")
    identity = json.loads(receipt.read_text())
    if (not isinstance(identity, dict) or set(identity) != IDENTITY_FIELDS or
            any(not isinstance(value, str) or not value.strip() for value in identity.values()) or
            identity["schema"] != "n2m-sof/1" or
            not re.fullmatch(r"[0-9a-f]{40}", identity["source_sha"]) or
            any(not re.fullmatch(r"[0-9a-f]{64}", identity[key])
                for key in ("sha256", "inputs_sha256", "evidence_sha256"))):
        raise ValueError("incomplete or malformed source-bound SOF identity receipt")
    if identity["project"] != project or identity["device"] != device:
        raise ValueError("SOF identity/hash does not match selected board")
    for path, key in ((sof, "sha256"), (sof.with_suffix(".inputs.json"), "inputs_sha256"),
                      (sof.with_suffix(".evidence.json"), "evidence_sha256")):
        if (not path.is_file() or path.is_symlink() or
                hashlib.sha256(path.read_bytes()).hexdigest() != identity[key]):
            raise ValueError(f"SOF identity/hash mismatch or missing transferred file: {path.name}")
    try:
        inputs = json.loads(sof.with_suffix(".inputs.json").read_text())
        evidence = json.loads(sof.with_suffix(".evidence.json").read_text())
        board = next(name for name, part in BOARD_DEVICES.items() if part == device)
        config = selected_config(ROOT, board, library=library)
        require(config["project"] == project, "selected cartridge profile mismatch")
        require(isinstance(inputs, dict) and set(inputs) == {"config", "sha256"}, "input manifest fields")
        # Why: the board tool must run with the reviewed profile, not an unrelated
        # checkout whose pins, constraints or warning policy differ from the fit.
        require(json.dumps(inputs["config"], sort_keys=True) == json.dumps(config, sort_keys=True), "input board profile mismatch")
        names = set(config["sources"] + config["headers"] + [config["settings"], config["constraints"]])
        require(isinstance(inputs["sha256"], dict) and set(inputs["sha256"]) == names and all(
            isinstance(v, str) and re.fullmatch(r"[0-9a-f]{64}", v) for v in inputs["sha256"].values()), "input source hashes")
        validate_fit_evidence(evidence, config)
    except (KeyError, TypeError, AttributeError, RuntimeError, StopIteration, OverflowError) as error:
        raise ValueError(f"malformed transferred inputs/evidence: {error}") from error
    return identity


def image_identity(output: Path, config: dict, *, source_sha: str, quartus_version: str,
                   library: bool = False) -> dict:
    """Bind the image to source and copy the hashed inputs/evidence beside it.

    Transfer these four same-stem files together; verification uses only this
    bounded package, so it does not depend on the build host's directory tree.
    """
    sof = output / "output_files" / f'{config["project"]}.sof'
    inputs = output / "inputs.json"
    evidence = output / "evidence.json"
    record = {"schema": "n2m-sof/1", "project": config["project"], "device": config["device"],
              "source_sha": source_sha, "quartus_version": quartus_version,
              "sha256": hashlib.sha256(sof.read_bytes()).hexdigest(),
              "inputs_sha256": hashlib.sha256(inputs.read_bytes()).hexdigest(),
              "evidence_sha256": hashlib.sha256(evidence.read_bytes()).hexdigest()}
    sof.with_suffix(".inputs.json").write_bytes(inputs.read_bytes())
    sof.with_suffix(".evidence.json").write_bytes(evidence.read_bytes())
    sof.with_suffix(".json").write_text(json.dumps(record, indent=2) + "\n")
    return validate_image_identity(sof, project=config["project"], device=config["device"], library=library)


def record_launcher_child(sidecar: Path, process, timeout: int) -> None:
    """Write the identity of the owned child process group to its own file."""
    sidecar.write_text(json.dumps({"launcher_child": {
        "pid": process.pid,
        "pgid": os.getpgid(process.pid),
        "started_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "timeout_seconds": timeout}}, indent=1) + "\n")


def run(command: list[str], output: Path, log: str, timeout: int) -> None:
    print("$ " + shlex.join(command), flush=True)
    environment = os.environ.copy()
    # Why: Quartus 25.1 TBB allocator interposition can fail before compilation;
    # this process-local setting does not change synthesis.
    environment["TBB_MALLOC_DISABLE_REPLACEMENT"] = "1"
    with (output / log).open("w") as stream:
        # Why: a new session lets a timeout kill the whole tool process group.
        process = subprocess.Popen(command, cwd=output, env=environment,
                                   stdout=stream, stderr=subprocess.STDOUT,
                                   start_new_session=True)
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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--board", choices=BOARD_CONFIGS, default="de10_lite")
    parser.add_argument("--library", action="store_true", help="select the maintained DE2 menu/library profile")
    parser.add_argument("--config", help="optional manifest; must exactly match the selected maintained profile")
    parser.add_argument("--quartus", help="quartus_sh executable (or QUARTUS_SH/PATH)")
    parser.add_argument("--plan", action="store_true", help="validate and show inputs without writing")
    parser.add_argument("--verify-only", action="store_true",
                        help="rerun the report gate on an existing build without compiling")
    args = parser.parse_args(argv)
    try:
        config = selected_config(ROOT, args.board, library=args.library)
        if args.config and load_config(ROOT, args.config) != config:
            raise ValueError("manifest differs from the maintained board profile; update/review that profile first")
        output = ROOT / "workdir" / "quartus" / config["project"]
        parameters = " ".join(f"{k}={v}" for k, v in config["parameters"].items()) or "default parameters"
        print(f'{config["top"]}: {config["device"]}, {config["clock_hz"]} Hz, {parameters}, '
              f'{len(config["sources"])} sources, {len(config["pins"])} pins; timeout '
              f'{config["timeout_seconds"]} s per invocation; output {output.relative_to(ROOT)}')
        if args.plan:
            return 0
        if args.verify_only:
            result = verify_reports(confined(ROOT, output), config)
        else:
            executable = shutil.which(args.quartus or os.environ.get("QUARTUS_SH", "quartus_sh"))
            if not executable:
                raise ValueError("Quartus not found; pass --quartus or set QUARTUS_SH")
            with build_lock(ROOT, output):
                confined(ROOT, output)
                dirty = subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=normal"], cwd=ROOT, text=True)
                if dirty:
                    raise ValueError("commit the source before a qualified full compile")
                source_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
                tool_version = subprocess.check_output([executable, "--version"], text=True).strip()
                if output.exists():
                    shutil.rmtree(output)
                stage(ROOT, output, config)
                (output / "source.json").write_text(json.dumps({"source_sha": source_sha, "quartus_version": tool_version}, indent=2) + "\n")
                run([executable, "--flow", "compile", config["project"]], output,
                    "compile.log", config["timeout_seconds"])
                sta = str(Path(executable).with_name("quartus_sta"))
                run([sta, "-t", "audit.tcl"], output, "audit.log", config["timeout_seconds"])
                result = verify_reports(output, config)
        source = json.loads((output / "source.json").read_text())
        image_identity(output, config, library=args.library, **source)
        logic = result["resources"]["Total logic elements"]
        m9k = result["resources"]["M9Ks"]
        print(f'Hardware gate passed; LEs {logic["used"]}/{logic["available"]}, M9Ks {m9k["used"]}/{m9k["available"]}, '
              f'minimum slack {result["minimum_slack_ns"]} ns; evidence {output.relative_to(ROOT)}/evidence.json')
        return 0
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
