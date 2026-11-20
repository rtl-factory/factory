"""The frame-snapshot host tool: the record codec and its validation, the
manifest check, the palette and the 640x480 reconstruction against the
display model's pixel oracle, PNG and GIF structure through independent
standard-library decoders, the capture sequence against a fake loader
(success, NO_FRAME, a lost pin, a changed identity, the deadline, the
release after failure) and the command line with its workdir confinement."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import random
import struct
import sys
import tempfile
import types
import unittest
import zlib

from src.nand2mario.dv.video import display_model as dm
from src.nand2mario.scripts import cart_loader as loader
from src.nand2mario.scripts import frame_snapshot as fs
from src.nand2mario.scripts.tests.test_cart_loader import FakeDevice

ROOT = Path(__file__).resolve().parents[4]


def random_frame(rng):
    return [[rng.randrange(4) for _ in range(160)] for _ in range(144)]


def pack(frame, frame_id, bank, on_screen=True):
    """Independent record encoder from the display HAS (not the tool's)."""
    flat = [shade for row in frame for shade in row]
    pixels = bytearray()
    for m in range(len(flat) // 4):
        pixels.append(flat[4 * m] | flat[4 * m + 1] << 2 | flat[4 * m + 2] << 4 | flat[4 * m + 3] << 6)
    return b'NM\x01' + bytes([bank | on_screen << 7]) + struct.pack('<I', frame_id) + bytes(pixels)


def png_decode(data):
    """Width, height, RGB rows and tEXt fields of a filter-0 8-bit RGB PNG."""
    assert data[:8] == b'\x89PNG\r\n\x1a\n'
    position, chunks, text = 8, {}, {}
    while position < len(data):
        length = struct.unpack('>I', data[position:position + 4])[0]
        kind, body = data[position + 4:position + 8], data[position + 8:position + 8 + length]
        assert struct.unpack('>I', data[position + 8 + length:position + 12 + length])[0] == zlib.crc32(kind + body)
        if kind == b'tEXt':
            key, value = body.split(b'\x00', 1)
            text[key.decode()] = value.decode('latin-1')
        else:
            chunks[kind] = chunks.get(kind, b'') + body
        position += 12 + length
    width, height, depth, colour = struct.unpack('>IIBB', chunks[b'IHDR'][:10])
    assert (depth, colour) == (8, 2)
    raw = zlib.decompress(chunks[b'IDAT'])
    stride = 1 + 3 * width
    rows = []
    for y in range(height):
        line = raw[y * stride:(y + 1) * stride]
        assert line[0] == 0
        rows.append([tuple(line[1 + 3 * x:4 + 3 * x]) for x in range(width)])
    return width, height, rows, text


def gif_decode(data):
    """Frames (index rows), delays (ms), loop flag and comment of a GIF89a
    with a global palette, decoded with an independent LZW reader."""
    assert data[:6] == b'GIF89a' and data[-1:] == b'\x3b'
    width, height, packed = struct.unpack('<HHB', data[6:11])
    palette = data[13:13 + 3 * (2 << (packed & 7))]
    position, frames, delays, comment, loops = 13 + len(palette), [], [], b'', False
    delay = None

    def blocks(position):
        out = b''
        while data[position]:
            out += data[position + 1:position + 1 + data[position]]
            position += 1 + data[position]
        return out, position + 1

    while data[position] != 0x3B:
        if data[position] == 0x21:
            label = data[position + 1]
            body, position = blocks(position + 2)
            if label == 0xF9:
                delay = struct.unpack('<H', body[1:3])[0] * 10
            elif label == 0xFE:
                comment += body
            elif label == 0xFF and body.startswith(b'NETSCAPE2.0'):
                loops = True
        else:
            assert data[position] == 0x2C
            assert struct.unpack('<HHHHB', data[position + 1:position + 10]) == (0, 0, width, height, 0)
            minimum = data[position + 10]
            stream, position = blocks(position + 11)
            indices = lzw_decode(stream, minimum)
            assert len(indices) == width * height
            frames.append([indices[y * width:(y + 1) * width] for y in range(height)])
            delays.append(delay)
    levels = [palette[3 * i] for i in range(len(palette) // 3)]
    return width, height, frames, delays, loops, comment.decode(), levels


def lzw_decode(stream, minimum):
    clear, end = 1 << minimum, (1 << minimum) + 1
    bits = int.from_bytes(stream, 'little')
    position, size, out, table, previous = 0, minimum + 1, [], None, None
    while True:
        code = bits >> position & ((1 << size) - 1)
        position += size
        if code == clear:
            table = [[i] for i in range(clear)] + [None, None]
            size, previous = minimum + 1, None
            continue
        if code == end:
            return out
        if previous is None:
            entry = table[code]
        else:
            entry = table[code] if code < len(table) else previous + previous[:1]
            table.append(previous + entry[:1])
            if len(table) == 1 << size and size < 12:
                size += 1
        out.extend(entry)
        previous = entry


class SnapshotDevice(FakeDevice):
    """FakeDevice with the display's snapshot side: `available` (a frame is
    displayed), the pin, the pinned frame's record, `lose_pin_after` (the pin
    vanishes after that many SNAP_READ answers, as on a core reset) and
    `repin_with` (after the record reads, the pin is lost and silently
    re-taken on a different record, which the closing header must expose)."""

    def __init__(self, record):
        super().__init__()
        self.available, self.pinned, self.record = True, False, record
        self.lose_pin_after = None
        self.repin_with = None
        self.snap_reads = 0
        self.releases = 0
        self.fail_release_from = None   # the n-th SNAP_RELEASE onward goes unanswered
        self.release_error = None       # ...or raises this from the transport instead
        self.read_error = None          # (n, exception): the n-th SNAP_READ write raises it

    def write(self, frame):
        command = frame[1]
        if command not in (loader.CMD_SNAP_PIN, loader.CMD_SNAP_RELEASE, loader.CMD_SNAP_READ):
            return super().write(frame)
        self.requests.append(bytes(frame))
        body = frame[1:-2]
        address, length = body[1] | body[2] << 8 | body[3] << 16, body[4] + 1
        data = b''
        if command == loader.CMD_SNAP_READ and not self.pinned:
            code = loader.STATUS_NOT_PINNED
        elif command == loader.CMD_SNAP_READ and address + length > loader.SNAPSHOT_RECORD_BYTES:
            code = loader.STATUS_BAD_ADDRESS
        elif command == loader.CMD_SNAP_PIN and not self.pinned and not self.available:
            code = loader.STATUS_NO_FRAME
        else:
            code = loader.STATUS_OK
            if command == loader.CMD_SNAP_PIN:
                self.pinned = True
            elif command == loader.CMD_SNAP_RELEASE:
                self.releases += 1
                if self.fail_release_from is not None and self.releases >= self.fail_release_from:
                    if self.release_error is not None:
                        raise self.release_error
                    return None
                self.pinned = False
            else:
                if self.read_error is not None and self.snap_reads + 1 == self.read_error[0]:
                    raise self.read_error[1]
                data = self.record[address:address + length]
                self.snap_reads += 1
                if self.lose_pin_after is not None and self.snap_reads >= self.lose_pin_after:
                    self.pinned = False
                if self.repin_with is not None and self.snap_reads == 23:
                    self.record, self.repin_with = self.repin_with, None
        self.pending += self.frame_for(0x40 | code, command, body[1:5], data)
        return None


class Clock:
    def __init__(self, step=0.0):
        self.now, self.step = 0.0, step

    def __call__(self):
        self.now += self.step
        return self.now


class RecordTests(unittest.TestCase):
    def setUp(self):
        self.rng = random.Random(377)
        self.frame = random_frame(self.rng)
        self.record = pack(self.frame, 0x01020304, 2)

    def test_record_codec_against_the_independent_packer(self):
        snapshot = fs.parse_record(self.record)
        self.assertEqual((snapshot.frame_id, snapshot.bank, snapshot.on_screen), (0x01020304, 2, True))
        self.assertEqual(list(snapshot.shades), [shade for row in self.frame for shade in row])
        self.assertEqual(snapshot.sha256, hashlib.sha256(self.record).hexdigest())
        self.assertEqual(fs.raw_indices(snapshot), self.frame)
        self.assertEqual(self.record, dm.snapshot_record(self.frame, 0x01020304, 2), 'the DV model agrees')
        self.assertFalse(fs.parse_record(pack(self.frame, 7, 0, on_screen=False)).on_screen)

    def test_invalid_records(self):
        cases = {'truncated': self.record[:-1], 'padded': self.record + b'\x00', 'empty': b'',
                 'magic': b'NX' + self.record[2:], 'version': self.record[:2] + b'\x02' + self.record[3:],
                 'bank 3': self.record[:3] + b'\x03' + self.record[4:],
                 'reserved flag bits': self.record[:3] + b'\x42' + self.record[4:],
                 'frame id 0': self.record[:4] + bytes(4) + self.record[8:]}
        for name, record in cases.items():
            with self.subTest(name), self.assertRaises(ValueError):
                fs.parse_record(record)

    def test_manifest_check(self):
        snapshot = fs.parse_record(self.record)
        manifest = {'schema': fs.SCHEMA, 'record_bytes': 5768, 'sha256': snapshot.sha256, 'frame_id': 0x01020304,
                    'bank': 2, 'format_version': 1}
        fs.check_manifest(snapshot, manifest)
        for key, value in (('sha256', '0' * 64), ('frame_id', 5), ('bank', 1), ('schema', 'other/1'), ('record_bytes', 5767)):
            with self.subTest(key), self.assertRaises(ValueError):
                fs.check_manifest(snapshot, dict(manifest, **{key: value}))

    def test_palette_and_reconstruction_against_the_display_model(self):
        snapshot = fs.parse_record(self.record)
        self.assertEqual(fs.GREY, tuple(dm.PALETTE[shade][0] * 17 for shade in range(4)))
        rows = fs.vga_indices(snapshot)
        self.assertEqual((len(rows), len(rows[0])), (480, 640))
        for y in range(480):
            for x in range(640):
                level = dm.expected_rgb(self.frame, x, y)[0] * 17
                self.assertEqual(fs.GREY[rows[y][x]], level, (x, y))

    def test_png_raw_and_reconstruction(self):
        snapshot = fs.parse_record(self.record)
        width, height, rows, text = png_decode(fs.png_bytes(fs.raw_indices(snapshot), fs.labels(snapshot, False)))
        self.assertEqual((width, height), (160, 144))
        self.assertEqual(rows, [[(fs.GREY[s],) * 3 for s in row] for row in self.frame])
        self.assertIn('frame 16909060 (bank 2)', text['Title'])
        self.assertIn('not a VGA capture', text['Description'])
        self.assertIn(snapshot.sha256, text['Source'])
        width, height, rows, text = png_decode(fs.png_bytes(fs.vga_indices(snapshot), fs.labels(snapshot, True)))
        self.assertEqual((width, height, rows[24][80], rows[0][0]), (640, 480, (fs.GREY[self.frame[0][0]],) * 3, (0, 0, 0)))
        self.assertIn('not an electrical raster capture', text['Description'])
        hidden = fs.parse_record(pack(self.frame, 9, 1, on_screen=False))
        self.assertIn('LCD-off blank', fs.labels(hidden, False)['Description'])

    def test_gif_frames_delays_and_comment(self):
        snapshots = [fs.parse_record(pack(random_frame(self.rng), i, i % 3)) for i in (3, 8, 20)]
        frames = [fs.raw_indices(s) for s in snapshots]
        width, height, decoded, delays, loops, comment, levels = gif_decode(fs.gif_bytes(frames, [500, 250, 1000], 'sampled'))
        self.assertEqual((width, height, decoded, delays, loops, comment), (160, 144, frames, [500, 250, 1000], True, 'sampled'))
        self.assertEqual(levels, list(fs.GREY))
        # A large random frame exercises the 4096-entry table reset.
        big = [[self.rng.randrange(4) for _ in range(640)] for _ in range(480)]
        self.assertEqual(gif_decode(fs.gif_bytes([big], [10], 'x'))[2], [big])


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.record = pack(random_frame(random.Random(5)), 42, 1)

    def snapshot(self, device, **kwargs):
        return loader.Session(device, retry_pause=0).snapshot(**kwargs)

    def commands(self, device):
        return [frame[1] for frame in device.requests]

    def test_sequence_and_record(self):
        device = SnapshotDevice(self.record)
        record, closing = self.snapshot(device)
        self.assertEqual((record, closing), (self.record, self.record[:8]))
        self.assertEqual(device.requests, fs.plan_frames(), 'the session sends exactly the planned frames')
        self.assertFalse(device.pinned)

    def test_no_frame_is_final_and_released(self):
        device = SnapshotDevice(self.record)
        device.available = False
        with self.assertRaisesRegex(loader.LoaderError, 'no-frame'):
            self.snapshot(device)
        self.assertEqual(self.commands(device), [loader.CMD_SNAP_RELEASE, loader.CMD_SNAP_PIN, loader.CMD_SNAP_RELEASE])

    def test_lost_pin_fails_without_repinning(self):
        device = SnapshotDevice(self.record)
        device.lose_pin_after = 10
        with self.assertRaisesRegex(loader.LoaderError, 'not-pinned'):
            self.snapshot(device)
        self.assertEqual(self.commands(device).count(loader.CMD_SNAP_PIN), 1)
        self.assertEqual(self.commands(device)[-1], loader.CMD_SNAP_RELEASE)

    def test_changed_identity_fails(self):
        device = SnapshotDevice(self.record)
        device.repin_with = pack(random_frame(random.Random(6)), 43, 0)
        with self.assertRaisesRegex(loader.LoaderError, 'identity changed'):
            self.snapshot(device)
        self.assertEqual(self.commands(device)[-1], loader.CMD_SNAP_RELEASE)

    def test_off_screen_at_the_end_is_reported_not_failed(self):
        device = SnapshotDevice(self.record)
        device.repin_with = self.record[:3] + bytes([self.record[3] & 0x7F]) + self.record[4:]
        record, closing = self.snapshot(device)
        self.assertEqual((record[3] >> 7, closing[3] >> 7), (1, 0))

    def test_deadline_and_failed_release(self):
        device = SnapshotDevice(self.record)
        with self.assertRaisesRegex(loader.LoaderError, 'deadline'):
            self.snapshot(device, deadline=5.0, clock=Clock(step=1.0))
        self.assertEqual(self.commands(device)[-1], loader.CMD_SNAP_RELEASE)
        self.assertFalse(device.pinned)
        device = SnapshotDevice(self.record)
        device.fail_release_from = 2
        with self.assertRaisesRegex(loader.LoaderError, 'the pin may remain'):
            loader.Session(device, retries=1, retry_pause=0).snapshot()
        self.assertTrue(device.pinned)

    def test_release_on_transport_error_and_interrupt(self):
        # A transport OSError mid-read becomes a loader error after the pin
        # was released; an interrupt is re-raised after the release.
        for error, raised in ((OSError('port vanished'), loader.LoaderError), (KeyboardInterrupt(), KeyboardInterrupt)):
            with self.subTest(error=type(error).__name__):
                device = SnapshotDevice(self.record)
                device.read_error = (5, error)
                with self.assertRaises(raised) as context:
                    self.snapshot(device)
                if raised is loader.LoaderError:
                    self.assertRegex(str(context.exception), "transport error: OSError.*the pin was released")
                self.assertEqual(self.commands(device)[-1], loader.CMD_SNAP_RELEASE)
                self.assertFalse(device.pinned, 'the display is not left frozen')
                self.assertEqual(self.commands(device).count(loader.CMD_SNAP_RELEASE), 2)
        # A read-side transport error (the port's read raises mid-response).
        class ReadFault(SnapshotDevice):
            def read(self, count):
                if self.snap_reads == 3 and self.pinned and self.pending:
                    self.pending = b''
                    raise OSError('read failed on the port')
                return super().read(count)

        device = ReadFault(self.record)
        with self.assertRaisesRegex(loader.LoaderError, "transport error: OSError.*read failed.*the pin was released"):
            self.snapshot(device)
        self.assertFalse(device.pinned)
        self.assertEqual(self.commands(device)[-1], loader.CMD_SNAP_RELEASE)
        # The closing release itself fails: a clear error, the pin reported.
        for release_error in (None, OSError('port vanished')):
            with self.subTest(release_error=release_error):
                device = SnapshotDevice(self.record)
                device.read_error = (5, OSError('read failed'))
                device.fail_release_from, device.release_error = 2, release_error
                with self.assertRaisesRegex(loader.LoaderError, 'read failed.*not confirmed.*the pin may remain'):
                    loader.Session(device, retries=1, retry_pause=0).snapshot()
                self.assertTrue(device.pinned)
        device = SnapshotDevice(self.record)
        device.read_error = (5, KeyboardInterrupt())
        device.fail_release_from = 2
        with self.assertRaises(KeyboardInterrupt) as raised:
            loader.Session(device, retries=1, retry_pause=0).snapshot()
        self.assertIn('the pin may remain', ' '.join(getattr(raised.exception, '__notes__', [])))

    def test_cleanup_survives_port_reconfiguration_failure(self):
        """After a transport failure the adapter refuses reconfiguration (a
        pyserial timeout setter raises OSError on a disconnected port): the
        release is still attempted, the original error stays the error and
        the setter failures and the release outcome are attached."""
        class Disconnecting(SnapshotDevice):
            def __init__(self, record, release_fails=False):
                super().__init__(record)
                self.timeout = self.write_timeout = loader.IO_TIMEOUT
                self.release_fails = release_fails

            def __setattr__(self, name, value):
                if name in ('timeout', 'write_timeout') and getattr(self, 'disconnected', False):
                    raise OSError('adapter settings unavailable')
                super().__setattr__(name, value)

            def write(self, frame):
                if frame[1] == loader.CMD_SNAP_READ:
                    self.disconnected = True
                    raise OSError('original read transport failure')
                if frame[1] == loader.CMD_SNAP_RELEASE and self.release_fails and getattr(self, 'disconnected', False):
                    raise OSError('write on a disconnected port')
                return super().write(frame)

        device = Disconnecting(self.record)
        with self.assertRaises(loader.LoaderError) as raised:
            loader.Session(device, retry_pause=0).snapshot(deadline=100.0, clock=lambda: 0.0)
        message = str(raised.exception)
        self.assertRegex(message, r"transport error: OSError\('original read transport failure'\); the pin was released"
                                  r"; cleanup: setting timeout failed .*adapter settings unavailable")
        self.assertIsInstance(raised.exception.__cause__, OSError)
        # The failing SNAP_READ never reached the device; the release did.
        self.assertEqual(self.commands(device), [loader.CMD_SNAP_RELEASE, loader.CMD_SNAP_PIN, loader.CMD_SNAP_RELEASE])
        self.assertEqual(device.releases, 2)
        self.assertFalse(device.pinned)
        device = Disconnecting(self.record, release_fails=True)
        with self.assertRaisesRegex(loader.LoaderError, "original read transport failure.*not confirmed.*"
                                                        "the pin may remain.*cleanup: setting timeout failed"):
            loader.Session(device, retries=1, retry_pause=0).snapshot(deadline=100.0, clock=lambda: 0.0)
        self.assertTrue(device.pinned)

    def test_blocked_write_is_bounded_and_released(self):
        """A pyserial-like port whose write cannot drain: with a finite
        write_timeout it raises a write timeout (an OSError) after that bound;
        an unbounded write would hang, which this mock refuses."""
        class WriteTimeout(OSError):        # pyserial's SerialTimeoutException is an OSError
            pass

        class BlockingPort(SnapshotDevice):
            def __init__(self, record, block_from, block_release=False):
                super().__init__(record)
                self.timeout = self.write_timeout = None    # as opened without bounds
                self.block_from, self.block_release = block_from, block_release
                self.write_bounds = []

            def write(self, frame):
                blocked = (frame[1] == loader.CMD_SNAP_READ and self.snap_reads + 1 >= self.block_from) or \
                          (frame[1] == loader.CMD_SNAP_RELEASE and self.block_release and self.pinned)
                if blocked:
                    if self.write_timeout is None:
                        raise AssertionError('an unbounded write would block forever')
                    self.write_bounds.append(self.write_timeout)
                    raise WriteTimeout('Write timeout')
                return super().write(frame)

        clock = Clock()
        device = BlockingPort(self.record, block_from=4)
        with self.assertRaisesRegex(loader.LoaderError, "transport error: WriteTimeout.*the pin was released"):
            loader.Session(device, retry_pause=0).snapshot(deadline=0.3, clock=clock)
        self.assertEqual(device.write_bounds, [0.3])
        self.assertFalse(device.pinned)
        self.assertEqual(self.commands(device)[-1], loader.CMD_SNAP_RELEASE)
        self.assertEqual((device.timeout, device.write_timeout), (loader.IO_TIMEOUT, loader.IO_TIMEOUT))
        # The closing release is blocked too: bounded by its own short write
        # timeout and reported as not confirmed.
        device = BlockingPort(self.record, block_from=4, block_release=True)
        with self.assertRaisesRegex(loader.LoaderError, 'WriteTimeout.*not confirmed.*the pin may remain'):
            loader.Session(device, retry_pause=0).snapshot(deadline=30.0, clock=Clock())
        self.assertEqual(device.write_bounds, [loader.IO_TIMEOUT, loader.RELEASE_IO_TIMEOUT])
        self.assertTrue(device.pinned)

    def test_deadline_bounds_every_attempt(self):
        """The I/O bounds follow the time left, and an attempt after the
        deadline is refused even inside one command's retries."""
        class Port(SnapshotDevice):
            def __init__(self, record):
                super().__init__(record)
                self.timeout = self.write_timeout = loader.IO_TIMEOUT
                self.seen = []

            def write(self, frame):
                self.seen.append((frame[1], self.timeout, self.write_timeout))
                if frame[1] == loader.CMD_SNAP_READ:
                    self.drop_next = True       # every read reply is lost: retries
                return super().write(frame)

        clock = Clock(step=0.1)
        device = Port(self.record)
        with self.assertRaisesRegex(loader.LoaderError, '^snapshot deadline expired$'):
            loader.Session(device, retries=50, retry_pause=0).snapshot(deadline=0.75, clock=clock)
        reads = [bounds for command, *bounds in device.seen if command == loader.CMD_SNAP_READ]
        # One clock tick per check: the snapshot's own at 0.1 s, the pin at
        # 0.2 s, read attempts at 0.3 .. 0.7 s, each bounded by the time
        # left; the check at 0.8 s ends the retries.
        self.assertEqual(len(reads), 5, reads)
        for (timeout, write_timeout), now in zip(reads, (0.3, 0.4, 0.5, 0.6, 0.7)):
            self.assertAlmostEqual(timeout, 0.75 - now)
            self.assertAlmostEqual(write_timeout, 0.75 - now)
        self.assertEqual(self.commands(device)[-1], loader.CMD_SNAP_RELEASE)
        self.assertFalse(device.pinned)

    def test_open_serial_bounds_both_directions(self):
        opened = {}
        fake = types.ModuleType('serial')
        fake.Serial = lambda port, baud, **kwargs: opened.update(port=port, baud=baud, **kwargs) or 'port'
        saved = sys.modules.get('serial')
        sys.modules['serial'] = fake
        try:
            self.assertEqual(loader.open_serial('/dev/x', 115200), 'port')
        finally:
            if saved is None:
                del sys.modules['serial']
            else:
                sys.modules['serial'] = saved
        self.assertEqual(opened, {'port': '/dev/x', 'baud': 115200, 'timeout': loader.IO_TIMEOUT,
                                  'write_timeout': loader.IO_TIMEOUT})

    def test_transport_retry_of_a_lost_reply(self):
        device = SnapshotDevice(self.record)
        session = loader.Session(device, retry_pause=0)
        session.snap_release()
        device.drop_next = True
        session.snap_pin()          # resent; the pin is idempotent
        self.assertTrue(device.pinned)
        self.assertEqual(session.snap_read(0, 8), self.record[:8])
        with self.assertRaises(ValueError):
            session.snap_read(5767, 2)


class CommandLineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT / 'workdir')
        self.dir = Path(self.temporary.name)
        self.rng = random.Random(11)

    def tearDown(self):
        self.temporary.cleanup()

    def run_main(self, *argv, **kwargs):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = fs.main(list(argv), **kwargs)
        return code, out.getvalue(), err.getvalue()

    def test_plan_and_confinement(self):
        code, out, _ = self.run_main('plan', '--out', str(self.dir / 'plan.bin'))
        self.assertEqual(code, 0)
        self.assertEqual((self.dir / 'plan.bin').read_bytes(), b''.join(fs.plan_frames()))
        self.assertEqual(json.loads(out)['frames'], 27)
        self.assertEqual(self.run_main('plan', '--out', '/tmp/escape.bin')[0], 2)
        self.assertEqual(self.run_main('capture', '--port', 'X', '--out', '/tmp/escape')[0], 2)

    def test_capture_writes_record_and_manifest(self):
        record = pack(random_frame(self.rng), 77, 0)
        opened = []

        def open_serial(port, baud):
            opened.append((port, baud))
            return SnapshotDevice(record)

        stem = self.dir / 'cap'
        code, out, _ = self.run_main('capture', '--port', '/dev/fake', '--out', str(stem), open_serial=open_serial)
        self.assertEqual((code, opened), (0, [('/dev/fake', 115200)]))
        self.assertEqual(stem.with_suffix('.bin').read_bytes(), record)
        manifest = json.loads(stem.with_suffix('.json').read_text())
        self.assertEqual((manifest['frame_id'], manifest['bank'], manifest['sha256'], manifest['on_screen_at_end']),
                         (77, 0, hashlib.sha256(record).hexdigest(), True))
        self.assertIn('not a VGA capture', manifest['kind'])

        def no_frame(port, baud):
            device = SnapshotDevice(record)
            device.available = False
            return device

        code, _, err = self.run_main('capture', '--port', 'p', '--out', str(self.dir / 'none'), open_serial=no_frame)
        self.assertEqual(code, 1)
        self.assertIn('no-frame', err)
        self.assertFalse((self.dir / 'none.bin').exists())

    def test_decode_and_gif(self):
        records = []
        for frame_id in (4, 9, 15):
            path = self.dir / f'f{frame_id}.bin'
            path.write_bytes(pack(random_frame(self.rng), frame_id, frame_id % 3))
            records.append(path)
        snapshot = fs.parse_record(records[0].read_bytes())
        manifest = self.dir / 'f4.json'
        manifest.write_text(json.dumps({'schema': fs.SCHEMA, 'record_bytes': 5768, 'sha256': snapshot.sha256,
                                        'frame_id': 4, 'bank': 1, 'format_version': 1, 'on_screen_at_end': False}))
        code, out, _ = self.run_main('decode', str(records[0]), '--manifest', str(manifest),
                                     '--png', str(self.dir / 'raw.png'), '--vga-png', str(self.dir / 'vga.png'))
        self.assertEqual(code, 0)
        result = json.loads(out)
        self.assertEqual((result['frame_id'], sum(result['shade_histogram']), result['on_screen_at_end']), (4, 23040, False))
        self.assertIn('LCD-off blank', png_decode((self.dir / 'raw.png').read_bytes())[3]['Description'])
        self.assertEqual(png_decode((self.dir / 'vga.png').read_bytes())[:2], (640, 480))
        corrupt = self.dir / 'corrupt.bin'
        corrupt.write_bytes(records[0].read_bytes()[:-3])
        self.assertEqual(self.run_main('decode', str(corrupt))[0], 1)
        manifest.write_text(json.dumps({'schema': fs.SCHEMA, 'sha256': '0' * 64}))
        self.assertEqual(self.run_main('decode', str(records[0]), '--manifest', str(manifest))[0], 1)
        self.assertEqual(self.run_main('decode', str(records[0]), '--png', '/tmp/x.png')[0], 2)
        gif = self.dir / 'anim.gif'
        code, out, _ = self.run_main('gif', *map(str, records), '--durations-ms', '500', '500', '1000',
                                     '--out', str(gif), '--vga')
        self.assertEqual(code, 0)
        width, height, frames, delays, loops, comment, _ = gif_decode(gif.read_bytes())
        self.assertEqual((width, height, len(frames), delays), (640, 480, 3, [500, 500, 1000]))
        self.assertIn('editorial, not a real-time rate', comment)
        self.assertIn('[4, 9, 15]', comment)
        self.assertEqual(self.run_main('gif', str(records[1]), str(records[0]), '--durations-ms', '10', '10',
                                       '--out', str(gif))[0], 1)
        self.assertEqual(self.run_main('gif', str(records[0]), '--durations-ms', '15', '--out', str(gif))[0], 2)
        self.assertEqual(self.run_main('gif', str(records[0]), '--durations-ms', '10', '20', '--out', str(gif))[0], 2)

    def test_gif_labels_follow_the_records_and_manifests(self):
        frames = {}
        for frame_id, on_screen in ((3, True), (6, False), (9, True)):
            path = self.dir / f'g{frame_id}.bin'
            path.write_bytes(pack(random_frame(self.rng), frame_id, 0, on_screen=on_screen))
            frames[frame_id] = path
        gif = self.dir / 'labels.gif'
        code, out, _ = self.run_main('gif', *map(str, frames.values()), '--durations-ms', '100', '100', '100',
                                     '--out', str(gif))
        self.assertEqual(code, 0)
        result = json.loads(out)
        self.assertEqual((result['on_screen'], result['on_screen_at_end']), ([True, False, True], [None, None, None]))
        comment = gif_decode(gif.read_bytes())[5]
        # Without closing status an on-screen pin is not claimed frozen on screen.
        self.assertNotIn('frozen there while read', comment)
        self.assertIn('frame 3: on screen when pinned; whether it stayed on screen for the read is unknown', comment)
        self.assertIn('frame 6: the last frame displayed before an LCD-off blank, not on screen at readout', comment)
        self.assertIn('frame 9: on screen when pinned; whether it stayed on screen for the read is unknown', comment)
        # Manifests: a closing header off screen relabels the frame; a
        # manifest that does not match its record is rejected.
        manifests = []
        for frame_id, path in frames.items():
            snapshot = fs.parse_record(path.read_bytes())
            manifest = self.dir / f'g{frame_id}.json'
            manifest.write_text(json.dumps({'schema': fs.SCHEMA, 'record_bytes': 5768, 'sha256': snapshot.sha256,
                                            'frame_id': frame_id, 'bank': 0, 'format_version': 1,
                                            'on_screen_at_end': frame_id == 3}))
            manifests.append(str(manifest))
        code, out, _ = self.run_main('gif', *map(str, frames.values()), '--durations-ms', '100', '100', '100',
                                     '--out', str(gif), '--manifests', *manifests)
        self.assertEqual(code, 0)
        result = json.loads(out)
        self.assertEqual((result['on_screen'], result['on_screen_at_end']), ([True, False, False], [True, False, False]))
        comment = gif_decode(gif.read_bytes())[5]
        self.assertIn('frame 3: on screen and frozen there while read', comment)
        self.assertIn('frame 9: the last frame displayed before an LCD-off blank', comment)
        # A manifest without the closing status: unknown, not on screen.
        snapshot = fs.parse_record(frames[3].read_bytes())
        partial = self.dir / 'partial.json'
        partial.write_text(json.dumps({'schema': fs.SCHEMA, 'record_bytes': 5768, 'sha256': snapshot.sha256,
                                       'frame_id': 3, 'bank': 0, 'format_version': 1}))
        code, out, _ = self.run_main('gif', str(frames[3]), '--durations-ms', '100', '--out', str(gif),
                                     '--manifests', str(partial))
        self.assertEqual((code, json.loads(out)['on_screen_at_end']), (0, [None]))
        self.assertIn('frame 3: on screen when pinned; whether it stayed on screen for the read is unknown',
                      gif_decode(gif.read_bytes())[5])
        # The same three-state label on a decoded PNG.
        code, _, _ = self.run_main('decode', str(frames[3]), '--manifest', str(partial), '--png', str(self.dir / 'p.png'))
        self.assertEqual(code, 0)
        self.assertIn('is unknown', png_decode((self.dir / 'p.png').read_bytes())[3]['Description'])
        # A manifest claiming on screen at the end for a record off screen at
        # the pin is impossible and rejected.
        snapshot6 = fs.parse_record(frames[6].read_bytes())
        bad = self.dir / 'bad6.json'
        bad.write_text(json.dumps({'schema': fs.SCHEMA, 'record_bytes': 5768, 'sha256': snapshot6.sha256,
                                   'frame_id': 6, 'bank': 0, 'format_version': 1, 'on_screen_at_end': True}))
        self.assertEqual(self.run_main('gif', str(frames[6]), '--durations-ms', '100', '--out', str(gif),
                                       '--manifests', str(bad))[0], 1)
        # A manifest file that does not exist is refused before any output.
        self.assertEqual(self.run_main('gif', str(frames[3]), '--durations-ms', '100', '--out', str(gif),
                                       '--manifests', str(self.dir / 'absent.json'))[0], 2)
        Path(manifests[0]).write_text(json.dumps({'schema': fs.SCHEMA, 'sha256': '0' * 64}))
        self.assertEqual(self.run_main('gif', *map(str, frames.values()), '--durations-ms', '100', '100', '100',
                                       '--out', str(gif), '--manifests', *manifests)[0], 1)
        self.assertEqual(self.run_main('gif', *map(str, frames.values()), '--durations-ms', '100', '100', '100',
                                       '--out', str(gif), '--manifests', manifests[1])[0], 2)

    def test_help(self):
        for action in ('plan', 'capture', 'decode', 'gif'):
            with self.subTest(action), contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as exit_:
                fs.main([action, '--help'])
            self.assertEqual(exit_.exception.code, 0)


if __name__ == '__main__':
    unittest.main()
