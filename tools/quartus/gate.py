"""The report gate: a fitted image passes only on met timing, reviewed warnings and expected RAM.

verify_reports() reads one build's reports and logs and fails closed on any
missing report, failing or uncovered timing at any corner, unexpected clock
relationships, an unreviewed check_timing finding or ignored constraint,
RAM inferred other than the manifest expects, or any warning not reviewed in
the manifest; on a pass it writes evidence.json. verify_elaboration() gates
analysis and elaboration alone. Spec: wiki/tools/quartus/spec.md.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re

from tools.quartus.build import corner_label, staged_files
from tools.quartus.manifest import GENERATED_IP_FAMILIES, SYSTEM_CLOCK, top_source

EVIDENCE_SCHEMA = "quartus-evidence/1"
# Ref: the common board clock contract: every board top instantiates its board
# clock as u_clock, whose altpll is u_pll (wiki/src/common/fpga/<board>/board-spec.md).
SYSTEM_CLOCK_NAME = re.compile(r"u_clock\|u_pll\|.+\|clk\[0\]")
# Ref: the Virtual JTAG hub's clock; the SDC of a design with a virtual JTAG instance constrains it.
JTAG_CLOCK = {"name": "jtag_tck", "period_ns": 100, "target": "altera_reserved_tck"}
# Ref: every check_timing category of Quartus 25.1std; only the reviewed ones may be nonzero.
CHECK_TIMING_CATEGORIES = set(
    "no_clock multiple_clock pos_neg_clock_domain generated_clock virtual_clock no_input_delay "
    "no_output_delay partial_input_delay partial_output_delay io_min_max_delay_consistency reference_pin "
    "generated_io_delay latency_override partial_multicycle multicycle_consistency loops latches "
    "pll_cross_check uncertainty partial_min_max_delay clock_assignments_on_output_ports "
    "input_delay_assigned_to_clock".split())
UCP_CATEGORIES = ("Illegal Clocks", "Unconstrained Clocks", "Unconstrained Input Ports",
                  "Unconstrained Input Port Paths", "Unconstrained Output Ports", "Unconstrained Output Port Paths")
RESOURCES = ("Total logic elements", "Total registers", "M9Ks", "Total PLLs", "Embedded Multiplier 9-bit elements")
RAM_FIELDS = ("Name", "Type", "Mode", "Clock Mode", "Port A Depth", "Port A Width", "M9Ks",
              "Port B Depth", "Port B Width", "Port A RDW Mode", "Mixed Width RDW Mode")
CONNECTIVITY_DETAIL = ("Declared by entity but not connected by instance. "
                       "Logic that only feeds a dangling port will be removed.")
CONNECTIVITY_AGGREGATE = ("Warning (12241): 1 hierarchies have connectivity warnings - "
                          "see the Connectivity Checks report folder")
WARNING_LINE = re.compile(r"(?:Critical )?Warning\b")
ERROR_LINE = re.compile(r"(?:Critical )?Error \(")


def read(path: Path) -> str:
    # Why: the parsed fields are ASCII but a report may hold other bytes (a degree
    # sign); latin-1 decodes any byte losslessly.
    return path.read_text(encoding="latin-1")


def table_rows(report: str, first: list[str], needed: str):
    """Each data row, as a dict, of every report table whose header starts with first and has needed."""
    header = None
    for line in report.splitlines():
        cells = [value.strip() for value in line.split(";")[1:-1]]
        if cells[:len(first)] == first and needed in cells:
            header = cells
        elif header and len(cells) == len(header):
            yield dict(zip(header, cells))


def fitted_resources(report: str, config: dict) -> dict:
    """Resource use, and every fitted RAM checked against the manifest's RAM requirements."""
    resources = {}
    for label in RESOURCES:
        # Ref: the Quartus fitter's resource table marks "Total registers*" (registers in
        # LEs and I/O); the summary's unstarred row has no device total.
        match = re.search(r";\s*" + re.escape(label) + r"\*?\s*;\s*([\d,]+)\s*/\s*([\d,]+)", report)
        if not match:
            raise RuntimeError(f"missing fitted resource: {label}")
        used, available = (int(value.replace(",", "")) for value in match.groups())
        if used > available or (label == "Total PLLs" and used != 1):
            raise RuntimeError(f"invalid fitted resource: {label}={used}/{available}")
        resources[label] = {"used": used, "available": available, "remaining": available - used}
    # Ref: in the fitter RAM summary, Type is the requested implementation (often
    # AUTO); the fitted M9Ks column, not that request, proves block allocation.
    memories = [row for row in table_rows(report, ["Name", "Type", "Mode"], "M9Ks")
                if row["M9Ks"].isdigit() and int(row["M9Ks"]) > 0]
    resources["ram_summary"] = check_rams(memories, config, resources["M9Ks"]["used"])
    return resources


def check_rams(memories: list[dict], config: dict, used: int) -> list[dict]:
    accounted = 0
    for requirement in config["ram_requirements"]:
        if "name" in requirement:
            matches = [row for row in memories if requirement["name"] in row["Name"]]
            label = requirement["name"]
        else:
            matches = []
            for index in range(requirement["count"]):
                name = f'{requirement["prefix"]}[{index}].{requirement["array"]}'
                found = [row for row in memories if name in row["Name"]]
                if len(found) != 1:
                    raise RuntimeError(f"missing or ambiguous fitted RAM: {name}")
                matches += found
            label = requirement["prefix"]
        if len(matches) != requirement["count"]:
            raise RuntimeError(f'expected {requirement["count"]} fitted RAM(s) named {label}, found {len(matches)}')
        mode = requirement["mode"]
        for ram in matches:
            name = ram["Name"]
            for column, key in (("Port A Depth", "depth"), ("Port A Width", "width"), ("M9Ks", "m9ks_each")):
                if ram.get(column) != str(requirement[key]):
                    raise RuntimeError(f"fitted RAM geometry mismatch: {name} {column}={ram.get(column)}")
            single = mode == "Single Port"
            for column, key in (("Port B Depth", "depth"), ("Port B Width", "width")):
                if ram.get(column) != ("--" if single else str(requirement[key])):
                    raise RuntimeError(f"fitted RAM geometry mismatch: {name} {column}={ram.get(column)}")
            # Ref: Quartus reports an unused read-during-write field as Don't care or New
            # data, so only the port relationship the array uses is contractual.
            rdw = {"Single Port": ["Port A RDW Mode"], "Simple Dual Port": ["Mixed Width RDW Mode"],
                   "True Dual Port": ["Port A RDW Mode", "Mixed Width RDW Mode"]}[mode]
            if (ram.get("Mode") != mode or ram.get("Clock Mode") != "Single Clock"
                    or any(ram.get(column) != "Old data" for column in rdw)):
                raise RuntimeError(f'fitted RAM mode mismatch: {name} {ram.get("Mode")}/{ram.get("Clock Mode")}')
            accounted += int(ram["M9Ks"])
    if sum(int(row["M9Ks"]) for row in memories) != used:
        raise RuntimeError("fitted RAM table does not account for every M9K")
    if accounted != used:
        raise RuntimeError(f"RAM requirements account for {accounted} of {used} fitted M9Ks")
    return [{key: row.get(key, "") for key in RAM_FIELDS} for row in memories]


def fitted_clocks(report: str, system_clock: str, config: dict) -> dict:
    """Every clock of the timing report, checked against the board's clock relationships."""
    clocks = {}
    for row in table_rows(report, ["Clock Name", "Type"], "Targets"):
        if row["Type"] not in ("Base", "Generated", "Virtual"):
            continue
        if row["Clock Name"] in clocks:
            raise RuntimeError("duplicate fitted clock")
        clocks[row["Clock Name"]] = row
    return check_clocks(clocks, system_clock, config)


def check_clocks(clocks: dict, system_clock: str, config: dict) -> dict:
    reference_ns = 1e9 / config["reference_hz"]
    system_ns = 1e9 / config["clock_hz"]
    expected = {
        "reference": {"Type": "Base", "Period": reference_ns, "Rise": 0, "Fall": reference_ns / 2,
                      "Targets": config["reference_port"], "Master": "", "Source": ""},
        system_clock: {"Type": "Generated", "Period": system_ns, "Rise": 0, "Fall": system_ns / 2,
                       "Divide by": config["reference_hz"] // config["clock_hz"], "Multiply by": 1,
                       "Inverted": "false", "Master": "reference",
                       "Source": system_clock.rsplit("|", 1)[0] + "|inclk[0]", "Targets": system_clock},
    }
    # Why: a forwarded clock pin carries the inverted system clock, so each is a
    # generated clock on its pin, not a data output.
    for name, port in config["forwarded_clocks"].items():
        expected[name] = {"Type": "Generated", "Period": system_ns, "Rise": system_ns / 2, "Fall": system_ns,
                          "Divide by": 1, "Multiply by": 1, "Inverted": "true", "Master": system_clock,
                          "Source": system_clock, "Targets": port}
    if config["jtag_clock"]:
        period = JTAG_CLOCK["period_ns"]
        expected[JTAG_CLOCK["name"]] = {"Type": "Base", "Period": period, "Rise": 0, "Fall": period / 2,
                                        "Targets": JTAG_CLOCK["target"], "Master": "", "Source": ""}
    if set(clocks) != set(expected):
        raise RuntimeError(f"missing or unexpected fitted clocks: {sorted(clocks)}")
    for name, fields in expected.items():
        for key, value in fields.items():
            actual = clocks[name].get(key, "")
            if key == "Targets":
                actual = actual.strip("{} ")
            if isinstance(value, (int, float)):
                try:
                    actual = float(actual)
                except ValueError:
                    actual = None
            if actual != value:
                raise RuntimeError(f"fitted clock relationship mismatch: {name} {key}")
    return clocks


def path_report(file: Path, kind: str, corner: str) -> dict:
    """The one path of a single-path timing report, with its consistent nonnegative slack."""
    if not file.is_file() or file.is_symlink():
        raise RuntimeError(f"missing timing path report: {file.name}")
    report = read(file)
    summary = re.search(r"Report Timing: Found 1 " + kind +
                        r" paths \(0 violated\)\.\s+Worst case slack is (-?\d+(?:\.\d+)?)", report)
    detail = re.findall(r"^Path #1: " + kind.title() + r" slack is (-?\d+(?:\.\d+)?)\s*$", report, re.M)
    rows = re.findall(r"^;\s*(From Node|To Node|Launch Clock|Latch Clock|Slack)\s*;"
                      r"\s*([^;\n]+?)\s*;\s*$", report, re.M)
    values = dict(rows)
    if (not summary or len(detail) != 1 or len(rows) != 5 or len(values) != 5 or
            not re.search(r"Delay Model:\s*" + re.escape(corner) + r" Model", report)):
        raise RuntimeError(f"missing or malformed timing path: {file.name}")
    try:
        slack = float(values["Slack"])
    except ValueError as error:
        raise RuntimeError(f"invalid timing margin: {file.name}") from error
    if slack < 0 or slack != float(summary[1]) or slack != float(detail[0]):
        raise RuntimeError(f"failing or inconsistent timing margin: {file.name}")
    return {"slack_ns": slack, **values}


def timing_paths(reports: Path, config: dict, system_clock: str) -> dict:
    """Each path group's real path at every corner, with reviewed endpoints and clocks."""
    clock_names = {SYSTEM_CLOCK: system_clock}
    result = {}
    for name, group in config["timing_paths"].items():
        launch = clock_names.get(group["launch"], group["launch"])
        latch = clock_names.get(group["latch"], group["latch"])
        paths = []
        for corner in config["timing_corners"]:
            for kind in ("setup", "hold"):
                report = f"{name}_{corner_label(corner)}_{kind}.rpt"
                path = path_report(reports / report, kind, corner)
                if (path["Launch Clock"] != launch or path["Latch Clock"] != latch or not any(
                        re.fullmatch(endpoint["from"], path["From Node"]) and
                        re.fullmatch(endpoint["to"], path["To Node"]) for endpoint in group["endpoints"])):
                    raise RuntimeError(f"unexpected timing path endpoints/clocks: {report} {path}")
                paths.append({"report": report, "corner": corner, "check": kind, **path})
        result[name] = {"paths": paths, "worst_margin_ns": {
            kind: min(p["slack_ns"] for p in paths if p["check"] == kind) for kind in ("setup", "hold")}}
    return result


def fmax_summary(reports: Path, config: dict, system_clock: str) -> dict:
    """The system clock's restricted Fmax at every corner, at least the system clock."""
    fmax, floor = {}, config["clock_hz"] / 1e6
    for corner in config["timing_corners"]:
        file = reports / f"fmax_{corner_label(corner)}.rpt"
        if not file.is_file():
            raise RuntimeError(f"missing Fmax report: {file.name}")
        rows = re.findall(r";\s*(-?\d+(?:\.\d+)?) MHz\s*;\s*(-?\d+(?:\.\d+)?) MHz\s*;\s*([^;]+?)\s*;", read(file))
        match = [row for row in rows if row[2] == system_clock]
        if len(match) != 1:
            raise RuntimeError(f"missing system clock Fmax at {corner}")
        value, restricted, _ = match[0]
        if float(restricted) < floor:
            raise RuntimeError(f"restricted Fmax below {floor:g} MHz at {corner}: {restricted}")
        fmax[corner] = {"fmax_mhz": float(value), "restricted_fmax_mhz": float(restricted)}
    return fmax


def connectivity_warnings(report: str, config: dict) -> list[dict]:
    """Validate every diagnostic row in the map connectivity tables: only reviewed ports may warn."""
    contract = config["reviewed_warnings"].get("connectivity")
    expected = set() if contract is None else {
        (contract["hierarchy"], port, "Output", "Warning", CONNECTIVITY_DETAIL) for port in contract["unused_outputs"]}
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
            # Why: only the actual closing border ends a table, so a malformed row
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
    return [dict(zip(("hierarchy", "port", "type", "severity", "details"), row)) for row in observed]


def warning_rules(config: dict, *, elaboration: bool = False) -> list[dict]:
    """Every reviewed warning: its exact pattern and occurrence bounds, independent of line numbers."""
    rules = []
    contract = config["reviewed_warnings"]

    def add(code, category, identity, pattern, count=1, *, exact=True):
        rules.append({"code": code, "category": category, "identity": identity,
                      "pattern": rf"Warning \({code}\): " + pattern,
                      "minimum": 0 if elaboration or not exact else count, "maximum": count})

    for item in contract.get("unused_objects", []):
        source, obj = item["source"], item["object"]
        add(10036, "unused_rtl", item,
            r"Verilog HDL or VHDL warning at " + re.escape(Path(source).name) +
            r'\((?P<line>[0-9]+)\): object "' + re.escape(obj) +
            r'" assigned a value but never read File: [^\r\n]+/' + re.escape(source) + r" Line: (?P=line)")
    if "connectivity" in contract:
        add(12241, "vendor_connectivity", {"hierarchy": contract["connectivity"]["hierarchy"]},
            re.escape(CONNECTIVITY_AGGREGATE.split(": ", 1)[1]))
    if contract.get("logiclock_count"):
        add(292013, "tool_license", {"feature": "LogicLock"},
            re.escape("Feature LogicLock is only available with a valid subscription license. "
                      "You can purchase a software subscription to gain full access to this feature."),
            contract["logiclock_count"])
    for entry in contract.get("patterns", []):
        pattern = entry if isinstance(entry, str) else entry["pattern"]
        code = re.match(r"Warning \\\((\d+)\\\)", pattern)
        rules.append({"code": int(code[1]) if code else 0, "category": "reviewed_pattern",
                      "identity": {"pattern": pattern}, "pattern": pattern,
                      "minimum": 0 if elaboration or isinstance(entry, str) else entry["count"],
                      "maximum": math.inf if isinstance(entry, str) else entry["count"]})
    if elaboration:
        return rules
    constants = contract.get("constant_outputs", {})
    source = top_source(config)
    if constants:
        add(13024, "constant_outputs", {"summary": "VCC or GND"}, "Output pins are stuck at VCC or GND")
    for pin, value in constants.items():
        add(13410, "constant_outputs", {"source": source, "pin": pin, "value": value},
            re.escape(f'Pin "{pin}" is stuck at {value}') + r" File: [^\r\n]+/" + re.escape(source) + r" Line: [0-9]+")
    if "pll_output" in contract:
        add(15064, "board_clock_routing", {"pll": config["clock_module"], "output": "clk[0]",
                                           "pin": contract["pll_output"]},
            r'PLL "' + re.escape(config["clock_module"]) +
            r':u_clock\|altpll:u_pll\|(?P<generated>altpll_[A-Za-z0-9]+):auto_generated\|pll1" '
            r'output port clk\[0\] feeds output pin "' + re.escape(contract["pll_output"]) +
            re.escape('~output" via non-dedicated routing -- jitter performance depends on switching rate '
                      'of other design elements. Use PLL dedicated clock outputs to ensure jitter performance') +
            r' File: [^\r\n]+/db/(?P=generated)\.tdf Line: [0-9]+')
    if "electrical_pins" in contract:
        count = len(contract["electrical_pins"])
        add(169177, "electrical_advisory", {"family": config["family"], "pins": count},
            re.escape(f"{count} pins must meet Intel FPGA requirements for 3.3-, 3.0-, and 2.5-V interfaces. "
                      f"For more information, refer to AN 447: Interfacing {config['family']} Devices "
                      "with 3.3/3.0/2.5-V LVTTL/LVCMOS I/O Systems."))
    return rules


def generated_warning(line: str, map_report: Path | None) -> dict | None:
    """Bind a 12125 generated-file warning to an actual generated, read design file.

    Without the map report (evidence checked away from its build) only the
    warning's identity is parsed.
    """
    match = re.fullmatch(
        r"Warning \(12125\): Using design file (?P<file>db/(?P<entity>(?P<family>altpll|altsyncram|decode|mux)_"
        r"[A-Za-z0-9]+)\.tdf), which is not specified as a design file for the current project, "
        r"but contains definitions for 1 design units and 1 entities in project", line)
    if not match:
        return None
    filename, entity, family = match["file"], match["entity"], match["family"]
    rule = {"code": 12125, "category": "generated_ip", "identity": {"file": filename, "family": family},
            "minimum": 0, "maximum": 1, "count": 1}
    if map_report is None:
        return rule
    path = map_report.parent.parent / filename
    rows = [[cell.strip() for cell in row.split(";")[1:-1]]
            for row in read(map_report).splitlines() if row.startswith(";")]
    rows = [row for row in rows if row and row[0] == filename]
    if (len(rows) != 1 or len(rows[0]) != 5 or rows[0][1:3] != ["yes", "Auto-Generated Megafunction"]
            or not rows[0][3].endswith("/" + filename) or rows[0][4]
            or path.is_symlink() or path.parent.is_symlink() or not path.is_file()):
        raise RuntimeError(f"unreviewed generated IP evidence: {filename}")
    text = read(path)
    primitive = {"decode": "lpm_decode", "mux": "lpm_mux"}.get(family, family)
    if (not text.startswith("--" + primitive + " ") or
            re.findall(r"^SUBDESIGN\s+(\w+)\s*$", text, re.M) != [entity]):
        raise RuntimeError(f"unreviewed generated IP identity: {filename}")
    return rule


def standard_key(standard: str) -> str:
    """An I/O-standard name for comparison: Quartus reports vary its case and spacing."""
    return " ".join(standard.split()).casefold()


def electrical_warnings(report: str, config: dict) -> list[dict]:
    """Require the complete AN 447 detail set with exact ports, pads and standards."""
    source = top_source(config)
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


def tally(warnings: list[str], config: dict, *, elaboration: bool = False,
          map_report: Path | None = None) -> list[dict]:
    """Each reviewed rule with its observed count; fails on any unreviewed, ambiguous or miscounted warning."""
    rules = warning_rules(config, elaboration=elaboration)
    for rule in rules:
        rule["count"] = 0
    generated = {}
    for line in warnings:
        matches = [rule for rule in rules if re.fullmatch(rule["pattern"], line)]
        if len(matches) > 1:
            raise RuntimeError("ambiguous reviewed warning (several rules match):\n" + line)
        if matches:
            matches[0]["count"] += 1
            continue
        rule = generated_warning(line, map_report)
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
    limits = config["reviewed_warnings"].get("generated_ip_limits", {})
    for family in sorted(GENERATED_IP_FAMILIES):
        count = sum(rule["identity"]["family"] == family for rule in generated.values())
        if count > limits.get(family, 0):
            raise RuntimeError(f"too many generated IP warnings: {family} {count} > {limits.get(family, 0)}")
    return [*rules, *generated.values()]


def check_aggregate(warnings: list[str], connectivity: list) -> None:
    """The connectivity aggregate appears once exactly when connectivity rows warn."""
    aggregates = [line for line in warnings if line == CONNECTIVITY_AGGREGATE]
    if aggregates and (not connectivity or len(aggregates) != 1):
        raise RuntimeError("missing or duplicate reviewed connectivity evidence")
    if connectivity and not aggregates:
        raise RuntimeError("connectivity warnings without their aggregate diagnostic")


def reviewed_warnings(warnings: list[str], map_report: Path, config: dict, *,
                      elaboration: bool = False, fit_report: Path | None = None) -> dict:
    """Fail closed on identity, multiplicity, completeness and supporting evidence."""
    connectivity = connectivity_warnings(read(map_report), config)
    check_aggregate(warnings, connectivity)
    rules = tally(warnings, config, elaboration=elaboration, map_report=map_report)
    electrical = []
    if not elaboration and "electrical_pins" in config["reviewed_warnings"]:
        if fit_report is None:
            raise RuntimeError("missing electrical advisory fitter evidence")
        electrical = electrical_warnings(read(fit_report), config)
    entries = [{key: (None if value == math.inf else value) for key, value in rule.items() if key != "pattern"}
               for rule in rules]
    by_code, by_category = {}, {}
    for rule in entries:
        by_code[str(rule["code"])] = by_code.get(str(rule["code"]), 0) + rule["count"]
        by_category[rule["category"]] = by_category.get(rule["category"], 0) + rule["count"]
    return {"connectivity_warnings": connectivity, "electrical_advisory": electrical,
            "warning_summary": {"stage": "analysis_and_elaboration" if elaboration else "full_compile",
                                "occurrences": len(warnings), "by_code": by_code,
                                "by_category": by_category, "identities": entries}}


def log_warnings(output: Path, logs: tuple[str, ...]) -> list[str]:
    return [line.strip() for log in logs for line in read(output / log).splitlines()
            if WARNING_LINE.match(line.strip())]


def check_staged_inputs(output: Path, config: dict) -> None:
    """The reports belong to this config and to the files it staged, unchanged since."""
    staged = json.loads((output / "inputs.json").read_text())
    if staged["config"] != json.loads(json.dumps(config)):
        raise RuntimeError("report configuration differs from staged inputs; fresh compile required")
    if set(staged["sha256"]) != set(staged_files(config)):
        raise RuntimeError("staged inputs do not match the config's files")
    for name, digest in staged["sha256"].items():
        if hashlib.sha256((output / name).read_bytes()).hexdigest() != digest:
            raise RuntimeError(f"staged input changed after compilation: {name}")


TIMING_KINDS = ("Setup", "Hold", "Recovery", "Removal", "Minimum Pulse Width")
EVIDENCE_FIELDS = {"schema", "gate", "stage", "project", "board", "top", "device", "connectivity_warnings",
                   "electrical_advisory", "warning_summary", "minimum_slack_ns", "clocks_ns", "timing", "fmax",
                   "timing_paths", "check_timing", "clock_relationships", "resources", "reviewed_warnings"}


def required_timing(config: dict, system_clock: str, clocks: dict) -> set:
    """The (corner, check, clock) rows the timing summary must hold."""
    corners = config["timing_corners"]
    required = {(corner, kind, system_clock) for corner in corners for kind in ("Setup", "Hold", "Minimum Pulse Width")}
    required |= {(corner, kind, clock) for corner in corners
                 for kind in ("Setup", "Hold") for clock in config["forwarded_clocks"]}
    required |= {(corner, "Minimum Pulse Width", "reference") for corner in corners}
    if config["jtag_clock"]:
        # Why: outside the reset synchronizers, whose asynchronous inputs are untimed
        # (a board pin, PLL lock, or a crossing the SDC cuts), only the JTAG scan domain
        # resets asynchronously (wiki/bkm/rtl.md, Rules: reset and clocks), so recovery
        # and removal are required on TCK; the system clock has none to report.
        required |= {(corner, kind, JTAG_CLOCK["name"]) for corner in corners for kind in TIMING_KINDS}
    return required


def expected_check_timing(config: dict) -> dict:
    """Every check_timing category with its reviewed count."""
    # Why: the PLL lock synchronizer has no clock of its own and no virtual clock is
    # defined; each forwarded clock pin is its generated clock's target, not a data
    # output that needs set_output_delay.
    allowed = {"no_clock": 1, "virtual_clock": 1, "no_output_delay": len(config["forwarded_clocks"])}
    return {name: str(allowed.get(name, 0)) for name in CHECK_TIMING_CATEGORIES}


def nonnegative(value) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def require(condition: bool, detail: str) -> None:
    if not condition:
        raise ValueError("invalid gate evidence: " + detail)


def check_evidence(e: dict, config: dict) -> None:
    """Recheck, from evidence.json alone, every gate obligation it records for this config.

    It proves the evidence describes a passing gate of this config; it does not
    prove where the evidence came from (the image receipt's hashes bind that).
    """
    try:
        require(isinstance(e, dict) and set(e) == EVIDENCE_FIELDS, "fields")
        require((e["schema"], e["gate"], e["stage"]) == (EVIDENCE_SCHEMA, "passed", "full_compile"), "schema")
        require((e["project"], e["board"], e["top"], e["device"]) ==
                (config["project"], config["board"], config["top"], config["device"]), "board identity")
        require(e["check_timing"] == expected_check_timing(config), "check_timing findings")
        clocks = e["clock_relationships"]
        require(isinstance(clocks, dict) and all(isinstance(row, dict) and all(isinstance(v, str) for v in row.values())
                                                for row in clocks.values()), "clock relationship fields")
        systems = [name for name in clocks if SYSTEM_CLOCK_NAME.fullmatch(name)]
        require(len(systems) == 1, "system clock")
        system = systems[0]
        try:
            check_clocks(clocks, system, config)
        except RuntimeError as error:
            require(False, str(error))
        require(set(e["clocks_ns"]) == set(clocks) and all(
            math.isfinite(float(e["clocks_ns"][name])) and float(e["clocks_ns"][name]) == float(row["Period"])
            for name, row in clocks.items()), "clock periods")
        corners, observed = set(config["timing_corners"]), set()
        require(isinstance(e["timing"], list) and bool(e["timing"]), "timing rows")
        for row in e["timing"]:
            require(set(row) == {"corner", "check", "clock", "slack_ns", "tns_ns"}, "timing row fields")
            key = (row["corner"], row["check"], row["clock"])
            require(key not in observed and key[0] in corners and key[1] in TIMING_KINDS and key[2] in clocks,
                    "timing coverage")
            require(nonnegative(row["slack_ns"]) and row["tns_ns"] == 0, "negative/nonfinite slack or nonzero TNS")
            observed.add(key)
        require(required_timing(config, system, clocks) <= observed, "timing coverage")
        require(e["minimum_slack_ns"] == min(row["slack_ns"] for row in e["timing"]), "timing minimum")
        require(isinstance(e["fmax"], dict) and set(e["fmax"]) == corners, "Fmax corners")
        for values in e["fmax"].values():
            require(set(values) == {"fmax_mhz", "restricted_fmax_mhz"} and all(nonnegative(v) for v in values.values())
                    and config["clock_hz"] / 1e6 <= values["restricted_fmax_mhz"] <= values["fmax_mhz"], "Fmax")
        resources = e["resources"]
        require(isinstance(resources, dict) and set(resources) == set(RESOURCES) | {"ram_summary"}, "resource fields")
        for name in RESOURCES:
            values = resources[name]
            require(set(values) == {"used", "available", "remaining"} and all(
                type(v) is int and v >= 0 for v in values.values()) and values["available"] > 0
                and values["used"] + values["remaining"] == values["available"], "resource capacity")
        require(resources["Total PLLs"]["used"] == 1, "PLL count")
        require(isinstance(resources["ram_summary"], list) and all(
            isinstance(row, dict) and set(row) == set(RAM_FIELDS) and all(isinstance(v, str) for v in row.values())
            for row in resources["ram_summary"]), "RAM summary fields")
        try:
            check_rams(resources["ram_summary"], config, resources["M9Ks"]["used"])
        except RuntimeError as error:
            require(False, str(error))
        groups = e["timing_paths"]
        require(isinstance(groups, dict) and set(groups) == set(config["timing_paths"]), "timing path groups")
        names = {SYSTEM_CLOCK: system}
        for name, group in config["timing_paths"].items():
            paths, seen = groups[name].get("paths"), set()
            require(set(groups[name]) == {"paths", "worst_margin_ns"} and isinstance(paths, list), "path group fields")
            for path in paths:
                key = (path.get("corner"), path.get("check"))
                require(key[0] in corners and key[1] in ("setup", "hold") and key not in seen, "path coverage")
                require(set(path) == {"report", "corner", "check", "slack_ns", "From Node", "To Node",
                                      "Launch Clock", "Latch Clock", "Slack"}, "path fields")
                require(nonnegative(path["slack_ns"]) and isinstance(path["Slack"], str)
                        and float(path["Slack"]) == path["slack_ns"], "path margin")
                require(path["Launch Clock"] == names.get(group["launch"], group["launch"])
                        and path["Latch Clock"] == names.get(group["latch"], group["latch"])
                        and any(re.fullmatch(end["from"], path["From Node"]) and re.fullmatch(end["to"], path["To Node"])
                                for end in group["endpoints"]), "path endpoints/clocks")
                seen.add(key)
            require(seen == {(c, k) for c in corners for k in ("setup", "hold")}, f"missing {name} paths")
            require(groups[name]["worst_margin_ns"] == {k: min(p["slack_ns"] for p in paths if p["check"] == k)
                                                       for k in ("setup", "hold")}, f"{name} worst margins")
        warnings = e["reviewed_warnings"]
        require(isinstance(warnings, list) and all(isinstance(w, str) for w in warnings), "warning list")
        try:
            tally(warnings, config)
        except RuntimeError as error:
            require(False, str(error))
        contract = config["reviewed_warnings"].get("connectivity")
        expected = [] if contract is None else sorted(
            ({"hierarchy": contract["hierarchy"], "port": port, "type": "Output", "severity": "Warning",
              "details": CONNECTIVITY_DETAIL} for port in contract["unused_outputs"]), key=lambda r: r["port"])
        connectivity = e["connectivity_warnings"]
        require(isinstance(connectivity, list) and (not connectivity or sorted(
            connectivity, key=lambda r: r["port"]) == expected), "connectivity warnings")
        try:
            check_aggregate(warnings, connectivity)
        except RuntimeError as error:
            require(False, str(error))
    except (KeyError, TypeError, AttributeError, ValueError, OverflowError) as error:
        if isinstance(error, ValueError) and str(error).startswith("invalid gate evidence"):
            raise
        raise ValueError(f"invalid gate evidence: {error!r}") from error


def verify_reports(output: Path, config: dict) -> dict:
    """The full report gate of one compiled and audited build; writes evidence.json on a pass."""
    check_staged_inputs(output, config)
    reports = output / "output_files"
    project = config["project"]
    for name in ("map.rpt", "fit.rpt", "sta.rpt", "sta.summary", "asm.rpt", "sof"):
        artifact = reports / f"{project}.{name}"
        if not artifact.is_file() or artifact.is_symlink() or artifact.stat().st_size == 0:
            raise RuntimeError(f"missing Quartus artifact: {name}")
    for name in ("unconstrained.rpt", "check_timing.rpt", "ignored.rpt", "setup.rpt", "hold.rpt", "clocks.tsv"):
        if not (reports / name).is_file():
            raise RuntimeError(f"missing timing audit: {name}")
    timing = read(reports / f"{project}.sta.summary")
    entries = re.findall(r"^Type\s*:\s*(.+?) Model (.+?) '([^']+)'\s*"
                         r"Slack\s*:\s*(-?\d+(?:\.\d+)?)\s*"
                         r"TNS\s*:\s*(-?\d+(?:\.\d+)?)", timing, re.M)
    if (not entries or len(entries) != len(re.findall(r"^Type\s*:", timing, re.M)) or
            any(float(slack) < 0 or float(tns) != 0 for _, _, _, slack, tns in entries)):
        raise RuntimeError("missing or failing all-corner timing slack")
    keys = {(corner, kind, clock) for corner, kind, clock, _, _ in entries}
    system_clocks = {clock for _, _, clock, _, _ in entries if SYSTEM_CLOCK_NAME.fullmatch(clock)}
    if len(keys) != len(entries) or len(system_clocks) != 1:
        raise RuntimeError("ambiguous timing summary")
    system_clock = next(iter(system_clocks))
    clock_relationships = fitted_clocks(read(reports / f"{project}.sta.rpt"), system_clock, config)
    required = required_timing(config, system_clock, clock_relationships)
    if not required <= keys:
        raise RuntimeError(f"incomplete timing summary: {sorted(required - keys)}")
    check = read(reports / "check_timing.rpt")
    counts = dict(re.findall(r";\s*([a-z_]+)\s*;\s*(\d+)\s*;", check))
    forwarded_ports = config["forwarded_clocks"].values()
    if (counts != expected_check_timing(config) or
            not all(re.search(r";\s*" + re.escape(port) + r"\s*;\s*No output delay was set on output port\. "
                              r"This port has clock assignments\.\s*;", check) for port in forwarded_ports) or
            not re.search(r";\s*" + re.escape(config["clock_module"]) + r":u_clock\|altpll:u_pll\|altpll_[A-Za-z0-9]+:"
                          r"auto_generated\|pll_lock_sync\s*;\s*No clock feeds this register's clock port\.\s*;", check) or
            not re.search(r";\s*;\s*No virtual clock was found\.\s*;", check)):
        raise RuntimeError("unreviewed check_timing findings")
    ignored = read(reports / "ignored.rpt")
    if not re.fullmatch(r"\s*-+\s*;\s*SDC Assignments\s*;\s*-+\s*No constraints were ignored\.\s*", ignored):
        raise RuntimeError("ignored or incomplete SDC constraints report")
    resources = fitted_resources(read(reports / f"{project}.fit.rpt"), config)
    ucp = read(reports / "unconstrained.rpt")
    for category in UCP_CATEGORIES:
        values = re.search(r";\s*" + category + r"\s*;\s*(\d+)\s*;\s*(\d+)\s*;", ucp)
        if not values or values.groups() != ("0", "0"):
            raise RuntimeError(f"timing audit failed: {category}")
    clocks = dict(line.split("\t") for line in (reports / "clocks.tsv").read_text().splitlines())
    if (set(clocks) != set(clock_relationships) or
            any(float(clocks[name]) != float(row["Period"]) for name, row in clock_relationships.items())):
        raise RuntimeError("audit clocks differ from the fitted clocks")
    fmax = fmax_summary(reports, config, system_clock)
    paths = timing_paths(reports, config, system_clock)
    warnings = log_warnings(output, ("compile.log", "audit.log"))
    audit = reviewed_warnings(warnings, reports / f"{project}.map.rpt", config,
                              fit_report=reports / f"{project}.fit.rpt")
    result = {"schema": EVIDENCE_SCHEMA, "gate": "passed", "stage": "full_compile",
              "project": project, "board": config["board"], "top": config["top"], "device": config["device"],
              **audit,
              "minimum_slack_ns": min(float(entry[3]) for entry in entries), "clocks_ns": clocks,
              "timing": [{"corner": corner, "check": kind, "clock": clock, "slack_ns": float(slack),
                          "tns_ns": float(tns)} for corner, kind, clock, slack, tns in entries],
              "fmax": fmax, "timing_paths": paths, "check_timing": counts,
              "clock_relationships": clock_relationships, "resources": resources,
              "reviewed_warnings": warnings}
    check_evidence(json.loads(json.dumps(result)), config)
    (output / "evidence.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def verify_elaboration(output: Path, config: dict) -> dict:
    """Gate analysis and elaboration alone: success status and reviewed warnings.

    The fast front-end check: it proves Quartus accepts and elaborates the
    board closure, not that it fits or meets timing.
    """
    project = config["project"]
    for name in ("map.summary", "map.rpt"):
        report = output / "output_files" / f"{project}.{name}"
        if not report.is_file() or report.is_symlink() or report.stat().st_size == 0:
            raise RuntimeError(f"missing analysis and elaboration report: {name}")
    text = read(output / "output_files" / f"{project}.map.summary")
    if not re.search(r"^Analysis & Elaboration Status\s*:\s*Successful\b", text, re.M):
        raise RuntimeError("analysis and elaboration did not succeed")
    lines = [line.strip() for line in read(output / "elaborate.log").splitlines()]
    errors = [line for line in lines if ERROR_LINE.match(line)]
    if errors:
        raise RuntimeError("Quartus elaboration errors:\n" + "\n".join(errors))
    warnings = [line for line in lines if WARNING_LINE.match(line)]
    audit = reviewed_warnings(warnings, output / "output_files" / f"{project}.map.rpt", config, elaboration=True)
    result = {"schema": EVIDENCE_SCHEMA, "gate": "passed", "stage": "analysis_and_elaboration",
              "project": project, "board": config["board"], "top": config["top"], "device": config["device"],
              **audit, "reviewed_warnings": warnings}
    (output / "evidence.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def warning_summary_text(result: dict) -> str:
    """A count-preserving summary; the raw diagnostics remain in their logs."""
    summary = result["warning_summary"]
    categories = ", ".join(f"{name}={count}" for name, count in summary["by_category"].items() if count)
    return f"{summary['occurrences']} reviewed warning occurrences ({categories})"
