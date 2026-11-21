#!/usr/bin/env python3
"""Play Doom on the DE2-115 or the DE10-Lite with one command.

    python3 src/nand2doom/scripts/host/n2d_play.py --port /dev/serial/by-id/usb-FTDI_...-port0
    python3 src/nand2doom/scripts/host/n2d_play.py --port PORT --plan
    python3 src/nand2doom/scripts/host/n2d_play.py --port PORT --skip-program          # reload, keep the FPGA image
    python3 src/nand2doom/scripts/host/n2d_play.py --port PORT --skip-load --transport jtag   # reconnect controls

Steps: refuse if another process holds the UART or a JTAG client is running;
check the board with jtagconfig (and, for JTAG controls, resolve the
quartus_stp device name by IDCODE); program the SOF; Ping and Halt; stream-load
the image at 3 Mbaud, skipping every non-program segment whose stored SDRAM
digest already matches; verify every loaded segment's stored digest; reset the
PC and Resume; then open the real-time controls. Spec:
wiki/src/nand2doom/scripts/host/spec.md#play.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.board import jtag_chain  # noqa: E402
from src.nand2doom.scripts.host import n2d_uart as uart  # noqa: E402
from src.nand2doom.scripts.host import n2d_uart_debug_host as wire  # noqa: E402

BOARDS = {
    "de10_lite": dict(idcode="031050DD", project="n2d_de10_lite", baud="de10_lite"),
    "de2_115": dict(idcode="020F70DD", project="n2d_de2_115", baud="de2_115"),
}
# Why: these processes open the USB-Blaster through jtagd; jtagd itself is the
# shared server, so it is not a conflicting client.
JTAG_CLIENTS = frozenset({"quartus_pgm", "quartus_stp", "quartus_stpw", "quartus_stp_tcl",
                          "system-console", "nios2-terminal", "jtagconfig", "quartus_jli"})
SEED = 0xFFFFFFFF
FRAME_GAP_SECONDS = 0.05     # Why: just over the device's 40 ms partial-frame abandon (wiki/src/nand2doom/rtl/io/mas.md "Streaming load session"), so a gap ends any partial frame
SESSION_IDLE_SECONDS = 2.0   # Ref: wiki/src/nand2doom/rtl/io/mas.md "Streaming load session": the device closes an idle session after 2 s
PROGRAM_TIMEOUT = 180
PORT_ENV = "N2D_UART_PORT"

EXIT_OK, EXIT_ARGS, EXIT_DEVICE, EXIT_BUSY, EXIT_INTERRUPTED = 0, 2, 3, 4, 130


class PlayError(Exception):
    """A step failed; the message says what state the board is in and how to recover."""


class _NoUart(Exception):
    pass


class LoadInterrupted(Exception):
    def __init__(self, ended):
        super().__init__("load interrupted")
        self.ended = ended



def checkout_roots(root=ROOT):
    """This checkout first, then the main checkout when this is a linked worktree."""
    roots = [root]
    marker = root / ".git"
    if marker.is_file():
        match = re.match(r"gitdir:\s*(.+)", marker.read_text().strip())
        if match:
            gitdir = Path(match[1])
            gitdir = gitdir if gitdir.is_absolute() else (root / gitdir)
            if gitdir.parent.name == "worktrees" and gitdir.parent.parent.name == ".git":
                roots.append(gitdir.parent.parent.parent)
    return list(dict.fromkeys(r.resolve() for r in roots))


def newest_sof(board, roots):
    project = BOARDS[board]["project"]
    found = [p for r in roots for p in [r / "workdir/quartus" / project / "output_files" / f"{project}.sof"]
             if p.is_file()]
    return max(found, key=lambda p: p.stat().st_mtime) if found else None


def holders(port=None, *, jtag=True, proc=Path("/proc"), own=None):
    """Other processes holding the serial device or acting as JTAG clients: [(pid, name, what)]."""
    own = os.getpid() if own is None else own
    target = os.path.realpath(port) if port else None
    found = []
    for entry in proc.iterdir():
        if not entry.name.isdigit() or int(entry.name) == own:
            continue
        pid = int(entry.name)
        try:
            name = (entry / "comm").read_text().strip()
        except OSError:
            continue
        if jtag and name in JTAG_CLIENTS:
            found.append((pid, name, "USB-Blaster (JTAG client)"))
        if target:
            try:
                fds = list((entry / "fd").iterdir())
            except OSError:
                continue  # Why: another user's process is not inspectable, so skip it
            for fd in fds:
                try:
                    if os.readlink(fd) == target:
                        found.append((pid, name, f"UART {target}"))
                        break
                except OSError:
                    continue
    return found


run_tool = jtag_chain.run_tool


@dataclass
class SegmentResult:
    name: str
    address: int
    size: int
    action: str
    reason: str
    seconds: float


def program_segment(manifest, segment):
    return segment.address <= manifest.entry < segment.address + len(segment.data)


def segment_chunks(segment):
    step = wire.STREAM_MAX_PAYLOAD
    return [(segment.address + offset, segment.data[offset:offset + step])
            for offset in range(0, len(segment.data), step)]


def drain(session, seconds):
    """Discard replies that may still arrive for an abandoned exchange."""
    limit = session.clock() + seconds
    while session.clock() < limit:
        session._poll()
        session.sleep(0.005)
    session.buffer.clear()


def abandon(session):
    """After an interrupt: let any partial frame expire, then End the session (bounded)."""
    try:
        session.sleep(FRAME_GAP_SECONDS)
        session.end()
        return True
    except BaseException:  # Why: after a second Ctrl-C or no reply, the device idle bound still closes the session
        return False


def load_image(client, manifest, *, baud, force_full=False, report=print, window=8):
    """Halt, stream what is needed and verify every segment's stored digest.

    A non-program segment whose stored SDRAM digest already equals the manifest
    digest is skipped. The program segment (the one holding the entry) is always
    loaded. A digest mismatch or a failed digest request loads the segment."""
    clock = client.clock
    client.command(wire.CMD_HALT)
    session = uart.StreamSession(client.transport, baud, window=window, clock=clock,
                                 sleep=client.sleep, deadline=client.operation_deadline)
    results, frames = [], 0
    try:
        session.hello()
        for segment in manifest.segments:
            started = clock()
            expected = wire.stream_digest(segment.data, SEED)
            if program_segment(manifest, segment):
                reason = "program segment, always loaded"
            elif force_full:
                reason = "--force-full"
            else:
                try:
                    stored = session.digest(segment.address, len(segment.data), SEED)
                except uart.StreamError as error:
                    reason = f"digest request failed ({error})"
                    drain(session, session.frame_timeout)
                else:
                    if stored == expected:
                        results.append(SegmentResult(segment.name, segment.address, len(segment.data),
                                                     "skipped", "stored digest matches", clock() - started))
                        report(f"  {segment.name}: skipped, stored digest 0x{stored:08x} matches "
                               f"({len(segment.data)} bytes, {clock() - started:.2f} s)")
                        continue
                    reason = f"stored digest 0x{stored:08x} != 0x{expected:08x}"
            chunks = segment_chunks(segment)
            report(f"  {segment.name}: loading {len(segment.data)} bytes in {len(chunks)} frames ({reason})")
            session.write(chunks, lambda message: report("    " + message))
            frames += len(chunks)
            stored = session.digest(segment.address, len(segment.data), SEED)
            if stored != expected:
                raise uart.StreamError(f"stored digest mismatch in segment {segment.name} after loading")
            results.append(SegmentResult(segment.name, segment.address, len(segment.data),
                                         "loaded", reason, clock() - started))
            report(f"  {segment.name}: loaded and verified, digest 0x{stored:08x} "
                   f"({clock() - started:.2f} s)")
        committed = session.end()
        if committed < frames:
            raise uart.StreamError("device committed fewer frames than were sent")
    except KeyboardInterrupt:
        client.failed = True
        raise LoadInterrupted(abandon(session)) from None
    except Exception as error:
        client.failed = True
        raise uart.StreamError(f"streaming load stopped: {error}; endpoint remains halted") from error
    report(f"  stream session ended; {session.retransmissions} retransmissions")
    return results


def start(client, manifest):
    client.command(wire.CMD_RESET_PC, address=manifest.entry)
    client.command(wire.CMD_RESUME)


def ping_client(transport, timeout, attempts=3, settle=0.2, sleep=time.sleep):
    """Identity Ping on fresh Clients (a just-configured FPGA may emit one stray byte)."""
    last = None
    for attempt in range(attempts):
        if attempt:
            sleep(settle)
            if hasattr(transport, "reset_input_buffer"):
                transport.reset_input_buffer()
        client = uart.Client(transport, timeout)
        try:
            return client, client.command(wire.CMD_PING)
        except uart.HostError as error:
            last = error
    raise PlayError(f"no N2D1 Ping answer after {attempts} attempts: {last}")



def parser():
    p = argparse.ArgumentParser(prog="n2d_play.py", description=__doc__.split("\n\n", 1)[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter,
                                epilog=__doc__.split("\n\n", 1)[1])
    p.add_argument("--board", choices=sorted(BOARDS), default="de2_115")
    p.add_argument("--port", default=os.environ.get(PORT_ENV),
                   help=f"UART port of the board (default ${PORT_ENV})")
    p.add_argument("--sof", type=Path, help="SOF to program (default: newest workdir/quartus build)")
    p.add_argument("--manifest", type=Path, default=ROOT / "workdir/software/n2d_doom/manifest.json",
                   help="Doom image manifest (default %(default)s)")
    p.add_argument("--quartus-bin", help="directory of jtagconfig/quartus_pgm/quartus_stp (default PATH)")
    p.add_argument("--skip-program", action="store_true", help="keep the FPGA image; Halt and reload")
    p.add_argument("--skip-load", action="store_true",
                   help="reconnect to a running game: no programming, no load (implies --skip-program)")
    p.add_argument("--force-full", action="store_true", help="load every segment; no stored-digest skip")
    p.add_argument("--controls", choices=("auto", "gui", "terminal", "none"), default="auto",
                   help="auto: Tk window when available, else terminal (default)")
    p.add_argument("--transport", choices=("uart", "jtag"), default="uart",
                   help="controls transport (default uart)")
    p.add_argument("--jtag-device", help="JTAG controls: exact quartus_stp device name "
                   "(default: the cable's one device with the board IDCODE, from quartus_stp)")
    p.add_argument("--timeout", type=float, default=1.0, help="UART transaction seconds (0.01..1)")
    p.add_argument("--budget", type=float, default=900.0, help="whole load seconds (default 900)")
    p.add_argument("--plan", "--dry-run", dest="plan", action="store_true",
                   help="print the steps and inputs; open nothing")
    return p


class Steps:
    def __init__(self, names, out=print, clock=time.monotonic):
        self.names, self.out, self.clock, self.index = names, out, clock, 0
        self.started = clock()

    def begin(self, name):
        self.index += 1
        self.current, self.t0 = name, self.clock()
        self.out(f"[{self.index}/{len(self.names)}] {name} ...", flush=True)

    def done(self, detail=""):
        self.out(f"[{self.index}/{len(self.names)}] {self.current}: ok in {self.clock() - self.t0:.1f} s"
                 + (f" ({detail})" if detail else ""), flush=True)


def tool_path(args, name):
    return str(Path(args.quartus_bin) / name) if args.quartus_bin else name


def pending_device(board):
    return f"<resolved by IDCODE 0x{board['idcode']} at the board check>"


def resolve_device(args, board, cable, runner):
    """The quartus_stp device name for JTAG controls: --jtag-device, else the IDCODE match."""
    if args.jtag_device:
        return args.jtag_device
    try:
        return jtag_chain.resolve(cable, args.quartus_bin, board["idcode"], runner=runner)
    except (ValueError, RuntimeError) as error:
        raise PlayError(f"JTAG device: {error}") from error


def controls_argv(args, board, cable, device=None):
    argv = ["--transport", args.transport, "--baud", str(args.baud), "--connect",
            "--timeout", str(args.timeout)]
    if args.transport == "jtag":
        argv += ["--cable", cable or "USB-Blaster [1-1]",
                 "--device", device or args.jtag_device or pending_device(board)]
        if args.quartus_bin:
            argv += ["--quartus-bin", args.quartus_bin]
    else:
        argv += ["--port", args.port]
    if args.controls == "terminal":
        argv.append("--terminal")
    return argv


def step_names(args):
    names = ["exclusive-use check"]
    if not args.skip_program or args.transport == "jtag":
        names.append("board check (jtagconfig)")
    if not args.skip_program:
        names.append("program FPGA")
    if args.port:
        names.append("Ping" if args.skip_load else "Ping and Halt")
    if not args.skip_load:
        names += ["stream load with digest skip", "reset PC and Resume"]
    if args.controls != "none":
        names.append(f"controls over {args.transport.upper()}")
    return names


def recovery(args):
    base = f"{sys.executable} {Path(__file__).resolve()} --port {args.port}"
    return (f"Recovery: wait {SESSION_IDLE_SECONDS:.0f} s (the device closes an abandoned stream session), "
            f"then rerun `{base} --skip-program` (the CPU stays halted; unchanged segments are skipped). "
            f"If Ping does not answer, rerun `{base}` to reprogram.")


def show_plan(args, board, manifest, sof):
    print(f"n2d_play plan for {args.board}: nothing is opened")
    for index, name in enumerate(step_names(args), 1):
        print(f"  {index}. {name}")
    print(f"UART {args.port or '(none)'} at {args.baud} baud; controls {args.controls} over {args.transport}")
    if args.transport == "jtag":
        print("JTAG device: " + (f"{args.jtag_device} (--jtag-device)" if args.jtag_device else
              f"the one quartus_stp device with IDCODE 0x{board['idcode']} on the checked cable"))
    if not args.skip_program:
        print(f"SOF {sof}" + (f" (modified {time.ctime(sof.stat().st_mtime)})" if sof and sof.exists() else ""))
        print(f"program: {tool_path(args, 'quartus_pgm')} -c <cable with {board['idcode']}> -m jtag -o 'p;{sof}@1'")
    if manifest:
        total = sum(len(s.data) for s in manifest.segments)
        for segment in manifest.segments:
            kind = "always loaded (program)" if program_segment(manifest, segment) else (
                "loaded (--force-full)" if args.force_full else "skipped when its stored digest matches")
            frames = len(segment_chunks(segment))
            print(f"  segment {segment.name} @0x{segment.address:08x}: {len(segment.data)} bytes, "
                  f"{wire.stream_wire_seconds(len(segment.data), args.baud, frames=frames):.1f} s wire; {kind}")
        print(f"full load {total} bytes: {wire.stream_wire_seconds(total, args.baud):.1f} s wire time")
    for pid, name, what in holders(args.port, jtag=True):
        print(f"currently held: {what} by PID {pid} ({name}); a live run would refuse")
    print("controls: n2d_control.py " + " ".join(controls_argv(args, board, None)))


def validate(args, p):
    if args.skip_load:
        args.skip_program = True
    if not 0.01 <= args.timeout <= 1:
        p.error("--timeout must be in 0.01..1 seconds")
    if not args.timeout <= args.budget <= 86400:
        p.error("--budget must be at least one timeout and at most 86400 seconds")
    if not args.port and (not args.skip_load or args.transport == "uart"):
        p.error(f"give --port (or set ${PORT_ENV}); the UART is needed for loading and UART controls")
    board = BOARDS[args.board]
    args.baud = uart.baud_value(board["baud"])
    sof = None
    if not args.skip_program:
        sof = args.sof or newest_sof(args.board, checkout_roots())
        if sof is None:
            p.error(f"no {board['project']}.sof under workdir/quartus; build with "
                    f"`bin/n2d_quartus --board {args.board}` or pass --sof")
        if not Path(sof).is_file():
            p.error(f"SOF not found: {sof}")
        sof = Path(sof).resolve()
    manifest = None
    if not args.skip_load:
        if not args.manifest.is_file():
            p.error(f"manifest not found: {args.manifest}; build the image with "
                    "`bin/n2d_doom build` or pass --manifest")
        try:
            manifest = uart.validate_manifest(args.manifest)
        except (ValueError, OSError) as error:
            p.error(f"manifest {args.manifest}: {error}")
    return board, manifest, sof


def play(args, board, manifest, sof, *, runner=run_tool, opener=uart.open_serial,
         find_holders=holders, launch_controls=None, out=print):
    steps = Steps(step_names(args), out)
    cable = device = None
    steps.begin("exclusive-use check")
    busy = find_holders(args.port, jtag=not args.skip_program or args.transport == "jtag")
    if busy:
        for pid, name, what in busy:
            out(f"refused: {what} is held by PID {pid} ({name}); close it first (never killed by this tool)")
        return EXIT_BUSY
    steps.done("UART and USB-Blaster free")
    if not args.skip_program or args.transport == "jtag":
        steps.begin("board check (jtagconfig)")
        try:
            cable = jtag_chain.find_cable(board["idcode"], args.quartus_bin, runner=runner)
        except (ValueError, RuntimeError) as error:
            raise PlayError(str(error)) from error
        if args.transport == "jtag":
            device = resolve_device(args, board, cable, runner)
        steps.done(f"{cable}: {board['idcode']}" + (f"; JTAG device {device!r}" if device else ""))
    if not args.skip_program:
        steps.begin("program FPGA")
        argv = jtag_chain.program_command(sof, cable, args.quartus_bin)
        out("  " + " ".join(argv), flush=True)
        try:
            rc, text = runner(argv, PROGRAM_TIMEOUT)
        except KeyboardInterrupt:
            out("interrupted while programming: the FPGA configuration is undefined; rerun n2d_play to reprogram")
            return EXIT_INTERRUPTED
        if not jtag_chain.program_succeeded(rc, text):
            raise PlayError(f"quartus_pgm exited {rc}: {text.strip()[-300:]}")
        steps.done(Path(sof).name)
    transport = None
    try:
        if not args.port:
            raise _NoUart
        steps.begin("Ping" if args.skip_load else "Ping and Halt")
        transport = opener(args.port, args.timeout, args.baud)
        client, (identity, halted) = ping_client(transport, args.timeout)
        if args.skip_load:
            if halted:
                raise PlayError("the CPU is halted (an interrupted load?); rerun without --skip-load")
            steps.done("N2D1 running")
        else:
            steps.done(f"N2D1, {'halted' if halted else 'running'}; Halt follows")
            steps.begin("stream load with digest skip")
            client.operation_deadline = client.clock() + args.budget
            try:
                results = load_image(client, manifest, baud=args.baud, force_full=args.force_full,
                                     report=lambda m: out(m, flush=True))
            except LoadInterrupted as stop:
                out("interrupted during the load; " + ("stream session ended cleanly. " if stop.ended else
                    "stream End unconfirmed. ") + "The CPU remains halted. " + recovery(args))
                return EXIT_INTERRUPTED
            except uart.StreamError as error:
                raise PlayError(f"{error}. {recovery(args)}") from error
            loaded = sum(r.size for r in results if r.action == "loaded")
            skipped = sum(r.size for r in results if r.action == "skipped")
            steps.done(f"{loaded} bytes loaded, {skipped} bytes skipped by matching stored digest")
            steps.begin("reset PC and Resume")
            start(client, manifest)
            steps.done(f"entry 0x{manifest.entry:08x}")
    except _NoUart:
        pass  # Why: --skip-load with JTAG controls and no UART: nothing to check
    except uart.HostError as error:
        raise PlayError(f"UART: {error}") from error
    finally:
        if transport is not None:
            transport.close()
    out(f"Doom is running ({time.monotonic() - steps.started:.1f} s total).", flush=True)
    argv = controls_argv(args, board, cable, device)
    if args.controls == "none":
        out("controls: " + " ".join([sys.executable, str(TOOLS / "n2d_control.py"), *argv]))
        return EXIT_OK
    steps.begin(f"controls over {args.transport.upper()}")
    if launch_controls is None:
        from src.nand2doom.scripts.host import n2d_control
        launch_controls = n2d_control.main
    return launch_controls(argv)


def main(argv=None, **kwargs):
    p = parser()
    args = p.parse_args(argv)
    board, manifest, sof = validate(args, p)
    if args.plan:
        show_plan(args, board, manifest, sof)
        return EXIT_OK
    previous = signal.signal(signal.SIGTERM, signal.default_int_handler)  # Why: SIGTERM must clean up like Ctrl-C
    try:
        return play(args, board, manifest, sof, **kwargs)
    except PlayError as error:
        print(f"n2d_play: {error}", file=sys.stderr)
        return EXIT_DEVICE
    except (OSError, subprocess.TimeoutExpired) as error:
        print(f"n2d_play: {error}", file=sys.stderr)
        return EXIT_DEVICE
    except KeyboardInterrupt:
        print("n2d_play: interrupted", file=sys.stderr)
        return EXIT_INTERRUPTED
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == "__main__":
    raise SystemExit(main())
