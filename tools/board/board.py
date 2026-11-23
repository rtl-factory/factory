#!/usr/bin/env python3
"""Board console: pick a SOF, program the FPGA, load a program, type on the board.

    python3 tools/board/board.py                       # the terminal console
    python3 tools/board/board.py list
    python3 tools/board/board.py program --project nand2mario --board de10_lite [--sof PATH] --yes
    python3 tools/board/board.py load --project nand2doom --board de2_115 --port /dev/ttyUSB0 --yes
    python3 tools/board/board.py keys --project nand2mario --board de10_lite --yes "hello"

One console for every project and board. It knows no design: each project
declares its boards in src/<project>/cfg/board.json (board IDCODE, default
SOF, UART rate, and the project command that loads a program). Programming is
quartus_pgm through the one cable whose chain is exactly the board's FPGA;
keys go through the common JTAG keyboard host; loading runs the project's own
loader. Without --yes every command prints its plan and opens nothing.
Spec: wiki/tools/board/spec.md.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.board import jtag_chain  # noqa: E402
from tools.board.keys import key_name  # noqa: E402

SCHEMA = "n2a-board/1"
PROGRAM_SECONDS = 180
FIELDS = ("python", "root", "program", "port", "baud", "board")


class BoardError(Exception):
    """A request the console refuses before touching the board, or a failed step."""


@dataclass(frozen=True)
class Board:
    """One project on one board, as its cfg/board.json declares it."""
    project: str
    name: str
    idcode: str
    baud: int
    sof: str
    program_label: str
    program_patterns: tuple[str, ...]
    program_default: str
    load: tuple[str, ...]
    pad: tuple[tuple[str, str], ...] = ()

    @property
    def key(self) -> str:
        return f"{self.project}/{self.name}"


def parse_config(data: dict, source: str) -> list[Board]:
    if data.get("schema") != SCHEMA:
        raise BoardError(f"{source}: schema must be {SCHEMA}")
    try:
        program, load = data["program"], tuple(data["load"])
        pad = tuple(data.get("pad", {}).items())
        if any(key_name(key) is None for _, key in pad):
            raise BoardError(f"{source}: every pad button must name a console key")
        if not load or not all(isinstance(part, str) for part in load):
            raise BoardError(f"{source}: load must be a nonempty list of strings")
        for part in load:
            try:
                part.format(**{name: "" for name in FIELDS})
            except (KeyError, IndexError, ValueError) as exc:
                raise BoardError(f"{source}: load field {part!r}: allowed {', '.join(FIELDS)}") from exc
        boards = []
        for name, board in data["boards"].items():
            idcode = jtag_chain.idcode_text(board["idcode"])
            baud = int(board["baud"])
            if not 9600 <= baud <= 12_000_000:
                raise BoardError(f"{source}: {name} baud {baud} outside 9600..12000000")
            boards.append(Board(data["project"], name, idcode, baud, board["sof"], program["label"],
                                tuple(program["patterns"]), program.get("default", ""), load, pad))
        return boards
    except (KeyError, TypeError, ValueError) as exc:
        raise BoardError(f"{source}: malformed board config: {exc!r}") from exc


def load_boards(root: Path = ROOT) -> dict[str, Board]:
    """Every project's boards, keyed project/board."""
    found = {}
    for path in sorted(root.glob("src/*/cfg/board.json")):
        for board in parse_config(json.loads(path.read_text()), str(path.relative_to(root))):
            found[board.key] = board
    return found


def pick(boards: dict[str, Board], project: str | None, board: str | None) -> Board:
    matches = [b for b in boards.values()
               if (project is None or b.project == project) and (board is None or b.name == board)]
    if len(matches) != 1:
        names = ", ".join(sorted(b.key for b in matches)) or "none"
        raise BoardError(f"--project and --board must name one board (matching: {names})")
    return matches[0]


def board_for_sof(boards: dict[str, Board], sof: Path) -> Board | None:
    """The board whose default SOF has this file name, when exactly one does."""
    matches = [b for b in boards.values() if Path(b.sof).name == Path(sof).name]
    return matches[0] if len(matches) == 1 else None


def detect_board(boards: dict[str, Board], quartus_bin=None, *, runner=jtag_chain.run_tool,
                 root: Path = ROOT) -> tuple[Board, str]:
    """The console's first board and why: the one whose IDCODE is alone on the one
    JTAG chain, the newest built SOF winning among projects on that board; else
    the first board. Never raises: a failed listing only explains the fallback."""
    first = boards[sorted(boards)[0]]
    fallback = f"opened on {first.key}; choose the board"
    try:
        rc, text = runner([jtag_chain.tool_path(quartus_bin, "jtagconfig")], jtag_chain.LIST_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return first, f"jtagconfig failed ({exc}); {fallback}"
    if rc:
        return first, f"jtagconfig exited {rc}: {text.strip()[:200]}; {fallback}"
    chains = jtag_chain.parse_jtagconfig(text)
    if len(chains) != 1 or len(chains[0][1]) != 1:
        found = "; ".join(f"{c}: {', '.join(d for d, _ in devs) or 'no device'}" for c, devs in chains)
        return first, f"expected one JTAG cable with one device, found {found or 'no cable'}; {fallback}"
    cable, [(idcode, _)] = chains[0]
    matches = [b for b in boards.values() if b.idcode == idcode]
    if not matches:
        return first, f"{cable}: no board has IDCODE {idcode}; {fallback}"

    def built(board):
        sof = resolve_path(board.sof, root)
        return sof.stat().st_mtime if sof.is_file() else -1.0

    board = max(sorted(matches, key=lambda b: b.key), key=built)
    return board, f"{cable}: IDCODE {idcode} is {board.name}; selected {board.key}"


def check_chain(boards: dict[str, Board], board: Board, quartus_bin=None, *,
                runner=jtag_chain.run_tool) -> tuple[bool, str, str]:
    """(ready, what one `jtagconfig` run found, what to do next) for `board`'s
    JTAG keyboard. Never raises: every failure becomes a finding and a hint."""
    try:
        rc, text = runner([jtag_chain.tool_path(quartus_bin, "jtagconfig")], jtag_chain.LIST_TIMEOUT)
    except FileNotFoundError:
        return False, "jtagconfig not found", "put Quartus's bin directory on PATH, or pass --quartus-bin"
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"jtagconfig failed: {exc}", "check the JTAG server (jtagd) and the USB-Blaster"
    chains = jtag_chain.parse_jtagconfig(text)
    if "Insufficient port permissions" in text:
        return False, "USB-Blaster found but not accessible (insufficient port permissions)", \
            "install the udev rule tools/toolchain/51-fpga-boards.rules (wiki/tools/toolchain/spec.md#boards), then replug"
    if "Chain in use" in text:
        return False, "JTAG chain in use by another program", \
            "close the other board console, Signal Tap or quartus_stp that holds the cable (a keyboard this console has connected counts too)"
    if rc and not chains:
        return False, f"jtagconfig exited {rc}: {text.strip()[:120]}", "check the USB-Blaster and the JTAG server (jtagd)"
    if not chains:
        return False, "no JTAG cable found", "plug in the board's USB-Blaster (the USB port by the power switch) and power the board"
    if len(chains) > 1:
        return False, "cables: " + ", ".join(c for c, _ in chains), "unplug every USB-Blaster but the board's"
    cable, devices = chains[0]
    ids = [d for d, _ in devices]
    if not ids:
        return False, f"{cable}: no device on the chain", "power the board on, and check the USB-Blaster cable"
    if ids == [board.idcode]:
        return True, f"{cable}: {board.idcode}, matches {board.key}", "connect the keyboard, then play"
    others = sorted({b.name for b in boards.values() if b.idcode in ids})
    hint = (f"the connected board is a {', '.join(others)}: choose that board in the Board list"
            if others else "this chain holds no declared board: connect the right board")
    return False, f"{cable}: {', '.join(ids)}, expected {board.idcode} for {board.key}", hint


def resolve_path(text: str, root: Path = ROOT) -> Path:
    path = Path(text).expanduser()
    return path if path.is_absolute() else root / path


FTDI_VENDOR = 0x0403


def serial_ports() -> list[str]:
    """USB serial adapters pyserial sees, FTDI cables first; empty without
    pyserial. Built-in ports (ttyS*) are never a board UART, so they are left out."""
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    usb = [p for p in list_ports.comports() if p.vid is not None]
    return [p.device for p in sorted(usb, key=lambda p: (p.vid != FTDI_VENDOR, p.device))]


def program_plan(board: Board, sof: Path, cable: str | None, quartus_bin: str | None) -> dict:
    return {"board": board.key, "sof": str(sof), "exists": sof.is_file(),
            "cable": cable or f"<the one cable with IDCODE {board.idcode}>",
            "command": jtag_chain.program_command(sof, cable or "<cable>", quartus_bin)}


def program(board: Board, sof: Path, *, cable=None, quartus_bin=None, runner=jtag_chain.run_tool,
            out=print) -> str:
    """Configure the FPGA from `sof`; returns the cable used. Raises BoardError."""
    if not sof.is_file():
        raise BoardError(f"SOF not found: {sof}")
    try:
        cable = cable or jtag_chain.find_cable(board.idcode, quartus_bin, runner=runner)
    except (ValueError, RuntimeError) as exc:
        raise BoardError(str(exc)) from exc
    argv = jtag_chain.program_command(sof, cable, quartus_bin)
    out("$ " + " ".join(argv))
    try:
        rc, text = runner(argv, PROGRAM_SECONDS)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BoardError(f"quartus_pgm: {exc}") from exc
    if not jtag_chain.program_succeeded(rc, text):
        raise BoardError(f"quartus_pgm exited {rc}: {text.strip()[-300:]}")
    out(f"programmed {sof.name} through {cable}")
    return cable


def load_command(board: Board, program_file: Path, port: str, *, python=sys.executable,
                 root: Path = ROOT) -> list[str]:
    """The project's loader command line, its fields filled in."""
    values = dict(python=python, root=str(root), program=str(program_file), port=port,
                  baud=str(board.baud), board=board.name)
    return [part.format(**values) for part in board.load]


def load(board: Board, program_file: Path, port: str, *, out=print, popen=subprocess.Popen,
         root: Path = ROOT) -> None:
    """Run the project's loader, streaming its output. Raises BoardError."""
    if not port:
        raise BoardError("loading needs the board's serial --port")
    if not program_file.is_file():
        raise BoardError(f"{board.program_label.lower()} not found: {program_file}")
    argv = load_command(board, program_file, port, root=root)
    out("$ " + " ".join(argv))
    try:
        process = popen(argv, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, errors="replace")
    except OSError as exc:
        raise BoardError(f"loader: {exc}") from exc
    for line in process.stdout:
        out(line.rstrip("\n"))
    if process.wait():
        raise BoardError(f"loader exited {process.returncode}")


def jtag_device(board: Board, *, cable=None, device=None, quartus_bin=None,
                runner=jtag_chain.run_tool) -> tuple[str, str]:
    """(cable, device) of the board's JTAG keyboard: given, else found by IDCODE."""
    try:
        cable = cable or jtag_chain.find_cable(board.idcode, quartus_bin, runner=runner)
        device = device or jtag_chain.resolve(cable, quartus_bin, board.idcode, runner=runner)
    except (ValueError, RuntimeError) as exc:
        raise BoardError(str(exc)) from exc
    if jtag_chain.device_idcode(device) != int(board.idcode, 16):
        raise BoardError(f"JTAG device {device!r} is not the {board.name} (IDCODE {board.idcode})")
    return cable, device


class Keyboard:
    """One open JTAG keyboard session: `send` delivers Set-2 bytes in order."""

    def __init__(self, cable: str, device: str, quartus_bin=None, *, jtag=None):
        if jtag is None:
            from src.common.scripts.jtag import n2a_jtag_keyboard as jtag
        self.jtag = jtag
        data = jtag.settings({"jtag_cable": cable, "jtag_device": device,
                              "quartus_bin": quartus_bin or ""})
        self.transport = jtag.open_transport(data)
        try:
            self.session = jtag.Session(self.transport)
        except Exception:
            self.transport.close()
            raise

    def send(self, codes: bytes, seconds: float = 5.0) -> int:
        return self.jtag.send_all(self.session, codes, deadline=time.monotonic() + seconds)

    def type_text(self, text: str, hold: float = 0.05) -> int:
        return self.jtag.type_text(self.session, text, hold=hold)

    def close(self) -> str:
        return self.transport.close()


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="board.py", description=__doc__.split("\n\n", 1)[0])
    sub = p.add_subparsers(dest="action")
    sub.add_parser("list", help="projects, boards, default files and serial ports")
    commands = []
    g = sub.add_parser("program", help="configure the FPGA from a SOF")
    g.add_argument("--sof", help="SOF to program (default: the board's built SOF)")
    commands.append(g)
    lo = sub.add_parser("load", help="load a program with the project's loader")
    lo.add_argument("--program", help="program file (default: the project's default)")
    lo.add_argument("--port", help="serial device of the board UART")
    commands.append(lo)
    k = sub.add_parser("keys", help="type text on the board's JTAG keyboard")
    k.add_argument("text")
    k.add_argument("--device", help="exact quartus_stp device name (default: found by IDCODE)")
    commands.append(k)
    w = sub.add_parser("tui", help="open the terminal console (the default)")
    w.add_argument("--port", help="prefill the serial port")
    commands.append(w)
    for command in commands:
        command.add_argument("--project", help="project, e.g. nand2mario")
        command.add_argument("--board", help="board, e.g. de10_lite or de2_115")
        command.add_argument("--quartus-bin", help="directory of jtagconfig, quartus_pgm, quartus_stp (default PATH)")
        if command is not w:
            command.add_argument("--yes", action="store_true", help="act on the board; without it only the plan prints")
        if command in (g, k):
            command.add_argument("--cable", help="exact JTAG cable name (default: the one with the board's IDCODE)")
    return p


def run_list(boards, out=print) -> int:
    for board in boards.values():
        sof = resolve_path(board.sof)
        default = resolve_path(board.program_default) if board.program_default else None
        out(f"{board.key}: IDCODE {board.idcode}, UART {board.baud} baud")
        out(f"  sof     {board.sof} ({'built' if sof.is_file() else 'not built'})")
        out(f"  program {board.program_label}: " + (f"{board.program_default} "
            f"({'present' if default.is_file() else 'missing'})" if default else "choose a file"))
    ports = serial_ports()
    out("serial ports: " + (", ".join(ports) if ports else "none found (or pyserial missing)"))
    return 0


def main(argv=None, *, runner=jtag_chain.run_tool, popen=subprocess.Popen, jtag=None, out=print) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv and argv[0].startswith("-") and argv[0] not in ("-h", "--help"):
        argv = ["tui", *argv]   # Why: the console is the default command, flags and all
    args = parser().parse_args(argv)
    try:
        boards = load_boards()
        if args.action == "list":
            return run_list(boards, out)
        if args.action in (None, "tui"):
            from tools.board import tui
            quartus_bin = getattr(args, "quartus_bin", None)
            project, name = getattr(args, "project", None), getattr(args, "board", None)
            if project or name:
                board = pick(boards, project, name)
                first = (board, f"selected {board.key}")
            else:
                first = detect_board(boards, quartus_bin, runner=runner)
            ports = serial_ports()
            port = getattr(args, "port", None) or (ports[0] if len(ports) == 1 else "")
            return tui.main(boards, port=port, quartus_bin=quartus_bin, first=first)
        board = pick(boards, args.project, args.board)
        if args.action == "program":
            sof = resolve_path(args.sof or board.sof)
            if not args.yes:
                out(json.dumps(dict(program_plan(board, sof, args.cable, args.quartus_bin),
                                    dry_run="nothing programmed; add --yes"), indent=2))
                return 0
            program(board, sof, cable=args.cable, quartus_bin=args.quartus_bin, runner=runner, out=out)
            return 0
        if args.action == "load":
            chosen = args.program or board.program_default
            if not chosen:
                raise BoardError(f"give --program ({board.program_label.lower()})")
            program_file = resolve_path(chosen)
            if not args.yes:
                out(json.dumps({"board": board.key, "program": str(program_file),
                                "exists": program_file.is_file(),
                                "command": load_command(board, program_file, args.port or "<port>"),
                                "dry_run": "no port opened; add --yes with --port"}, indent=2))
                return 0
            load(board, program_file, args.port or "", out=out, popen=popen)
            return 0
        if not args.yes:
            out(json.dumps({"board": board.key, "text": args.text,
                            "cable": args.cable or f"<the one cable with IDCODE {board.idcode}>",
                            "device": args.device or f"<the device with IDCODE {board.idcode}>",
                            "dry_run": "nothing opened; add --yes"}, indent=2))
            return 0
        cable, device = jtag_device(board, cable=args.cable, device=args.device,
                                    quartus_bin=args.quartus_bin, runner=runner)
        keyboard = Keyboard(cable, device, args.quartus_bin, jtag=jtag)
        try:
            out(f"typed {keyboard.type_text(args.text)} keys on {board.key}")
        finally:
            closed = keyboard.close()
        if closed != "closed":
            raise BoardError(f"JTAG close: {closed}")
        return 0
    except (BoardError, ValueError, OSError, RuntimeError) as exc:
        print(f"board: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
