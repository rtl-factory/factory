#!/usr/bin/env python3
"""nand2mario board host flow: compile, program and joypad keys over JTAG.

    python3 src/nand2mario/scripts/board/n2m_board.py compile [--plan]
    python3 src/nand2mario/scripts/board/n2m_board.py program [--sof PATH] [--cable 'USB-Blaster [1-1]'] [--yes]
    python3 src/nand2mario/scripts/board/n2m_board.py load IMAGE [--port /dev/ttyUSB0] [--yes]
    python3 src/nand2mario/scripts/board/n2m_board.py snapshot [--port /dev/ttyUSB0] [--out PATH] [--yes]
    python3 src/nand2mario/scripts/board/n2m_board.py joypad start a --hold 0.2
    python3 src/nand2mario/scripts/board/n2m_board.py joypad --mode press right
    ... joypad --cable 'USB-Blaster [1-1]' --yes start     # device resolved by the board IDCODE

`compile` runs the shared Quartus build tool (tools/quartus/quartus.py) on the
board's manifest. `program` prints the exact
quartus_pgm command for the built SOF and runs it only with --yes. `load`
sends a cartridge image through the UART loader host (cart_loader.py) at the
board's 2 Mbaud: without --yes it writes the request frames to a dump
under workdir and opens no port. `snapshot` reads back the displayed frame
through frame_snapshot.py capture; without --yes it writes the planned
request frames under workdir and opens no port. `joypad`
translates button names to the Set-2 keys the board decodes (arrows, X, Z,
Space, Enter) and delivers them through the common JTAG keyboard transport
(src/common/scripts/jtag/n2a_jtag_keyboard.py); without --yes it prints the plan and
opens nothing. Contract: wiki/src/nand2mario/scripts/board/spec.md.
"""
from __future__ import annotations

import argparse
import re
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.board import jtag_chain  # noqa: E402
from tools.quartus import image, manifest  # noqa: E402
from tools.quartus import quartus as quartus_tool  # noqa: E402

DEFAULT_SOF = "workdir/quartus/n2m_de10_lite/output_files/n2m_de10_lite.sof"
# Ref: board spec "Joypad": button to the Set-2 host key bytes the board decodes.
BUTTON_KEYS = {"right": b"\x1b[C", "left": b"\x1b[D", "up": b"\x1b[A", "down": b"\x1b[B",
               "a": b"x", "b": b"z", "select": b" ", "start": b"\r"}
BATCH = 4
MANIFESTS = {"de10_lite": "src/nand2mario/cfg/n2m_quartus.json",
             "de2_115": "src/nand2mario/cfg/n2m_de2_115_quartus.json"}
LIBRARY_PROFILE = "src/nand2mario/cfg/n2m_library_quartus_profile.json"


def image_arguments(board: str, library: bool) -> list[str]:
    """The Quartus tool's manifest (and library profile) arguments of one board image."""
    return ["--manifest", MANIFESTS[board]] + (["--profile", LIBRARY_PROFILE] if library else [])


def image_config(board: str, library: bool) -> dict:
    """The resolved config of one board image, as the Quartus tool builds it."""
    return manifest.load(ROOT, MANIFESTS[board], None, LIBRARY_PROFILE if library else None)


def button_strokes(names, jtag) -> list[tuple[bytes, bytes]]:
    """One (make, break) Set-2 pair per named button, in order."""
    strokes = []
    for name in names:
        key = BUTTON_KEYS.get(name.lower())
        if key is None:
            raise ValueError(f"unknown button {name!r}; choose from {', '.join(BUTTON_KEYS)}")
        strokes.extend(jtag.key_strokes(key))
    return strokes


def key_steps(names, mode, jtag) -> list[bytes]:
    """`tap`: make every button in order, then break them in reverse (the hold
    sits between the two steps). `press`/`release`: only that half."""
    strokes = button_strokes(names, jtag)
    make = b"".join(m for m, _ in strokes)
    brk = b"".join(b for _, b in reversed(strokes))
    return {"tap": [make, brk], "press": [make], "release": [brk]}[mode]


def plan(steps) -> dict:
    return {"steps": [{"set2": step.hex(" "),
                       "batches": [step[i:i + BATCH].hex(" ") for i in range(0, len(step), BATCH)]}
                      for step in steps],
            "bytes": sum(len(step) for step in steps)}


BOARD_IDCODES = {"de10_lite": "031050DD", "de2_115": "020F70DD"}


def jtag_settings(args, jtag, resolve=jtag_chain.resolve) -> dict:
    """Without --device, the cable's one device with the board IDCODE is named
    (a read-only quartus_stp listing); a given --device must carry that IDCODE."""
    board = getattr(args, "board", "de10_lite")
    expected_id = BOARD_IDCODES[board]
    if args.device and not re.search(r"\(0x" + expected_id + r"\)$", args.device, re.I):
        raise ValueError("JTAG device IDCODE does not match selected board")
    device = args.device or (resolve(args.cable, args.quartus_bin, expected_id) if args.cable else "")
    data = {"jtag_cable": args.cable or "", "jtag_device": device,
            "jtag_tck_hz": str(args.tck_hz), "jtag_instance": "0",
            "jtag_clock_mode": args.clock_mode, "jtag_cable_index": args.cable_index or "",
            "quartus_bin": args.quartus_bin or ""}
    return jtag.settings(data)


def run_joypad(args, *, jtag=None, resolve=jtag_chain.resolve, clock=time.monotonic, sleep=time.sleep):
    """Return (exit status, JSON-able result). Without --yes nothing is opened
    but the device listing that names a missing --device."""
    if jtag is None:
        from src.common.scripts.jtag import n2a_jtag_keyboard as jtag
    steps = key_steps(args.names, args.mode, jtag)
    data = jtag_settings(args, jtag, resolve)
    result = dict(plan(steps), jtag_cable=data["jtag_cable"], jtag_device=data["jtag_device"])
    if not args.yes:
        result["dry_run"] = "nothing opened; add --yes with --cable to send"
        return 0, result
    if not data["jtag_cable"]:
        raise ValueError("JTAG input unavailable: give the exact --cable name; nothing sent")
    transport = jtag.open_transport(data)
    result.update(sent=0, status="error")
    try:
        session = jtag.Session(transport)
        deadline = clock() + args.seconds
        for index, step in enumerate(steps):
            if index:
                sleep(args.hold)
            result["sent"] += jtag.send_all(session, step, deadline=deadline, sleep=sleep)
        result["status"] = "ok"
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        result["close"] = transport.close()
        if result["close"] != "closed":
            result["status"] = "error"
    return (0 if result["status"] == "ok" else 1), result


def program_command(sof: Path, cable: str, quartus_bin: str | None) -> list[str]:
    """The vendor SRAM-programming command for one SOF through one cable."""
    return jtag_chain.program_command(sof, cable, quartus_bin)


def run_program(args, *, runner=subprocess.run, which=shutil.which):
    """Dry run by default: the plan names the SOF and the exact command.
    With --yes the command runs and its exit status is returned."""
    board = getattr(args, "board", "de10_lite")
    library = getattr(args, "library", False)
    config = image_config(board, library)
    project = config["project"]
    sof = (ROOT / (args.sof or f"workdir/quartus/{project}/output_files/{project}.sof")).resolve()
    result = {"board": board, "library": library, "sof": str(sof), "exists": sof.is_file(), "cable": args.cable or ""}
    if not sof.is_relative_to(ROOT / "workdir"):
        raise ValueError("the SOF must live below workdir (a built image, never a committed one)")
    receipt = sof.with_suffix(".json")
    if receipt.exists() or args.yes:
        result["identity"] = image.verify_image(sof, config)
    elif any(name in sof.name and name != f"n2m_{board}" for name in ("n2m_de10_lite", "n2m_de2_115")):
        raise ValueError("SOF name does not match selected board")
    result["command"] = shlex.join(program_command(sof, args.cable or "<cable>", args.quartus_bin))
    if not args.yes:
        result["dry_run"] = "nothing programmed; add --yes with --cable to configure the board"
        return 0, result
    if not args.cable:
        raise ValueError("programming needs the exact --cable name; nothing programmed")
    if not sof.is_file():
        raise ValueError(f"missing SOF: {sof}")
    command = program_command(sof, args.cable, args.quartus_bin)
    if not which(command[0]):
        raise ValueError(f"{command[0]} not found; pass --quartus-bin")
    completed = runner(command, timeout=args.seconds, check=False)
    result["returncode"] = completed.returncode
    result["status"] = "ok" if completed.returncode == 0 else "error"
    return completed.returncode, result


def run_load(args, *, loader=None):
    """Dry run by default: the loader's own dump transport records every
    request frame under workdir/ and fabricates OK responses. With --yes the
    loader opens --port at the board rate, loads, verifies and releases."""
    if loader is None:
        from src.nand2mario.scripts.board import cart_loader as loader
    image = Path(args.image)
    if not image.is_file():
        raise ValueError(f"missing image: {image}")
    if args.yes:
        if not args.port:
            raise ValueError("loading needs the exact --port of the 3.3 V adapter; nothing sent")
        argv = ["--image", str(image), "--port", args.port, "--baud", str(args.baud)]
    else:
        dump = (ROOT / args.dump).resolve()
        if not dump.is_relative_to(ROOT / "workdir"):
            raise ValueError("the dry-run dump must live below workdir")
        dump.parent.mkdir(parents=True, exist_ok=True)
        # Why: the dump transport fabricates zero READ payloads, so a dry run
        # cannot verify; it records the HOLD, WRITE and RELEASE frames.
        argv = ["--image", str(image), "--dump", str(dump), "--no-verify"]
    if args.no_verify and args.yes:
        argv.append("--no-verify")
    code = loader.main(argv)
    result = {"image": str(image), "bytes": image.stat().st_size, "baud": args.baud,
              "status": "ok" if code == 0 else "error"}
    if not args.yes:
        result["dump"] = str((ROOT / args.dump).resolve())
        result["dry_run"] = "no port opened and no readback; add --yes with --port to load and verify the board"
    return code, result


def run_snapshot(args, *, snapshot_tool=None):
    """Dry run by default: the capture sequence's request frames are written
    below workdir/ and nothing is opened. With --yes the capture tool pins,
    reads and releases the displayed frame over --port into --out."""
    if snapshot_tool is None:
        from src.nand2mario.scripts.board import frame_snapshot as snapshot_tool
    out = (ROOT / args.out).resolve()
    if not out.is_relative_to(ROOT / "workdir"):
        raise ValueError("the snapshot output must live below workdir")
    if args.yes:
        if not args.port:
            raise ValueError("a snapshot needs the exact --port of the 3.3 V adapter; nothing sent")
        code = snapshot_tool.main(["capture", "--port", args.port, "--baud", str(args.baud), "--out", str(out)])
        return code, {"port": args.port, "baud": args.baud, "out": str(out), "status": "ok" if code == 0 else "error"}
    plan_file = (ROOT / args.plan_file).resolve()
    if not plan_file.is_relative_to(ROOT / "workdir"):
        raise ValueError("the dry-run plan file must live below workdir")
    frames = snapshot_tool.plan_frames()
    plan_file.parent.mkdir(parents=True, exist_ok=True)
    plan_file.write_bytes(b"".join(frames))
    return 0, {"frames": len(frames), "bytes": sum(len(frame) for frame in frames), "plan_file": str(plan_file),
               "baud": args.baud,
               "dry_run": "no port opened; add --yes with --port to pin, read and release the displayed frame"}


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="n2m_board.py", description=__doc__.split("\n\n", 1)[0])
    sub = p.add_subparsers(dest="action", required=True)
    c = sub.add_parser("compile", help="run the Quartus build gate (tools/quartus/quartus.py)")
    c.add_argument("--plan", action="store_true", help="validate and show inputs without writing")
    c.add_argument("--quartus", help="quartus_sh executable (or QUARTUS_SH/PATH)")
    g = sub.add_parser("program", help="configure the FPGA SRAM from the built SOF (dry run without --yes)")
    g.add_argument("--sof", default=None, help=f"image below workdir (default {DEFAULT_SOF})")
    g.add_argument("--cable", help="exact JTAG cable name, e.g. 'USB-Blaster [1-1]'")
    g.add_argument("--quartus-bin", help="directory holding quartus_pgm (default: PATH)")
    g.add_argument("--seconds", type=float, default=120.0, help="programming bound (default 120)")
    g.add_argument("--yes", action="store_true", help="program; without it only the command is printed")
    lo = sub.add_parser("load", help="load a cartridge image over the UART loader (dry-run dump without --yes)")
    lo.add_argument("image", help="cartridge image file")
    lo.add_argument("--port", help="serial device of the 3.3 V USB-to-UART adapter (pyserial)")
    lo.add_argument("--baud", type=int, default=2_000_000, help="board loader rate (default 2000000)")
    lo.add_argument("--dump", default="workdir/n2m_board/load_frames.bin",
                    help="dry-run frame dump below workdir (default workdir/n2m_board/load_frames.bin)")
    lo.add_argument("--no-verify", action="store_true", help="skip the readback comparison")
    lo.add_argument("--yes", action="store_true", help="open the port and load; without it only the dump is written")
    sn = sub.add_parser("snapshot", help="read back the displayed frame over the UART loader (dry run without --yes)")
    sn.add_argument("--port", help="serial device of the 3.3 V USB-to-UART adapter (pyserial)")
    sn.add_argument("--baud", type=int, default=2_000_000, help="board loader rate (default 2000000)")
    sn.add_argument("--out", default="workdir/n2m_board/snapshot",
                    help="capture stem below workdir, .bin and .json (default workdir/n2m_board/snapshot)")
    sn.add_argument("--plan-file", default="workdir/n2m_board/snapshot_frames.bin",
                    help="dry-run request frames below workdir (default workdir/n2m_board/snapshot_frames.bin)")
    sn.add_argument("--yes", action="store_true", help="open the port and capture; without it only the plan is written")
    j = sub.add_parser("joypad", help="buttons: " + ", ".join(BUTTON_KEYS))
    j.add_argument("names", nargs="+", metavar="BUTTON")
    j.add_argument("--mode", choices=("tap", "press", "release"), default="tap",
                   help="tap (default) makes, holds, then breaks; press or release sends one half")
    j.add_argument("--hold", type=float, default=0.1, help="tap hold in seconds (default 0.1)")
    j.add_argument("--cable", help="exact JTAG cable name, e.g. 'USB-Blaster [1-1]'")
    j.add_argument("--device", help="exact JTAG device name as quartus_stp lists it "
                   "(default: the cable's one device with the board IDCODE)")
    j.add_argument("--quartus-bin", help="directory holding quartus_stp (default: PATH)")
    j.add_argument("--tck-hz", type=int, default=6000000, help="TCK frequency (default 6000000)")
    j.add_argument("--clock-mode", default="fixed-original-6mhz",
                   choices=("fixed-original-6mhz", "adjustable"), help="cable clock mode")
    j.add_argument("--cable-index", help="cable index, required by --clock-mode adjustable")
    j.add_argument("--seconds", type=float, default=20.0, help="whole-request delivery bound (default 20)")
    j.add_argument("--yes", action="store_true", help="send; without it only the translation is printed")
    for command in (c, g):
        command.add_argument("--library", action="store_true", help="select the maintained DE2 menu/library image")
    for command in (c, g, lo, sn, j):
        command.add_argument("--board", choices=("de10_lite", "de2_115"), default="de10_lite",
                             help="explicit board profile (default de10_lite)")
    return p


def main(argv=None, *, jtag=None, runner=subprocess.run, resolve=jtag_chain.resolve) -> int:
    args = parser().parse_args(argv)
    try:
        if args.action == "compile":
            quartus_argv = image_arguments(args.board, args.library) + (["--plan"] if args.plan else [])
            if args.quartus:
                quartus_argv += ["--quartus", args.quartus]
            return quartus_tool.main(quartus_argv, root=ROOT)
        if args.action == "program":
            code, result = run_program(args, runner=runner)
        elif args.action == "load":
            code, result = run_load(args)
        elif args.action == "snapshot":
            code, result = run_snapshot(args)
        else:
            code, result = run_joypad(args, jtag=jtag, resolve=resolve)
    except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"n2m_board: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
