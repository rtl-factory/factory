"""Project manifests and board pin maps: the only inputs of the Quartus build tool.

A project manifest (src/<project>/cfg/*.json) names its board, its top library,
the board pins its top uses and its gate contracts; each board's pins, I/O
standards, device and clock live once in src/common/fpga/<board>/pins.json.
load() resolves a manifest, an optional board override and an optional profile
into the one config the stage and the report gate read.
Spec: wiki/tools/quartus/spec.md.
"""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import re

IDENTIFIER = re.compile(r"[A-Za-z0-9_]+")
PIN_SPEC = re.compile(r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?:\[(?P<msb>\d+)(?::(?P<lsb>\d+))?\])?")
LOCATION = re.compile(r"[A-Z]+\d+")
STANDARD = re.compile(r"[\w .-]+")
RAM_MODES = ("Single Port", "Simple Dual Port", "True Dual Port")
SYSTEM_CLOCK = "system"
BOARD_KEYS = {"board", "family", "device", "reference_port", "reference_hz", "clock_module",
              "default_io_standard", "io_standards", "pins"}
REQUIRED_KEYS = {"board", "project", "top", "clock_hz", "timeout_seconds", "timing_corners", "libraries",
                 "settings", "constraints", "pins", "forwarded_clocks", "jtag_clock", "ram_requirements",
                 "reviewed_warnings"}
OPTIONAL_KEYS = {"flavors", "parameters", "verilog_macros", "timing_paths",
                 "require_clean_source", "boards"}
# Why: a board override swaps board facts only; the design's libraries, and the
# board an override itself is, stay those of the base manifest.
OVERRIDE_KEYS = (REQUIRED_KEYS | OPTIONAL_KEYS | {"source_replacements"}) - {
    "board", "boards", "libraries", "flavors", "clock_hz", "timing_corners"}
PROFILE_KEYS = {"board", "project", "parameters", "ram_requirements", "reviewed_warnings"}
WARNING_KEYS = {"logiclock_count", "pll_output", "constant_outputs", "electrical_pins", "unused_objects",
                "generated_ip_limits", "connectivity", "patterns"}
GENERATED_IP_FAMILIES = {"altpll", "altsyncram", "decode", "mux"}


def board_file(root: Path, board: str) -> Path:
    return root / "src" / "common" / "fpga" / board / "pins.json"


def expand(spec: str) -> list[str]:
    """The port bits a pin spec names: `X`, `X[3]` or `X[7:0]` (least significant bit first)."""
    match = PIN_SPEC.fullmatch(spec)
    if not match:
        raise ValueError(f"invalid pin spec: {spec}")
    if match["msb"] is None:
        return [spec]
    msb, lsb = int(match["msb"]), int(match["lsb"] if match["lsb"] is not None else match["msb"])
    if lsb > msb:
        raise ValueError(f"pin range must be [msb:lsb]: {spec}")
    return [f'{match["name"]}[{index}]' for index in range(lsb, msb + 1)]


def load_board(root: Path, board: str) -> dict:
    """One board's facts with every pin as a bit: {"pins": {"KEY[0]": "B8", ...}, ...}."""
    if not IDENTIFIER.fullmatch(board):
        raise ValueError(f"invalid board: {board}")
    path = board_file(root, board)
    if not path.is_file():
        raise ValueError(f"unknown board: {board} (no {path.relative_to(root)})")
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or set(data) != BOARD_KEYS or data["board"] != board:
        raise ValueError(f"{path.name} of {board}: keys must be exactly {sorted(BOARD_KEYS)}")
    pins = {}
    for name, value in data["pins"].items():
        bits = ([(name, value)] if isinstance(value, str) else
                [(f"{name}[{index}]", location) for index, location in enumerate(value)])
        for bit, location in bits:
            if not PIN_SPEC.fullmatch(bit) or not isinstance(location, str) or not LOCATION.fullmatch(location):
                raise ValueError(f"{board}: invalid pin {bit}")
            pins[bit] = location
    if len(set(pins.values())) != len(pins):
        raise ValueError(f"{board}: duplicate physical pin")
    buses = {bit.split("[")[0] for bit in pins}
    for name, standard in data["io_standards"].items():
        if (name not in pins and name not in buses) or not (standard is None or STANDARD.fullmatch(standard)):
            raise ValueError(f"{board}: invalid I/O standard assignment: {name}")
    if data["reference_port"] not in pins or type(data["reference_hz"]) is not int or data["reference_hz"] <= 0:
        raise ValueError(f"{board}: invalid reference clock")
    for key in ("device", "clock_module"):
        if not IDENTIFIER.fullmatch(data[key]):
            raise ValueError(f"{board}: invalid {key}")
    return {**data, "pins": pins}


def board_standard(board: dict, pin: str):
    """A board pin's I/O standard; None when the project's settings fragment sets it."""
    standards = board["io_standards"]
    return standards.get(pin, standards.get(pin.split("[")[0], board["default_io_standard"]))


def resolve_pins(manifest: dict, board: dict) -> tuple[dict, dict]:
    """The top's ports with their locations and I/O standards, in manifest order.

    A pins entry names board pins the top's ports share names with (`X`, `X[3]`,
    `X[7:0]`), or wires one port to a board pin of another name (`RESET_N=KEY[0]`).
    """
    wiring = []
    for entry in manifest["pins"]:
        if not isinstance(entry, str):
            raise ValueError(f"invalid pin spec: {entry}")
        port, _, pin = entry.partition("=")
        if not pin:
            wiring += [(bit, bit) for bit in expand(port)]
        elif expand(port) == [port] and expand(pin) == [pin]:
            wiring.append((port, pin))
        else:
            raise ValueError(f"a pin alias wires one bit: {entry}")
    pins, standards = {}, {}
    for port, pin in wiring:
        if port in pins:
            raise ValueError(f"duplicate pin: {port}")
        if pin not in board["pins"]:
            raise ValueError(f'{board["board"]} has no pin {pin} (add it to its pins.json)')
        pins[port], standards[port] = board["pins"][pin], board_standard(board, pin)
    if len(set(pins.values())) != len(pins):
        raise ValueError("duplicate physical pin")
    return pins, standards


def select_board(manifest: dict, board: str | None) -> dict:
    """The manifest with one board's overrides applied; the base board when board is None."""
    manifest = copy.deepcopy(manifest)
    boards = manifest.pop("boards", {})
    if board in (None, manifest["board"]):
        return manifest
    if board not in boards:
        raise ValueError(f'manifest has no board {board} (its board is {manifest["board"]}; '
                         f'overrides: {sorted(boards) or "none"})')
    overrides = copy.deepcopy(boards[board])
    unknown = set(overrides) - OVERRIDE_KEYS
    if unknown:
        raise ValueError(f"unsupported board override keys: {sorted(unknown)}")
    replacements = overrides.pop("source_replacements", {})
    if not set(replacements) <= set(manifest["sources"]):
        raise ValueError("board source replacement names no shared source")
    # Why: an override restates only the facts its board changes, so a dict value
    # (the reviewed warnings, the clocks, the path groups) keeps the base's other entries.
    for key, value in overrides.items():
        manifest[key] = {**manifest[key], **value} if isinstance(value, dict) and isinstance(
            manifest.get(key), dict) else value
    manifest["sources"] = [replacements.get(name, name) for name in manifest["sources"]]
    manifest["board"] = board
    return manifest


def merge(base, extra):
    """A profile value over a manifest value: dicts merge, lists extend, the rest replaces."""
    if isinstance(base, dict) and isinstance(extra, dict):
        return {**base, **{key: merge(base.get(key), value) for key, value in extra.items()}}
    if isinstance(base, list) and isinstance(extra, list):
        return base + extra
    return extra


def apply_profile(manifest: dict, profile: dict) -> dict:
    if not isinstance(profile, dict) or not set(profile) <= PROFILE_KEYS or "project" not in profile:
        raise ValueError(f"a profile holds a project and only {sorted(PROFILE_KEYS)}")
    if profile.get("board", manifest["board"]) != manifest["board"]:
        raise ValueError(f'profile {profile["project"]} requires --board {profile["board"]}')
    return {**manifest, **{key: merge(manifest.get(key), value) for key, value in profile.items()
                           if key != "board"}}


def hdl_libraries(root: Path):
    """The library resolver, tools/bazel/hdl_libraries.py, of the tree being built."""
    # Why: tools/bazel is its own Bazel package, so the resolver is loaded by path
    # rather than imported; the tree's own copy resolves the tree's libraries.
    path = root / "tools" / "bazel" / "hdl_libraries.py"
    if not path.is_file():
        path = Path(__file__).resolve().parents[1] / "bazel" / "hdl_libraries.py"
    spec = importlib.util.spec_from_file_location("hdl_libraries", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def resolve_sources(manifest: dict, root: Path) -> dict:
    """The manifest with its libraries resolved into the ordered sources and headers."""
    resolver = hdl_libraries(root)
    resolved = resolver.resolve_target(manifest, resolver.load(root))
    resolved.pop("flavors", None)
    resolved["libraries"] = manifest["libraries"]
    return resolved


def load(root: Path, manifest_path: str, board: str | None = None, profile_path: str | None = None) -> dict:
    """The resolved, validated config of one image."""
    raw = json.loads((root / manifest_path).read_text())
    if not isinstance(raw, dict) or not REQUIRED_KEYS <= set(raw) or not set(raw) <= REQUIRED_KEYS | OPTIONAL_KEYS:
        raise ValueError(f"{manifest_path}: needs {sorted(REQUIRED_KEYS)}, may have {sorted(OPTIONAL_KEYS)}")
    manifest = select_board(resolve_sources(raw, root), board)
    if profile_path:
        manifest = apply_profile(manifest, json.loads((root / profile_path).read_text()))
    facts = load_board(root, manifest["board"])
    pins, standards = resolve_pins(manifest, facts)
    config = {
        "manifest": manifest_path, "profile": profile_path, "board": manifest["board"],
        "project": manifest["project"], "top": manifest["top"],
        "family": facts["family"], "device": facts["device"], "clock_module": facts["clock_module"],
        "reference_port": facts["reference_port"], "reference_hz": facts["reference_hz"],
        "clock_hz": manifest["clock_hz"], "timeout_seconds": manifest["timeout_seconds"],
        "timing_corners": manifest["timing_corners"],
        "sources": manifest["sources"], "headers": manifest["headers"],
        "settings": manifest["settings"], "constraints": manifest["constraints"],
        "verilog_macros": manifest.get("verilog_macros", []), "parameters": manifest.get("parameters", {}),
        "pins": pins, "io_standards": standards,
        "forwarded_clocks": manifest["forwarded_clocks"], "jtag_clock": manifest["jtag_clock"],
        "timing_paths": manifest.get("timing_paths", {}),
        "ram_requirements": manifest["ram_requirements"], "reviewed_warnings": manifest["reviewed_warnings"],
        "require_clean_source": manifest.get("require_clean_source", False),
    }
    if type(config["jtag_clock"]) is not bool:
        raise ValueError(f"{manifest_path}: jtag_clock must be true or false")
    return validate(config, root)


def top_source(config: dict) -> str:
    """The source file that defines the top level, named after it."""
    matches = [name for name in config["sources"] if Path(name).stem == config["top"]]
    if len(matches) != 1:
        raise ValueError(f'expected one source named {config["top"]}.sv, found {matches}')
    return matches[0]


def check_path(root: Path, name: str) -> None:
    if (not isinstance(name, str) or Path(name).is_absolute() or ".." in Path(name).parts
            or not re.fullmatch(r"[\w./-]+", name)):
        raise ValueError(f"invalid source path: {name}")
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError(f"missing or out-of-tree source: {name}")


def settings_standard(root: Path, config: dict, pin: str):
    """The IO_STANDARD the settings fragment gives one pin, or None."""
    settings = (root / config["settings"]).read_text()
    matches = re.findall(r'set_instance_assignment -name IO_STANDARD "([^"\n]+)" -to '
                         + re.escape(pin) + r"$", settings, re.M)
    return matches[0] if len(matches) == 1 else None


def validate_warnings(root: Path, config: dict) -> None:
    warnings, pins = config["reviewed_warnings"], config["pins"]
    if not isinstance(warnings, dict) or not set(warnings) <= WARNING_KEYS:
        raise ValueError(f"reviewed_warnings keys must be among {sorted(WARNING_KEYS)}")
    count = warnings.get("logiclock_count", 0)
    if type(count) is not int or count < 0:
        raise ValueError("invalid reviewed warning contract: logiclock_count")
    constants = warnings.get("constant_outputs", {})
    if not set(constants) <= set(pins) or set(constants.values()) - {"VCC", "GND"}:
        raise ValueError("invalid reviewed warning contract: constant_outputs")
    if "pll_output" in warnings and warnings["pll_output"] not in config["forwarded_clocks"].values():
        raise ValueError("invalid reviewed warning contract: pll_output")
    for pin, standard in warnings.get("electrical_pins", {}).items():
        if pin not in pins or not isinstance(standard, str):
            raise ValueError("invalid electrical advisory pin")
        assigned = config["io_standards"][pin]
        if assigned is None:
            assigned = settings_standard(root, config, pin)
        if standard != assigned:
            raise ValueError(f"electrical advisory standard disagrees with pin assignment: {pin}")
    objects = warnings.get("unused_objects", [])
    if (any(not isinstance(item, dict) or set(item) != {"source", "object"} or item["source"] not in config["sources"]
            or not re.fullmatch(r"\w+", item["object"]) for item in objects)
            or len({(item["source"], item["object"]) for item in objects}) != len(objects)):
        raise ValueError("invalid reviewed unused object contract")
    limits = warnings.get("generated_ip_limits", {})
    if not set(limits) <= GENERATED_IP_FAMILIES or any(type(v) is not int or v <= 0 for v in limits.values()):
        raise ValueError("invalid generated IP warning limits")
    connectivity = warnings.get("connectivity")
    if connectivity is not None and (
            set(connectivity) != {"hierarchy", "unused_outputs"} or not connectivity["unused_outputs"]
            or not re.fullmatch(r"[\w:|]+", connectivity["hierarchy"])):
        raise ValueError("invalid reviewed connectivity contract")
    for entry in warnings.get("patterns", []):
        pattern = entry if isinstance(entry, str) else entry.get("pattern") if isinstance(entry, dict) else None
        if not isinstance(pattern, str) or (isinstance(entry, dict) and (
                set(entry) != {"pattern", "count"} or type(entry["count"]) is not int or entry["count"] < 0)):
            raise ValueError(f"invalid reviewed warning pattern: {entry}")
        try:
            re.compile(pattern)
        except re.error as error:
            raise ValueError(f"invalid reviewed warning pattern: {entry}: {error}") from error


def validate(config: dict, root: Path) -> dict:
    """Refuse a config the stage or the gate could not trust, before anything is written."""
    for key in ("project", "top", "device", "clock_module", "reference_port"):
        if not isinstance(config[key], str) or not IDENTIFIER.fullmatch(config[key]):
            raise ValueError(f"invalid {key}")
    if type(config["timeout_seconds"]) is not int or config["timeout_seconds"] <= 0:
        raise ValueError("timeout_seconds must be a positive integer")
    if (type(config["clock_hz"]) is not int or config["clock_hz"] <= 0
            or config["reference_hz"] % config["clock_hz"]):
        raise ValueError("clock_hz must divide the board reference clock")
    corners = config["timing_corners"]
    if not corners or any(not re.fullmatch(r"(Slow|Fast) \d+mV -?\d+C", corner) for corner in corners):
        raise ValueError("timing_corners must name Quartus operating conditions")
    for name, value in config["parameters"].items():
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", name) or type(value) is not int or value < 0:
            raise ValueError(f"invalid top-level parameter: {name}")
    for macro in config["verilog_macros"]:
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*(=\w+)?", macro):
            raise ValueError(f"invalid Verilog macro: {macro}")
    for name in config["sources"] + config["headers"] + [config["settings"], config["constraints"]]:
        check_path(root, name)
    if len(set(config["sources"])) != len(config["sources"]):
        raise ValueError("duplicate source")
    top_source(config)
    pins, clocks = config["pins"], config["forwarded_clocks"]
    if config["reference_port"] not in pins:
        raise ValueError("the top must use the board reference clock pin")
    if (not isinstance(clocks, dict) or SYSTEM_CLOCK in clocks
            or any(not re.fullmatch(r"[a-z_]+", name) or port not in pins for name, port in clocks.items())):
        raise ValueError("invalid forwarded clocks")
    for name, group in config["timing_paths"].items():
        known = {SYSTEM_CLOCK, *clocks}
        if (not re.fullmatch(r"[a-z_]+", name) or not isinstance(group, dict)
                or set(group) != {"launch", "latch", "endpoints"} | ({"from_ports"} if "from_ports" in group
                                                                     else {"to_clock"})
                or group["launch"] not in known or group["latch"] not in known
                or group.get("to_clock", group["latch"]) not in clocks.keys() | {group["latch"]}
                or not group["endpoints"]
                or any(not isinstance(e, dict) or set(e) != {"from", "to"} for e in group["endpoints"])):
            raise ValueError(f"invalid timing path group: {name}")
        if "from_ports" in group and not re.fullmatch(r"[\w*\[\]]+", group["from_ports"]):
            raise ValueError(f"invalid timing path ports: {name}")
        for endpoint in group["endpoints"]:
            try:
                re.compile(endpoint["from"]), re.compile(endpoint["to"])
            except (re.error, TypeError) as error:
                raise ValueError(f"invalid timing path endpoint: {name}: {error}") from error
    for requirement in config["ram_requirements"]:
        named = {"name"} if "name" in requirement else {"prefix", "array"}
        if (set(requirement) != named | {"count", "depth", "width", "m9ks_each", "mode"}
                or requirement["mode"] not in RAM_MODES
                or any(not isinstance(requirement[key], str) or not requirement[key] for key in named)
                or any(type(requirement[key]) is not int or requirement[key] <= 0
                       for key in ("count", "depth", "width", "m9ks_each"))):
            raise ValueError(f"invalid RAM requirement: {requirement}")
    if type(config["require_clean_source"]) is not bool:
        raise ValueError("require_clean_source must be true or false")
    validate_warnings(root, config)
    return config
