#!/usr/bin/env python3
"""Load a raw image or a segment manifest into a board through the common UART memory endpoint.

The load transaction is uart_mem.load: identify and check the project, hold
the CPU, write and verify every segment, set the entry state, release. A
`.json` image is a segment manifest (segments at addresses and an entry).
Without --yes only the plan is printed as JSON and no port is opened.
Spec: wiki/src/common/scripts/uart_mem/spec.md#load-script.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.common.scripts.uart_mem import uart_mem as um  # noqa: E402

DEFAULT_BAUD = um.BOARD_BAUD


class SerialTransport:
    """uart_mem.Transport over a pyserial port: 8-N-1, no flow control."""

    def __init__(self, port: str, baud: int, write_timeout: float = 5.0):
        import serial
        self.serial = serial.Serial(port=port, baudrate=baud, bytesize=8, parity="N", stopbits=1,
                                    timeout=0.01, write_timeout=write_timeout,
                                    xonxoff=False, rtscts=False, dsrdtr=False)

    def write(self, data: bytes) -> None:
        self.serial.write(data)
        self.serial.flush()

    def read(self, size: int, deadline: float) -> bytes:
        # Why: return once anything has arrived; waiting for `size` bytes made every
        # short response sit out the whole command deadline.
        out = bytearray()
        while not out and time.monotonic() < deadline:
            out += self.serial.read(max(1, min(size, self.serial.in_waiting)))
        return bytes(out)

    def close(self) -> None:
        self.serial.close()


def project_id(text: str) -> int:
    """A project id from its four ASCII characters (`n2m `) or 0x-prefixed hex."""
    if text.lower().startswith("0x"):
        return int(text, 16)
    if len(text) != 4 or not text.isascii():
        raise argparse.ArgumentTypeError("a project is four ASCII characters or 0x-prefixed hex")
    return int.from_bytes(text.encode("ascii"), "little")


def read_manifest(path: Path) -> tuple[list[um.Segment], int]:
    """The segments and entry of a segment manifest: `entry_address` (or
    `entry`) and `segments`, each with `address` and `file` (or `path`)
    relative to the manifest's folder, and optionally `byte_length` and
    `sha256`, which are checked. Every byte is read before any port opens."""
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("segments"), list) \
            or not manifest["segments"]:
        raise ValueError("a manifest is an object with a nonempty segments list")
    entry = manifest.get("entry_address", manifest.get("entry"))
    if type(entry) is not int or entry < 0:
        raise ValueError("a manifest needs an integer entry_address")
    root = path.resolve().parent
    segments = []
    for index, item in enumerate(manifest["segments"]):
        name = item.get("file", item.get("path")) if isinstance(item, dict) else None
        address = item.get("address") if isinstance(item, dict) else None
        if not isinstance(name, str) or type(address) is not int or address < 0:
            raise ValueError(f"segment {index} needs an integer address and a file")
        source = (root / name).resolve()
        if not source.is_relative_to(root):
            raise ValueError(f"segment {index} file escapes the manifest's folder")
        data = source.read_bytes()
        if "byte_length" in item and item["byte_length"] != len(data):
            raise ValueError(f"segment {index} ({name}) is not byte_length bytes")
        if "sha256" in item and hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise ValueError(f"segment {index} ({name}) does not match its sha256")
        segments.append(um.Segment(address, data))
    return segments, entry


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("image", type=Path, help="raw image file, or a .json segment manifest")
    p.add_argument("--port", help="serial device of the board's USB-to-UART adapter")
    p.add_argument("--baud", type=int, default=DEFAULT_BAUD,
                   help=f"board UART rate (default {DEFAULT_BAUD})")
    p.add_argument("--address", type=lambda s: int(s, 0), default=None,
                   help="load address (default: the base of the first writable window)")
    p.add_argument("--entry", type=lambda s: int(s, 0), default=None,
                   help="entry address (default: the endpoint's advertised entry)")
    p.add_argument("--project", type=project_id, default=None,
                   help="expected project, for example 'n2m ' (default: any)")
    p.add_argument("--skip-unchanged", action="store_true",
                   help="skip a segment that does not hold the entry and whose stored digest matches")
    p.add_argument("--timeout", type=float, default=1.0, help="seconds per command (default 1)")
    p.add_argument("--yes", action="store_true", help="open the port and load; without it only the plan is printed")
    return p


def run(args, *, transport=None, out=sys.stdout) -> int:
    if args.image.suffix.lower() == ".json":
        if args.address is not None:
            raise SystemExit("uart_mem_load: --address does not apply to a manifest")
        try:
            segments, manifest_entry = read_manifest(args.image)
        except (OSError, ValueError) as error:
            raise SystemExit(f"uart_mem_load: manifest {args.image}: {error}")
    else:
        data = args.image.read_bytes()
        if not data:
            raise SystemExit(f"uart_mem_load: {args.image} is empty")
        segments, manifest_entry = [um.Segment(args.address, data)], None
    entry = args.entry if args.entry is not None else manifest_entry
    total = sum(len(segment.data) for segment in segments)
    if not args.yes:
        json.dump({"image": str(args.image), "bytes": total, "port": args.port, "baud": args.baud,
                   "address": args.address, "entry": entry, "project": args.project,
                   "segments": [{"address": s.address, "bytes": len(s.data)} for s in segments],
                   "skip_unchanged": args.skip_unchanged, "dry_run": True}, out, indent=2)
        out.write("\n")
        return 0
    if transport is None:
        if not args.port:
            raise SystemExit("uart_mem_load: --port is required with --yes")
        try:
            transport = SerialTransport(args.port, args.baud)
        except (ImportError, OSError, ValueError) as error:
            print(f"uart_mem_load: cannot open {args.port}: {error}", file=sys.stderr)
            return 1
    client = um.Client(transport, args.timeout)
    try:
        identity = client.identify()
        um.check_identity(identity, args.project)
        writable = [w for w in identity.windows if w.attributes & um.ATTR_WRITE]
        if segments[0].address is None:
            segments[0].address = writable[0].base if writable else 0
        entry = entry if entry is not None else identity.entry
        start = time.monotonic()
        print(f"loading {total} bytes in {len(segments)} segment(s) into '{identity.project}'"
              f"{'' if identity.digest else ' (no Digest: verifying by readback)'}", file=out, flush=True)
        outcomes = um.load(client, segments, entry, args.project, skip_unchanged=args.skip_unchanged)
    except um.EndpointError as error:
        print(f"uart_mem_load: {error}", file=sys.stderr)
        return 1
    finally:
        close = getattr(transport, "close", None)
        if close is not None:
            close()
    for segment, outcome in zip(segments, outcomes):
        print(f"  0x{segment.address:x} {len(segment.data)} bytes: {outcome}", file=out)
    print(f"loaded {total} bytes into '{identity.project}', entry 0x{entry:x}, "
          f"{time.monotonic() - start:.1f} s", file=out)
    return 0


def main(argv=None) -> int:
    return run(parser().parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
