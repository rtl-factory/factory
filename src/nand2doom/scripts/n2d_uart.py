#!/usr/bin/env python3
"""Bounded N2D1 host commands; serial is imported only for explicit live use."""

from __future__ import annotations

import argparse
from collections import OrderedDict, deque
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import sys
import time

import n2d_image as image
import n2d_uart_debug_host as wire

CONFIG = json.loads((Path(__file__).resolve().parents[1] / "cfg/uart.json").read_text())
ROOT = Path(__file__).resolve().parents[3]


class HostError(Exception):
    """Transport or device failure; command effects may be uncertain."""


class BulkError(HostError):
    """A checked device error can report a committed prefix; loss is unknown."""

    def __init__(self, message, *, completed=None):
        super().__init__(message)
        self.completed = completed


def integer(value, name, low=0, high=0xFFFFFFFF):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an integer in {low}..{high}")
    return value


def memory_range(address, count):
    integer(address, "address")
    integer(count, "word count", 1, wire.MAX_WORDS)
    if address % 4 or not any(start <= address and address + count * 4 <= end
                              for start, end in CONFIG["memory_windows"]):
        raise ValueError("unaligned address or burst outside one debug memory window")


@dataclass(frozen=True)
class Segment:
    address: int
    data: bytes
    name: str


@dataclass(frozen=True)
class Manifest:
    entry: int
    segments: tuple[Segment, ...]


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate manifest key: {key}")
        result[key] = value
    return result


def validate_manifest(path: Path) -> Manifest:
    """Snapshot every byte before opening; do not re-read files during loading."""
    manifest = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be an object")
    for key, expected in (("schema_version", 1), ("architecture", image.ARCH),
                          ("abi", image.ABI), ("endianness", "little"),
                          ("memory_map_revision", image.MEMORY_MAP_REVISION)):
        if type(manifest.get(key)) is not type(expected) or manifest[key] != expected:
            raise ValueError(f"unsupported manifest {key}")
    entry = integer(manifest.get("entry_address", manifest.get("entry")), "entry")
    if "entry" in manifest and manifest["entry"] != entry:
        raise ValueError("conflicting entry fields")
    if entry % 4 or entry >= CONFIG["memory_windows"][0][1]:
        raise ValueError("entry must be aligned SDRAM")
    entries = manifest.get("segments")
    if not isinstance(entries, list) or not 1 <= len(entries) <= 256:
        raise ValueError("manifest requires 1..256 segments")
    segments = []
    root = path.resolve().parent
    for item in entries:
        if not isinstance(item, dict):
            raise ValueError("segment must be an object")
        address = integer(item.get("address"), "segment address")
        length = integer(item.get("byte_length"), "segment byte_length", 1, 0x4000000)
        if address % 4 or address + length > CONFIG["memory_windows"][0][1]:
            raise ValueError("segments require aligned starts and must fit SDRAM")
        name = item.get("file", item.get("path"))
        if not isinstance(name, str) or not name or ("path" in item and item["path"] != name):
            raise ValueError("segment requires one unambiguous file/path")
        # Both slash styles are checked even when validating a Windows plan on Linux.
        if "\\" in name or ":" in name or Path(name).is_absolute():
            raise ValueError("segment file must be a portable relative path")
        source = (root / name).resolve()
        if not source.is_relative_to(root):
            raise ValueError("segment file escapes manifest directory")
        if any(address < previous.address + len(previous.data) and
               previous.address < address + length for previous in segments):
            raise ValueError("overlapping segments")
        if source.stat().st_size != length:
            raise ValueError(f"segment length mismatch: {name}")
        data = source.read_bytes()
        if len(data) != length or hashlib.sha256(data).hexdigest() != item.get("sha256"):
            raise ValueError(f"segment SHA-256/length mismatch: {name}")
        segments.append(Segment(address, data, name))
    if not any(s.address <= entry and entry + 4 <= s.address + len(s.data) for s in segments):
        raise ValueError("entry instruction is not contained in a segment")
    # High SDRAM is the reserved game-data window, including manifests stripped
    # of provenance. The build recipe remains the owner of the pinned facts.
    import n2d_doom as doom
    if "engine" in manifest or "game_data" in manifest or any(
            s.address + len(s.data) > doom.WAD_ADDRESS or s.name.lower().endswith(".wad")
            for s in segments):
        expected_engine = dict(name="doomgeneric", revision=doom.ENGINE_REVISION,
                               archive_sha256=doom.ENGINE_SHA256,
                               license=doom.ENGINE_LICENSE, url=doom.ENGINE_URL)
        expected_game = dict(name="Freedoom Phase 1", version=doom.FREEDOOM_VERSION,
                             archive_sha256=doom.FREEDOOM_SHA256,
                             wad_sha256=doom.WAD_SHA256, license="BSD-3-Clause",
                             url=doom.FREEDOOM_URL, byte_length=doom.WAD_BYTE_LENGTH,
                             address=doom.WAD_ADDRESS)
        if manifest.get("engine") != expected_engine or manifest.get("game_data") != expected_game:
            raise ValueError("Doom engine/game-data provenance does not match pinned recipe")
        assets = [s for s in segments if s.address == doom.WAD_ADDRESS]
        if len(assets) != 1 or len(assets[0].data) != doom.WAD_BYTE_LENGTH or hashlib.sha256(
                assets[0].data).hexdigest() != doom.WAD_SHA256:
            raise ValueError("Doom asset segment does not match pinned WAD")
    return Manifest(entry, tuple(sorted(segments, key=lambda s: s.address)))


def baud_value(text):
    """Parse --baud: an integer rate or a board name from uart.json board_baudrates."""
    if text in CONFIG["board_baudrates"]:
        return CONFIG["board_baudrates"][text]
    try:
        value = int(text, 0)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"baud must be an integer or one of {sorted(CONFIG['board_baudrates'])}") from None
    if not 9600 <= value <= 12_000_000:
        raise argparse.ArgumentTypeError("baud must be in 9600..12000000")
    return value


def add_baud_option(parser):
    """The link rate is a property of the loaded image; shared by every n2d host tool."""
    boards = ", ".join(f"{name}={rate}" for name, rate in CONFIG["board_baudrates"].items())
    parser.add_argument("--baud", type=baud_value, default=CONFIG["baudrate"],
                        help=f"link rate of the loaded image or a board name ({boards}); "
                             "default %(default)s")


def open_serial(port, timeout, baud=None):
    try:
        import serial
    except ImportError as error:
        raise HostError("live serial requires optional pyserial; install pyserial in the host Python environment") from error
    connection = serial.Serial(port=None, baudrate=baud or CONFIG["baudrate"], bytesize=8,
                               parity="N", stopbits=1, timeout=0, write_timeout=timeout,
                               xonxoff=False, rtscts=False, dsrdtr=False)
    try:
        connection.dtr = False
        connection.rts = False
        connection.port = port
        connection.open()
    except Exception:
        connection.close()
        raise
    return connection


class Client:
    """One outstanding request. Any failure poisons this session; never retry."""

    def __init__(self, transport, timeout, *, clock=time.monotonic, sleep=time.sleep):
        if type(timeout) not in (float, int) or not math.isfinite(timeout) or not .01 <= timeout <= 60:
            raise ValueError("timeout must be finite and in 0.01..60 seconds")
        self.transport, self.timeout = transport, timeout
        self.clock, self.sleep = clock, sleep
        self.sequence = 0
        self.failed = False
        self._capabilities = None
        self.operation_deadline = None

    def _deadline(self):
        now = self.clock()
        if self.operation_deadline is not None:
            if (not math.isfinite(self.operation_deadline) or
                    now + self.timeout > self.operation_deadline):
                raise HostError("whole-operation budget cannot reserve one complete transaction")
        return now + self.timeout

    def _read(self, count, deadline):
        data = bytearray()
        while len(data) < count:
            if self.clock() >= deadline:
                raise HostError(f"response timeout after {len(data)}/{count} bytes")
            part = self.transport.read(count - len(data))
            if self.clock() >= deadline:
                raise HostError("response deadline exceeded")
            if len(part) > count - len(data):
                raise HostError("transport returned excess bytes")
            data.extend(part)
            if not part:
                self.sleep(min(0.001, max(0, deadline - self.clock())))
        return bytes(data)

    def command(self, command, *, address=0, words=(), count=None, byte_enable=0):
        if self.failed:
            raise HostError("session failed; explicit operator recovery required")
        sequence = self.sequence
        self.sequence = (sequence + 1) & 255
        packet = wire.request(command, sequence, address=address, words=words,
                              count=count, byte_enable=byte_enable)
        expected = (2 if command == wire.CMD_PING else 3 if command == wire.CMD_CAPS
                    else count if command == wire.CMD_PEEK else 0)
        deadline = self._deadline()
        try:
            # A short write is uncertain, not an invitation to send a second frame.
            if self.transport.write(packet) != len(packet):
                raise HostError("short write; transaction completion uncertain")
            if self.clock() >= deadline:
                raise HostError("write deadline exceeded; completion uncertain")
            header = self._read(6, deadline)
            if header[0] != wire.RESPONSE_SOF or header[1] != wire.VERSION:
                raise HostError("invalid response framing/version")
            if header[2] != command | 0x80 or header[3] != sequence:
                raise HostError("stale/wrong response command or sequence")
            status, returned = header[4:6]
            if status > wire.ST_BUSY or returned != (0 if status else expected):
                raise HostError("invalid response status/count")
            response = wire.decode_response(header + self._read(1 + 4 * returned, deadline))
            if response.status:
                raise HostError(f"device status 0x{response.status:02x}")
            if command == wire.CMD_PING and (response.words[0] != CONFIG["identity"] or
                                             response.words[1] not in (0, 1)):
                raise HostError("device is not the expected N2D1 endpoint")
            return response.words
        except Exception as error:
            self.failed = True
            raise HostError(f"command 0x{command:02x} sequence {sequence}: {error}; no retry") from error

    def capabilities(self):
        """Negotiate once per connection, before issuing any bulk command."""
        if self.failed:
            raise HostError("session failed; explicit operator recovery required")
        if self._capabilities is None:
            words = self.command(wire.CMD_CAPS)
            if words != (wire.BULK_IDENTITY, wire.BULK_LIMIT, 3):
                self.failed = True
                raise HostError("unsupported bulk capabilities; no legacy fallback")
            self._capabilities = words
        return self._capabilities

    def _bulk(self, command, address, length, data=b""):
        if self.failed:
            raise HostError("session failed; explicit operator recovery required")
        if self._capabilities is None:
            raise ValueError("call capabilities() once before bulk transfers")
        packet = wire.bulk_request(command, self.sequence, address, length, data)
        deadline = self._deadline()
        sequence = self.sequence
        self.sequence = (sequence + 1) & 255
        try:
            if self.transport.write(packet) != len(packet):
                raise BulkError("short bulk write; completion uncertain")
            if self.clock() >= deadline:
                raise BulkError("bulk write deadline exceeded; completion uncertain")
            header = self._read(13, deadline)
            if (header[:2] != b"\x6a\x02" or header[2] != command | 128 or
                    header[3] != sequence or int.from_bytes(header[5:9], "little") != address):
                raise BulkError("stale/wrong bulk framing/version/command/sequence/address")
            status = header[4]
            returned = int.from_bytes(header[9:11], "little")
            completed = int.from_bytes(header[11:13], "little")
            expected = length if not status and command == wire.CMD_BULK_READ else 0
            if (status > 0x0D or returned != expected or completed % 4 or
                    completed > length or (not status and completed != length) or
                    (status in (1, 2, 3, 4, 5, 6, 10) and completed != 0)):
                raise BulkError("invalid bulk status/length/completed count")
            response = wire.decode_bulk_response(header + self._read(returned + 4, deadline))
            if status:
                raise BulkError(f"bulk device status 0x{status:02x}; checked prefix {completed}",
                                completed=completed)
            return response.data
        except Exception as error:
            self.failed = True
            raise BulkError(f"bulk command 0x{command:02x} sequence {sequence}: {error}; no retry",
                            completed=error.completed if isinstance(error, BulkError) else None) from error

    def bulk_write(self, address, data):
        """Write one checked aligned block; no automatic retry on any failure."""
        self._bulk(wire.CMD_BULK_WRITE, address, len(data), data)

    def bulk_read(self, address, length):
        """Return complete CRC32-verified bytes or raise (never partial data)."""
        return self._bulk(wire.CMD_BULK_READ, address, length)


def load(client, manifest, *, verify=False, resume=False, report=print, bulk=False):
    if bulk:
        if not verify:
            raise ValueError("bulk load requires complete readback verification")
        client.capabilities()
    client.command(wire.CMD_HALT)
    acknowledged = 0
    started = time.monotonic()
    total = sum(len(s.data) for s in manifest.segments)
    next_report = 65536
    try:
        for segment in manifest.segments:
            report(f"segment {segment.name}: address=0x{segment.address:08x} bytes={len(segment.data)}")
            block_size = wire.BULK_LIMIT if bulk else 32
            for offset in range(0, len(segment.data), block_size):
                chunk = segment.data[offset:offset + block_size]
                full = len(chunk) // 4 * 4
                address = segment.address + offset
                if full:
                    words = tuple(int.from_bytes(chunk[i:i + 4], "little") for i in range(0, full, 4))
                    if bulk:
                        client.bulk_write(address, chunk[:full])
                    else:
                        client.command(wire.CMD_LOAD, address=address, words=words, byte_enable=15)
                    acknowledged += full
                if full < len(chunk):
                    tail = chunk[full:]
                    client.command(wire.CMD_POKE, address=address + full,
                                   words=(int.from_bytes(tail, "little"),), byte_enable=(1 << len(tail)) - 1)
                    acknowledged += len(tail)
                if verify:
                    if bulk:
                        observed = client.bulk_read(address, full) if full else b""
                        if full < len(chunk):
                            tail_word = client.command(wire.CMD_PEEK, address=address+full, count=1)[0]
                            observed += tail_word.to_bytes(4, "little")[:len(chunk)-full]
                    else:
                        words = client.command(wire.CMD_PEEK, address=address, count=(len(chunk) + 3) // 4)
                        observed = b"".join(w.to_bytes(4, "little") for w in words)[:len(chunk)]
                    if observed != chunk:
                        raise HostError(f"readback mismatch at 0x{address:08x}")
                if acknowledged >= next_report or acknowledged == total:
                    elapsed = max(time.monotonic() - started, 1e-9)
                    rate = acknowledged / elapsed
                    report(f"acknowledged {acknowledged}/{total} bytes; {rate:.0f} B/s; ETA {(total - acknowledged) / rate:.1f}s")
                    next_report = acknowledged + 65536
        client.command(wire.CMD_RESET_PC, address=manifest.entry)
        if resume:
            client.command(wire.CMD_RESUME)
    except Exception as error:
        client.failed = True
        raise HostError(f"load stopped after {acknowledged}/{total} acknowledged bytes: {error}; no further commands sent") from error


class StreamError(HostError):
    """A streaming load could not complete; earlier frames may be committed."""


# Statuses a resend of the same idempotent frame can cure.
STREAM_RETRYABLE = {wire.ST_CHECKSUM, wire.ST_MEMORY, wire.ST_TIMEOUT, wire.ST_GRANT, wire.ST_BUSY}


def stream_chunks(manifest):
    """Split every segment into aligned frames of at most 1024 bytes."""
    return [(segment.address + offset, segment.data[offset:offset + wire.STREAM_MAX_PAYLOAD])
            for segment in manifest.segments
            for offset in range(0, len(segment.data), wire.STREAM_MAX_PAYLOAD)]


class StreamSession:
    """Windowed streaming load over one owned transport.

    Frames carry absolute addresses, so a resend is idempotent. Replies come in
    frame order: a reply for a later frame proves every earlier unanswered
    frame was lost, and those are resent. A frame without a reply within the
    window timeout is resent. Only link-level failures are retried, each frame
    a bounded number of times; a protocol rejection stops the load.
    """

    def __init__(self, transport, baud, *, window=8, retries=8, latency=0.05,
                 clock=time.monotonic, sleep=time.sleep, deadline=None):
        if type(baud) is not int or not 9600 <= baud <= 12_000_000:
            raise ValueError("baud must be an integer in 9600..12000000")
        if type(window) is not int or not 1 <= window <= 64:
            raise ValueError("window must be 1..64 frames")
        self.transport, self.baud, self.window, self.retries = transport, baud, window, retries
        self.clock, self.sleep, self.deadline = clock, sleep, deadline
        frame_seconds = (wire.STREAM_MAX_PAYLOAD + wire.STREAM_OVERHEAD_BYTES) * 10 / baud
        self.frame_timeout = latency + max(0.2, 3 * window * frame_seconds)
        self.sequence = 0
        self.buffer = bytearray()
        self.retransmissions = 0

    def _next_sequence(self):
        self.sequence = (self.sequence + 1) & 0xFFFF
        return self.sequence

    def _check_deadline(self):
        if self.deadline is not None and self.clock() >= self.deadline:
            raise StreamError("whole-operation budget exhausted during streaming load")

    def _send(self, frame):
        if self.transport.write(frame) != len(frame):
            raise StreamError("short stream write")

    def _poll(self):
        """Return every complete CRC-valid reply now available (resynchronizing)."""
        part = self.transport.read(4096)
        if part:
            self.buffer.extend(part)
        replies = []
        while True:
            start = self.buffer.find(wire.STREAM_REPLY_SOF)
            if start < 0:
                self.buffer.clear()
                return replies
            del self.buffer[:start]
            if len(self.buffer) < wire.STREAM_REPLY_BYTES:
                return replies
            try:
                replies.append(wire.decode_stream_reply(bytes(self.buffer[:wire.STREAM_REPLY_BYTES])))
                del self.buffer[:wire.STREAM_REPLY_BYTES]
            except ValueError:
                del self.buffer[:1]

    def _exchange(self, frame_for, kind, timeout, attempts=1):
        """Stop-and-wait for one control frame, ignoring stale replies."""
        for _ in range(attempts):
            sequence = self._next_sequence()
            self._send(frame_for(sequence))
            limit = self.clock() + timeout
            while self.clock() < limit:
                self._check_deadline()
                for reply in self._poll():
                    if reply.sequence == sequence and reply.kind == kind:
                        if reply.status:
                            raise StreamError(f"stream frame 0x{kind:02x} status 0x{reply.status:02x}")
                        return reply.value
                self.sleep(0.0005)
        raise StreamError(f"no reply to stream frame 0x{kind:02x}")

    def hello(self):
        value = self._exchange(lambda seq: wire.stream_frame(wire.STREAM_HELLO, seq),
                               wire.STREAM_HELLO, self.frame_timeout, attempts=3)
        if value != wire.STREAM_IDENTITY:
            raise StreamError("device is not the expected N2S1 stream endpoint")

    def write(self, chunks, report=lambda message: None):
        queue = deque(range(len(chunks)))
        attempts = [0] * len(chunks)
        inflight = OrderedDict()
        done = 0
        total = sum(len(data) for _, data in chunks)
        next_report = 1 << 20
        while queue or inflight:
            self._check_deadline()
            while queue and len(inflight) < self.window:
                index = queue.popleft()
                attempts[index] += 1
                if attempts[index] > self.retries:
                    raise StreamError(f"frame 0x{chunks[index][0]:08x} failed {self.retries} times")
                if attempts[index] > 1:
                    self.retransmissions += 1
                sequence = self._next_sequence()
                self._send(wire.stream_write_frame(sequence, *chunks[index]))
                inflight[sequence] = (index, self.clock())
            progressed = False
            for reply in self._poll():
                if reply.kind != wire.STREAM_WRITE:
                    raise StreamError(f"unexpected stream reply kind 0x{reply.kind:02x}")
                if reply.sequence not in inflight:
                    continue  # stale reply for a frame already resent
                lost = []
                for sequence in list(inflight):
                    if sequence == reply.sequence:
                        break
                    lost.append(inflight.pop(sequence)[0])
                queue.extendleft(reversed(lost))
                index, _ = inflight.pop(reply.sequence)
                progressed = True
                if reply.status == wire.ST_OK:
                    done += len(chunks[index][1])
                elif reply.status in STREAM_RETRYABLE:
                    queue.appendleft(index)
                else:
                    raise StreamError(f"frame 0x{chunks[index][0]:08x} rejected with status 0x{reply.status:02x}")
            if inflight and self.clock() - next(iter(inflight.values()))[1] > self.frame_timeout:
                queue.extendleft(reversed([index for index, _ in inflight.values()]))
                inflight.clear()
                progressed = True
            if done >= next_report or (done == total and progressed and not queue and not inflight):
                report(f"streamed {done}/{total} bytes; {self.retransmissions} retransmissions")
                next_report = done + (1 << 20)
            if not progressed:
                self.sleep(0.0005)
        return done

    def digest(self, address, length, seed):
        # Generous device-scan allowance: 100 system clocks per word at 25 MHz.
        timeout = self.frame_timeout + 2 + (length / 4) * 100 / 25e6
        return self._exchange(lambda seq: wire.stream_digest_frame(seq, address, length, seed),
                              wire.STREAM_DIGEST, timeout)

    def end(self):
        return self._exchange(lambda seq: wire.stream_frame(wire.STREAM_END, seq),
                              wire.STREAM_END, self.frame_timeout)


def stream_load(client, manifest, *, baud, resume=False, report=print, window=8):
    """Halt, stream every segment, verify the chained stored-SDRAM digest, set PC."""
    client.command(wire.CMD_HALT)
    session = StreamSession(client.transport, baud, window=window, clock=client.clock,
                            sleep=client.sleep, deadline=client.operation_deadline)
    try:
        session.hello()
        chunks = stream_chunks(manifest)
        session.write(chunks, report)
        seed = 0xFFFFFFFF
        for segment in manifest.segments:
            value = session.digest(segment.address, len(segment.data), seed)
            if value != wire.stream_digest(segment.data, seed):
                raise StreamError(f"stored digest mismatch in segment {segment.name}")
            seed = value
        committed = session.end()
        if committed < len(chunks):
            raise StreamError("device committed fewer frames than the image contains")
    except Exception as error:
        client.failed = True
        raise StreamError(f"streaming load stopped: {error}; endpoint remains halted") from error
    report(f"whole-image CRC32 0x{seed ^ 0xFFFFFFFF:08x} verified in stored SDRAM; "
           f"{session.retransmissions} retransmissions")
    client.command(wire.CMD_RESET_PC, address=manifest.entry)
    if resume:
        client.command(wire.CMD_RESUME)


def output_file(name, root=ROOT):
    relative = Path(name)
    if (relative.is_absolute() or "\\" in str(name) or ":" in str(name) or
            ".." in relative.parts or len(relative.parts) < 2 or relative.parts[0] != "workdir"):
        raise ValueError("output must be a fresh file below relative workdir/")
    current = root.resolve()
    for index, part in enumerate(relative.parts):
        current /= part
        if current.is_symlink():
            raise ValueError("output symlinks are forbidden")
        if current.exists() and (index == len(relative.parts)-1 or not current.is_dir()):
            raise ValueError("output exists or ancestor is not a directory")
    return current


def dump(client, address, length):
    """Dump bytes under maintained Halt; not a coherent-frame capture protocol."""
    client.capabilities()
    state = client.command(wire.CMD_PING)
    if state[1] != 1:
        raise HostError("dump requires an already maintained Halt; no implicit Halt/Resume")
    data = bytearray()
    for offset in range(0, length, wire.BULK_LIMIT):
        data.extend(client.bulk_read(address+offset, min(wire.BULK_LIMIT, length-offset)))
    return bytes(data)


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--port", help="explicit OS port; operator must own the identified N2D1 bench")
    result.add_argument("--timeout", type=float, default=1.0, help="whole transaction seconds (0.01..60)")
    result.add_argument("--budget", type=float, default=1800.0,
                        help="finite whole-operation seconds, reserving each complete transaction")
    add_baud_option(result)
    result.add_argument("--dry-run", "--plan", action="store_true", help="validate and plan without serial import/open")
    commands = result.add_subparsers(dest="command", required=True)
    number = lambda value: int(value, 0)
    for name in ("ping", "halt", "resume", "caps"):
        commands.add_parser(name)
    reset = commands.add_parser("reset-pc")
    reset.add_argument("address", type=number)
    peek = commands.add_parser("peek")
    peek.add_argument("address", type=number)
    peek.add_argument("count", type=number)
    poke = commands.add_parser("poke")
    poke.add_argument("address", type=number)
    poke.add_argument("words", nargs="+", type=number)
    poke.add_argument("--byte-enable", type=number, default=15)
    event = commands.add_parser("input")
    event.add_argument("event", type=number, help="Set-2 {extended, break, scan_code}, 0..1023")
    loader = commands.add_parser("load")
    loader.add_argument("manifest", type=Path)
    loader.add_argument("--verify", action="store_true", help="read back each <=32-byte chunk")
    loader.add_argument("--resume", action="store_true", help="resume only after the complete load/reset-PC succeeds")
    loader.add_argument("--bulk", action="store_true", help="CRC32 blocks, requires --verify and capabilities")
    loader.add_argument("--stream", action="store_true",
                        help="windowed streaming session with stored-SDRAM digest verification")
    reader = commands.add_parser("dump")
    reader.add_argument("address", type=number)
    reader.add_argument("length", type=number)
    reader.add_argument("--output", required=True)
    reader.add_argument("--bulk", action="store_true", required=True)
    return result


def plan(args):
    if not math.isfinite(args.timeout) or not 0.01 <= args.timeout <= 60:
        raise ValueError("timeout must be finite and in 0.01..60 seconds")
    if not math.isfinite(args.budget) or not args.timeout <= args.budget <= 86400:
        raise ValueError("budget must be finite, at least one timeout, and at most 86400 seconds")
    if type(args.baud) is not int or not 9600 <= args.baud <= 12_000_000:
        raise ValueError("baud must be an integer in 9600..12000000")
    if args.port is not None and not args.port.strip():
        raise ValueError("port must not be empty")
    if not args.dry_run and not args.port:
        raise ValueError("live commands require --port and exclusive ownership of the identified N2D1 bench")
    manifest = None
    kwargs = {}
    command = None if args.command == "dump" else getattr(wire, "CMD_" + args.command.upper().replace("-", "_"))
    if args.command in ("peek", "poke"):
        count = args.count if args.command == "peek" else len(args.words)
        memory_range(args.address, count)
        kwargs = dict(address=args.address, count=count)
        if args.command == "poke":
            for word in args.words:
                integer(word, "word")
            integer(args.byte_enable, "byte enable", 1, 15)
            kwargs.update(words=tuple(args.words), byte_enable=args.byte_enable)
    elif args.command == "reset-pc":
        integer(args.address, "reset-PC", 0, CONFIG["memory_windows"][0][1] - 4)
        if args.address % 4:
            raise ValueError("reset-PC must be aligned")
        kwargs = dict(address=args.address)
    elif args.command == "input":
        integer(args.event, "Set-2 event", 0, 1023)
        kwargs = dict(address=wire.INPUT_ADDRESS, words=(args.event,), byte_enable=15)
    elif args.command == "load":
        if args.bulk and not args.verify:
            raise ValueError("bulk load requires --verify")
        if args.stream and (args.bulk or args.verify):
            raise ValueError("stream load verifies by stored digest; do not combine with --bulk/--verify")
        manifest = validate_manifest(args.manifest)
    elif args.command == "dump":
        integer(args.address, "dump address")
        integer(args.length, "dump length", 4, 0x4000000)
        if args.address % 4 or args.length % 4 or not any(
                start <= args.address and args.address+args.length <= end
                for start, end in ((0, 0x4000000), (0x10000000, 0x10010000))):
            raise ValueError("dump must be aligned and fit one bulk-read window")
        output_file(args.output)
    return command, kwargs, manifest


def _close(handle):
    """Close the owned transport once; False only when close itself failed."""
    if not handle:
        return True
    try:
        handle[0].close()
    except OSError as error:
        print(f"transport close failed: {error}", file=sys.stderr)
        return False
    return True


def _live(args, opener, handle, command, kwargs, manifest):
    """Run one live invocation; `handle` receives the opened transport."""
    try:
        connection = opener(args.port, args.timeout, args.baud)
        handle.append(connection)
        client = Client(connection, args.timeout)
        client.operation_deadline = client.clock() + args.budget
        identity = client.command(wire.CMD_PING)
        if manifest and args.stream:
            stream_load(client, manifest, baud=args.baud, resume=args.resume)
        elif manifest:
            load(client, manifest, verify=args.verify, resume=args.resume, bulk=args.bulk)
        elif args.command == "dump":
            data = dump(client, args.address, args.length)
            target = output_file(args.output)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as stream:
                stream.write(data)
            print(f"complete dump: bytes={len(data)} sha256={hashlib.sha256(data).hexdigest()}; CPU remains halted")
        elif args.command == "caps":
            print("OK capabilities", client.capabilities())
        else:
            words = identity if command == wire.CMD_PING else client.command(command, **kwargs)
            print("OK" + "".join(f" 0x{word:08x}" for word in words))
        return 0
    except (HostError, OSError, ValueError) as error:
        print(f"transport/device error: {error}; inspect endpoint state before another invocation", file=sys.stderr)
        return 3


def main(argv=None, *, opener=open_serial):
    args = parser().parse_args(argv)
    try:
        command, kwargs, manifest = plan(args)
    except (ValueError, OSError) as error:
        print(f"validation error: {error}", file=sys.stderr)
        return 2
    stream = args.command == "load" and args.stream
    policy = "idempotent resend of failed stream frames" if stream else "no retries"
    print(f"N2D1 {args.command}: {args.baud} baud 8-N-1; deadline={args.timeout}s; {policy}")
    if manifest and stream:
        size = sum(len(s.data) for s in manifest.segments)
        frames = len(stream_chunks(manifest))
        print(f"validated {len(manifest.segments)} segments, {size} bytes; {frames} stream frames of at most {wire.STREAM_MAX_PAYLOAD} bytes")
        print(f"stream wire time {wire.stream_wire_seconds(size, args.baud, frames=frames):.1f}s at {args.baud} baud "
              f"({(size + frames * wire.STREAM_OVERHEAD_BYTES) * 10} bits, no per-frame turnaround); "
              f"plus {len(manifest.segments)} stored-SDRAM digest scans of {(size + 3) // 4} words; resume={args.resume}")
    elif manifest:
        size = sum(len(s.data) for s in manifest.segments)
        block = wire.BULK_LIMIT if args.bulk else 32
        packets = sum((len(s.data)//4*4 + block-1)//block + bool(len(s.data)%4)
                      for s in manifest.segments)
        print(f"validated {len(manifest.segments)} segments, {size} bytes; at least {packets} write acknowledgements")
        print(f"wire-only lower bound {(size + packets * 18) * 10 / args.baud:.1f}s; USB/device latency adds one round trip per packet; verify={args.verify}; resume={args.resume}")
    elif args.command == "dump":
        print(f"validated {args.length} dump bytes in {(args.length+4095)//4096} bulk reads; no frame coherence claim")
    else:
        print(f"request sequence 0: {wire.request(command, 0, **kwargs).hex()}")
    if args.dry_run:
        return 0
    # The owned handle is closed exactly once after the invocation, whatever
    # its outcome. A close failure exits 3 and takes precedence over the result.
    handle = []
    try:
        status = _live(args, opener, handle, command, kwargs, manifest)
    except BaseException:
        if not _close(handle):
            return 3
        raise
    return status if _close(handle) else 3


if __name__ == "__main__":
    raise SystemExit(main())
