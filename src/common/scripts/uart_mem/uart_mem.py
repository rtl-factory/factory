"""Host wire protocol of the common UART memory endpoint.

The framing, commands and statuses are owned by
the common UART memory block (its spec is not ported yet); the load transaction by
wiki/src/common/scripts/uart_mem/spec.md. This module has no serial
dependency: a transport is any object with ``write(data)`` and
``read(size, deadline) -> bytes`` (returning at most ``size`` bytes as soon
as any have arrived, or nothing at the monotonic deadline). Any failure poisons the client: no command is retried.

Project traffic sharing the UART may precede a response, 0x8a included;
the client separates it by the expected header and the response CRC.
"""
from __future__ import annotations

import time
import zlib
from dataclasses import dataclass, field
from typing import Callable, Protocol, Sequence

REQUEST_SOF = 0xA8
RESPONSE_SOF = 0x8A
VERSION = 0x01
MAGIC = 0x4D41324E
FIXED_IDENT_WORDS = 5

IDENTIFY, HOLD, RELEASE, ENTRY, WRITE, READ = 0x01, 0x02, 0x03, 0x04, 0x10, 0x11

STATUS_NAMES = {
    0x00: "ok", 0x01: "version", 0x02: "command", 0x03: "checksum",
    0x04: "alignment", 0x05: "range", 0x06: "length", 0x07: "not held",
    0x08: "timeout", 0x09: "memory", 0x0A: "overrun",
}
OK = 0x00

ATTR_WRITE, ATTR_READ_HELD, ATTR_READ_LIVE = 0x1, 0x2, 0x4


class EndpointError(RuntimeError):
    """Transport, framing or device failure; the client is poisoned."""


class StatusError(EndpointError):
    def __init__(self, command: int, status: int):
        self.command, self.status = command, status
        super().__init__(f"command 0x{command:02x} failed: "
                         f"{STATUS_NAMES.get(status, f'status 0x{status:02x}')}")


class LoadError(EndpointError):
    """A load stopped; the CPU is left held."""
    def __init__(self, segment: int | None, message: str):
        self.segment = segment
        super().__init__(message if segment is None else f"segment {segment}: {message}")


class Transport(Protocol):
    def write(self, data: bytes) -> None: ...
    def read(self, size: int, deadline: float) -> bytes: ...


def crc8(data: bytes) -> int:
    """CRC-8/ATM: polynomial 0x07, init 0, no reflection, no final XOR."""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def encode_request(command: int, seq: int, address: int = 0, length: int = 0,
                   payload: bytes = b"") -> bytes:
    if not 0 <= address <= 0xFFFF_FFFF or not 0 <= length <= 0xFFFF or not 0 <= seq <= 0xFF:
        raise ValueError("request field out of range")
    if payload and (command != WRITE or len(payload) != length):
        raise ValueError("only a write carries a payload of its declared length")
    header = bytes([VERSION, command, seq]) + address.to_bytes(4, "little") + \
        length.to_bytes(2, "little")
    frame = bytes([REQUEST_SOF]) + header + bytes([crc8(header)]) + payload
    if payload:
        frame += zlib.crc32(payload).to_bytes(4, "little")
    return frame


@dataclass(frozen=True)
class Response:
    command: int
    seq: int
    payload: bytes
    status: int


def encode_response(command: int, seq: int, payload: bytes, status: int) -> bytes:
    """Device-side encoding (fake endpoints and recorded fixtures)."""
    body = bytes([VERSION, command | 0x80, seq]) + len(payload).to_bytes(2, "little") + \
        payload + bytes([status])
    return bytes([RESPONSE_SOF]) + body + zlib.crc32(body).to_bytes(4, "little")


def decode_response(frame: bytes) -> Response:
    """Decode exactly one complete response frame."""
    if len(frame) < 11 or frame[0] != RESPONSE_SOF or frame[1] != VERSION or \
            not frame[2] & 0x80:
        raise EndpointError(f"malformed response {frame[:8].hex()}")
    length = int.from_bytes(frame[4:6], "little")
    if len(frame) != 6 + length + 5 or \
            zlib.crc32(frame[1:-4]) != int.from_bytes(frame[-4:], "little"):
        raise EndpointError(f"malformed response {frame[:8].hex()}")
    return Response(frame[2] & 0x7F, frame[3], frame[6:6 + length], frame[-5])


@dataclass(frozen=True)
class Window:
    base: int
    size: int
    attributes: int

    def contains(self, address: int, length: int) -> bool:
        return self.base <= address and address + length <= self.base + self.size


@dataclass(frozen=True)
class Identity:
    project_id: int
    max_length: int
    held: bool
    hold_requested: bool
    entry: int
    entry_any: bool
    live_gap_cycles: int
    windows: tuple[Window, ...]

    @property
    def project(self) -> str:
        return self.project_id.to_bytes(4, "little").decode("ascii", "replace")


def parse_identity(payload: bytes) -> Identity:
    if len(payload) % 4 or len(payload) < 4 * FIXED_IDENT_WORDS:
        raise EndpointError("identify payload has the wrong size")
    words = [int.from_bytes(payload[i:i + 4], "little") for i in range(0, len(payload), 4)]
    if words[0] != MAGIC:
        raise EndpointError(f"identify magic 0x{words[0]:08x} is not N2AM")
    count = (words[2] >> 8) & 0xFF
    if len(words) != FIXED_IDENT_WORDS + 3 * count:
        raise EndpointError("identify window count does not match its payload")
    windows = tuple(Window(words[5 + 3 * i], words[6 + 3 * i], words[7 + 3 * i])
                    for i in range(count))
    return Identity(project_id=words[1], max_length=words[2] >> 16, held=bool(words[2] & 1),
                    hold_requested=bool(words[2] & 2), entry=words[3],
                    entry_any=bool(words[4] & 0x10000), live_gap_cycles=words[4] & 0xFFFF,
                    windows=windows)


@dataclass
class Segment:
    address: int
    data: bytes


@dataclass
class Client:
    """One endpoint session. ``on_project_bytes`` receives bytes of the
    project's own traffic (for example program prints) that arrive before a
    response; they never interleave with a response."""
    transport: Transport
    timeout: float = 1.0
    clock: Callable[[], float] = time.monotonic
    on_project_bytes: Callable[[bytes], None] | None = None
    seq: int = 0
    poisoned: bool = False
    identity: Identity | None = field(default=None, repr=False)
    _buffer: bytearray = field(default_factory=bytearray, repr=False)

    def _fail(self, error: EndpointError) -> EndpointError:
        self.poisoned = True
        return error

    def _receive(self, deadline: float, command: int, seq: int,
                 max_length: int) -> Response:
        """Find the response to (command, seq). Project bytes may precede it
        and may contain 0x8a. A candidate is the SOF, version, the matching
        command and sequence, and a length no larger than the request allows.
        Anything else, or a candidate whose CRC fails, is one project byte.
        The leading candidate keeps precedence while it is incomplete, so a
        response-shaped run inside a real payload is never taken early. Only
        when the deadline passes with the leading candidate still incomplete
        is the earliest later complete, CRC-valid candidate accepted (its
        leading bytes were project data)."""
        crc_failures = 0

        def candidate(offset: int):
            """None: not a candidate. False: incomplete. Response: valid.
            "bad": complete with a failing CRC."""
            header = self._buffer[offset:offset + 6]
            if not all((
                    len(header) < 2 or header[1] == VERSION,
                    len(header) < 3 or header[2] == (command | 0x80),
                    len(header) < 4 or header[3] == seq,
                    len(header) < 6 or int.from_bytes(header[4:6], "little") <= max_length)):
                return None
            if len(header) < 6:
                return False
            total = 6 + int.from_bytes(header[4:6], "little") + 5
            if len(self._buffer) - offset < total:
                return False
            try:
                return decode_response(bytes(self._buffer[offset:offset + total]))
            except EndpointError:
                return "bad"

        def accept(offset: int, response: Response) -> Response:
            self._project(bytes(self._buffer[:offset]))
            del self._buffer[:offset + 6 + len(response.payload) + 5]
            return response

        while True:
            start = self._buffer.find(bytes([RESPONSE_SOF]))
            if start < 0:
                self._project(bytes(self._buffer))
                self._buffer.clear()
            elif start > 0:
                self._project(bytes(self._buffer[:start]))
                del self._buffer[:start]
            if self._buffer:
                found = candidate(0)
                if isinstance(found, Response):
                    return accept(0, found)
                if found is None or found == "bad":
                    crc_failures += found == "bad"
                    self._project(bytes(self._buffer[:1]))
                    del self._buffer[:1]
                    continue
            if self.clock() >= deadline:
                # Why: the leading candidate never completed: it was project data
                # if a later candidate is a complete valid response.
                later = self._buffer.find(bytes([RESPONSE_SOF]), 1)
                while later > 0:
                    found = candidate(later)
                    if isinstance(found, Response):
                        return accept(later, found)
                    later = self._buffer.find(bytes([RESPONSE_SOF]), later + 1)
                reason = (f"response timeout after {crc_failures} candidate(s) with a bad CRC"
                          if crc_failures else "response timeout")
                raise self._fail(EndpointError(reason))
            self._buffer += self.transport.read(4096, deadline)

    def _project(self, data: bytes) -> None:
        if data and self.on_project_bytes is not None:
            self.on_project_bytes(data)

    def command(self, command: int, address: int = 0, length: int = 0,
                payload: bytes = b"") -> bytes:
        if self.poisoned:
            raise EndpointError("client is poisoned by an earlier failure")
        self.seq = (self.seq + 1) & 0xFF
        frame = encode_request(command, self.seq, address, length, payload)
        try:
            self.transport.write(frame)
        except OSError as error:
            raise self._fail(EndpointError(f"write failed: {error}")) from error
        if command == READ:
            max_length = length
        elif command == IDENTIFY:
            max_length = 4 * (FIXED_IDENT_WORDS + 3 * 8)
        else:
            max_length = 0
        response = self._receive(self.clock() + self.timeout, command, self.seq, max_length)
        if (response.command, response.seq) != (command, self.seq):
            raise self._fail(EndpointError(
                f"response 0x{response.command:02x}/{response.seq} does not match "
                f"0x{command:02x}/{self.seq}"))
        if response.status != OK:
            raise self._fail(StatusError(command, response.status))
        return response.payload

    def identify(self) -> Identity:
        self.identity = parse_identity(self.command(IDENTIFY))
        return self.identity

    def hold(self) -> None:
        self.command(HOLD)

    def release(self) -> None:
        self.command(RELEASE)

    def entry(self, address: int) -> None:
        self.command(ENTRY, address)

    def _chunks(self, address: int, length: int):
        if address % 4 or length % 4 or length <= 0:
            raise ValueError("endpoint transfers are whole aligned words")
        limit = (self.identity or self.identify()).max_length
        for offset in range(0, length, limit):
            yield address + offset, offset, min(limit, length - offset)

    def write(self, address: int, data: bytes) -> None:
        for chunk_address, offset, size in self._chunks(address, len(data)):
            self.command(WRITE, chunk_address, size, data[offset:offset + size])

    def read(self, address: int, length: int) -> bytes:
        out = bytearray()
        for chunk_address, _, size in self._chunks(address, length):
            data = self.command(READ, chunk_address, size)
            if len(data) != size:
                raise self._fail(EndpointError("short read payload"))
            out += data
        return bytes(out)


def check_identity(identity: Identity, project_id: int | None) -> None:
    if project_id is not None and identity.project_id != project_id:
        raise EndpointError(f"endpoint project 0x{identity.project_id:08x} is not "
                            f"the expected 0x{project_id:08x}")


def load(client: Client, segments: Sequence[Segment], entry: int,
         project_id: int | None = None,
         progress: Callable[[int, int], None] | None = None) -> None:
    """The bench load transaction: hold once, write and read back every
    segment, set the entry state only after all verified, release once.

    A segment or identity that fails preflight raises before any hold. A
    failure from the hold through the last verify raises LoadError and sends
    no entry or release, so the CPU stays held. If the entry or release reply
    itself is lost, the CPU state is unknown and must be inspected."""
    identity = client.identify()
    check_identity(identity, project_id)
    for index, segment in enumerate(segments):
        if segment.address % 4 or not segment.data:
            raise LoadError(index, "segment must be nonempty and word aligned")
        writable = [w for w in identity.windows if w.attributes & ATTR_WRITE]
        if not any(w.contains(segment.address, len(segment.data)) for w in writable):
            raise LoadError(index, f"0x{segment.address:x}+{len(segment.data)} is outside "
                                   "every writable window")
    client.hold()
    for index, segment in enumerate(segments):
        data = segment.data
        try:
            tail = len(data) % 4
            if tail:
                # Why: writes are whole words, so the bytes past a partial final
                # word are read first and written back unchanged.
                last = segment.address + len(data) - tail
                data = data + client.read(last, 4)[tail:]
            client.write(segment.address, data)
            if client.read(segment.address, len(data)) != data:
                raise LoadError(index, "readback differs from the image")
        except LoadError:
            client.poisoned = True
            raise
        except EndpointError as error:
            raise LoadError(index, str(error)) from error
        if progress is not None:
            progress(index, len(segments))
    try:
        client.entry(entry)
        client.release()
    except EndpointError as error:
        raise LoadError(None, str(error)) from error


def read_window(client: Client, address: int, length: int) -> bytes:
    """One screen readback; the CPU keeps running."""
    return client.read(address, length)
