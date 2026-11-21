"""Wire codec for the bounded nand2doom UART debug protocol.

Spec: wiki/src/nand2doom/scripts/host/spec.md#uart-host.
"""

from __future__ import annotations

from dataclasses import dataclass
import zlib


REQUEST_SOF = 0xA5
RESPONSE_SOF = 0x5A
VERSION = 1
MAX_WORDS = 8

CMD_PING = 0x01
CMD_HALT = 0x02
CMD_RESUME = 0x03
CMD_RESET_PC = 0x04
CMD_CAPS = 0x05
CMD_BULK_WRITE = 0x31
CMD_BULK_READ = 0x32
BULK_LIMIT = 4096
BULK_IDENTITY = 0x3242324E
CMD_PEEK = 0x10
CMD_POKE = 0x11
CMD_LOAD = 0x12
CMD_INPUT = 0x20
INPUT_ADDRESS = 0x2000200C

ST_OK = 0x00
ST_VERSION = 0x01
ST_COMMAND = 0x02
ST_CHECKSUM = 0x03
ST_ALIGNMENT = 0x04
ST_RANGE = 0x05
ST_LENGTH = 0x06
ST_NOT_HALTED = 0x07
ST_TIMEOUT = 0x08
ST_MEMORY = 0x09
ST_FRAMING = 0x0A
ST_GRANT = 0x0B
ST_BUSY = 0x0C


def crc8(data: bytes) -> int:
    """Return CRC-8/ATM (poly 0x07, init 0, no reflection/xorout)."""

    value = 0
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = ((value << 1) ^ 0x07) & 0xFF if value & 0x80 else (value << 1) & 0xFF
    return value


def request(
    command: int,
    sequence: int,
    *,
    address: int = 0,
    words: tuple[int, ...] = (),
    count: int | None = None,
    byte_enable: int = 0,
    version: int = VERSION,
) -> bytes:
    """Encode one request; poke/load/input word payload is little-endian."""

    if count is None:
        count = len(words)
    if not all(0 <= value <= 0xFFFFFFFF for value in words):
        raise ValueError("words must contain unsigned 32-bit values")
    body = bytearray((version, command & 0xFF, sequence & 0xFF))
    body.extend((address & 0xFFFFFFFF).to_bytes(4, "little"))
    body.extend((count & 0xFF, byte_enable & 0x0F))
    for word in words:
        body.extend(word.to_bytes(4, "little"))
    return bytes((REQUEST_SOF,)) + bytes(body) + bytes((crc8(body),))


def input_request(sequence: int, event: int) -> bytes:
    """Encode one Set-2 event injection for the unified input FIFO."""

    if not 0 <= event < (1 << 10):
        raise ValueError("input event must fit {extended, break, scan_code}")
    return request(
        CMD_INPUT,
        sequence,
        address=INPUT_ADDRESS,
        words=(event,),
        byte_enable=0xF,
    )


@dataclass(frozen=True)
class Response:
    command: int
    sequence: int
    status: int
    words: tuple[int, ...]


def response_length_from_header(header: bytes) -> int:
    """Return full response length after the six-byte header is available."""

    if len(header) != 6 or header[0] != RESPONSE_SOF:
        raise ValueError("invalid response header")
    return 7 + (header[5] * 4)


def decode_response(packet: bytes) -> Response:
    if len(packet) < 7 or packet[0] != RESPONSE_SOF:
        raise ValueError("invalid response framing")
    if packet[1] != VERSION:
        raise ValueError("unsupported response version")
    count = packet[5]
    if len(packet) != 7 + (count * 4):
        raise ValueError("response length mismatch")
    if crc8(packet[1:-1]) != packet[-1]:
        raise ValueError("response checksum mismatch")
    payload = packet[6:-1]
    words = tuple(
        int.from_bytes(payload[offset : offset + 4], "little")
        for offset in range(0, len(payload), 4)
    )
    return Response(packet[2], packet[3], packet[4], words)


def bulk_range(address: int, length: int, *, write: bool) -> None:
    if type(address) is not int or type(length) is not int:
        raise ValueError("bulk address and length must be integers")
    if not 4 <= length <= BULK_LIMIT or length % 4 or address % 4:
        raise ValueError("bulk requires aligned 4..4096-byte length/address")
    windows = [(0, 0x04000000)]
    if not write:
        windows.append((0x10000000, 0x10010000))
    if not any(start <= address and address + length <= end for start, end in windows):
        raise ValueError("bulk request must fit one supported memory window")


def bulk_request(command: int, sequence: int, address: int, length: int,
                 data: bytes = b"") -> bytes:
    if command not in (CMD_BULK_WRITE, CMD_BULK_READ):
        raise ValueError("unknown bulk command")
    bulk_range(address, length, write=command == CMD_BULK_WRITE)
    if type(sequence) is not int or not 0 <= sequence <= 255:
        raise ValueError("sequence must be a byte")
    if type(data) is not bytes or len(data) != (length if command == CMD_BULK_WRITE else 0):
        raise ValueError("bulk payload length mismatch")
    body = bytes((2, command, sequence)) + address.to_bytes(4, "little")
    body += length.to_bytes(2, "little") + data
    return b"\xa6" + body + zlib.crc32(body).to_bytes(4, "little")


@dataclass(frozen=True)
class BulkResponse:
    command: int
    sequence: int
    status: int
    address: int
    completed: int
    data: bytes


def decode_bulk_response(packet: bytes) -> BulkResponse:
    if len(packet) < 17 or packet[:2] != b"\x6a\x02":
        raise ValueError("invalid bulk response framing/version")
    length = int.from_bytes(packet[9:11], "little")
    if length > BULK_LIMIT or length % 4 or len(packet) != 17 + length:
        raise ValueError("bulk response length mismatch")
    if zlib.crc32(packet[1:-4]) != int.from_bytes(packet[-4:], "little"):
        raise ValueError("bulk response CRC32 mismatch")
    return BulkResponse(packet[2], packet[3], packet[4],
                        int.from_bytes(packet[5:9], "little"),
                        int.from_bytes(packet[11:13], "little"), packet[13:-4])


# Ref: wiki/src/nand2doom/rtl/io/mas.md "Streaming load session".
STREAM_SOF = 0xA7
STREAM_REPLY_SOF = 0x7A
STREAM_HELLO = 0x48
STREAM_WRITE = 0x57
STREAM_DIGEST = 0x56
STREAM_END = 0x45
STREAM_IDENTITY = 0x3153324E
STREAM_MAX_PAYLOAD = 1024
STREAM_SLOTS = 2
STREAM_HEADER_BYTES = 13
STREAM_OVERHEAD_BYTES = STREAM_HEADER_BYTES + 4
STREAM_REPLY_BYTES = 10
SDRAM_BYTES = 0x04000000


def stream_frame(kind: int, sequence: int, *, address: int = 0, length: int = 0,
                 payload: bytes = b"") -> bytes:
    """Encode one stream frame: SOF, CRC-8-protected header, payload, CRC-32."""

    if type(sequence) is not int or not 0 <= sequence <= 0xFFFF:
        raise ValueError("stream sequence must be a u16")
    if type(address) is not int or type(length) is not int:
        raise ValueError("stream address and length must be integers")
    if kind == STREAM_WRITE:
        if not 1 <= len(payload) <= STREAM_MAX_PAYLOAD or length != len(payload):
            raise ValueError("stream write carries 1..1024 payload bytes")
    elif kind == STREAM_DIGEST:
        if len(payload) != 4 or not 1 <= length <= SDRAM_BYTES:
            raise ValueError("stream digest carries a 4-byte seed and 1..64 MiB length")
    elif kind in (STREAM_HELLO, STREAM_END):
        if length or payload:
            raise ValueError("hello/end frames carry no length or payload")
    else:
        raise ValueError("unknown stream frame kind")
    if kind in (STREAM_WRITE, STREAM_DIGEST) and (
            address % 4 or address + length > SDRAM_BYTES or address < 0):
        raise ValueError("stream write/digest must be aligned and inside SDRAM")
    header = bytes((kind,)) + sequence.to_bytes(2, "little")
    header += (address & 0xFFFFFFFF).to_bytes(4, "little") + (length & 0xFFFFFFFF).to_bytes(4, "little")
    body = header + bytes((crc8(header),)) + payload
    return bytes((STREAM_SOF,)) + body + zlib.crc32(body).to_bytes(4, "little")


def stream_write_frame(sequence: int, address: int, data: bytes) -> bytes:
    return stream_frame(STREAM_WRITE, sequence, address=address, length=len(data), payload=data)


def stream_digest_frame(sequence: int, address: int, length: int, seed: int) -> bytes:
    return stream_frame(STREAM_DIGEST, sequence, address=address, length=length,
                        payload=(seed & 0xFFFFFFFF).to_bytes(4, "little"))


def stream_digest(data: bytes, seed: int = 0xFFFFFFFF) -> int:
    """Raw (non-inverted) reflected CRC-32 register after `data` from `seed`.

    Chaining segment digests with the previous raw value as the next seed
    yields ``~zlib.crc32(concatenated image)`` for the whole image.
    """

    return zlib.crc32(data, seed ^ 0xFFFFFFFF) ^ 0xFFFFFFFF


@dataclass(frozen=True)
class StreamReply:
    kind: int
    sequence: int
    status: int
    value: int


def decode_stream_reply(packet: bytes) -> StreamReply:
    if len(packet) != STREAM_REPLY_BYTES or packet[0] != STREAM_REPLY_SOF:
        raise ValueError("invalid stream reply framing")
    if crc8(packet[1:-1]) != packet[-1]:
        raise ValueError("stream reply checksum mismatch")
    if not packet[1] & 0x80:
        raise ValueError("stream reply lacks the response bit")
    return StreamReply(packet[1] & 0x7F, int.from_bytes(packet[2:4], "little"), packet[4],
                       int.from_bytes(packet[5:9], "little"))


def stream_wire_seconds(payload_bytes: int, baud: int, *, frames: int | None = None) -> float:
    """8-N-1 host-to-device wire time for streaming `payload_bytes`."""

    if frames is None:
        frames = -(-payload_bytes // STREAM_MAX_PAYLOAD)
    return (payload_bytes + frames * STREAM_OVERHEAD_BYTES) * 10 / baud
