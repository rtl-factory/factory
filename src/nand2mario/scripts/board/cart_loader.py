#!/usr/bin/env python3
"""Host side of the nand2mario UART cartridge loader protocol.

Frames the loader protocol (wiki/src/nand2mario/scripts/board/spec.md#loader-protocol): SYNC, command,
24-bit address, length-1, payload (WRITE), CRC-16/CCITT-FALSE; and the
response SYNC, status, command, address, length-1, payload (READ), CRC. The
pure functions here are shared by the simulation benches; `Session` drives
any byte transport (a pyserial port on the board, a fake in tests) with
stop-and-wait retries, and the command line loads an image file into the
cartridge backing while the console is held in reset, optionally reading it
back for verification, then releases the console. The session also runs the
frame-snapshot sequence (SNAP_RELEASE, SNAP_PIN, the record in SNAP_READ
chunks, the header again, SNAP_RELEASE); frame_snapshot.py is its command.

Spec: wiki/src/nand2mario/scripts/board/spec.md.
"""
import argparse
from dataclasses import dataclass
from pathlib import Path
import sys
import time

SYNC = 0xA5
CRC_INIT, CRC_POLY = 0xFFFF, 0x1021
CMD_PING, CMD_HOLD, CMD_RELEASE, CMD_WRITE, CMD_READ = 0x01, 0x02, 0x03, 0x04, 0x05
CMD_SNAP_PIN, CMD_SNAP_RELEASE, CMD_SNAP_READ = 0x06, 0x07, 0x08
COMMANDS = {'ping': CMD_PING, 'hold': CMD_HOLD, 'release': CMD_RELEASE, 'write': CMD_WRITE, 'read': CMD_READ,
            'snap-pin': CMD_SNAP_PIN, 'snap-release': CMD_SNAP_RELEASE, 'snap-read': CMD_SNAP_READ}
(STATUS_OK, STATUS_BAD_CRC, STATUS_BAD_COMMAND, STATUS_BAD_ARGUMENT, STATUS_NOT_HELD, STATUS_BAD_ADDRESS,
 STATUS_NO_FRAME, STATUS_NOT_PINNED) = range(8)
STATUS_NAMES = {STATUS_OK: 'ok', STATUS_BAD_CRC: 'bad-crc', STATUS_BAD_COMMAND: 'bad-command',
                STATUS_BAD_ARGUMENT: 'bad-argument', STATUS_NOT_HELD: 'not-held', STATUS_BAD_ADDRESS: 'bad-address',
                STATUS_NO_FRAME: 'no-frame', STATUS_NOT_PINNED: 'not-pinned'}
# Why: retrying cannot change these answers, so the session reports them at once.
FINAL_CODES = (STATUS_BAD_COMMAND, STATUS_BAD_ARGUMENT, STATUS_NOT_HELD, STATUS_BAD_ADDRESS, STATUS_NO_FRAME,
               STATUS_NOT_PINNED)
PAYLOAD_COMMANDS = (CMD_READ, CMD_SNAP_READ)
# Ref: wiki/src/nand2mario/scripts/board/spec.md "Frame snapshot" (record layout).
SNAPSHOT_HEADER_BYTES = 8
SNAPSHOT_RECORD_BYTES = SNAPSHOT_HEADER_BYTES + 160 * 144 // 4
ADDRESS_RAM_BIT = 21
ROM_BYTES, RAM_BYTES = 1 << 21, 1 << 15
MAX_PAYLOAD = 256
HEADER_BYTES = 7                 # Ref: board spec "Loader protocol": SYNC, status, command, 24-bit address, length minus one.
CRC_BYTES = 2
DEFAULT_BAUD = 2_000_000   # Ref: wiki/src/nand2mario/rtl/has.md: the factory board rate, 12 1/2 clocks at 25 MHz
# Why: each recovery loop (the SYNC search, the stale-frame discard, the drain
# before a retry) is bounded, so a noisy or stuck link ends in an error, not a hang.
RESYNC_LIMIT = 2 * (HEADER_BYTES + MAX_PAYLOAD + CRC_BYTES)
STALE_FRAME_LIMIT = 4
DRAIN_LIMIT = 64 * 1024
# Why: every read and write of the port is bounded so a blocked write never hangs
# a session; during a snapshot both shrink to the time left so the deadline holds,
# and the closing release gets its own short bound past the deadline so the pin
# is still released.
IO_TIMEOUT = 1.0
MIN_IO_TIMEOUT = 0.05
RELEASE_IO_TIMEOUT = 0.5


def crc16(data, crc=CRC_INIT):
    """CRC-16/CCITT-FALSE over `data` continuing from `crc`."""
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ CRC_POLY) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def address_field(offset, ram=False):
    space = RAM_BYTES if ram else ROM_BYTES
    if not 0 <= offset < space:
        raise ValueError(f'offset {offset:#x} outside the {"RAM" if ram else "ROM"} space')
    return offset | (ram << ADDRESS_RAM_BIT)


def encode_request(command, address=0, payload=b'', length=1):
    """One request frame. `address` is the 24-bit address field (see
    `address_field`); WRITE takes the payload, READ the byte count."""
    if command == CMD_WRITE:
        if not 1 <= len(payload) <= MAX_PAYLOAD:
            raise ValueError('a WRITE carries 1 to 256 bytes')
        length = len(payload)
    elif payload:
        raise ValueError('only WRITE carries a payload')
    if not 1 <= length <= MAX_PAYLOAD:
        raise ValueError('length must be 1 to 256')
    body = bytes([command & 0xFF, address & 0xFF, (address >> 8) & 0xFF, (address >> 16) & 0xFF, length - 1]) + bytes(payload)
    crc = crc16(body)
    return bytes([SYNC]) + body + bytes([crc & 0xFF, crc >> 8])


@dataclass(frozen=True)
class Response:
    status: int
    command: int
    address: int
    length: int
    payload: bytes
    crc_ok: bool

    @property
    def code(self):
        return self.status & 0x07

    @property
    def ok(self):
        return self.crc_ok and self.code == STATUS_OK

    @property
    def held(self):
        return bool(self.status & 0x80)

    @property
    def initialized(self):
        return bool(self.status & 0x40)

    @property
    def framing(self):
        return bool(self.status & 0x20)

    @property
    def timeout(self):
        return bool(self.status & 0x10)

    @property
    def overrun(self):
        return bool(self.status & 0x08)

    @property
    def ram(self):
        return bool(self.address >> ADDRESS_RAM_BIT & 1)

    @property
    def offset(self):
        return self.address & (ROM_BYTES - 1)

    def describe(self):
        flags = ''.join(name[0] if getattr(self, name) else '-' for name in ('held', 'initialized', 'framing', 'timeout', 'overrun'))
        return f'{STATUS_NAMES.get(self.code, self.code)} flags={flags} cmd={self.command:#04x} address={self.address:#08x} length={self.length}'


def response_total_length(header):
    """Frame length implied by a response header (the first 7 bytes)."""
    if len(header) < HEADER_BYTES or header[0] != SYNC:
        raise ValueError('response header must start with SYNC')
    status, command, length = header[1], header[2], header[6] + 1
    payload = length if (status & 0x07) == STATUS_OK and command in PAYLOAD_COMMANDS else 0
    return HEADER_BYTES + payload + CRC_BYTES


def decode_response(frame):
    """Parse one complete response frame."""
    frame = bytes(frame)
    if len(frame) < HEADER_BYTES + CRC_BYTES or frame[0] != SYNC:
        raise ValueError('malformed response frame')
    total = response_total_length(frame[:HEADER_BYTES])
    if len(frame) != total:
        raise ValueError(f'response frame has {len(frame)} bytes, expected {total}')
    body, crc = frame[1:-CRC_BYTES], frame[-1] << 8 | frame[-2]
    address = body[2] | body[3] << 8 | body[4] << 16
    return Response(status=body[0], command=body[1], address=address, length=body[5] + 1,
                    payload=body[6:], crc_ok=crc16(body) == crc)


def chunks(image, size=MAX_PAYLOAD):
    """(offset, bytes) pieces of an image in WRITE-sized chunks."""
    return [(offset, bytes(image[offset:offset + size])) for offset in range(0, len(image), size)]


class LoaderError(Exception):
    pass


class Session:
    """Stop-and-wait exchanges over a byte transport with `write(bytes)` and
    `read(count) -> bytes` (returning fewer bytes on timeout)."""

    def __init__(self, transport, retries=3, log=None, retry_pause=0.05):
        self.transport = transport
        self.retries = retries
        self.retry_pause = retry_pause
        self.log = log or (lambda message: None)
        self.deadline = None

    def bound_io(self, seconds, problems=None):
        """Bound the transport's per-read and per-write waits (pyserial's
        `timeout` and `write_timeout`) when it has them. A pyserial setter
        reconfigures the port and can raise OSError (a disconnected adapter):
        with a `problems` list the failure is recorded there instead of
        raised, so cleanup can go on."""
        for name in ('timeout', 'write_timeout'):
            if hasattr(self.transport, name):
                try:
                    setattr(self.transport, name, max(MIN_IO_TIMEOUT, min(IO_TIMEOUT, seconds)))
                except OSError as error:
                    if problems is None:
                        raise
                    problems.append(f'setting {name} failed ({error!r})')

    def check_deadline(self):
        """Raise once the snapshot deadline passed; otherwise shrink the I/O
        bounds to the time left, so no single wait overruns it by more than
        MIN_IO_TIMEOUT."""
        if self.deadline is None:
            return
        deadline, clock = self.deadline
        remaining = deadline - clock()
        if remaining <= 0:
            raise LoaderError('snapshot deadline expired')
        self.bound_io(remaining)

    def drain(self):
        """Discard whatever input is queued (stale or misframed bytes) so the
        next attempt starts at a frame boundary; bounded."""
        for _ in range(DRAIN_LIMIT // 256):
            if not self.transport.read(256):
                return

    def read_frame(self):
        """One response frame: skip to the next SYNC (bounded), then the
        header, then the length the header implies. A timeout, a missing
        SYNC or a short tail raises LoaderError."""
        for _ in range(RESYNC_LIMIT):
            sync = self.transport.read(1)
            if not sync:
                raise LoaderError('response timeout')
            if sync[0] == SYNC:
                break
        else:
            raise LoaderError('no response frame sync')
        header = sync + self.transport.read(HEADER_BYTES - 1)
        if len(header) < HEADER_BYTES:
            raise LoaderError('truncated response header')
        try:
            total = response_total_length(header)
            rest = self.transport.read(total - HEADER_BYTES)
            if len(rest) < total - HEADER_BYTES:
                raise LoaderError('truncated response tail')
            return decode_response(header + rest)
        except ValueError as error:
            raise LoaderError(f'malformed response: {error}') from error

    def exchange(self, frame):
        """Send one request frame and return its response. A CRC-valid frame
        whose echoed command, address or length differs from the request is
        a stale or delayed reply to an earlier request: it is discarded and
        the next frame is read, at most STALE_FRAME_LIMIT times. A timeout, a
        short or malformed frame or a CRC failure raises LoaderError so the
        caller drains the input and retries. Only BAD_CRC answers are exempt
        from the echo check (the loader echoes the fields it received, which
        are then untrusted); the caller retries those anyway."""
        expected = (frame[1], frame[2] | frame[3] << 8 | frame[4] << 16, frame[5] + 1)
        self.transport.write(frame)
        for _ in range(STALE_FRAME_LIMIT + 1):
            response = self.read_frame()
            if not response.crc_ok:
                raise LoaderError('response CRC mismatch')
            if response.code == STATUS_BAD_CRC:
                return response
            echoed = (response.command, response.address, response.length)
            if echoed == expected:
                return response
            self.log(f'discarding a stale response {echoed}, expected {expected}')
        raise LoaderError('no response matching the request')

    def command(self, command, address=0, payload=b'', length=1):
        """One command with bounded retries; every request is idempotent."""
        frame = encode_request(command, address, payload, length)
        last = None
        for attempt in range(self.retries + 1):
            self.check_deadline()
            try:
                response = self.exchange(frame)
            except LoaderError as error:
                last = str(error)
                self.log(f'retry {attempt + 1}: {last}')
                time.sleep(self.retry_pause)
                self.drain()
                continue
            if response.ok:
                return response
            last = response.describe()
            if response.code in FINAL_CODES:
                break
            self.log(f'retry {attempt + 1}: {last}')
            self.drain()
        raise LoaderError(f'command {command:#04x} failed: {last}')

    def ping(self):
        return self.command(CMD_PING)

    def hold(self):
        return self.command(CMD_HOLD)

    def release(self):
        return self.command(CMD_RELEASE)

    def write(self, offset, data, ram=False):
        return self.command(CMD_WRITE, address_field(offset, ram), bytes(data))

    def read(self, offset, length, ram=False):
        response = self.command(CMD_READ, address_field(offset, ram), length=length)
        if len(response.payload) != length:
            raise LoaderError('short READ payload')
        return response.payload

    def snap_pin(self):
        return self.command(CMD_SNAP_PIN)

    def snap_release(self):
        return self.command(CMD_SNAP_RELEASE)

    def snap_read(self, offset, length):
        if not (0 <= offset and 1 <= length <= MAX_PAYLOAD and offset + length <= SNAPSHOT_RECORD_BYTES):
            raise ValueError('a SNAP_READ range must lie inside the 5768-byte record')
        response = self.command(CMD_SNAP_READ, offset, length=length)
        if len(response.payload) != length:
            raise LoaderError('short SNAP_READ payload')
        return response.payload

    def snapshot(self, deadline=None, clock=time.monotonic):
        """Pin the displayed frame, read its whole record and the header
        again, release. Returns (record, closing header). A final status, a
        header that changed between the first and the closing read (the pin
        was lost, so the bank may have been recycled), a transport error
        (including a pyserial write timeout) or the optional `deadline` (a
        `clock()` value) raises LoaderError; the pin is never re-taken on the
        caller's behalf. The deadline is checked before every attempt of
        every command and bounds each port read and write to the time left.

        Once the pin may have been taken, the closing SNAP_RELEASE is
        attempted on every exit path, including an interrupt: a pinned
        display stays frozen until released. The release is one bounded
        command with its own short I/O bound, outside the deadline. If it
        cannot be confirmed the error says so (the next session's leading
        release clears the pin); an interrupt is re-raised with that note."""
        self.snap_release()
        self.deadline = None if deadline is None else (deadline, clock)
        try:
            self.check_deadline()
            self.snap_pin()
            parts = []
            for offset in range(0, SNAPSHOT_RECORD_BYTES, MAX_PAYLOAD):
                parts.append(self.snap_read(offset, min(MAX_PAYLOAD, SNAPSHOT_RECORD_BYTES - offset)))
            record = b''.join(parts)
            closing = self.snap_read(0, SNAPSHOT_HEADER_BYTES)
            if record[:2] != b'NM' or closing[:3] != record[:3] or closing[3] & 0x7F != record[3] & 0x7F \
                    or closing[4:] != record[4:SNAPSHOT_HEADER_BYTES]:
                raise LoaderError(f'snapshot identity changed during the read: {record[:8].hex()} then {closing.hex()}')
        except BaseException as failure:
            # Why: the cleanup must not raise, or its error would hide the original failure;
            # the release outcome (and any port reconfiguration failure) is added to it.
            unconfirmed, problems = self._release_after(failure)
            outcome = unconfirmed or 'the pin was released'
            if problems:
                outcome += '; cleanup: ' + '; '.join(problems)
            if not isinstance(failure, Exception):
                if unconfirmed is not None or problems:
                    failure.add_note(outcome)
                raise
            if isinstance(failure, LoaderError) and unconfirmed is None and not problems:
                raise
            message = str(failure) if isinstance(failure, LoaderError) else f'transport error: {failure!r}'
            raise LoaderError(f'{message}; {outcome}') from failure
        unconfirmed, problems = self._release_after(None)
        if unconfirmed is not None:
            raise LoaderError(unconfirmed + ('; cleanup: ' + '; '.join(problems) if problems else ''))
        for problem in problems:
            self.log(f'after the confirmed release: {problem}')
        return record, closing

    def _release_after(self, failure):
        """One closing SNAP_RELEASE outside the deadline, with short I/O
        bounds. Never raises an Exception: returns (None when the release was
        confirmed, else the message that the pin may remain; the list of I/O
        bound setter failures, which never skip the release attempt)."""
        self.deadline = None
        problems = []
        self.bound_io(RELEASE_IO_TIMEOUT, problems)
        unconfirmed = None
        try:
            self.snap_release()
        except (LoaderError, OSError) as error:
            reason = f'SNAP_RELEASE {"after the failure " if failure is not None else ""}was not confirmed ({error!r})'
            unconfirmed = f'{reason}; the pin may remain (display frozen) until the next snapshot releases it'
        self.bound_io(IO_TIMEOUT, problems)
        return unconfirmed, problems

    def load_image(self, image, verify=True, release=True):
        """Hold the console, write every chunk, optionally read every chunk
        back and compare, then release. Returns the number of bytes written."""
        if not 1 <= len(image) <= ROM_BYTES:
            raise LoaderError('image must be 1 byte to 2 MiB')
        self.hold()
        for offset, data in chunks(image):
            self.write(offset, data)
        if verify:
            for offset, data in chunks(image):
                observed = self.read(offset, len(data))
                if observed != data:
                    raise LoaderError(f'verify mismatch at {offset:#07x}')
        if release:
            self.release()
        return len(image)


class DumpTransport:
    """Records request frames to a file; every response is a fabricated OK so
    a load can be inspected without a board."""

    def __init__(self, path):
        self.path = Path(path)
        self.frames = []
        self.pending = None

    def write(self, frame):
        self.frames.append(bytes(frame))
        command = frame[1]
        body = bytes([0x80, command]) + bytes(frame[2:6])
        if command in PAYLOAD_COMMANDS:
            body += bytes(frame[5] + 1)
        crc = crc16(body)
        self.pending = bytes([SYNC]) + body + bytes([crc & 0xFF, crc >> 8])

    def read(self, count):
        data, self.pending = self.pending[:count], self.pending[count:]
        return data

    def close(self):
        self.path.write_bytes(b''.join(self.frames))


def open_serial(port, baud):
    try:
        import serial
    except ImportError as error:
        raise LoaderError('live serial requires optional pyserial; install pyserial in the host Python environment') from error
    # Why: without write_timeout a write that cannot drain (a stuck adapter) blocks
    # forever; with it pyserial raises SerialTimeoutException, an OSError.
    return serial.Serial(port, baud, timeout=IO_TIMEOUT, write_timeout=IO_TIMEOUT)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--image', type=Path, help='cartridge image to load')
    parser.add_argument('--port', help='serial device of the board (pyserial)')
    parser.add_argument('--baud', type=int, default=DEFAULT_BAUD)
    parser.add_argument('--dump', type=Path, help='write the request frames to this file instead of a port')
    parser.add_argument('--no-verify', action='store_true', help='skip the readback comparison')
    parser.add_argument('--no-release', action='store_true', help='leave the console held after loading')
    parser.add_argument('--ping', action='store_true', help='only exchange a PING and print the status')
    args = parser.parse_args(argv)
    if (args.port is None) == (args.dump is None):
        parser.error('give exactly one of --port or --dump')
    if not args.ping and args.image is None:
        parser.error('--image is required unless --ping')
    transport = None
    try:
        transport = DumpTransport(args.dump) if args.dump else open_serial(args.port, args.baud)
        session = Session(transport, log=lambda message: print(message, file=sys.stderr))
        if args.ping:
            print(session.ping().describe())
            return 0
        image = args.image.read_bytes()
        started = time.monotonic()
        count = session.load_image(image, verify=not args.no_verify, release=not args.no_release)
        print(f'{args.image}: {count} bytes loaded{"" if args.no_verify else " and verified"} in {time.monotonic() - started:.2f} s')
        return 0
    except (OSError, ValueError, LoaderError) as error:
        print(f'error: {error}', file=sys.stderr)
        return 2
    finally:
        if transport is not None and hasattr(transport, 'close'):
            transport.close()


if __name__ == '__main__':
    raise SystemExit(main())
