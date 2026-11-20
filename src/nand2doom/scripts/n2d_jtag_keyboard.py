#!/usr/bin/env python3
"""Send Doom keys to nand2doom over the JTAG keyboard (USB-Blaster Virtual JTAG).

    python3 src/nand2doom/scripts/n2d_jtag_keyboard.py key "Turn left" --hold 0.5
    python3 src/nand2doom/scripts/n2d_jtag_keyboard.py send 0x275 0x375
    python3 src/nand2doom/scripts/n2d_jtag_keyboard.py type idkfa
    ... --cable 'USB-Blaster [1-1]' --yes     # device resolved by IDCODE 0x020F70DD

Every request is translated to Set-2 make/break bytes and delivered through the
common host transport and session (src/common/scripts/jtag/n2a_jtag_keyboard.py); the n2d
endpoint folds them into the same input FIFO as physical PS/2 and UART input.
Without --device the quartus_stp device name is resolved from the cable's
device listing by the DE2-115 IDCODE (n2d_jtag_device.py). Without --yes only
the translation, batches and that resolved name are printed; the read-only
listing is the only cable access. Contract: wiki/src/nand2doom/scripts/n2d_jtag_keyboard/spec.md.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parent))
if str(ROOT) not in sys.path:
    sys.path.insert(1, str(ROOT))
from n2d_control import ACTIONS  # noqa: E402  (Doom action -> 10-bit Set-2 event)
import n2d_jtag_device  # noqa: E402

EXTENDED, RELEASE = 0xE0, 0xF0
BATCH = 4


def event_codes(event: int) -> bytes:
    """One 10-bit `{extended, break, scan_code}` event as its Set-2 byte sequence."""
    if isinstance(event, bool) or not isinstance(event, int) or not 0 <= event <= 0x3FF:
        raise ValueError(f"Set-2 event must be 0..1023, got {event!r}")
    code = event & 0xFF
    if code in (EXTENDED, RELEASE):
        raise ValueError(f"scan code {code:#04x} is a prefix byte and cannot be an event")
    return bytes(([EXTENDED] if event & 0x200 else []) + ([RELEASE] if event & 0x100 else []) + [code])


def action_event(name: str) -> int:
    """Doom action by name, ignoring case and treating '-'/'_' as spaces."""
    wanted = " ".join(name.replace("-", " ").replace("_", " ").split()).lower()
    for action, event in ACTIONS.items():
        if action.lower() == wanted:
            return event
    raise ValueError(f"unknown action {name!r}; choose from {', '.join(ACTIONS)}")


def key_steps(actions, mode: str) -> list[bytes]:
    """`tap`: make every action in order, then break them in reverse (the hold
    sits between the two steps). `press`/`release`: only the make or break half."""
    events = [action_event(name) for name in actions]
    make = b"".join(event_codes(e) for e in events)
    brk = b"".join(event_codes(e | 0x100) for e in reversed(events))
    return {"tap": [make, brk], "press": [make], "release": [brk]}[mode]


def plan(steps) -> dict:
    """Offline description: each step's bytes and its batches of at most four."""
    return {"steps": [{"set2": step.hex(" "),
                       "batches": [step[i:i + BATCH].hex(" ") for i in range(0, len(step), BATCH)]}
                      for step in steps],
            "bytes": sum(len(step) for step in steps)}


def requested_steps(args, jtag) -> list[bytes]:
    """Alternating make/break steps (`key` tap, `type`) or one step (`send`, press, release)."""
    if args.action == "key":
        return key_steps(args.names, args.mode)
    if args.action == "send":
        return [b"".join(event_codes(e) for e in args.events)]
    return [part for make, brk in jtag.key_strokes(args.text.encode()) for part in (make, brk)]


def device_name(args, resolve) -> tuple[str, str]:
    """(device, source): the explicit --device, else the cable's one IDCODE match."""
    if args.device:
        return args.device, "--device"
    if args.cable:
        return resolve(args.cable, args.quartus_bin), f"IDCODE 0x{n2d_jtag_device.DE2_115_IDCODE:08X}"
    return "", "unresolved: give --cable"


def jtag_settings(args, jtag, device) -> dict:
    data = {"jtag_cable": args.cable or "", "jtag_device": device,
            "jtag_tck_hz": str(args.tck_hz), "jtag_instance": "0",
            "jtag_clock_mode": args.clock_mode, "jtag_cable_index": args.cable_index or "",
            "quartus_bin": args.quartus_bin or ""}
    return jtag.settings(data)


def run(args, *, jtag=None, resolve=n2d_jtag_device.resolve, clock=time.monotonic, sleep=time.sleep):
    """Return (exit status, JSON-able result). Without --yes no transport is opened."""
    if jtag is None:
        from src.common.scripts.jtag import n2a_jtag_keyboard as jtag
    steps = requested_steps(args, jtag)
    device, source = device_name(args, resolve)
    data = jtag_settings(args, jtag, device)
    result = dict(plan(steps), jtag_cable=data["jtag_cable"], jtag_device=data["jtag_device"],
                  jtag_device_source=source)
    if not args.yes:
        result["dry_run"] = "no transport opened; add --yes with --cable to send"
        return 0, result
    if not data["jtag_cable"]:
        raise ValueError("JTAG input unavailable: give the exact --cable name; nothing sent")
    transport = jtag.open_transport(data)
    result.update(sent=0, status="error")
    try:
        session = jtag.Session(transport)
        deadline = clock() + args.seconds
        for index, step in enumerate(steps):
            if index:  # hold after a make step, gap after a break step
                sleep(args.hold if index % 2 else args.gap)
            result["sent"] += jtag.send_all(session, step, deadline=deadline, sleep=sleep)
        result["status"] = "ok"
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        result["close"] = transport.close()
        if result["close"] != "closed":
            result["status"] = "error"
    return (0 if result["status"] == "ok" else 1), result


def number(text: str) -> int:
    return int(text, 0)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="n2d_jtag_keyboard.py", description=__doc__.split("\n\n", 1)[0])
    p.add_argument("--cable", help="exact JTAG cable name, e.g. 'USB-Blaster [1-1]'")
    p.add_argument("--device", help="exact JTAG device name as quartus_stp lists it "
                   "(default: the cable's one device with IDCODE 0x020F70DD)")
    p.add_argument("--quartus-bin", help="directory holding quartus_stp (default: PATH)")
    p.add_argument("--tck-hz", type=int, default=6000000, help="TCK frequency (default 6000000)")
    p.add_argument("--clock-mode", default="fixed-original-6mhz",
                   choices=("fixed-original-6mhz", "adjustable"), help="cable clock mode")
    p.add_argument("--cable-index", help="cable index, required by --clock-mode adjustable")
    p.add_argument("--seconds", type=float, default=20.0, help="whole-request delivery bound (default 20)")
    p.add_argument("--yes", action="store_true", help="send; without it only the translation is printed")
    sub = p.add_subparsers(dest="action", required=True)
    k = sub.add_parser("key", help="Doom actions: " + ", ".join(ACTIONS))
    k.add_argument("names", nargs="+", metavar="ACTION")
    k.add_argument("--mode", choices=("tap", "press", "release"), default="tap",
                   help="tap (default) makes, holds, then breaks; press or release sends one half")
    k.add_argument("--hold", type=float, default=0.1, help="tap hold in seconds (default 0.1)")
    k.set_defaults(gap=0.0)
    s = sub.add_parser("send", help="raw 10-bit Set-2 events {extended, break, scan_code}")
    s.add_argument("events", nargs="+", type=number, metavar="EVENT")
    s.set_defaults(hold=0.0, gap=0.0)
    t = sub.add_parser("type", help="type text as held keys (letters, digits, Enter, Esc, arrows)")
    t.add_argument("text")
    t.add_argument("--hold", type=float, default=0.05, help="per-key hold in seconds (default 0.05)")
    t.add_argument("--gap", type=float, default=0.05, help="pause between keys in seconds (default 0.05)")
    return p


def main(argv=None, *, jtag=None, resolve=n2d_jtag_device.resolve) -> int:
    args = parser().parse_args(argv)
    try:
        code, result = run(args, jtag=jtag, resolve=resolve)
    except (ValueError, OSError, RuntimeError) as exc:  # refused before any device effect
        print(f"n2d_jtag_keyboard: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
