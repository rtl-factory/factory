#!/usr/bin/env python3
"""Compile a board image at a target system clock and tabulate its timing.

Analysis only: this never gates or programs a board. The shipped 25 MHz
constraints are staged unchanged; any other --mhz value adds a scratch
override of the PLL system-clock period to the staged copy below workdir/.
--sta-only --build-dir analyzes an existing compiled project instead, for
example the quartus.py hardware build, without compiling again.

Spec: wiki/src/nand2doom/scripts/board/spec.md#timing-analysis.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("n2d_quartus", HERE / "quartus.py")
quartus = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(quartus)
ROOT = quartus.ROOT
BOARD_MHZ = 25
REFERENCE_MHZ = 50

CLASSES = [
    ("Board status pins", "glue", r"-> (?:LEDR|LEDG|HEX\d)"),
    ("SDRAM pins / controller", "SDRAM controller", r"DRAM_|u_sdram\|"),
    ("Multiply", "M extension", r"u_core\|[^>]*(?:mult|mul_|product|lpm_mult|Mult\d)"),
    ("Divide", "M extension", r"u_core\|[^>]*(?:div|quotient|remainder)"),
    ("I-cache lookup to state", "cache front end", r"u_icache\|"),
    ("D-cache lookup to state", "cache front end", r"u_dcache\|"),
    ("Memory arbiter / controller", "SDRAM controller", r"u_memory\||u_arbiter\|"),
    ("Core decode/ALU/register file to PC/state", "pipelined core", r"u_core\|"),
    ("Framebuffer / VGA", "glue", r"u_framebuffer\||u_vga|VGA_"),
    ("UART debug / bulk", "glue", r"u_uart|u_debug|u_bulk|u_staging|UART_"),
    ("PS/2 keyboard", "glue", r"u_keyboard\||PS2_"),
]

TCL = r"""project_open {project}
create_timing_netlist
read_sdc
update_timing_netlist
set out [open output_files/timing.tsv w]
foreach_in_collection corner [get_available_operating_conditions] {{
    set_operating_conditions $corner
    update_timing_netlist
    set label [get_operating_conditions_info -display_name $corner]
    # The analyzed system clock, so results are labeled by what was timed.
    set system_clock [get_clocks {{*u_pll*|clk[0]}}]
    if {{[get_collection_size $system_clock] != 1}} {{error "expected one PLL system clock"}}
    set system [get_clock_info -name $system_clock]
    puts $out [join [list clock $label $system [get_clock_info -period $system_clock]] "\t"]
    foreach row [get_clock_fmax_info] {{
        puts $out [join [list fmax $label {{*}}$row] "\t"]
    }}
    foreach check {{setup hold recovery removal}} {{
        foreach row [get_clock_domain_info -$check] {{
            puts $out [join [list domain $label $check [lindex $row 0] [lindex $row 1] [lindex $row 2]] "\t"]
        }}
    }}
    # "path": worst paths over all clocks, I/O included. "corepath": only
    # paths launched and latched by the system clock, the ones a faster clock must fix.
    foreach {{kind filter}} [list path {{}} corepath [list -from_clock $system -to_clock $system]] {{
    set rank 0
    foreach_in_collection path [get_timing_paths -setup -npaths {paths} -nworst 1 {{*}}$filter] {{
        incr rank
        set from_clock [get_path_info $path -from_clock]
        set to_clock [get_path_info $path -to_clock]
        set launch [expr {{$from_clock eq "" ? "" : [get_clock_info -name $from_clock]}}]
        set latch [expr {{$to_clock eq "" ? "" : [get_clock_info -name $to_clock]}}]
        set through {{}}
        foreach_in_collection point [get_path_info $path -arrival_points] {{
            set node [get_point_info $point -node]
            if {{$node ne ""}} {{
                set name [get_node_info -name $node]
                if {{[lsearch -exact $through $name] < 0}} {{lappend through $name}}
            }}
        }}
        puts $out [join [list $kind $label $rank \
            [get_path_info $path -slack] \
            [get_node_info -name [get_path_info $path -from]] \
            [get_node_info -name [get_path_info $path -to]] \
            $launch $latch \
            [get_path_info $path -clock_relationship] \
            [get_path_info $path -data_delay] \
            [get_path_info $path -num_logic_levels] \
            [join $through " "]] "\t"]
    }}
    }}
}}
close $out
project_close
"""


def what_if_sdc(sdc: str, mhz: int) -> str:
    """Redefine the derived PLL system clock at mhz; everything downstream follows."""
    anchor = "derive_pll_clocks\n"
    if sdc.count(anchor) != 1:
        raise ValueError("shipped SDC must contain exactly one derive_pll_clocks line")
    if not 1 <= mhz <= 200:
        raise ValueError("what-if clock must be 1..200 MHz")
    override = f"""# Scratch what-if (never shipped): system clock at {mhz} MHz.
set what_if_clock [get_clocks {{*u_pll*|clk[0]}}]
if {{[get_collection_size $what_if_clock] != 1}} {{error "expected one PLL system clock"}}
set what_if_name [get_clock_info -name $what_if_clock]
# The PLL input pin by its reported name: get_pins matches the literal
# brackets, while an escaped pattern matches nothing and Quartus then ignores
# the override (check_clock rejects such an unchanged period).
create_generated_clock -name $what_if_name \\
    -source [get_pins [get_clock_info -master_clock_pin $what_if_clock]] \\
    -multiply_by {mhz} -divide_by {REFERENCE_MHZ} [get_clock_info -targets $what_if_clock]
"""
    return sdc.replace(anchor, anchor + override)


def classify(source: str, target: str, through: str = "") -> tuple[str, str]:
    text = f"{source} -> {target} via {through}"
    for name, owner, pattern in CLASSES:
        if re.search(pattern, text):
            return name, owner
    return "Unclassified", "glue"


def parse(tsv: str) -> dict:
    result = {"clock": None, "fmax": [], "domains": [], "paths": [], "core_paths": []}
    for line in tsv.splitlines():
        kind, corner, *cells = line.split("\t")
        if kind == "clock":
            name, period = cells
            clock = {"name": name, "period_ns": float(period)}
            if result["clock"] not in (None, clock):
                raise ValueError("inconsistent system clock across corners")
            result["clock"] = clock
        elif kind == "fmax":
            # Ref: Quartus 25.1 get_clock_fmax_info rows end with a (often empty) note cell.
            clock, fmax, restricted, *_ = cells
            result["fmax"].append({"corner": corner, "clock": clock,
                                   "fmax_mhz": float(fmax), "restricted_mhz": float(restricted)})
        elif kind == "domain":
            check, clock, slack, tns = cells
            result["domains"].append({"corner": corner, "check": check, "clock": clock,
                                      "slack_ns": float(slack), "tns_ns": float(tns)})
        elif kind in ("path", "corepath"):
            rank, slack, source, target, launch, latch, relationship, delay, levels, through = cells
            name, owner = classify(source, target, through)
            result["paths" if kind == "path" else "core_paths"].append({"corner": corner, "rank": int(rank), "slack_ns": float(slack),
                                    "from": source, "to": target, "launch": launch, "latch": latch,
                                    "relationship_ns": float(relationship),
                                    "data_delay_ns": float(delay), "levels": int(levels),
                                    "through": through.split(),
                                    "class": name, "owner": owner})
        else:
            raise ValueError(f"unknown timing record: {kind}")
    if not result["clock"] or not result["fmax"] or not result["domains"] or not result["paths"]:
        raise ValueError("incomplete timing records")
    return result


def check_clock(result: dict, mhz: int) -> None:
    """Refuse to label results with a clock other than the one analyzed."""
    analyzed = 1000.0 / result["clock"]["period_ns"]
    if abs(analyzed - mhz) > 0.01:
        raise ValueError(f"analyzed system clock is {analyzed:g} MHz "
                         f"({result['clock']['period_ns']:g} ns), not the requested {mhz} MHz")


def worst_corner(result: dict) -> str:
    setup = [row for row in result["domains"] if row["check"] == "setup"]
    return min(setup, key=lambda row: row["slack_ns"])["corner"]


def short(node: str) -> str:
    """Drop entity prefixes (entity:instance) so the table shows instances."""
    return re.sub(r"[A-Za-z0-9_]+:", "", node)


def code(text: str) -> str:
    """A GFM table code cell; hierarchy pipes are escaped, as backticks do not protect them."""
    return "`" + text.replace("|", "\\|") + "`"


def markdown(result: dict, mhz: int) -> str:
    corner = worst_corner(result)
    lines = [f"System clock {mhz} MHz.", "",
             "| Corner | Clock | Fmax (MHz) | Restricted Fmax (MHz) |", "| --- | --- | ---: | ---: |"]
    lines += [f"| {r['corner']} | {code(r['clock'])} | {r['fmax_mhz']:.2f} | {r['restricted_mhz']:.2f} |"
              for r in result["fmax"]]
    lines += ["", "| Corner | Check | Clock | Worst slack (ns) | TNS (ns) |",
              "| --- | --- | --- | ---: | ---: |"]
    lines += [f"| {r['corner']} | {r['check']} | {code(r['clock'])} | {r['slack_ns']:.3f} | {r['tns_ns']:.3f} |"
              for r in result["domains"]]
    lines += ["", f"Top setup paths, one per endpoint, at {corner}:", "",
              "| # | Slack (ns) | Levels | From | To | Class | Owner |",
              "| ---: | ---: | ---: | --- | --- | --- | --- |"]
    rows = lambda paths: [f"| {p['rank']} | {p['slack_ns']:.3f} | {p['levels']} | {code(short(p['from']))} | "
                          f"{code(short(p['to']))} | {p['class']} | {p['owner']} |"
                          for p in paths if p["corner"] == corner]
    lines += rows(result["paths"])
    if any(p["corner"] == corner for p in result["core_paths"]):
        lines += ["", f"Top setup paths launched and latched by the system clock at {corner}:", "",
                  "| # | Slack (ns) | Levels | From | To | Class | Owner |",
                  "| ---: | ---: | ---: | --- | --- | --- | --- |"]
        lines += rows(result["core_paths"])
    return "\n".join(lines) + "\n"


def analyze(config: dict, output: Path, mhz: int, paths: int, executable: str, sta_only: bool) -> dict:
    with quartus.build_lock(ROOT, output):
        quartus.confined(ROOT, output)
        if not sta_only:
            if output.exists():
                shutil.rmtree(output)
            quartus.stage(ROOT, output, config)
            if mhz != BOARD_MHZ:
                staged = output / config["constraints"]
                staged.write_text(what_if_sdc(staged.read_text(), mhz))
            quartus.run([executable, "--flow", "compile", config["project"]], output,
                        "compile.log", config["timeout_seconds"])
        elif not (output / "db").is_dir():
            raise ValueError(f"no compiled project in {output}")
        (output / "timing.tcl").write_text(TCL.format(project=config["project"], paths=paths))
        sta = str(Path(executable).with_name("quartus_sta"))
        quartus.run([sta, "-t", "timing.tcl"], output, "timing.log", config["timeout_seconds"])
        result = parse((output / "output_files" / "timing.tsv").read_text(encoding="latin-1"))
        check_clock(result, mhz)
        result["mhz"] = mhz
        (output / "timing.json").write_text(json.dumps(result, indent=2) + "\n")
        (output / "timing.md").write_text(markdown(result, mhz))
        return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="src/nand2doom/cfg/n2d_quartus.json")
    parser.add_argument("--board", choices=quartus.BOARDS, default=quartus.BASE_BOARD,
                        help=f"board to analyze (default {quartus.BASE_BOARD})")
    parser.add_argument("--quartus", help="quartus_sh executable (or QUARTUS_SH/PATH)")
    parser.add_argument("--mhz", type=int, nargs="+", default=[BOARD_MHZ, 50],
                        help="system clocks to analyze (default: 25 50)")
    parser.add_argument("--paths", type=int, default=20, help="setup paths per corner (default 20)")
    parser.add_argument("--sta-only", action="store_true",
                        help="rerun only the analysis on an already compiled project")
    parser.add_argument("--build-dir",
                        help="with --sta-only and one --mhz: the compiled project directory "
                             "below workdir/ (for example workdir/quartus/n2d_de2_115)")
    parser.add_argument("--plan", action="store_true", help="validate and show outputs without writing")
    args = parser.parse_args(argv)
    try:
        config = quartus.load_config(ROOT, args.config, args.board)
        if not 1 <= args.paths <= 200:
            raise ValueError("--paths must be 1..200")
        outputs = {mhz: ROOT / "workdir" / "timing" / f'{config["project"]}_{mhz}mhz'
                   for mhz in dict.fromkeys(args.mhz)}
        if args.build_dir:
            if not args.sta_only or len(outputs) != 1:
                raise ValueError("--build-dir requires --sta-only and exactly one --mhz")
            outputs = {mhz: quartus.confined(ROOT, (ROOT / args.build_dir).resolve())
                       for mhz in outputs}
        sdc = (ROOT / config["constraints"]).read_text()
        for mhz, output in outputs.items():
            if mhz != BOARD_MHZ:
                what_if_sdc(sdc, mhz)
            print(f"{mhz} MHz: {output.relative_to(ROOT)}")
        if args.plan:
            return 0
        executable = shutil.which(args.quartus or os.environ.get("QUARTUS_SH", "quartus_sh"))
        if not executable:
            raise ValueError("Quartus not found; pass --quartus or set QUARTUS_SH")
        for mhz, output in outputs.items():
            analyze(config, output, mhz, args.paths, executable, args.sta_only)
            print((output / "timing.md").read_text())
        return 0
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
