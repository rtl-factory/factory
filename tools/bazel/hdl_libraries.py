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
            entry = entry["variants"][next((f for f in flavors if f in entry["variants"]), "default")]
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


def _references(text: str) -> list[tuple[str, tuple]]:
    """Each module, package or include a file names, with the `ifdef conditions around it."""
    found, stack = [], []
    instance = re.compile(r"^\s*(\w+)\s*(?:#\s*\(|\s+\w+\s*(?:\[[^\]]*\]\s*)*\()")
    for line in _strip_comments(text).splitlines():
        directive = re.match(r"\s*`(ifdef|ifndef|elsif|else|endif)\b\s*(\w*)", line)
        if directive:
            kind, name = directive.groups()
            if kind in ("ifdef", "ifndef"):
                # Why: an include guard (`ifndef X_VH) wraps a whole header and selects nothing.
                guard = kind == "ifndef" and name.endswith("_VH")
                stack.append(None if guard else ("if" if kind == "ifdef" else "unless", name))
            elif kind == "else" and stack and stack[-1]:
                cond, name_ = stack.pop()
                stack.append(("unless" if cond == "if" else "if", name_))
            elif kind == "elsif" and stack:
                stack.pop()
                stack.append(("if", name))
            elif kind == "endif" and stack:
                stack.pop()
            continue
        names = {m.group(1) for m in re.finditer(r"\b(\w+)::", line)}
        match = instance.match(line)
        if match and match.group(1) not in KEYWORDS:
            names.add(match.group(1))
        names |= {"`" + m.group(1) for m in re.finditer(r'`include\s+"([^"]+)"', line)}
        found += [(n, tuple(c for c in stack if c)) for n in sorted(names)]
    return found


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
    for name, defining in defines.items():
        if len(defining) > 1:
            # Why: one module with several implementations (portable, intel, asic) is one
            # library with a variant per implementation; a target picks one by flavor.
            for f in defining:
                stem = Path(f).stem
                library_of[f] = name
                flavor_of[f] = "default" if stem in (name, name + "_portable") else stem[len(name) + 1:]
    for f in files:
        library_of.setdefault(f, Path(f).stem)
    seen = {}
    for f in files:
        if f not in flavor_of and seen.setdefault(library_of[f], f) != f:
            raise ValueError(f"{f} and {seen[library_of[f]]} would both be library {library_of[f]}")

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
        target = resolve_target(parse_sby(path), libraries) if parse_sby(path)["libraries"] else {}
        formal.setdefault(path.parts[-4], {})[path.stem] = {
            "level": target["level"], "timeout_seconds": target["timeout_seconds"], "sby": sby,
            "files": target.get("headers", []) + target.get("sources", [])}
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
