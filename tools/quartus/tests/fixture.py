"""A synthetic checkout (one demo project on two demo boards) and the reports a passing build of it leaves.

Every report is a sanitized stand-in in the shape Quartus 25.1 writes, never
vendor output; tests corrupt one obligation at a time.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.quartus import build  # noqa: E402

MANIFEST = "src/demo/cfg/quartus.json"
PROFILE = "src/demo/cfg/big.json"
SYSTEM_CLOCK = "u_clock|u_pll|auto_generated|pll1|clk[0]"
JTAG_HIERARCHY = "n2a_jtag_virtual:u_jtag_virtual|sld_virtual_jtag:u_vjtag"
UNUSED_JTAG_PORTS = ["virtual_state_e1dr", "tms", "jtag_state_tlr"]
CONNECTIVITY_DETAIL = ("Declared by entity but not connected by instance. "
                       "Logic that only feeds a dangling port will be removed.")
CONNECTIVITY_BORDER = "+-----+------+----------+--------+\n"
CONNECTIVITY = (f'; Port Connectivity Checks: "{JTAG_HIERARCHY}" ;\n' + CONNECTIVITY_BORDER +
                '; Port ; Type ; Severity ; Details ;\n' + CONNECTIVITY_BORDER +
                '; ir_out ; Input ; Info ; Stuck at GND ;\n' +
                "".join(f'; {port} ; Output ; Warning ; {CONNECTIVITY_DETAIL} ;\n' for port in UNUSED_JTAG_PORTS) +
                CONNECTIVITY_BORDER)
AGGREGATE = "Warning (12241): 1 hierarchies have connectivity warnings - see the Connectivity Checks report folder"
LICENSE = ("Warning (292013): Feature LogicLock is only available with a valid subscription license. "
           "You can purchase a software subscription to gain full access to this feature.")
TRUNCATION = "Warning (10230): Verilog HDL assignment warning at demo_top.sv(12): truncated value"
SWEPT = "Warning (14284): Synthesized away the following node(s):"

BOARDS = {
    "demo_board": {
        "board": "demo_board", "family": "MAX 10", "device": "10M50DAF484C7G", "reference_port": "CLK_50",
        "reference_hz": 50_000_000, "clock_module": "n2a_demo_clock", "default_io_standard": "3.3-V LVTTL",
        "io_standards": {"KEY": None, "LED": "2.5 V"},
        "pins": {"CLK_50": "P11", "KEY": ["B8", "A7"], "LED": ["A8", "A9", "A10", "B10"], "GPIO": ["V10", "W10"],
                 "DRAM_CLK": "L14", "DRAM_CKE": "N22", "DRAM_DQ": ["Y21", "Y20", "AA22", "AA21"],
                 "DRAM_ADDR": ["U17", "W19"]}},
    "demo_wide": {
        "board": "demo_wide", "family": "Cyclone IV E", "device": "EP4CE115F29C7", "reference_port": "CLOCK_50",
        "reference_hz": 50_000_000, "clock_module": "n2a_wide_clock", "default_io_standard": "3.3-V LVTTL",
        "io_standards": {"KEY": "2.5 V"},
        "pins": {"CLOCK_50": "Y2", "KEY": ["M23"], "GPIO": ["AB22", "AC15"], "DRAM_CLK": "AE5", "DRAM_CKE": "AA6",
                 "DRAM_DQ": ["W3", "W2", "V4", "W1"], "DRAM_ADDR": ["R6", "V8"], "VGA_CLK": "A12"}},
}
SOURCES = {
    "src/demo/fpga/demo_top.sv": "module demo_top (input logic CLK_50);\nendmodule\n",
    "src/demo/fpga/demo_wide.sv": "module demo_wide (input logic CLOCK_50);\nendmodule\n",
    "src/demo/fpga/demo_clock_intel.sv": "module n2a_demo_clock;\nendmodule\n",
    "src/demo/fpga/demo_clock_portable.sv": "module n2a_demo_clock;\nendmodule\n",
    "src/demo/fpga/demo.qsf": 'set_instance_assignment -name IO_STANDARD "3.3 V SCHMITT TRIGGER" -to RESET_N\n',
    "src/demo/fpga/demo.sdc": "create_clock -name reference -period 20.000 [get_ports CLK_50]\n",
    "src/demo/fpga/wide.qsf": "set_global_assignment -name SEED 1\n",
    "src/demo/fpga/wide.sdc": "create_clock -name reference -period 20.000 [get_ports CLOCK_50]\n",
}
LIBRARIES = {"schema_version": 1, "libraries": {
    "demo_top": {"deps": ["demo_clock"], "srcs": ["src/demo/fpga/demo_top.sv"]},
    "demo_clock": {"variants": {"intel": {"srcs": ["src/demo/fpga/demo_clock_intel.sv"]},
                                "default": {"srcs": ["src/demo/fpga/demo_clock_portable.sv"]}}}}}
CONTROLLER = r"u_memory\|n2a_sdram_ctrl:u_sdram\|"
RAMS = [
    {"prefix": "u_cache|n2a_sync_ram:g_way", "array": "u_ram|altsyncram:u_ram", "mode": "Single Port",
     "count": 2, "depth": 512, "width": 32, "m9ks_each": 2},
    {"name": "u_fifo|n2a_sdp_ram:u_ram", "mode": "Simple Dual Port", "count": 1, "depth": 256, "width": 8,
     "m9ks_each": 1},
    {"name": "n2a_tdp_ram:u_palette", "mode": "True Dual Port", "count": 1, "depth": 512, "width": 8, "m9ks_each": 1},
]
MANIFEST_DATA = {
    "board": "demo_board", "project": "demo_image", "top": "demo_top", "clock_hz": 25_000_000,
    "timeout_seconds": 60, "timing_corners": ["Slow 1200mV 85C", "Fast 1200mV 0C"],
    "libraries": ["demo_top"], "flavors": ["intel"],
    "settings": "src/demo/fpga/demo.qsf", "constraints": "src/demo/fpga/demo.sdc",
    "verilog_macros": ["DISABLE_ASSERTIONS=1"],
    "pins": ["CLK_50", "LED[3:0]", "DRAM_CLK", "DRAM_CKE", "DRAM_DQ[3:0]", "DRAM_ADDR[1:0]",
             "RESET_N=KEY[0]", "UART_RX=GPIO[0]"],
    "forwarded_clocks": {"sdram_clock": "DRAM_CLK"},
    "timing_paths": {
        "sdram_read": {"from_ports": "DRAM_DQ[*]", "launch": "sdram_clock", "latch": "system", "endpoints": [
            {"from": r"DRAM_DQ\[[0-3]\]", "to": CONTROLLER + r"read_data\[[0-3]\]"}]},
        "sdram_write": {"to_clock": "sdram_clock", "launch": "system", "latch": "sdram_clock", "endpoints": [
            {"from": CONTROLLER + ".+", "to": r"DRAM_(?!CLK$)\w+(\[\d+\])?"},
            {"from": r"n2a_reset_sync:u_reset\|.+", "to": "DRAM_CKE"}]}},
    "ram_requirements": RAMS,
    "reviewed_warnings": {
        "logiclock_count": 1, "pll_output": "DRAM_CLK", "constant_outputs": {"LED[3]": "GND"},
        "electrical_pins": {"CLK_50": "3.3-V LVTTL", "RESET_N": "3.3 V SCHMITT TRIGGER", "UART_RX": "3.3-V LVTTL",
                            **{f"DRAM_DQ[{i}]": "3.3-V LVTTL" for i in range(4)}},
        "unused_objects": [{"source": "src/demo/fpga/demo_top.sv", "object": "spare"}],
        "generated_ip_limits": {"altpll": 1, "altsyncram": 2},
        "connectivity": {"hierarchy": JTAG_HIERARCHY, "unused_outputs": UNUSED_JTAG_PORTS},
        "patterns": [r"Warning \(10230\): Verilog HDL assignment warning at demo_top\.sv\([0-9]+\): truncated value"]},
    "boards": {"demo_wide": {
        "project": "demo_wide", "top": "demo_wide", "timeout_seconds": 90,
        "settings": "src/demo/fpga/wide.qsf", "constraints": "src/demo/fpga/wide.sdc",
        "source_replacements": {"src/demo/fpga/demo_top.sv": "src/demo/fpga/demo_wide.sv"},
        "pins": ["CLOCK_50", "KEY[0]", "DRAM_CLK", "DRAM_CKE", "DRAM_DQ[3:0]", "DRAM_ADDR[1:0]", "VGA_CLK",
                 "UART_RX=GPIO[0]"],
        "forwarded_clocks": {"vga_dac_clock": "VGA_CLK"},
        "reviewed_warnings": {"constant_outputs": {}, "pll_output": "VGA_CLK",
                              "electrical_pins": {"CLOCK_50": "3.3-V LVTTL", "UART_RX": "3.3-V LVTTL"},
                              "unused_objects": []}}},
}
PROFILE_DATA = {
    "board": "demo_board", "project": "demo_image_big", "parameters": {"BIG": 1},
    "ram_requirements": [{"name": "u_big|n2a_sdp_ram:u_rom", "mode": "Simple Dual Port", "count": 1,
                          "depth": 4096, "width": 8, "m9ks_each": 4}],
    "reviewed_warnings": {"patterns": [
        {"pattern": r"Warning \(14284\): Synthesized away the following node\(s\):", "count": 1}]}}


def make_checkout(root: Path, manifest: dict | None = None, profile: dict | None = None) -> Path:
    """Write the demo project, its boards and its manifest below root; returns root."""
    files = {f"src/common/fpga/{name}/pins.json": json.dumps(data) for name, data in BOARDS.items()}
    files.update(SOURCES)
    files["src/demo/cfg/libraries.json"] = json.dumps(LIBRARIES)
    files[MANIFEST] = json.dumps(manifest or MANIFEST_DATA)
    files[PROFILE] = json.dumps(profile or PROFILE_DATA)
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    (root / "workdir").mkdir(exist_ok=True)
    return root


def warnings_for(config: dict) -> tuple[list[str], str]:
    """The compile log's reviewed warnings and the fit report's AN 447 section of a passing build."""
    top = next(name for name in config["sources"] if Path(name).stem == config["top"])
    contract = config["reviewed_warnings"]
    warnings = [f'Warning (10036): Verilog HDL or VHDL warning at {Path(item["source"]).name}(65): '
                f'object "{item["object"]}" assigned a value but never read File: /staged/{item["source"]} Line: 65'
                for item in contract.get("unused_objects", [])]
    warnings.append(AGGREGATE)
    constants = contract.get("constant_outputs", {})
    if constants:
        warnings.append("Warning (13024): Output pins are stuck at VCC or GND")
    warnings += [f'Warning (13410): Pin "{pin}" is stuck at {value} File: /staged/{top} Line: 99'
                 for pin, value in constants.items()]
    warnings += [LICENSE, TRUNCATION] + ([SWEPT] if config["profile"] else [])
    warnings.append(f'Warning (15064): PLL "{config["clock_module"]}:u_clock|altpll:u_pll|altpll_example:auto_generated|pll1" '
                    f'output port clk[0] feeds output pin "{contract["pll_output"]}~output" via non-dedicated routing -- '
                    'jitter performance depends on switching rate of other design elements. Use PLL '
                    'dedicated clock outputs to ensure jitter performance File: /staged/db/altpll_example.tdf Line: 31')
    pins = contract["electrical_pins"]
    advisory = (f"Warning (169177): {len(pins)} pins must meet Intel FPGA requirements for 3.3-, 3.0-, and 2.5-V "
                f"interfaces. For more information, refer to AN 447: Interfacing {config['family']} Devices "
                "with 3.3/3.0/2.5-V LVTTL/LVCMOS I/O Systems.")
    warnings.append(advisory)
    details = [f"    Info (169178): Pin {pin} uses I/O standard {standard} at {config['pins'][pin]} "
               f"File: /staged/{top} Line: 123" for pin, standard in pins.items()]
    return warnings, advisory + "\n" + "\n".join(details) + "\n"


def clocks_table(config: dict) -> str:
    """The Clocks table of the timing report: reference, JTAG, system and one inverted row per forwarded pin."""
    rows = ["; Clock Name ; Type ; Period ; Frequency ; Rise ; Fall ; Duty Cycle ; Divide by ; Multiply by ; Phase ;"
            " Offset ; Edge List ; Edge Shift ; Inverted ; Master ; Source ; Targets ;",
            f"; reference ; Base ; 20.000 ; 50.0 MHz ; 0.000 ; 10.000 ; ; ; ; ; ; ; ; ; ; ; {{ {config['reference_port']} }} ;",
            "; jtag_tck ; Base ; 100.000 ; 10.0 MHz ; 0.000 ; 50.000 ; ; ; ; ; ; ; ; ; ; ; { altera_reserved_tck } ;"]
    rows += [f"; {name} ; Generated ; 40.000 ; 25.0 MHz ; 20.000 ; 40.000 ; ; 1 ; 1 ; ; ; ; ; true ; {SYSTEM_CLOCK} ;"
             f" {SYSTEM_CLOCK} ; {{ {port} }} ;" for name, port in config["forwarded_clocks"].items()]
    rows.append(f"; {SYSTEM_CLOCK} ; Generated ; 40.000 ; 25.0 MHz ; 0.000 ; 20.000 ; 50.00 ; 2 ; 1 ; ; ; ; ; false ;"
                f" reference ; u_clock|u_pll|auto_generated|pll1|inclk[0] ; {{ {SYSTEM_CLOCK} }} ;")
    return "\n".join(rows) + "\n"


def path_report(corner: str, kind: str, rows: dict) -> str:
    """One single-path timing report, with irrelevant path-delay rows omitted."""
    return (f"Report Timing: Found 1 {kind} paths (0 violated). Worst case slack is {rows['Slack']}\n"
            f"Delay Model:\n {corner} Model\nPath #1: {kind.title()} slack is {rows['Slack']}\n"
            "; Path Summary ;\n; Property ; Value ;\n" +
            "".join(f"; {key} ; {value} ;\n" for key, value in rows.items()))


def path_rows(config: dict, group: str) -> dict:
    """A reviewed path of one path group: its endpoints and clocks."""
    spec = config["timing_paths"][group]
    clock = {"system": SYSTEM_CLOCK}
    ends = {"sdram_read": ("DRAM_DQ[2]", "u_memory|n2a_sdram_ctrl:u_sdram|read_data[2]"),
            "sdram_write": ("u_memory|n2a_sdram_ctrl:u_sdram|beat_q[1]", "DRAM_ADDR[1]")}[group]
    return {"From Node": ends[0], "To Node": ends[1], "Launch Clock": clock.get(spec["launch"], spec["launch"]),
            "Latch Clock": clock.get(spec["latch"], spec["latch"]), "Slack": "3.870"}


def fit_report(config: dict, warnings_section: str) -> str:
    """Resource use, a RAM summary row for every required RAM, and the AN 447 section."""
    m9ks = sum(r["count"] * r["m9ks_each"] for r in config["ram_requirements"])
    lines = ["; Total logic elements ; 12,345 / 49,760 ;", "; Total registers* ; 9000 / 51,000 ;",
             f"; M9Ks ; {m9ks} / 182 ;", "; Total PLLs ; 1 / 4 ;", "; Embedded Multiplier 9-bit elements ; 0 / 288 ;",
             "; Name ; Type ; Mode ; Clock Mode ; Port A Depth ; Port A Width ; Port B Depth ; Port B Width ;"
             " M9Ks ; Mixed Width RDW Mode ; Port A RDW Mode ; Port B RDW Mode ;"]
    for requirement in config["ram_requirements"]:
        for index in range(requirement["count"]):
            name = (f'top|{requirement["name"]}|altsyncram_{index}:auto_generated|ALTSYNCRAM' if "name" in requirement
                    else f'top|{requirement["prefix"]}[{index}].{requirement["array"]}|ALTSYNCRAM')
            mode = requirement["mode"]
            single = mode == "Single Port"
            port_b = "-- ; --" if single else f'{requirement["depth"]} ; {requirement["width"]}'
            rdw = {"Single Port": "Don't care ; Old data", "Simple Dual Port": "Old data ; New data with NBE Read",
                   "True Dual Port": "Old data ; Old data"}[mode]
            lines.append(f'; {name} ; AUTO ; {mode} ; Single Clock ; {requirement["depth"]} ; {requirement["width"]} ;'
                         f' {port_b} ; {requirement["m9ks_each"]} ; {rdw} ; New data with NBE Read ;')
    return "\n".join(lines) + "\n" + warnings_section


def check_timing(config: dict) -> str:
    counts = {"no_clock": 1, "virtual_clock": 1, "no_output_delay": len(config["forwarded_clocks"])}
    categories = """no_clock multiple_clock pos_neg_clock_domain generated_clock virtual_clock no_input_delay
        no_output_delay partial_input_delay partial_output_delay io_min_max_delay_consistency reference_pin
        generated_io_delay latency_override partial_multicycle multicycle_consistency loops latches pll_cross_check
        uncertainty partial_min_max_delay clock_assignments_on_output_ports input_delay_assigned_to_clock""".split()
    return ("\n".join(f"; {name} ; {counts.get(name, 0)} ;" for name in categories) + "\n" +
            "".join(f"; {port} ; No output delay was set on output port. This port has clock assignments. ;\n"
                    for port in config["forwarded_clocks"].values()) +
            f"; {config['clock_module']}:u_clock|altpll:u_pll|altpll_example:auto_generated|pll_lock_sync ;"
            " No clock feeds this register's clock port. ;\n; ; No virtual clock was found. ;\n")


def write_build(root: Path, output: Path, config: dict) -> Path:
    """Stage config into output and write every report and log of a passing compile and audit."""
    build.stage(root, output, config)
    reports = output / "output_files"
    reports.mkdir(parents=True, exist_ok=True)
    project = config["project"]
    for name in ("asm.rpt", "sof"):
        (reports / f"{project}.{name}").write_text("fixture\n")
    timing = []
    for corner in config["timing_corners"]:
        for clock, kinds in ((SYSTEM_CLOCK, ("Setup", "Hold", "Recovery", "Removal", "Minimum Pulse Width")),
                             *((name, ("Setup", "Hold")) for name in config["forwarded_clocks"]),
                             ("jtag_tck", ("Setup", "Hold", "Minimum Pulse Width")),
                             ("reference", ("Minimum Pulse Width",))):
            timing += [f"Type : {corner} Model {kind} '{clock}'\nSlack : 0.125\nTNS : 0.000\n" for kind in kinds]
        label = build.corner_label(corner)
        (reports / f"fmax_{label}.rpt").write_text(
            "; Fmax ; Restricted Fmax ; Clock Name ; Note ;\n"
            f"; 27.5 MHz ; 27.5 MHz ; {SYSTEM_CLOCK} ; ;\n; 98.0 MHz ; 98.0 MHz ; jtag_tck ; ;\n")
        for group in config["timing_paths"]:
            for kind in ("setup", "hold"):
                (reports / f"{group}_{label}_{kind}.rpt").write_text(path_report(corner, kind, path_rows(config, group)))
    (reports / f"{project}.sta.summary").write_text("\n".join(timing))
    (reports / f"{project}.sta.rpt").write_text(clocks_table(config))
    warnings, electrical = warnings_for(config)
    (reports / f"{project}.fit.rpt").write_text(fit_report(config, electrical))
    (reports / f"{project}.map.rpt").write_text(CONNECTIVITY)
    (reports / "unconstrained.rpt").write_text("\n".join(
        f"; {c} ; 0 ; 0 ;" for c in ("Illegal Clocks", "Unconstrained Clocks", "Unconstrained Input Ports",
                                     "Unconstrained Input Port Paths", "Unconstrained Output Ports",
                                     "Unconstrained Output Port Paths")))
    (reports / "clocks.tsv").write_text(f"reference\t20\njtag_tck\t100\n{SYSTEM_CLOCK}\t40\n" +
                                        "".join(f"{name}\t40\n" for name in config["forwarded_clocks"]))
    for name in ("setup.rpt", "hold.rpt"):
        (reports / name).write_text("fixture\n")
    (reports / "check_timing.rpt").write_text(check_timing(config))
    (reports / "ignored.rpt").write_text("-------------------\n; SDC Assignments ;\n"
                                         "-------------------\nNo constraints were ignored.\n")
    (output / "compile.log").write_text("\n".join(warnings) + "\n")
    (output / "audit.log").write_text("")
    return reports
