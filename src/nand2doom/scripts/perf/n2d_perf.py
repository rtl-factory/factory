#!/usr/bin/env python3
"""Run the native nand2doom Doom performance harness and summarize it.

The harness (`n2d_perf_tb`) preloads the pinned Doom image into the product
system, starts the CPU over real UART packets and records per-frame cycles,
retirements, cache events, framebuffer CRCs and VGA rasters. A frame ends at
a framebuffer bank swap. This script builds or reuses the image, compiles the
selected backend and framebuffer-geometry target with the simulation runner
(tools/sim), runs it, parses its report and checks optional expected CRCs.

Spec: wiki/src/nand2doom/scripts/perf/spec.md#harness-run.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))
from src.nand2doom.scripts.sw_build.n2d_doom import VARIANTS, _sha256  # noqa: E402
from src.nand2doom.scripts.host.n2d_uart_debug_host import (  # noqa: E402
    CMD_RESET_PC, CMD_RESUME, input_request, request,
)

BACKENDS = {"fast": "n2d_perf_fast", "sdram": "n2d_perf_sdram"}
# Legacy oracle, scoped to its reference firmware. Firmware instruction changes
# can change deterministic game tics; never repin ordinary frame ordinals.
DETERMINISTIC_PINS = {
    "scene": "9da80229",
    "scene_vga": "516470a8",
    "gameplay": ["53dfbd59", "a4c3aa79"],
    "gameplay_vga": ["d4cec909", "855eb305"],
}
LEGACY_IMAGE_SHA256 = "b00113ee9f38aec8cfdeb1d6d24df4784a331c41b05c7d1391a6c8c36b750b98"
LEGACY_ELF_SHA256 = "7124e815b3181fb4c84b85d83011d093bc7cac361876374205509aee8723904a"
RENDER_ORACLE_PATH = REPO_ROOT / "src/nand2doom/dv/software/render_oracle.json"
RENDER_HALF_ORACLE_PATH = RENDER_ORACLE_PATH.with_name("render_oracle_half.json")



def build_command(target: str) -> list[str]:
    """Build the native harness with tools/sim; its targets are compile-only."""
    return [sys.executable, "tools/sim/sim.py", "test", target]

def render_oracle_path(geometry: str) -> Path:
    if geometry not in ("full", "half"):
        raise ValueError(f"unknown render geometry: {geometry}")
    return RENDER_ORACLE_PATH if geometry == "full" else RENDER_HALF_ORACLE_PATH


def render_oracle(geometry: str = "full") -> dict:
    """Shared full state/call contract with independently projected half pixels."""
    fixture = json.loads(RENDER_ORACLE_PATH.read_text())
    if geometry == "full":
        return fixture
    half = json.loads(render_oracle_path(geometry).read_text())
    if (half["full_oracle"]["path"] != RENDER_ORACLE_PATH.name
            or half["full_oracle"]["sha256"] != _sha256(RENDER_ORACLE_PATH)
            or half["geometry"] != "half" or half["schema_version"] != 1):
        raise ValueError("half oracle full reference/geometry/schema differs")
    for group in ("framebuffer_sha256", "vga_ppm_sha256", "crcs"):
        if half[group].keys() != fixture[group].keys():
            raise ValueError(f"half oracle pixel/CRC membership differs: {group}")
    if any(len(half["crcs"][group]) != fixture["frames"] for group in ("gameplay", "gameplay_vga")):
        raise ValueError("half oracle requires two gameplay pixel pins")
    for key in ("geometry", "crcs", "framebuffer_sha256", "vga_ppm_sha256"):
        fixture[key] = half[key]
    fixture["projection"] = half["projection"]
    return fixture


# Framebuffer geometry target suffix: full width (DE2-115) or half width
# (DE10-Lite).
GAME_STATE_SYMBOLS = ("gametic", "maketic", "ticdup", "leveltime",
                      "prndindex", "rndindex", "fuzzpos")
GEOMETRIES = {"full": "", "half": "_half"}
PACKET_GAP = 0x100  # list token: wait before the next packet
INPUT_LIMIT = 8     # the unified input FIFO depth; nothing drains it while halted
CLOCK_HZ = 25_000_000

# PMON event names by ID (wiki/src/nand2doom/rtl/core/mas.md "Performance monitor").
PMON_EVENTS = {
    1: "CYCLES", 2: "INSTRET", 3: "IFETCH_WAIT", 4: "DATA_HOLD", 5: "LOAD_USE_HOLD",
    6: "MULDIV_HOLD", 8: "DATA_ADMISSION_HOLD", 9: "BRANCH_REDIRECT", 10: "TRAP_ENTRY",
    12: "ICACHE_DEMAND_ACCESS", 13: "ICACHE_DEMAND_MISS", 14: "ICACHE_REFILL",
    15: "DCACHE_READ_ACCESS", 16: "DCACHE_READ_MISS", 17: "DCACHE_WRITE_MISS",
    18: "DCACHE_REFILL", 19: "DCACHE_WRITEBACK", 29: "MEM_FETCH_READ", 30: "MEM_DATA_READ",
    32: "MEM_WRITE", 33: "MEM_BACKPRESSURE", 34: "MEM_READ_SERVICE",
    64: "DCACHE_WRITE_ACCESS", 65: "UNCACHED_FB_STORE", 66: "UNCACHED_MMIO_ACCESS",
    67: "UNCACHED_WAIT", 68: "SYSTEM_SERIALIZE_HOLD", 69: "MUL_USE_HOLD",
    76: "DEBUG_HALTED", 77: "MEM_MASTER_WAIT", 78: "BRANCH_PREDICT_TAKEN",
    79: "BRANCH_MISPREDICT", 80: "DCACHE_PREFETCH_ISSUE", 81: "DCACHE_PREFETCH_USEFUL",
    82: "DCACHE_PREFETCH_DROP", 83: "IFETCH_PREDICT_BUBBLE", 84: "IFETCH_MISPREDICT_REFILL",
    85: "IFETCH_REDIRECT_REFILL", 86: "IFETCH_SUPPLY_WAIT", 87: "MEM_OWNER_FETCH",
    88: "MEM_OWNER_DATA_READ", 89: "MEM_OWNER_DATA_PREFETCH", 90: "MEM_OWNER_DATA_WRITE",
    91: "MEM_OWNER_DEBUG", 92: "FETCH_PREDICT_TAKEN", 93: "FETCH_PREDICT_STALE",
    94: "DCACHE_WRITE_BUFFER_PENDING", 95: "DCACHE_WRITE_BUFFER_WAIT",
    96: "DCACHE_PREFETCH_DEFERRED", 97: "DCACHE_PREFETCH_CANCEL",
}
# Exclusive per-clock cycle classes in harness priority order; "other" is
# the unattributed remainder.
CYCLE_CLASSES = ("retire", "divide", "uncached_wait", "dmiss_wait", "load_use",
               "mul_use", "data_admission", "serialize", "ifetch_wait", "halted",
               "other")
# Root-cause split of two stall classes (wiki/src/nand2doom/dv/platform/dv-plan.md "Stall split"): each
# clock of the parent class is in exactly one of its split classes.
STALL_SPLIT = {
    "ifetch_wait": ("ifetch_predict", "ifetch_mispredict", "ifetch_redirect", "ifetch_miss",
                    "ifetch_other"),
    "dmiss_wait": ("dmiss_service", "dmiss_fetch", "dmiss_prefetch", "dmiss_writeback",
                   "dmiss_debug", "dmiss_refresh", "dmiss_cache"),
}
SPLIT_CLASSES = tuple(name for names in STALL_SPLIT.values() for name in names)


def uart_tokens(inputs: tuple[int, ...] = ()) -> list[int]:
    """Byte tokens for Reset-PC to zero, optional queued Set-2 input events,
    then Resume, with response gaps."""
    if len(inputs) > INPUT_LIMIT or any(not 0 <= e <= 0x3FF for e in inputs):
        raise ValueError(f"at most {INPUT_LIMIT} input events, each 0..0x3ff")
    packets = [request(CMD_RESET_PC, 1, address=0)]
    packets += [input_request(2 + index, event) for index, event in enumerate(inputs)]
    packets.append(request(CMD_RESUME, 2 + len(inputs)))
    tokens: list[int] = []
    for packet in packets:
        tokens.extend(packet)
        tokens.append(PACKET_GAP)
    return tokens


def preload_lines(image_dir: Path, manifest: dict) -> list[str]:
    lines = []
    for segment in manifest["segments"]:
        path = (image_dir / segment["path"]).resolve()
        if path.stat().st_size != int(segment["byte_length"]):
            raise ValueError(f"segment size mismatch: {path}")
        lines.append(f"{int(segment['address']):07x} {path}")
    return lines


def _fields(line: str) -> tuple[str, dict[str, str]]:
    kind, _, rest = line.partition(" ")
    values = {}
    for item in rest.split():
        key, sep, value = item.partition("=")
        if sep:
            values[key] = value
    return kind, values


def _pmon_events(values: dict[str, str]) -> dict[str, int]:
    """Map e<ID>=count fields to event names; unknown IDs keep their number."""
    events = {}
    for key, value in values.items():
        if key.startswith("e") and key[1:].isdigit():
            event_id = int(key[1:])
            events[PMON_EVENTS.get(event_id, f"EVENT_{event_id}")] = int(value)
    return events


def parse_report(text: str) -> dict:
    """Parse the harness key=value report into a structured summary."""
    result: dict = {"markers": [], "frames": [], "states": [], "vga": {}, "scene": None,
                    "done": None, "trap": None, "resumed": None, "swap": None, "single_tic": [],
                    "pmon": {"total": None, "intervals": []}}
    for line in text.splitlines():
        kind, values = _fields(line.strip())
        if kind in ("pmon", "cycles", "split"):
            scope, index = values["scope"], int(values["index"])
            key = {"pmon": "events", "cycles": "cycles", "split": "split"}[kind]
            if kind == "pmon":
                data = _pmon_events(values)
            else:
                names = CYCLE_CLASSES if kind == "cycles" else SPLIT_CLASSES
                data = {name: int(values[name]) for name in names if name in values}
            if scope == "total":
                result["pmon"]["total"] = result["pmon"]["total"] or {}
                result["pmon"]["total"][key] = data
            else:
                intervals = result["pmon"]["intervals"]
                if not intervals or intervals[-1]["index"] != index:
                    intervals.append({"index": index, "gameplay": scope == "gameplay"})
                intervals[-1][key] = data
        elif kind == "marker":
            result["markers"].append({k: values[k] for k in ("cycle", "value", "retired")})
        elif kind == "frame":
            frame = {k: int(values[k], 0) for k in (
                "index", "start", "end", "retired_start", "retired_end",
                "ihit", "imiss", "dhit", "dmiss", "after_scene")}
            for key in ("wait", "spin"):
                if key in values:
                    frame[key] = int(values[key])
            frame["crc"] = values["crc"]
            result["frames"].append(frame)
        elif kind == "state":
            result["states"].append({k: v if k == "label" else int(v, 0)
                                     for k, v in values.items()})
        elif kind == "single_tic":
            result["single_tic"].append({k: int(v, 0) for k, v in values.items()})
        elif kind == "scene":
            result["scene"] = {"cycle": int(values["cycle"]), "retired": int(values["retired"]),
                               "crc": values["crc"], "ihit": int(values["ihit"]),
                               "imiss": int(values["imiss"]), "dhit": int(values["dhit"]),
                               "dmiss": int(values["dmiss"])}
        elif kind == "vga":
            result["vga"][values["label"]] = values["crc"]
        elif kind == "done":
            result["done"] = {"status": values["status"], "run_cycles": int(values["run_cycles"]),
                              "retired": int(values["retired"]), "ihit": int(values["ihit"]),
                              "imiss": int(values["imiss"]), "dhit": int(values["dhit"]),
                              "dmiss": int(values["dmiss"])}
        elif kind == "swap":
            result["swap"] = {"geometry": values["geometry"],
                              **{k: int(values[k]) for k in (
                                  "count", "wait", "spin", "guard_violations")}}
        elif kind == "trap":
            result["trap"] = values
        elif kind == "resumed":
            result["resumed"] = int(values["cycle"])
    return result


def split_errors(classes: dict[str, int], split: dict[str, int] | None) -> list[str]:
    """Self-check: each split must sum exactly to its parent class."""
    if split is None:
        return []
    errors = []
    for parent, names in STALL_SPLIT.items():
        total = sum(split.get(name, 0) for name in names)
        if total != classes.get(parent, 0):
            errors.append(f"{parent} split sums to {total}, class is {classes.get(parent, 0)}")
    return errors


def stall_split(classes: dict[str, int], split: dict[str, int]) -> dict:
    """Each split class as cycles, percent of all cycles and percent of its
    parent class."""
    cycles = sum(classes.values())
    report: dict = {}
    for parent, names in STALL_SPLIT.items():
        parent_cycles = classes.get(parent, 0)
        report[parent] = {"cycles": parent_cycles, "classes": {
            name: {"cycles": split.get(name, 0),
                   "cycle_percent": round(100 * split.get(name, 0) / cycles, 3) if cycles else None,
                   "parent_percent": (round(100 * split.get(name, 0) / parent_cycles, 2)
                                      if parent_cycles else None)}
            for name in names}}
    return report


def fetch_supply_wait(events: dict[str, int]) -> int:
    """IFETCH_WAIT clocks spent on fetch supply (I-cache misses and fills):
    the exact IFETCH_SUPPLY_WAIT when reported, else IFETCH_WAIT less two
    refill clocks per taken transfer, which holds only for reports from
    cores before the fetch BTB."""
    if "IFETCH_SUPPLY_WAIT" in events:
        return events["IFETCH_SUPPLY_WAIT"]
    return max(0, events.get("IFETCH_WAIT", 0) - 2 * events.get("BRANCH_REDIRECT", 0))


def cycle_breakdown(events: dict[str, int], classes: dict[str, int],
                    split: dict[str, int] | None = None) -> dict:
    """IPC first, then the exclusive cycle classes as percentages of all
    cycles, their coverage, the stall split, and derived hit ratios and miss
    latencies."""
    cycles = sum(classes.values())
    retired = events.get("INSTRET", 0)
    stack: dict = {"ipc": round(retired / cycles, 4) if cycles else None,
                   "cycles": cycles, "retired": retired}
    if cycles:
        stack["coverage_percent"] = round(100 * (1 - classes.get("other", 0) / cycles), 3)
        stack["cycle_percent"] = {name: round(100 * classes.get(name, 0) / cycles, 3)
                                  for name in CYCLE_CLASSES}
    if split is not None:
        stack["stall_split"] = stall_split(classes, split)

    def ratio(numerator: int, denominator: int, digits: int = 5):
        return round(numerator / denominator, digits) if denominator else None

    i_access, i_miss = events.get("ICACHE_DEMAND_ACCESS", 0), events.get("ICACHE_DEMAND_MISS", 0)
    d_read, d_write = events.get("DCACHE_READ_ACCESS", 0), events.get("DCACHE_WRITE_ACCESS", 0)
    d_miss = events.get("DCACHE_READ_MISS", 0) + events.get("DCACHE_WRITE_MISS", 0)
    stack["icache_hit_ratio"] = ratio(i_access - i_miss, i_access)
    stack["dcache_hit_ratio"] = ratio(d_read + d_write - d_miss, d_read + d_write)
    stack["dcache_read_hit_ratio"] = ratio(d_read - events.get("DCACHE_READ_MISS", 0), d_read)
    stack["dcache_write_hit_ratio"] = ratio(d_write - events.get("DCACHE_WRITE_MISS", 0), d_write)
    stack["imiss_penalty_cycles"] = ratio(fetch_supply_wait(events), i_miss, 2)
    stack["dmiss_penalty_cycles"] = ratio(classes.get("dmiss_wait", 0), d_miss, 2)
    stack["dcache_writebacks_per_miss"] = ratio(events.get("DCACHE_WRITEBACK", 0), d_miss, 3)
    stack["line_read_latency_cycles"] = ratio(
        events.get("MEM_READ_SERVICE", 0),
        events.get("MEM_FETCH_READ", 0) + events.get("MEM_DATA_READ", 0), 2)
    stack["uncached_wait_per_access"] = ratio(
        events.get("UNCACHED_WAIT", 0),
        events.get("UNCACHED_FB_STORE", 0) + events.get("UNCACHED_MMIO_ACCESS", 0), 2)
    # Next-line D-prefetch (wiki/src/nand2doom/rtl/memory/mas.md): share of issued lines a miss used.
    stack["dcache_prefetch_accuracy"] = ratio(events.get("DCACHE_PREFETCH_USEFUL", 0),
                                              events.get("DCACHE_PREFETCH_ISSUE", 0), 4)
    return stack


def summarize(result: dict) -> dict:
    """Derive IPC, cycles per gameplay frame and hit ratios."""
    summary: dict = {}
    scene = result["scene"]
    if scene:
        summary["scene_cycles"] = scene["cycle"]
        summary["scene_ipc"] = round(scene["retired"] / scene["cycle"], 4) if scene["cycle"] else None
    gameplay = [f for f in result["frames"] if f["after_scene"]]
    # A gameplay frame interval is end-to-end between consecutive bank swaps.
    ends = [f["end"] for f in result["frames"]]
    retires = [f["retired_end"] for f in result["frames"]]
    if len(gameplay) >= 1 and len(ends) >= 2:
        first = len(ends) - len(gameplay)
        intervals = [ends[i] - ends[i - 1] for i in range(max(first, 1), len(ends))]
        retired = [retires[i] - retires[i - 1] for i in range(max(first, 1), len(ends))]
        if intervals:
            mean = sum(intervals) / len(intervals)
            summary["gameplay_frames"] = len(intervals)
            summary["cycles_per_frame"] = round(mean)
            summary["fps_at_25mhz"] = round(CLOCK_HZ / mean, 3)
            summary["gameplay_ipc"] = round(sum(retired) / sum(intervals), 4)
    # Swap request to swap completion is what a frame-end blocking wait would
    # spend; spin is what the deferred wait actually spends polling.
    timed = [f for f in gameplay if "wait" in f]
    if timed:
        summary["swap_wait_cycles_per_frame"] = round(sum(f["wait"] for f in timed) / len(timed))
        summary["swap_spin_cycles_per_frame"] = round(sum(f["spin"] for f in timed) / len(timed))
    if result.get("swap"):
        summary["swap"] = result["swap"]
    done = result["done"]
    if done:
        summary["icache_hit_ratio"] = (round(done["ihit"] / (done["ihit"] + done["imiss"]), 5)
                                       if done["ihit"] + done["imiss"] else None)
        summary["dcache_hit_ratio"] = (round(done["dhit"] / (done["dhit"] + done["dmiss"]), 5)
                                       if done["dhit"] + done["dmiss"] else None)
        summary["status"] = done["status"]
    pmon = result.get("pmon") or {}
    total = pmon.get("total")
    if total and "events" in total and "cycles" in total:
        summary["pmon_total"] = cycle_breakdown(total["events"], total["cycles"],
                                                total.get("split"))
    gameplay = [i for i in pmon.get("intervals", []) if i.get("gameplay")
                and "events" in i and "cycles" in i]
    if gameplay:
        events: dict[str, int] = {}
        classes: dict[str, int] = {}
        split: dict[str, int] | None = {} if all("split" in i for i in gameplay) else None
        for interval in gameplay:
            for name, value in interval["events"].items():
                events[name] = events.get(name, 0) + value
            for name, value in interval["cycles"].items():
                classes[name] = classes.get(name, 0) + value
            for name, value in interval.get("split", {}).items():
                if split is not None:
                    split[name] = split.get(name, 0) + value
        summary["pmon_gameplay"] = cycle_breakdown(events, classes, split)
        summary["pmon_gameplay"]["frames"] = len(gameplay)
        summary["gameplay_frame_ipc"] = [
            round(i["events"].get("INSTRET", 0) / sum(i["cycles"].values()), 4)
            for i in gameplay if sum(i["cycles"].values())]
    lead = ("overall_ipc", "scene_ipc", "gameplay_ipc", "gameplay_frame_ipc")
    if total and "pmon_total" in summary:
        summary["overall_ipc"] = summary["pmon_total"]["ipc"]
    summary = {**{k: summary[k] for k in lead if k in summary},
               **{k: v for k, v in summary.items() if k not in lead}}
    return summary


def oracle(result: dict) -> dict:
    """The CRCs that the deterministic image pins: scene and gameplay frames."""
    gameplay = [f for f in result["frames"] if f["after_scene"]]
    vga = result["vga"]
    return {
        "scene": result["scene"]["crc"] if result["scene"] else None,
        "scene_vga": vga.get("scene"),
        "gameplay": [f["crc"] for f in gameplay],
        "gameplay_vga": [vga.get(f"frame-{i:03d}") for i in range(1, len(gameplay) + 1)],
    }


def check_pins(observed: dict, pins: dict = DETERMINISTIC_PINS) -> list[str]:
    """Compare an oracle against the pins, up to the gameplay frames recorded."""
    errors = []
    for key in ("scene", "scene_vga"):
        if observed[key] is None or int(observed[key], 16) != int(pins[key], 16):
            errors.append(f"{key} CRC {observed[key]} != pinned {pins[key]}")
    for key in ("gameplay", "gameplay_vga"):
        for index, value in enumerate(observed[key][:len(pins[key])]):
            if value is None or int(value, 16) != int(pins[key][index], 16):
                errors.append(f"{key}[{index}] CRC {value} != pinned {pins[key][index]}")
    return errors



def validate_pin_image(image_dir: Path, manifest: dict, variant: str) -> None:
    """Fail before expensive compilation on incompatible/stale pin inputs."""
    from src.nand2doom.scripts.sw_build.n2d_doom import VARIANTS, WAD_SHA256, _sha256
    expected = VARIANTS[variant]["defines"]
    if manifest["variant"]["name"] != variant or manifest["variant"]["defines"] != expected:
        raise ValueError("pin image variant/defines differ")
    if (manifest["variant"]["time_source"] != VARIANTS[variant]["time_source"]
            or (manifest["architecture"], manifest["abi"], manifest["endianness"], manifest["entry"])
            != ("rv32im", "ilp32", "little", 0)
            or [s["address"] for s in manifest["segments"]] != [0, 0x02000000]):
        raise ValueError("pin image execution/layout configuration differs")
    for segment in manifest["segments"]:
        path = image_dir / segment["path"]
        if path.stat().st_size != segment["byte_length"] or _sha256(path) != segment["sha256"]:
            raise ValueError("pin image segment bytes differ from manifest")
    if manifest["segments"][1]["sha256"] != WAD_SHA256:
        raise ValueError("pin image WAD differs")
    if variant == "deterministic":
        if manifest["segments"][0]["sha256"] != LEGACY_IMAGE_SHA256:
            raise ValueError("legacy deterministic pins require reference firmware " + LEGACY_IMAGE_SHA256)
        if _sha256(image_dir / manifest["capture"]["elf"]["path"]) != LEGACY_ELF_SHA256:
            raise ValueError("legacy deterministic pins require reference ELF")
        return
    fixture = render_oracle()
    if manifest["engine"]["revision"] != fixture["provenance"]["engine_revision"]:
        raise ValueError("render oracle engine revision differs")
    capture = manifest["capture"]
    inputs = capture["identity_inputs"]
    if inputs["variant_defines"] != expected:
        raise ValueError("capture identity omits verification defines")
    for relative, digest in inputs["sources"].items():
        if _sha256(REPO_ROOT / relative) != digest:
            raise ValueError(f"capture identity source differs: {relative}; rebuild this image")
    if not {"src/nand2doom/sw/doom/platform.c", "src/nand2doom/scripts/sw_build/n2d_doom.py"} <= inputs["sources"].keys():
        raise ValueError("capture identity omits verification port/tool sources")
    identity = hashlib.sha256(json.dumps(inputs, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if identity != capture["identity"]:
        raise ValueError("capture recipe identity differs")
    if _sha256(image_dir / capture["elf"]["path"]) != capture["elf"]["sha256"]:
        raise ValueError("pin image ELF differs")
    binary = (image_dir / manifest["segments"][0]["path"]).read_bytes()
    address = capture["identity_address"]
    if binary[address:address + 32] != bytes.fromhex(identity):
        raise ValueError("capture identity differs from actual binary")


def check_render(result: dict, output: Path, fixture: dict | None = None) -> list[str]:
    """State-qualified no-input rendering oracle, independent of CPU speed."""
    fixture = fixture or render_oracle()
    errors = check(result, None, None)
    swap = result.get("swap")
    if (not swap or swap.get("geometry") != fixture["geometry"]
            or type(swap.get("guard_violations")) is not int
            or swap["guard_violations"] != 0):
        errors.append("render requires fixture-selected geometry and zero swap guards")
    if not result["done"] or result["done"]["status"] != "pass":
        errors.append("render verification requires complete pass, not a trace window")
    scene = [s for s in result["states"] if s["label"] == "scene"]
    frames = [f for f in result["frames"] if f["after_scene"]]
    if len(scene) != 1 or len(frames) != 2:
        return errors + ["render verification requires one scene and two gameplay frames"]
    states = [scene[0]]
    for frame in frames:
        matching = [s for s in result["states"] if s["label"] == "frame" and s["index"] == frame["index"]]
        if len(matching) != 1:
            return errors + ["missing/duplicate passive gameplay state"]
        states.append(matching[0])
    observed = [{key: s.get(key) for key in GAME_STATE_SYMBOLS} for s in states]
    if observed != fixture["states"] or any(type(v) is not int for state in observed for v in state.values()):
        errors.append(f"render game states differ: {observed}")
    calls = result["single_tic"]
    if (len(calls) < 3 or [c.get("display") for c in calls] != list(range(1, len(calls) + 1))
            or calls[0].get("tick_calls") != 2 or calls[0].get("since_previous") != 2
            or any(c.get("mode") != 1 for c in calls)
            or any(c.get("since_previous") != 1 or c.get("tick_calls") != c["display"] + 1 for c in calls[1:])):
        errors.append("missing/invalid compiled single-tic mode/call witnesses")
    errors += check_pins(oracle(result), fixture["crcs"])
    for group in ("framebuffer_sha256", "vga_ppm_sha256"):
        for name, digest in fixture[group].items():
            path = output / name
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                errors.append(f"render pixel bytes differ or absent: {name}")
    return errors


def check(result: dict, expect_scene: str | None, expect_scene_vga: str | None) -> list[str]:
    errors = []
    if result["trap"]:
        errors.append(f"trap: {result['trap']}")
    if result.get("swap") and result["swap"].get("guard_violations"):
        errors.append(f"framebuffer writes while a swap was pending: {result['swap']}")
    if not result["done"] or result["done"]["status"] not in ("pass", "fst-window"):
        errors.append(f"run did not pass: {result['done']}")
    if expect_scene and (not result["scene"] or int(result["scene"]["crc"], 16) != int(expect_scene, 16)):
        errors.append(f"scene CRC {result['scene'] and result['scene']['crc']} != {expect_scene}")
    if expect_scene_vga and int(result["vga"].get("scene", "0x0"), 16) != int(expect_scene_vga, 16):
        errors.append(f"scene VGA CRC {result['vga'].get('scene')} != {expect_scene_vga}")
    pmon = result.get("pmon") or {}
    for scope in [pmon.get("total") or {}] + pmon.get("intervals", []):
        if "cycles" in scope:
            errors += split_errors(scope["cycles"], scope.get("split"))
    return errors


def compress_stream(path: Path) -> Path:
    """Gzip the raw binary stream in place and return the .gz path."""
    target = path.with_name(path.name + ".gz")
    with path.open("rb") as source, gzip.open(target, "wb", compresslevel=6) as sink:
        while chunk := source.read(1 << 20):
            sink.write(chunk)
    path.unlink()
    return target


def _png(ppm_dir: Path) -> None:
    try:
        from PIL import Image
    except ImportError:
        return
    for ppm in sorted(ppm_dir.glob("*.ppm")):
        Image.open(ppm).save(ppm.with_suffix(".png"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=sorted(BACKENDS), default="fast")
    parser.add_argument("--geometry", choices=sorted(GEOMETRIES), default="full",
                        help="framebuffer geometry: full (DE2-115, the IPC measurement) or half (DE10-Lite)")
    parser.add_argument("--frames", type=int, default=2,
                        help="gameplay frames to record after the first completed scene")
    parser.add_argument("--max-cycles", type=int, default=400_000_000)
    parser.add_argument("--variant", choices=tuple(VARIANTS),
                        default="deterministic",
                        help="firmware (default: deterministic); render-verification is a "
                             "state-qualified correctness workload, not production FPS")
    parser.add_argument("--image-dir", default=None,
                        help="image directory (default: the variant's n2d_doom.py output)")
    parser.add_argument("--reuse-image", action="store_true",
                        help="use an existing manifest instead of rebuilding the image")
    parser.add_argument("--output", default=None, help="run directory below workdir/")
    parser.add_argument("--trace-cycles", default=None,
                        help="START:END absolute cycles for the text event trace")
    parser.add_argument("--game-state", action="store_true",
                        help="passively report ELF-resolved tic, level-time, RNG and fuzz state")
    parser.add_argument("--stream", action="store_true",
                        help="record the binary memory stream to stream.bin.gz for offline cache studies")
    parser.add_argument("--stream-start", type=int, default=None,
                        help="stream start in cycles after resume (default: the first-scene marker)")
    parser.add_argument("--fst", default=None,
                        help="START:END absolute cycles for an FST waveform window; the FST run ends at END")
    parser.add_argument("--expect-scene-crc", default=None)
    parser.add_argument("--expect-scene-vga-crc", default=None)
    parser.add_argument("--input", type=lambda text: int(text, 0), action="append", default=[],
                        metavar="EVENT", help="Set-2 event queued before Resume (repeatable, at most 8); "
                                              "Doom consumes them on its first tics")
    parser.add_argument("--check-pins", action="store_true",
                        help="check applicable legacy pins or the state-qualified render oracle")
    parser.add_argument("--timeout", type=int, default=6 * 3600)
    parser.add_argument("--dry-run", action="store_true", help="print the plan only")
    args = parser.parse_args(argv)
    render = args.variant == "render-verification"
    selected_oracle = render_oracle(args.geometry) if render else None
    if render:
        args.check_pins = True
        args.game_state = True
        if args.frames != 2 or args.input or args.fst:
            parser.error("render-verification requires two frames, no input and no FST window")
    if args.check_pins:
        if args.variant == "production":
            parser.error("production has no frame-ordinal pin oracle")
        if args.input or args.fst or not 0 <= args.frames <= 2:
            parser.error("pins require no input, no FST window and at most two gameplay frames")

    if args.fst and args.geometry != "full":
        raise SystemExit("error: --fst builds only the full-width geometry")
    if args.check_pins and not render and args.geometry != "full":
        parser.error("--check-pins pins the full-width geometry only")
    target = BACKENDS[args.backend] + GEOMETRIES[args.geometry] + ("_fst" if args.fst else "")
    image_dir = (REPO_ROOT / (args.image_dir or VARIANTS[args.variant]["output"])).resolve()
    default_output = f"workdir/perf/{args.backend}" + ("" if args.geometry == "full" else "-half")
    if render:
        default_output += "-render-verification"
    output = (REPO_ROOT / (args.output or default_output)).resolve()
    workdir = (REPO_ROOT / "workdir").resolve()
    for path in (image_dir, output):
        if workdir not in path.parents and path != workdir:
            raise SystemExit(f"error: {path} must be below {workdir}")
    binary = REPO_ROOT / f"workdir/sim/cocotb/{target}/obj/{target}_sim"  # tools/sim build layout
    try:
        uart_tokens(tuple(args.input))
    except ValueError as error:
        parser.error(str(error))
    if args.dry_run:
        print(json.dumps({"target": target, "variant": args.variant, "input": args.input,
                          "geometry": args.geometry,
                          "image_dir": str(image_dir), "output": str(output),
                          "binary": str(binary), "frames": args.frames,
                          "max_cycles": args.max_cycles, "check_pins": args.check_pins,
                          "game_state": args.game_state,
                          "workload": "render-verification" if render else args.variant}, indent=2))
        return 0

    manifest_path = image_dir / "manifest.json"
    if not (args.reuse_image and manifest_path.is_file()):
        from src.nand2doom.scripts.sw_build.n2d_doom import build
        build(image_dir, REPO_ROOT, None, args.variant)
    manifest = json.loads(manifest_path.read_text())
    built_variant = manifest.get("variant", {}).get("name", "production")
    if built_variant != args.variant:
        raise SystemExit(f"error: {manifest_path} is the {built_variant} image, "
                         f"not {args.variant}")
    if args.check_pins:
        validate_pin_image(image_dir, manifest, args.variant)
    output.mkdir(parents=True, exist_ok=True)
    (output / "preload.txt").write_text("\n".join(preload_lines(image_dir, manifest)) + "\n")
    (output / "uart.txt").write_text("".join(f"{token:x}\n" for token in uart_tokens(tuple(args.input))))
    report = output / "report.txt"
    plusargs = [f"+preload={output / 'preload.txt'}", f"+uart={output / 'uart.txt'}",
                f"+report={report}", f"+frames={args.frames}", f"+max_cycles={args.max_cycles}",
                f"+ppm_dir={output}"]
    if args.game_state:
        from src.nand2doom.dv.software.doom_gameplay import elf_symbols
        symbols = elf_symbols(image_dir / "n2d_doom.elf", GAME_STATE_SYMBOLS)
        state_list = output / "state-symbols.txt"
        state_list.write_text("".join(f"{name} {symbols[name]:08x}\n"
                                     for name in GAME_STATE_SYMBOLS))
        plusargs.append(f"+state_symbols={state_list}")
    if render:
        from src.nand2doom.dv.software.doom_gameplay import elf_call_symbols
        call_symbols = elf_call_symbols(image_dir / "n2d_doom.elf")
        call_list = output / "single-tic-call-symbols.txt"
        call_list.write_text(" ".join(f"{call_symbols[name]:08x}" for name in
                                     ("G_Ticker", "D_Display", "singletics")) + "\n")
        plusargs.append(f"+single_tic_calls={call_list}")
        (output / "experiment-identity.json").write_text(json.dumps({
            "variant": args.variant, "defines": manifest["variant"]["defines"],
            "capture": manifest["capture"], "segments": manifest["segments"],
            "state_symbols": symbols, "call_symbols": call_symbols,
            "oracle_path": str(render_oracle_path(args.geometry).relative_to(REPO_ROOT)),
            "oracle_sha256": _sha256(render_oracle_path(args.geometry)),
            "full_oracle_sha256": _sha256(RENDER_ORACLE_PATH),
            "input": args.input, "geometry": args.geometry, "frames": args.frames,
        }, indent=2) + "\n")
    if args.trace_cycles:
        start, _, end = args.trace_cycles.partition(":")
        plusargs += [f"+trace={output / 'trace.txt'}", f"+trace_start={int(start)}",
                     f"+trace_end={int(end)}"]
    if args.stream:
        plusargs.append(f"+stream={output / 'stream.bin'}")
        if args.stream_start is not None:
            plusargs.append(f"+stream_start={args.stream_start}")
    if args.fst:
        start, _, end = args.fst.partition(":")
        if not (start.isdigit() and end.isdigit() and int(end) > int(start)):
            raise SystemExit("error: --fst must be START:END with END > START")
        plusargs += [f"+fst={output / 'window.fst'}", f"+fst_start={int(start)}",
                     f"+fst_end={int(end)}"]
    subprocess.run(build_command(target),
                   cwd=REPO_ROOT, check=True)
    started = time.monotonic()
    run = subprocess.run([str(binary), *plusargs], cwd=REPO_ROOT, timeout=args.timeout)
    wall = time.monotonic() - started
    result = parse_report(report.read_text())
    summary = summarize(result)
    summary["backend"] = args.backend
    summary["geometry"] = args.geometry
    summary["wall_seconds"] = round(wall, 1)
    if result["done"]:
        total = result["done"]["run_cycles"]
        summary["sim_cycles_per_second"] = round(total / wall) if wall else None
    summary["image_variant"] = args.variant
    summary["performance_workload"] = args.variant
    if render and "fps_at_25mhz" in summary:
        summary["verification_fps_at_25mhz"] = summary.pop("fps_at_25mhz")
    summary["image_sha256"] = manifest["segments"][0]["sha256"]
    summary["oracle"] = oracle(result)
    if args.stream and (output / "stream.bin").is_file():
        compress_stream(output / "stream.bin")
    _png(output)
    errors = check(result, args.expect_scene_crc, args.expect_scene_vga_crc)
    if render:
        errors += [e for e in check_render(result, output, selected_oracle) if e not in errors]
    elif args.check_pins:
        errors += check_pins(summary["oracle"])
    if run.returncode != 0:
        errors.append(f"simulator exit status {run.returncode}")
    (output / "summary.json").write_text(json.dumps(
        {"summary": summary, "result": result, "errors": errors}, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    for error in errors:
        print(f"error: {error}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
