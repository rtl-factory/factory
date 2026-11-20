#!/usr/bin/env python3
"""Attribute a bounded native retirement trace to ELF symbols in gameplay.

Cycles are last-retired-PC intervals, not causal instruction stall costs.
Startup is clipped using the performance report's gameplay windows. Explicit
swap polling ranges and DG_SleepMs descendants are reported separately.
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
import json
from pathlib import Path
import re


def symbols(text: str) -> list[tuple[int, int, str]]:
    entries = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) == 4 and fields[2] in "TtWw":
            start, size = (int(v, 16) for v in fields[:2])
            if size:
                entry = (start, start + size, fields[3])
                entries[start] = min(entries.get(start, entry), entry)
    if not entries:
        raise ValueError("no sized text symbols")
    return sorted(entries.values())


def gameplay_windows(report: dict) -> list[tuple[int, int]]:
    # Frame/report cycles are resume-relative; the text trace is absolute.
    origin = report["result"]["resumed"]
    return [(v["start"] + origin, v["end"] + origin)
            for v in report["result"]["frames"] if v["after_scene"]]


def profile(lines, entries, windows, wait_ranges=()) -> dict:
    """Intervals sum exactly to each covered window; symbols only label them."""
    starts = [v[0] for v in entries]
    rows = defaultdict(Counter)
    pcs = Counter()
    totals = [Counter() for _ in windows]
    stack = []
    previous = None
    first = last = None
    for line in lines:
        match = re.fullmatch(r"(\d+) R pc=([0-9a-fA-F]+) insn=([0-9a-fA-F]+)\s*", line)
        if not match:
            continue
        cycle, pc, insn = (int(match[1]), int(match[2], 16), int(match[3], 16))
        if last is not None and cycle <= last:
            raise ValueError("retirements are not strictly ordered")
        first = cycle if first is None else first
        last = cycle
        index = bisect_right(starts, pc) - 1
        name = entries[index][2] if index >= 0 and pc < entries[index][1] else "<unknown>"
        waiting = name == "DG_SleepMs" or any(stack) or any(a <= pc < b for a, b in wait_ranges)
        label = "<software-wait>" if waiting else name
        if previous is not None:
            old_cycle, old_label = previous
            for number, (start, end) in enumerate(windows):
                elapsed = max(0, min(cycle, end) - max(old_cycle, start))
                rows[old_label]["cycles"] += elapsed
                totals[number]["cycles"] += elapsed
                if old_label == "<software-wait>":
                    totals[number]["wait_cycles"] += elapsed
        for number, (start, end) in enumerate(windows):
            if start <= cycle < end:
                row = rows[label]
                row["instructions"] += 1
                opcode = insn & 127
                row["loads"] += opcode == 3
                row["stores"] += opcode == 35
                row["branches"] += opcode == 99
                totals[number]["instructions"] += 1
                pcs[pc] += 1
        # Direct and indirect calls use ra/t0; returns leave the prior wait
        # context only after this retirement interval has been labelled.
        opcode, rd, rs1 = insn & 127, (insn >> 7) & 31, (insn >> 15) & 31
        if opcode in (103, 111) and rd in (1, 5):
            stack.append(waiting)
        elif opcode == 103 and rd == 0 and rs1 in (1, 5) and (insn >> 20) == 0:
            if stack:
                stack.pop()
        previous = cycle, label
    if first is None or first > min(a for a, _ in windows) or last < max(b for _, b in windows):
        raise ValueError("trace does not cover every complete gameplay window")
    for total, (start, end) in zip(totals, windows):
        if total["cycles"] != end - start:
            raise ValueError("gameplay interval accounting is incomplete")
    return {"method": "last-retired-PC interval attribution; boundary/pipeline costs are not causal per-instruction stalls",
            "windows": [{"start": a, "end": b, **t} for (a, b), t in zip(windows, totals)],
            "functions": dict(sorted(rows.items(), key=lambda v: -v[1]["cycles"])),
            "drawer_pc_instructions": {f"0x{pc:08x}": count for pc, count in sorted(pcs.items())
                                       if any(a <= pc < b and name in ("R_DrawColumn", "R_DrawSpan")
                                              for a, b, name in entries)}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--symbols", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--wait-range", action="append", default=[], help="ELF-verified poll loop START:END")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = json.loads(args.summary.read_text())
        if report["summary"]["status"] != "pass":
            raise ValueError("performance run did not pass")
        windows = gameplay_windows(report)
        ranges = [tuple(int(v, 0) for v in s.split(":")) for s in args.wait_range]
        with args.trace.open() as source:
            result = profile(source, symbols(args.symbols.read_text()), windows, ranges)
        frames = [v for v in report["result"]["frames"] if v["after_scene"]]
        for observed, frame in zip(result["windows"], frames):
            expected = frame["retired_end"] - frame["retired_start"]
            observed["reported_instructions"] = expected
            observed["instruction_boundary_delta"] = observed["instructions"] - expected
            if abs(observed["instruction_boundary_delta"]) > 1:
                raise ValueError("retirement counts do not reconcile with frame report")
            observed["swap_request_to_flip_cycles"] = frame["wait"]
            observed["swap_poll_to_flip_cycles"] = frame["spin"]
        rendered = json.dumps(result, indent=2) + "\n"
        if args.output:
            workdir = Path(__file__).resolve().parents[3] / "workdir"
            if workdir.resolve() not in args.output.resolve().parents:
                raise ValueError("output must be below issue workdir")
            args.output.write_text(rendered)
        else:
            print(rendered, end="")
        return 0
    except (ValueError, OSError, KeyError) as error:
        parser.exit(1, f"error: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
