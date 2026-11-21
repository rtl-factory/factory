#!/usr/bin/env python3
"""HDL libraries: the files of every design, declared once (wiki/tools/bazel/spec.md, Libraries).

Each src/<project>/cfg/libraries.json maps a library (one module, package or
header file, named after it) to its file and the libraries it needs. A simulation
target, a formal target or a board image names only its top libraries; this
module resolves them into the ordered file list (every library after the ones it
needs) that Verilator, SymbiYosys and Quartus read.

The files are derived from the RTL itself, never written by hand:

    python3 tools/bazel/hdl_libraries.py update     # rewrite every libraries.json
    python3 tools/bazel/hdl_libraries.py check      # fail if one is stale (L0)
    python3 tools/bazel/hdl_libraries.py files <library>... [--flavor F] [--define D] [--headers]
    python3 tools/bazel/hdl_libraries.py registry   # the targets as Bazel reads them
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
HDL_SUFFIXES = (".sv", ".v", ".svh", ".vh")
HEADER_SUFFIXES = (".svh", ".vh")
SCHEMA_VERSION = 1
FV_HEADER = re.compile(r"^#\s*(level|timeout|library|flavors|defines)\s*:\s*(.*?)\s*$")
FV_TIMEOUT_SECONDS = 600
# Why: a design's own folders; dv/ code may stand in for vendor blocks (stubs), so
# rtl/ and fpga/ code never depends on it.
DESIGN_AREAS = ("rtl", "fpga")
KEYWORDS = set("""module endmodule if else for begin end assign always always_ff always_comb always_latch case
casez casex endcase default generate endgenerate function endfunction task endtask initial final logic wire reg
input output inout parameter localparam typedef struct enum union packed import export return int integer genvar
while repeat forever foreach unique unique0 priority bit byte string assert property cover assume sequence
automatic static virtual class interface modport""".split())


def load(root: Path = ROOT) -> dict:
    """Every project's libraries, by name."""
    libraries = {}
    for path in sorted(root.glob("src/*/cfg/libraries.json")):
        for name, entry in json.loads(path.read_text())["libraries"].items():
            if name in libraries:
                raise ValueError(f"library {name} is declared twice")
            libraries[name] = entry
    return libraries


def resolve(libraries: dict, roots: list[str], flavors=(), defines=()) -> tuple[list[str], list[str]]:
    """The sources and headers of roots and every library they need, needed ones first."""
    sources, headers, done, active = [], [], set(), []
    defined = {d.split("=", 1)[0] for d in defines}

    def visit(name: str) -> None:
        if name in done:
            return
        if name not in libraries:
            raise ValueError(f"unknown library {name}" + (f" (needed by {active[-1]})" if active else ""))
        if name in active:
            if "headers" in libraries[name]:
                return  # Why: guarded headers may include each other.
            raise ValueError("library cycle: " + " -> ".join(active + [name]))
        active.append(name)
        entry = libraries[name]
        if "variants" in entry:
            variants = entry["variants"]
            flavor = next((f for f in flavors if f in variants), "default")
            if flavor not in variants:
                raise ValueError(f"library {name} has no default variant")
            entry = variants[flavor]
        for dep in entry.get("deps", []):
            if isinstance(dep, str):
                visit(dep)
            elif set(dep.get("if", [])) <= defined and not set(dep.get("unless", [])) & defined:
                for name_ in dep["deps"]:
                    visit(name_)
        active.pop()
        done.add(name)
        sources.extend(entry.get("srcs", []))
        headers.extend(entry.get("headers", []))

    for root in roots:
        visit(root)
    return sources, headers


def resolve_target(target: dict, libraries: dict) -> dict:
    """A target with its libraries (by default the library of its top) resolved into sources and headers."""
    if "sources" in target:
        raise ValueError(f"{target.get('top', 'a target')}: name libraries, not sources (wiki/tools/bazel/spec.md, Libraries)")
    roots = target.get("libraries") or [target["top"]]
    sources, headers = resolve(libraries, roots, target.get("flavors", []), target.get("defines", []))
    return {**target, "sources": sources, "headers": headers}


def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group().count("\n"), text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def _negate(condition: tuple) -> tuple:
    kind, name = condition
    return ("unless" if kind == "if" else "if", name)


def _references(text: str) -> list[tuple[str, tuple]]:
    """Each module, package, interface or include a file names, with the `ifdef conditions around it."""
    lines = _strip_comments(text).splitlines()
    # Why: each open `ifdef frame keeps the conditions of its earlier branches, so an
    # `elsif or `else branch holds only when none of them did.
    stack, conditions_of_line = [], []
    for i, line in enumerate(lines):
        directive = re.match(r"\s*`(ifdef|ifndef|elsif|else|endif)\b\s*(\w*)", line)
        if directive:
            kind, name = directive.groups()
            if kind in ("ifdef", "ifndef"):
                following = next((l for l in lines[i + 1:] if l.strip()), "")
                # Why: an include guard (`ifndef X then `define X) wraps a whole header and selects nothing.
                guard = kind == "ifndef" and re.match(rf"\s*`define\s+{name}\b", following)
                stack.append(None if guard else {"before": [], "own": [("if" if kind == "ifdef" else "unless", name)]})
            elif kind in ("elsif", "else") and stack and stack[-1]:
                frame = stack[-1]
                frame["before"] += frame["own"]
                frame["own"] = [("if", name)] if kind == "elsif" else []
            elif kind == "endif" and stack:
                stack.pop()
            lines[i] = ""
        active = [c for frame in stack if frame for c in [_negate(b) for b in frame["before"]] + frame["own"]]
        conditions_of_line.append(tuple(active))
    body = "\n".join(lines)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line) + 1)

    def conditions_at(offset: int) -> tuple:
        low, high = 0, len(lines) - 1
        while low < high:
            mid = (low + high + 1) // 2
            low, high = (mid, high) if starts[mid] <= offset else (low, mid - 1)
        return conditions_of_line[low] if lines else ()

    # Why: matched across lines, so an instance may put its name, parameters and
    # instance name on separate lines.
    patterns = {
        "parameterized instance": r"\b(\w+)\s*#\s*\(",
        "instance": r"\b(\w+)\s+\w+\s*(?:\[[^\]]*\]\s*)*\(",
        "package item or import": r"\b(\w+)::",
        "interface port with a modport": r"\b(\w+)\.\w+\s+\w+\s*[,;)\[]",
    }
    found = set()
    for pattern in patterns.values():
        for match in re.finditer(pattern, body):
            if match.group(1) not in KEYWORDS:
                found.add((match.group(1), conditions_at(match.start(1))))
    for match in re.finditer(r'`include\s+"([^"]+)"', body):
        found.add(("`" + match.group(1), conditions_at(match.start())))
    return sorted(found)


def derive(root: Path = ROOT) -> dict:
    """Every project's libraries, derived from its HDL files."""
    files = sorted(str(p.relative_to(root)) for p in root.glob("src/*/**/*")
                   if p.suffix in HDL_SUFFIXES and p.is_file())
    texts = {f: (root / f).read_text(errors="replace") for f in files}
    defines = defaultdict(list)
    for f in files:
        for m in re.finditer(r"^\s*(?:module|package|interface)\s+(?:automatic\s+)?(\w+)",
                             _strip_comments(texts[f]), re.M):
            defines[m.group(1)].append(f)
    library_of, flavor_of = {}, {}
    for f in files:
        names = [n for n, defining in defines.items() if f in defining and len(defining) > 1]
        stem = Path(f).stem
        # Why: one module with several implementations (portable, intel, asic) is one
        # library with a variant per implementation, named after the module the file is
        # named after; a target picks one by flavor.
        owners = [n for n in names if stem == n or stem.startswith(n + "_")]
        if not owners:
            continue
        name = max(owners, key=len)
        library_of[f] = name
        flavor_of[f] = "default" if stem in (name, name + "_portable") else stem[len(name) + 1:]
    for f in files:
        library_of.setdefault(f, Path(f).stem)
    seen = {}
    for f in files:
        key = (library_of[f], flavor_of.get(f))
        if seen.setdefault(key, f) != f:
            what = f"library {key[0]}" + (f" variant {key[1]}" if key[1] else "")
            raise ValueError(f"{f} and {seen[key]} would both be {what}")
    for name in {library_of[f] for f in flavor_of}:
        if (name, "default") not in seen:
            raise ValueError(f"library {name} has variants but no default file ({name}.sv or {name}_portable.sv)")
        if (name, None) in seen:
            raise ValueError(f"{seen[(name, None)]} would be library {name}, which has variants")

    def area(path: str) -> str:
        return path.split("/")[2]

    projects = defaultdict(dict)
    for f in files:
        deps, conditional = [], defaultdict(list)
        for ref, conditions in _references(texts[f]):
            targets = [ref[1:]] if ref.startswith("`") else defines.get(ref, [])
            libs = sorted({library_of[t] for t in targets if t in library_of and t != f and library_of[t] != library_of[f]
                           and not (area(t) not in DESIGN_AREAS and area(f) in DESIGN_AREAS)})
            for lib in libs:
                if conditions:
                    when = tuple(tuple(sorted({n for c, n in conditions if c == kind})) for kind in ("if", "unless"))
                    conditional[when].append(lib)
                else:
                    deps.append(lib)
        always = sorted(set(deps))
        entry = {"deps": list(always)}
        for (ifs, unless), libs in sorted(conditional.items()):
            if set(libs) - set(always):
                entry["deps"].append({**({"if": list(ifs)} if ifs else {}), **({"unless": list(unless)} if unless else {}),
                                      "deps": sorted(set(libs) - set(always))})
        entry["headers" if f.endswith(HEADER_SUFFIXES) else "srcs"] = [f]
        if not entry["deps"]:
            del entry["deps"]
        project = f.split("/")[1]
        if f in flavor_of:
            projects[project].setdefault(library_of[f], {"variants": {}})["variants"][flavor_of[f]] = entry
        else:
            projects[project][library_of[f]] = entry
    return {p: {name: libs[name] for name in sorted(libs)} for p, libs in sorted(projects.items())}


def render(libraries: dict) -> str:
    return json.dumps({"schema_version": SCHEMA_VERSION, "libraries": libraries}, indent=2) + "\n"


def stale(root: Path = ROOT) -> list[str]:
    """The libraries.json files that do not match the HDL."""
    out = []
    for project, libraries in derive(root).items():
        path = root / "src" / project / "cfg" / "libraries.json"
        if not path.is_file() or path.read_text() != render(libraries):
            out.append(str(path.relative_to(root)))
    return out


def parse_sby(path: Path) -> dict:
    """A formal target's header: level, time budget and the library it proves."""
    fields = {"level": None, "timeout_seconds": FV_TIMEOUT_SECONDS, "libraries": [], "flavors": [], "defines": []}
    for line in path.read_text().splitlines():
        if line.strip().startswith("["):
            break
        match = FV_HEADER.match(line)
        if match:
            key, value = match.groups()
            if key == "timeout":
                fields["timeout_seconds"] = int(value)
            elif key == "level":
                fields["level"] = value
            else:
                fields["libraries" if key == "library" else key] = value.split()
    return fields


def registry(root: Path = ROOT) -> dict:
    """Every simulation and formal target with its files resolved, and the files read."""
    libraries = load(root)
    sim, formal, inputs = {}, {}, sorted(str(p.relative_to(root)) for p in root.glob("src/*/cfg/libraries.json"))
    for path in sorted(root.glob("src/*/cfg/targets.json")):
        inputs.append(str(path.relative_to(root)))
        targets = json.loads(path.read_text())["targets"]
        sim[path.parts[-3]] = {name: resolve_target(t, libraries) for name, t in targets.items()}
    for path in sorted(root.glob("src/*/fv/*/*.sby")):
        sby = str(path.relative_to(root))
        inputs.append(sby)
        header = parse_sby(path)
        if len(header["libraries"]) != 1:
            raise ValueError(f"{sby}: the header needs one '# library: <harness>' line")
        target = resolve_target(header, libraries)
        formal.setdefault(path.parts[-4], {})[path.stem] = {
            "level": target["level"], "timeout_seconds": target["timeout_seconds"], "sby": sby,
            "files": target["headers"] + target["sources"]}
    return {"sim": sim, "formal": formal, "inputs": inputs}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=ROOT)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("update", help="rewrite every libraries.json from the HDL")
    sub.add_parser("check", help="fail if a libraries.json does not match the HDL")
    files = sub.add_parser("files", help="print the ordered file list (a .f file) of libraries")
    files.add_argument("libraries", nargs="+")
    files.add_argument("--flavor", action="append", default=[])
    files.add_argument("--define", action="append", default=[])
    files.add_argument("--headers", action="store_true", help="list the include files instead")
    sub.add_parser("registry", help="print every target with its files resolved, as JSON")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if args.command == "update":
        for project, libraries in derive(root).items():
            (root / "src" / project / "cfg" / "libraries.json").write_text(render(libraries))
        return 0
    if args.command == "check":
        bad = stale(root)
        for path in bad:
            print(f"{path} does not match the HDL: run python3 tools/bazel/hdl_libraries.py update", file=sys.stderr)
        return 1 if bad else 0
    if args.command == "files":
        sources, headers = resolve(load(root), args.libraries, args.flavor, args.define)
        print("\n".join(headers if args.headers else sources))
        return 0
    print(json.dumps(registry(root)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
