#!/usr/bin/env python3
"""Build the configured FPGA in workdir with the native Linux Quartus tools.

Spec: wiki/src/nand2doom/scripts/board/spec.md.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import shlex
import signal
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[4]
# Why: each "boards" entry overrides only board facts and swaps board-specific
# sources by "source_replacements", so the DE10-Lite top level stays the base
# and the shared source closure is written once.
BASE_BOARD = "de10_lite"
BOARDS = (BASE_BOARD, "de2_115")
DEFAULT_IO_STANDARD = "3.3-V LVTTL"
BOARD_DEFAULTS = {
    "reference_port": "MAX10_CLK1_50",
    "clock_module": "n2a_de10_lite_clock",
    # Why: DRAM_CLK carries the inverted system clock, so the gate treats it as a
    # generated clock target, not a data output.
    "forwarded_clocks": {"sdram_clock": "DRAM_CLK"},
    # Why: the settings fragment owns KEY0's Schmitt-trigger standard, so no default
    # standard is written for RESET_N.
    "io_standards": {"RESET_N": None},
}
BOARD_KEYS = {"project", "top", "family", "device", "settings", "constraints", "pins",
              "reviewed_warnings", "source_replacements", "ram_requirements",
              "timeout_seconds", *BOARD_DEFAULTS}


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


def select_board(config: dict, board: str) -> dict:
    """Resolve one board: the top level for the base board, else its overrides."""
    boards = config.pop("boards", {})
    if board == BASE_BOARD:
        resolved = config
    elif board in boards:
        overrides = dict(boards[board])
        unknown = set(overrides) - BOARD_KEYS
        if unknown:
            raise ValueError(f"unsupported board keys: {sorted(unknown)}")
        replacements = overrides.pop("source_replacements", {})
        if not set(replacements) <= set(config["sources"]):
            raise ValueError("board source replacement names no shared source")
        resolved = {**config, **overrides,
                    "sources": [replacements.get(name, name) for name in config["sources"]]}
    else:
        raise ValueError(f"unknown board: {board}")
    resolved["board"] = board
    for key, value in BOARD_DEFAULTS.items():
        resolved.setdefault(key, value)
    return resolved


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


def load_config(root: Path, filename: str, board: str = BASE_BOARD) -> dict:
    config = select_board(resolve_sources(json.loads((root / filename).read_text()), root), board)
    for key in ("project", "top", "device", "clock_module", "reference_port"):
        if not re.fullmatch(r"[A-Za-z0-9_]+", config[key]):
            raise ValueError(f"invalid {key}")
    if type(config["timeout_seconds"]) is not int or config["timeout_seconds"] <= 0:
        raise ValueError("timeout_seconds must be a positive integer")
    if config["clock_hz"] != 25_000_000:
        raise ValueError("this board contract requires 25 MHz")
    for name in config["sources"] + config["headers"] + [config["settings"], config["constraints"]]:
        if Path(name).is_absolute() or ".." in Path(name).parts or not re.fullmatch(r"[\w./-]+", name):
            raise ValueError(f"invalid source path: {name}")
        path = (root / name).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError(f"missing or out-of-tree source: {name}")
    pins = config["pins"]
    if len(set(pins.values())) != len(pins):
        raise ValueError("duplicate physical pin")
    for port, pin in pins.items():
        if not re.fullmatch(r"\w+(?:\[\d+\])?", port) or not re.fullmatch(r"[A-Z]+\d+", pin):
            raise ValueError("invalid pin assignment")
    buses = {port.split("[")[0] for port in pins}
    for port, standard in config["io_standards"].items():
        if (port not in pins and port not in buses) or not (
                standard is None or re.fullmatch(r"[\w .-]+", standard)):
            raise ValueError(f"invalid I/O standard assignment: {port}")
    clocks = config["forwarded_clocks"]
    if ("sdram_clock" not in clocks or config["reference_port"] not in pins or
            any(not re.fullmatch(r"[a-z_]+", name) or port not in pins
                for name, port in clocks.items())):
        raise ValueError("invalid board clock wiring")
    warnings = config["reviewed_warnings"]
    if (set(warnings) != {"constant_outputs", "pll_output", "electrical_pins", "logiclock_count"}
            or not warnings["electrical_pins"]
            or not set(warnings["constant_outputs"]) <= set(pins)
            or set(warnings["constant_outputs"].values()) - {"VCC", "GND"}
            or warnings["pll_output"] not in clocks.values()
            or type(warnings["logiclock_count"]) is not int or warnings["logiclock_count"] != 1):
        raise ValueError("invalid reviewed warning contract")
    settings = (root / config["settings"]).read_text()
    for pin, standard in warnings["electrical_pins"].items():
        if pin not in pins or not isinstance(standard, str):
            raise ValueError("invalid electrical advisory pin")
        assigned = io_standard(config, pin)
        if assigned is None:
            matches = re.findall(r'set_instance_assignment -name IO_STANDARD "([^"\n]+)" -to '
                                 + re.escape(pin) + r"$", settings, re.M)
            assigned = matches[0] if len(matches) == 1 else None
        if standard != assigned:
            raise ValueError(f"electrical advisory standard disagrees with pin assignment: {pin}")
    objects = config["reviewed_unused_objects"]
    if (any(set(item) != {"source", "object"} or item["source"] not in config["sources"]
            or not re.fullmatch(r"\w+", item["object"]) for item in objects)
            or len({(item["source"], item["object"]) for item in objects}) != len(objects)):
        raise ValueError("invalid reviewed unused object contract")
    limits = config["reviewed_generated_ip_limits"]
    if (set(limits) != {"altpll", "altsyncram", "decode", "mux"}
            or any(type(limit) is not int or limit <= 0 for limit in limits.values())):
        raise ValueError("invalid generated IP warning limits")
    return config


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
           # Why: Quartus drops the assertions anyway, so disabling them omits their unused
           # history logic; simulation must keep them, so the opt-out lives only here.
           'set_global_assignment -name VERILOG_MACRO "DISABLE_ASSERTIONS=1"',
           f'source "{config["settings"]}"',
           f'set_global_assignment -name SDC_FILE "{config["constraints"]}"']
    qsf += [f'set_global_assignment -name SYSTEMVERILOG_FILE "{name}"'
            for name in config["sources"]]
    for port, pin in config["pins"].items():
        qsf.append(f'set_location_assignment PIN_{pin} -to {port}')
        standard = io_standard(config, port)
        if standard:
            qsf.append(f'set_instance_assignment -name IO_STANDARD "{standard}" -to {port}')
    project = config["project"]
    (output / f"{project}.qsf").write_text("\n".join(qsf) + "\n")
    (output / f"{project}.qpf").write_text(f'PROJECT_REVISION = "{project}"\n')
    (output / "inputs.json").write_text(json.dumps({"config": config, "sha256": hashes}, indent=2) + "\n")
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
    foreach check {{setup hold}} {{
        report_timing -$check -from [get_ports {{DRAM_DQ[*]}}] -npaths 1 -detail full_path -file output_files/sdram_read_${{label}}_${{check}}.rpt
        report_timing -$check -to_clock [get_clocks sdram_clock] -npaths 1 -detail full_path -file output_files/sdram_write_${{label}}_${{check}}.rpt
    }}
}}
project_close
""")


def fitted_resources(report: str, config: dict) -> dict:
    resources = {}
    for label in ("Total logic elements", "M9Ks", "Total PLLs"):
        match = re.search(r";\s*" + label + r"\s*;\s*([\d,]+)\s*/\s*([\d,]+)", report)
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
            # Ref: in the Quartus fitter RAM summary, Type is the requested implementation
            # (normally AUTO). The fitted M9Ks column, not that request, proves physical
            # block allocation.
            if row["M9Ks"].isdigit() and int(row["M9Ks"]) > 0:
                memories.append(row)
    for requirement in config["ram_requirements"]:
        for index in range(requirement["count"]):
            name = (requirement["name"] if "name" in requirement else
                    f'{requirement["prefix"]}[{index}].{requirement["array"]}')
            matches = [row for row in memories if name in row["Name"]]
            if len(matches) != 1:
                raise RuntimeError(f"missing or ambiguous fitted RAM: {name}")
            ram = matches[0]
            for column, key in (("Port A Depth", "depth"), ("Port A Width", "width"),
                                ("M9Ks", "m9ks_each")):
                if int(ram[column]) != requirement[key]:
                    raise RuntimeError(f"fitted RAM geometry mismatch: {name} {column}")
            # Ref: Quartus reports unused RDW fields as Don't care/New data, so only the
            # port relationship actually used by this array is contractual. Every
            # requirement states its port relationship explicitly; the shared
            # n2a_sync_ram/n2a_sdp_ram contracts fix the expected mode.
            single = requirement["single_port"]
            mode = "Single Port" if single else "Simple Dual Port"
            rdw = "Port A RDW Mode" if single else "Mixed Width RDW Mode"
            if (ram.get("Mode") != mode or ram.get("Clock Mode") != "Single Clock" or
                    ram.get(rdw) != "Old data"):
                raise RuntimeError(f"fitted RAM timing/mode mismatch: {name}")
            for column, key in (("Port B Depth", "depth"), ("Port B Width", "width")):
                expected = "--" if single else str(requirement[key])
                if ram.get(column) != expected:
                    raise RuntimeError(f"fitted RAM geometry mismatch: {name} {column}")
    if sum(int(row["M9Ks"]) for row in memories) != resources["M9Ks"]["used"]:
        raise RuntimeError("fitted RAM table does not account for every M9K")
    resources["ram_summary"] = [{key: row[key] for key in (
        "Name", "Type", "Mode", "Clock Mode", "Port A Depth", "Port A Width", "M9Ks",
        "Port B Depth", "Port B Width", "Port A RDW Mode", "Mixed Width RDW Mode")}
        for row in memories]
    return resources


def sdram_timing(reports: Path, config: dict, system_clock: str) -> dict:
    """Require actual external read/write paths at every configured corner."""
    paths = []
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
                    target = re.fullmatch(r"n2d_memory_controller:u_memory\|n2a_sdram_ctrl:u_sdram\|"
                                          r"regs_q\.read_data\[(\d+)\]", values["To Node"])
                    valid = (values["From Node"] in {f"DRAM_DQ[{i}]" for i in range(16)} and
                             target is not None and int(target[1]) < 128 and
                             values["Launch Clock"] == "sdram_clock" and values["Latch Clock"] == system_clock)
                else:
                    # Why: the common controller drives CKE as !reset and CS_n as reset, so the
                    # board reset synchronizer launches those two pins.
                    valid = ((values["From Node"].startswith("n2d_memory_controller:u_memory|") or
                              (values["To Node"] in {"DRAM_CKE", "DRAM_CS_N"} and
                               values["From Node"].startswith("n2a_reset_sync:u_reset|"))) and
                             values["To Node"] in {pin for pin in config["pins"]
                                                   if pin.startswith("DRAM_") and pin != "DRAM_CLK"} and
                             values["Launch Clock"] == system_clock and values["Latch Clock"] == "sdram_clock")
                if not valid:
                    raise RuntimeError(f"unexpected SDRAM timing endpoints/clocks: {name}")
                paths.append({"report": name, "corner": corner, "direction": direction,
                              "check": kind, "slack_ns": slack, **values})
    worst = {f"{direction}_{kind}": min(path["slack_ns"] for path in paths
                                      if path["direction"] == direction and path["check"] == kind)
             for direction in ("read", "write") for kind in ("setup", "hold")}
    return {"paths": paths, "worst_margin_ns": worst}


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
    forwarded = config["forwarded_clocks"]
    if set(clocks) != {"reference", system_clock, "jtag_tck", *forwarded}:
        raise RuntimeError("missing or unexpected fitted clocks")
    expected = {
        "reference": {"Type": "Base", "Period": 20, "Rise": 0, "Fall": 10,
                      "Targets": config["reference_port"], "Master": "", "Source": ""},
        # Why: the keyboard endpoint in n2d_system sits behind the Virtual JTAG hub,
        # whose TCK the SDC constrains, so the gate expects this clock too.
        "jtag_tck": {"Type": "Base", "Period": 100, "Rise": 0, "Fall": 50,
                     "Targets": "altera_reserved_tck", "Master": "", "Source": ""},
        system_clock: {"Type": "Generated", "Period": 40, "Rise": 0, "Fall": 20,
                       "Divide by": 2, "Multiply by": 1, "Inverted": "false", "Master": "reference",
                       "Source": system_clock.rsplit("|", 1)[0] + "|inclk[0]", "Targets": system_clock},
    }
    for name, port in forwarded.items():
        expected[name] = {"Type": "Generated", "Period": 40, "Rise": 20, "Fall": 40,
                          "Divide by": 1, "Multiply by": 1, "Inverted": "true", "Master": system_clock,
                          "Source": system_clock, "Targets": port}
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
    """Validate every diagnostic row in the actual map connectivity tables."""
    contract = config["reviewed_connectivity"]
    detail = ("Declared by entity but not connected by instance. "
              "Logic that only feeds a dangling port will be removed.")
    expected = {(contract["hierarchy"], port, "Output", "Warning", detail)
                for port in contract["unused_outputs"]}
    observed, hierarchy, state = [], None, None
    border = re.compile(r"\+-+\+-+\+-+\+-+\+")
    for line in report.splitlines():
        if hierarchy is None:
            # Ref: TOC references are prose; table titles are semicolon-framed.
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
            # Why: only the actual closing border ends a table, so a malformed scalar row
            # cannot hide later warnings by clearing its owner.
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
    return [dict(zip(("hierarchy", "port", "type", "severity", "details"), row))
            for row in observed]


def warning_rules(config: dict, *, elaboration: bool = False) -> list[dict]:
    """Exact semantic identities and occurrence bounds, independent of line numbers."""
    rules = []

    def add(code, category, identity, pattern, count=1):
        rules.append({"code": code, "category": category, "identity": identity,
                      "pattern": rf"Warning \({code}\): " + pattern,
                      "minimum": 0 if elaboration else count, "maximum": count})

    for item in config["reviewed_unused_objects"]:
        source, obj = item["source"], item["object"]
        add(10036, "unused_rtl", item,
            r"Verilog HDL or VHDL warning at " + re.escape(Path(source).name) +
            r'\((?P<line>[0-9]+)\): object "' + re.escape(obj) +
            r'" assigned a value but never read File: [^\r\n]+/' + re.escape(source) + r" Line: (?P=line)")
    add(12241, "vendor_connectivity", {"hierarchy": config["reviewed_connectivity"]["hierarchy"]},
        r"1 hierarchies have connectivity warnings - see the Connectivity Checks report folder")
    contract = config["reviewed_warnings"]
    add(292013, "tool_license", {"feature": "LogicLock"},
        re.escape("Feature LogicLock is only available with a valid subscription license. "
                  "You can purchase a software subscription to gain full access to this feature."),
        1 if elaboration else contract["logiclock_count"])
    if elaboration:
        return rules
    if contract["constant_outputs"]:
        add(13024, "constant_outputs", {"summary": "VCC or GND"}, "Output pins are stuck at VCC or GND")
    source = f'src/nand2doom/fpga/{config["top"].removeprefix("n2d_")}/{config["top"]}.sv'
    for pin, value in contract["constant_outputs"].items():
        add(13410, "constant_outputs", {"source": source, "pin": pin, "value": value},
            re.escape(f'Pin "{pin}" is stuck at {value}') +
            r" File: [^\r\n]+/" + re.escape(source) + r" Line: [0-9]+")
    add(15064, "board_clock_routing", {"pll": config["clock_module"], "output": "clk[0]",
                                       "pin": contract["pll_output"]},
        r'PLL "' + re.escape(config["clock_module"]) +
        r':u_clock\|altpll:u_pll\|(?P<generated>altpll_[A-Za-z0-9]+):auto_generated\|pll1" '
        r'output port clk\[0\] feeds output pin "' + re.escape(contract["pll_output"]) +
        re.escape('~output" via non-dedicated routing -- jitter performance depends on switching rate '
                  'of other design elements. Use PLL dedicated clock outputs to ensure jitter performance') +
        r' File: [^\r\n]+/db/(?P=generated)\.tdf Line: [0-9]+')
    count = len(contract["electrical_pins"])
    add(169177, "electrical_advisory", {"family": config["family"], "pins": count},
        re.escape(f"{count} pins must meet Intel FPGA requirements for 3.3-, 3.0-, and 2.5-V interfaces. "
                  f"For more information, refer to AN 447: Interfacing {config['family']} Devices "
                  "with 3.3/3.0/2.5-V LVTTL/LVCMOS I/O Systems."))
    return rules


def generated_warning(line: str, map_report: Path, config: dict) -> dict | None:
    """Bind optional 12125 discovery to an actual generated, read design file."""
    match = re.fullmatch(
        r"Warning \(12125\): Using design file (?P<file>db/(?P<entity>(?P<family>altpll|altsyncram|decode|mux)_"
        r"[A-Za-z0-9]+)\.tdf), which is not specified as a design file for the current project, "
        r"but contains definitions for 1 design units and 1 entities in project", line)
    if not match:
        return None
    filename, entity, family = match["file"], match["entity"], match["family"]
    path = map_report.parent.parent / filename
    rows = [[cell.strip() for cell in row.split(";")[1:-1]]
            for row in map_report.read_text(encoding="latin-1").splitlines() if row.startswith(";")]
    rows = [row for row in rows if row and row[0] == filename]
    if (len(rows) != 1 or len(rows[0]) != 5 or rows[0][1:3] != ["yes", "Auto-Generated Megafunction"]
            or not rows[0][3].endswith("/" + filename) or rows[0][4]
            or path.is_symlink() or path.parent.is_symlink() or not path.is_file()):
        raise RuntimeError(f"unreviewed generated IP evidence: {filename}")
    text = path.read_text(encoding="latin-1")
    primitive = {"decode": "lpm_decode", "mux": "lpm_mux"}.get(family, family)
    if (not text.startswith("--" + primitive + " ") or
            re.findall(r"^SUBDESIGN\s+(\w+)\s*$", text, re.M) != [entity]):
        raise RuntimeError(f"unreviewed generated IP identity: {filename}")
    return {"code": 12125, "category": "generated_ip", "identity": {"file": filename, "family": family},
            "minimum": 0, "maximum": 1, "count": 1}


def standard_key(standard: str) -> str:
    """An I/O-standard name for comparison: Quartus reports vary its case and spacing."""
    return " ".join(standard.split()).casefold()


def electrical_warnings(report: str, config: dict) -> list[dict]:
    """Require the complete AN447 child set with exact ports, pads and standards."""
    source = f'src/nand2doom/fpga/{config["top"].removeprefix("n2d_")}/{config["top"]}.sv'
    # Why: Quartus may print a standard in other case or spacing, so standards match
    # by standard_key(); evidence keeps the configured spelling.
    canonical = {(pin, config["pins"][pin], standard_key(standard)): standard for pin, standard
                 in config["reviewed_warnings"]["electrical_pins"].items()}
    expected = set(canonical)
    summary = next(rule["pattern"] for rule in warning_rules(config) if rule["code"] == 169177)
    summaries, observed, active = 0, [], False
    for raw in report.splitlines():
        line = raw.strip()
        if re.search(r"\(169177\)", line):
            if not re.fullmatch(summary, line):
                raise RuntimeError("unreviewed electrical advisory summary")
            summaries += 1
            active = True
        elif re.search(r"\(169178\)", line):
            row = re.fullmatch(r"Info \(169178\): Pin (\w+(?:\[\d+\])?) uses I/O standard (.+?) at ([A-Z]+\d+)"
                               r" File: [^\r\n]+/" + re.escape(source) + r" Line: [0-9]+", line)
            if not active or not row:
                raise RuntimeError("malformed or unowned electrical advisory detail")
            pin, standard, pad = row.groups()
            item = (pin, pad, standard_key(standard))
            if item not in expected or item in observed:
                raise RuntimeError(f"unreviewed or duplicate electrical advisory pin: {item}")
            observed.append(item)
        else:
            active = False
    if summaries != 1 or set(observed) != expected:
        raise RuntimeError("missing or incomplete electrical advisory pin set")
    return [{"pin": pin, "pad": pad, "io_standard": canonical[(pin, pad, key)], "source": source}
            for pin, pad, key in observed]


def reviewed_warnings(warnings: list[str], map_report: Path, config: dict, *,
                      elaboration: bool = False, fit_report: Path | None = None) -> dict:
    """Fail closed on identity, multiplicity, completeness and supporting evidence."""
    connectivity = connectivity_warnings(map_report.read_text(encoding="latin-1"), config)
    aggregate = "Warning (12241): 1 hierarchies have connectivity warnings - see the Connectivity Checks report folder"
    aggregates = [line for line in warnings if line == aggregate]
    if aggregates and (not connectivity or len(aggregates) != 1):
        raise RuntimeError("missing or duplicate reviewed connectivity evidence")
    if connectivity and not aggregates:
        raise RuntimeError("connectivity warnings without their aggregate diagnostic")
    rules = warning_rules(config, elaboration=elaboration)
    for rule in rules:
        rule["count"] = 0
    generated = {}
    for line in warnings:
        matches = [rule for rule in rules if re.fullmatch(rule["pattern"], line)]
        if len(matches) == 1:
            matches[0]["count"] += 1
        else:
            rule = generated_warning(line, map_report, config)
            if rule is None:
                raise RuntimeError("unreviewed Quartus warnings:\n" + line)
            filename = rule["identity"]["file"]
            if filename in generated:
                raise RuntimeError(f"duplicate generated IP warning: {filename}")
            generated[filename] = rule
    for rule in rules:
        if not rule["minimum"] <= rule["count"] <= rule["maximum"]:
            raise RuntimeError(f"Quartus warning occurrence mismatch: {rule['code']} {rule['identity']}: "
                               f"expected {rule['minimum']}..{rule['maximum']}, observed {rule['count']}")
    for family, limit in config["reviewed_generated_ip_limits"].items():
        count = sum(rule["identity"]["family"] == family for rule in generated.values())
        if count > limit:
            raise RuntimeError(f"too many generated IP warnings: {family} {count} > {limit}")
    electrical = []
    if not elaboration:
        if fit_report is None:
            raise RuntimeError("missing electrical advisory fitter evidence")
        electrical = electrical_warnings(fit_report.read_text(encoding="latin-1"), config)
    entries = [{key: value for key, value in rule.items() if key != "pattern"}
               for rule in [*rules, *generated.values()]]
    by_code, by_category = {}, {}
    for rule in entries:
        code = str(rule["code"])
        by_code[code] = by_code.get(code, 0) + rule["count"]
        category = rule["category"]
        by_category[category] = by_category.get(category, 0) + rule["count"]
    return {"connectivity_warnings": connectivity, "electrical_advisory": electrical,
            "warning_summary": {"stage": "analysis_and_elaboration" if elaboration else "full_compile",
                                "occurrences": len(warnings), "by_code": by_code,
                                "by_category": by_category, "identities": entries}}


def verify_reports(output: Path, config: dict) -> dict:
    reports = output / "output_files"
    project = config["project"]
    for name in ("map.rpt", "fit.rpt", "sta.rpt", "sta.summary", "asm.rpt", "sof"):
        artifact = reports / f"{project}.{name}"
        if not artifact.is_file() or artifact.is_symlink() or artifact.stat().st_size == 0:
            raise RuntimeError(f"missing Quartus artifact: {name}")
    for name in ("unconstrained.rpt", "check_timing.rpt", "ignored.rpt", "setup.rpt", "hold.rpt", "clocks.tsv"):
        if not (reports / name).is_file():
            raise RuntimeError(f"missing timing audit: {name}")
    # Why: the parsed fields are ASCII but a report may hold other bytes (a degree
    # sign); latin-1 decodes any byte losslessly.
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
    # Why: outside the reset synchronizers, whose asynchronous inputs are untimed
    # (a board pin, PLL lock, or a crossing the SDC cuts), only the JTAG scan domain
    # resets asynchronously (wiki/bkm/rtl.md, Rules: reset and clocks), so recovery
    # and removal are required on TCK.
    required |= {(corner, kind, "jtag_tck") for corner in config["timing_corners"]
                 for kind in ("Recovery", "Removal")}
    required |= {(corner, kind, clock) for corner in config["timing_corners"]
                 for kind in ("Setup", "Hold") for clock in config["forwarded_clocks"]}
    required |= {(corner, "Minimum Pulse Width", "reference") for corner in config["timing_corners"]}
    if not required <= keys:
        raise RuntimeError(f"incomplete timing summary: {sorted(required - keys)}")
    sdram = sdram_timing(reports, config, system_clock)
    check = (reports / "check_timing.rpt").read_text(encoding="latin-1")
    counts = dict(re.findall(r";\s*([a-z_]+)\s*;\s*(\d+)\s*;", check))
    expected_checks = set("no_clock multiple_clock pos_neg_clock_domain generated_clock "
                          "virtual_clock no_input_delay no_output_delay partial_input_delay "
                          "partial_output_delay io_min_max_delay_consistency reference_pin "
                          "generated_io_delay latency_override partial_multicycle multicycle_consistency "
                          "loops latches pll_cross_check uncertainty partial_min_max_delay "
                          "clock_assignments_on_output_ports input_delay_assigned_to_clock".split())
    # Ref: wiki/src/nand2doom/fpga/de10_lite/board-spec.md "Report gate": all I/O is
    # related to real clocks. Each forwarded clock pin is its generated clock's
    # target, not a data output requiring set_output_delay.
    forwarded_ports = config["forwarded_clocks"].values()
    allowed = {"no_clock": 1, "virtual_clock": 1, "no_output_delay": len(forwarded_ports)}
    if (set(counts) != expected_checks or any(int(count) != allowed.get(name, 0)
                                              for name, count in counts.items()) or
            not all(re.search(r";\s*" + re.escape(port) + r"\s*;\s*No output delay was set on output port\. "
                              r"This port has clock assignments\.\s*;", check) for port in forwarded_ports) or
            not re.search(r";\s*" + re.escape(config["clock_module"]) + r":u_clock\|altpll:u_pll\|altpll_[A-Za-z0-9]+:"
                          r"auto_generated\|pll_lock_sync\s*;\s*No clock feeds this register's clock port\.\s*;", check) or
            not re.search(r";\s*;\s*No virtual clock was found\.\s*;", check)):
        raise RuntimeError("unreviewed check_timing findings")
    ignored = (reports / "ignored.rpt").read_text(encoding="latin-1")
    if not re.fullmatch(r"\s*-+\s*;\s*SDC Assignments\s*;\s*-+\s*"
                        r"No constraints were ignored\.\s*", ignored):
        raise RuntimeError("ignored or incomplete SDC constraints report")
    resources = fitted_resources((reports / f"{project}.fit.rpt").read_text(encoding="latin-1"), config)
    ucp = (reports / "unconstrained.rpt").read_text(encoding="latin-1")
    categories = ("Illegal Clocks", "Unconstrained Clocks", "Unconstrained Input Ports",
                  "Unconstrained Input Port Paths", "Unconstrained Output Ports",
                  "Unconstrained Output Port Paths")
    for category in categories:
        values = re.search(r";\s*" + category + r"\s*;\s*(\d+)\s*;\s*(\d+)\s*;", ucp)
        if not values or values.groups() != ("0", "0"):
            raise RuntimeError(f"timing audit failed: {category}")
    clocks = dict(line.split("\t") for line in (reports / "clocks.tsv").read_text().splitlines())
    if (set(clocks) != set(clock_relationships) or
            any(float(clocks[name]) != float(row["Period"]) for name, row in clock_relationships.items())):
        raise RuntimeError("missing required 50/25 MHz clocks")
    warnings = []
    for log in ("compile.log", "audit.log"):
        for line in (output / log).read_text(encoding="latin-1").splitlines():
            line = line.strip()
            if re.match(r"(?:Critical )?Warning\b", line):
                warnings.append(line)
    audit = reviewed_warnings(warnings, reports / f"{project}.map.rpt", config,
                              fit_report=reports / f"{project}.fit.rpt")
    result = {**audit, "minimum_slack_ns": min(float(entry[3]) for entry in entries), "clocks_ns": clocks,
              "timing": [{"corner": corner, "check": kind, "clock": clock, "slack_ns": float(slack)}
                         for corner, kind, clock, slack, _ in entries],
              "check_timing": counts,
              "clock_relationships": clock_relationships,
              "sdram_timing": sdram,
              "resources": resources,
              "reviewed_warnings": warnings}
    (output / "evidence.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def verify_elaboration(output: Path, config: dict) -> dict:
    """Gate analysis and elaboration alone: success status and reviewed warnings.

    This is the fast front-end check (FPGA DV plan): it proves Quartus Std
    accepts and elaborates the board closure, not that it fits or meets timing.
    """
    project = config["project"]
    for name in ("map.summary", "map.rpt"):
        report = output / "output_files" / f"{project}.{name}"
        if not report.is_file() or report.is_symlink() or report.stat().st_size == 0:
            raise RuntimeError(f"missing analysis and elaboration report: {name}")
    summary = output / "output_files" / f"{project}.map.summary"
    text = summary.read_text(encoding="latin-1")
    if not re.search(r"^Analysis & Elaboration Status\s*:\s*Successful\b", text, re.M):
        raise RuntimeError("analysis and elaboration did not succeed")
    lines = [line.strip() for line in (output / "elaborate.log").read_text(encoding="latin-1").splitlines()]
    errors = [line for line in lines if re.match(r"(?:Critical )?Error \(", line)]
    if errors:
        raise RuntimeError("Quartus elaboration errors:\n" + "\n".join(errors))
    warnings = [line for line in lines if re.match(r"(?:Critical )?Warning\b", line)]
    audit = reviewed_warnings(warnings, output / "output_files" / f"{project}.map.rpt", config,
                              elaboration=True)
    result = {"board": config["board"], "top": config["top"], "stage": "analysis_and_elaboration",
              **audit, "reviewed_warnings": warnings}
    (output / "evidence.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def warning_summary_text(result: dict) -> str:
    """A count-preserving summary; the raw diagnostics remain in their logs."""
    summary = result["warning_summary"]
    categories = ", ".join(f"{name}={count}" for name, count in summary["by_category"].items() if count)
    return f"{summary['occurrences']} reviewed warning occurrences ({categories})"


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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="src/nand2doom/cfg/n2d_quartus.json")
    parser.add_argument("--board", choices=BOARDS, default=BASE_BOARD,
                        help=f"board whose top, pins and constraints to build (default {BASE_BOARD})")
    parser.add_argument("--quartus", help="quartus_sh executable (or QUARTUS_SH/PATH)")
    parser.add_argument("--plan", action="store_true", help="validate and show inputs without writing")
    parser.add_argument("--elaborate-only", action="store_true",
                        help="run only quartus_map --analysis_and_elaboration (fast RTL acceptance "
                             "check; no fit, timing or image); exit 77 when Quartus is absent")
    parser.add_argument("--output", type=Path,
                        help="output directory below workdir (default workdir/quartus/<project>"
                             "[-elaborate])")
    args = parser.parse_args(argv)
    try:
        config = load_config(ROOT, args.config, args.board)
        suffix = "-elaborate" if args.elaborate_only else ""
        output = (args.output.resolve() if args.output else
                  ROOT / "workdir" / "quartus" / (config["project"] + suffix))
        confined(ROOT, output)
        print(f'{config["board"]} {config["top"]}: {config["device"]}, {config["clock_hz"]} Hz, '
              f'{len(config["sources"])} sources; timeout {config["timeout_seconds"]} s per invocation; '
              f'output {output.relative_to(ROOT)}' +
              ("; analysis and elaboration only" if args.elaborate_only else ""))
        if args.plan:
            return 0
        executable = shutil.which(args.quartus or os.environ.get("QUARTUS_SH", "quartus_sh"))
        if not executable and args.elaborate_only:
            # Why: exit 77 is an explicit skip, never a pass: no Quartus means no evidence.
            print("SKIPPED: Quartus not found; pass --quartus or set QUARTUS_SH", file=sys.stderr)
            return 77
        if not executable:
            raise ValueError("Quartus not found; pass --quartus or set QUARTUS_SH")
        if args.elaborate_only:
            with build_lock(ROOT, output):
                if output.exists():
                    shutil.rmtree(output)
                stage(ROOT, output, config)
                try:
                    run([str(Path(executable).with_name("quartus_map")), config["project"],
                         "--analysis_and_elaboration"], output, "elaborate.log", config["timeout_seconds"])
                except RuntimeError as error:
                    # Why: the exit status alone does not say which construct Quartus rejected.
                    log = (output / "elaborate.log").read_text(encoding="latin-1").splitlines()
                    raise RuntimeError("\n".join([str(error)] + [line.strip() for line in log if re.match(
                        r"\s*(?:Critical )?Error \(", line)])) from error
                result = verify_elaboration(output, config)
            print(f'Quartus analysis and elaboration passed for {config["top"]}; '
                  f'{warning_summary_text(result)}; '
                  f'evidence {output.relative_to(ROOT)}')
            return 0
        with build_lock(ROOT, output):
            # Why: an artifact from an earlier invocation must not satisfy this run.
            confined(ROOT, output)
            if output.exists():
                shutil.rmtree(output)
            stage(ROOT, output, config)
            run([executable, "--flow", "compile", config["project"]], output,
                "compile.log", config["timeout_seconds"])
            sta = str(Path(executable).with_name("quartus_sta"))
            run([sta, "-t", "audit.tcl"], output, "audit.log", config["timeout_seconds"])
            result = verify_reports(output, config)
        print(f"Full hardware gate passed; {warning_summary_text(result)}; "
              f"minimum slack {result['minimum_slack_ns']} ns; evidence {output.relative_to(ROOT)}")
        return 0
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
