"""The host cartridge loader: CRC check value, frame encoding and decoding,
chunking, the stop-and-wait session against a fake device, and the dump
transport."""
import contextlib
import io
from pathlib import Path
import tempfile
import unittest

from src.nand2mario.scripts import cart_loader as loader


class FakeDevice:
    """A loader-protocol device over a byte queue, like a serial port: each
    request appends its response to `pending`, and the next response can be
    dropped, delayed until after the next request, truncated, CRC-corrupted,
    given a corrupted SYNC, or have its echo rewritten (CRC kept valid);
    `inject` queues arbitrary bytes (stale frames, line noise)."""

    def __init__(self):
        self.held = False
        self.rom = bytearray(loader.ROM_BYTES)
        self.ram = bytearray(loader.RAM_BYTES)
        self.pending = b''
        self.drop_next = self.corrupt_next = self.truncate_next = self.bad_sync_next = False
        self.delay_next = False
        self.delayed = b''
        self.echo_next = None
        self.requests = []

    @staticmethod
    def frame_for(status, command, echo, data=b''):
        body = bytes([status, command]) + bytes(echo) + bytes(data)
        crc = loader.crc16(body)
        return bytes([loader.SYNC]) + body + bytes([crc & 0xFF, crc >> 8])

    def inject(self, data):
        self.pending += bytes(data)

    def write(self, frame):
        self.requests.append(bytes(frame))
        body, crc = frame[1:-2], frame[-1] << 8 | frame[-2]
        command, address, length = body[0], body[1] | body[2] << 8 | body[3] << 16, body[4] + 1
        ram, offset = address >> loader.ADDRESS_RAM_BIT & 1, address & (loader.ROM_BYTES - 1)
        store = self.ram if ram else self.rom
        if loader.crc16(body) != crc:
            code = loader.STATUS_BAD_CRC
        elif command in (loader.CMD_WRITE, loader.CMD_READ) and not self.held:
            code = loader.STATUS_NOT_HELD
        else:
            code = loader.STATUS_OK
        data = b''
        if code == loader.STATUS_OK:
            if command == loader.CMD_HOLD:
                self.held = True
            elif command == loader.CMD_RELEASE:
                self.held = False
            elif command == loader.CMD_WRITE:
                store[offset:offset + length] = body[5:]
            elif command == loader.CMD_READ:
                data = bytes(store[offset:offset + length])
        echo = bytes(body[1:5])
        if self.echo_next is not None:
            command_echo, echo = self.echo_next(command, echo)
            self.echo_next = None
        else:
            command_echo = command
        response = self.frame_for((self.held << 7) | 0x40 | code, command_echo, echo, data)
        if self.corrupt_next:
            response = response[:-1] + bytes([response[-1] ^ 1])
            self.corrupt_next = False
        if self.truncate_next:
            response = response[:-1]
            self.truncate_next = False
        if self.bad_sync_next:
            response = bytes([0x5A]) + response[1:]
            self.bad_sync_next = False
        if self.drop_next:
            response = b''
            self.drop_next = False
        self.pending += self.delayed
        self.delayed = b''
        if self.delay_next:
            self.delayed, response = response, b''
            self.delay_next = False
        self.pending += response

    def read(self, count):
        data, self.pending = self.pending[:count], self.pending[count:]
        return data


class CartLoaderTests(unittest.TestCase):
    def test_crc_check_value(self):
        self.assertEqual(loader.crc16(b'123456789'), 0x29B1)
        self.assertEqual(loader.crc16(b''), 0xFFFF)

    def test_encode_and_decode(self):
        frame = loader.encode_request(loader.CMD_WRITE, loader.address_field(0x1234), b'\x01\x02\x03')
        self.assertEqual(frame[:6], bytes([0xA5, 0x04, 0x34, 0x12, 0x00, 0x02]))
        self.assertEqual(frame[6:9], b'\x01\x02\x03')
        self.assertEqual(len(frame), 11)
        read = loader.encode_request(loader.CMD_READ, loader.address_field(0x7FFF, ram=True), length=256)
        self.assertEqual(read[1:6], bytes([0x05, 0xFF, 0x7F, 0x20, 0xFF]))
        with self.assertRaises(ValueError):
            loader.encode_request(loader.CMD_WRITE, 0, b'')
        with self.assertRaises(ValueError):
            loader.encode_request(loader.CMD_PING, 0, b'\x00')
        with self.assertRaises(ValueError):
            loader.address_field(loader.RAM_BYTES, ram=True)
        body = bytes([0x80 | 0x40, loader.CMD_READ, 0x00, 0x00, 0x20, 0x01, 0xAA, 0xBB])
        crc = loader.crc16(body)
        response = loader.decode_response(bytes([0xA5]) + body + bytes([crc & 0xFF, crc >> 8]))
        self.assertTrue(response.ok and response.held and response.initialized and response.ram)
        self.assertEqual((response.offset, response.length, response.payload), (0, 2, b'\xaa\xbb'))
        self.assertEqual(loader.response_total_length(bytes([0xA5]) + body[:6]), 11)
        bad = loader.decode_response(bytes([0xA5]) + body + bytes([(crc & 0xFF) ^ 1, crc >> 8]))
        self.assertFalse(bad.crc_ok)
        error_body = bytes([0x21 | 0x08, loader.CMD_WRITE, 1, 2, 3, 4])
        crc = loader.crc16(error_body)
        error = loader.decode_response(bytes([0xA5]) + error_body + bytes([crc & 0xFF, crc >> 8]))
        self.assertEqual((error.code, error.framing, error.overrun, error.payload), (loader.STATUS_BAD_CRC, True, True, b''))
        self.assertIn('bad-crc flags=--f-o', error.describe())

    def test_snapshot_commands_and_codes(self):
        self.assertEqual((loader.CMD_SNAP_PIN, loader.CMD_SNAP_RELEASE, loader.CMD_SNAP_READ), (6, 7, 8))
        self.assertEqual((loader.STATUS_NO_FRAME, loader.STATUS_NOT_PINNED), (6, 7))
        self.assertEqual(loader.SNAPSHOT_RECORD_BYTES, 8 + 160 * 144 // 4)
        frame = loader.encode_request(loader.CMD_SNAP_READ, 5512, length=256)
        self.assertEqual(frame[1:6], bytes([0x08, 0x88, 0x15, 0x00, 0xFF]))
        header = bytes([0xA5, 0x40, loader.CMD_SNAP_READ, 0, 0, 0, 7])
        self.assertEqual(loader.response_total_length(header), 7 + 8 + 2)
        self.assertEqual(loader.response_total_length(header[:1] + bytes([0x40 | loader.STATUS_NOT_PINNED]) + header[2:]), 9)
        self.assertEqual(loader.response_total_length(bytes([0xA5, 0x40, loader.CMD_SNAP_PIN, 0, 0, 0, 0])), 9)
        # NO_FRAME and NOT_PINNED are final: a retry cannot change them.
        for code in (loader.STATUS_NO_FRAME, loader.STATUS_NOT_PINNED):
            device = FakeDevice()
            body = bytes([0x40 | code, loader.CMD_SNAP_PIN, 0, 0, 0, 0])
            device.write = lambda frame, body=body, device=device: (device.requests.append(frame), device.inject(
                FakeDevice.frame_for(body[0], body[1], body[2:])))
            with self.assertRaises(loader.LoaderError):
                loader.Session(device, retry_pause=0).snap_pin()
            self.assertEqual(len(device.requests), 1, 'not retried')
        # The dump transport fabricates a SNAP_READ payload of the asked length.
        with tempfile.TemporaryDirectory() as temporary:
            dump = loader.DumpTransport(Path(temporary) / 'frames.bin')
            self.assertEqual(loader.Session(dump).snap_read(0, 8), bytes(8))

    def test_chunks(self):
        pieces = loader.chunks(bytes(range(256)) * 2 + b'\x7f')
        self.assertEqual([(offset, len(data)) for offset, data in pieces], [(0, 256), (256, 256), (512, 1)])

    def test_session_load_and_retry(self):
        device = FakeDevice()
        image = bytes((i * 7) & 0xFF for i in range(1000))
        session = loader.Session(device, retries=2)
        self.assertEqual(session.load_image(image), 1000)
        self.assertEqual(bytes(device.rom[:1000]), image)
        self.assertFalse(device.held)
        commands = [frame[1] for frame in device.requests]
        self.assertEqual(commands, [loader.CMD_HOLD] + [loader.CMD_WRITE] * 4 + [loader.CMD_READ] * 4 + [loader.CMD_RELEASE])
        # A dropped and a corrupted response are retried; a NOT_HELD is final.
        device.requests.clear()
        device.drop_next = True
        self.assertTrue(session.ping().ok)
        device.corrupt_next = True
        self.assertTrue(session.ping().ok)
        self.assertEqual(len(device.requests), 4)
        with self.assertRaises(loader.LoaderError):
            session.write(0, b'\x00')
        self.assertEqual(len(device.requests), 5)
        device.held = True
        session.write(5, b'\x11\x22', ram=True)
        self.assertEqual(session.read(5, 2, ram=True), b'\x11\x22')
        self.assertEqual(bytes(device.rom[5:7]), image[5:7])

    def session(self, device, retries=2):
        return loader.Session(device, retries=retries, retry_pause=0)

    def test_truncated_tail_is_retried_with_framing_restored(self):
        device = FakeDevice()
        session = self.session(device)
        device.truncate_next = True
        self.assertTrue(session.ping().ok)
        self.assertEqual(len(device.requests), 2, 'the truncated CRC tail was retried')
        self.assertEqual(device.pending, b'', 'nothing left misframed in the queue')
        # A truncated tail followed at once by the next frame misframes the
        # read; the CRC rejects it, the drain restores framing, the retry
        # succeeds and the following command is not confused.
        device.held = True
        device.truncate_next = True
        session.write(0x10, b'\x11\x22')
        self.assertEqual(bytes(device.rom[0x10:0x12]), b'\x11\x22')
        self.assertEqual(session.read(0x10, 2), b'\x11\x22')
        self.assertEqual(device.pending, b'')

    def test_corrupted_header_is_retried(self):
        device = FakeDevice()
        session = self.session(device)
        device.bad_sync_next = True
        self.assertTrue(session.ping().ok)
        self.assertEqual(len(device.requests), 2)
        # Line noise before a good frame is skipped by the SYNC search.
        device.inject(b'\x00\x5a\xff')
        self.assertTrue(session.hold().ok)
        self.assertEqual(len(device.requests), 3, 'noise before the frame needs no retry')
        # Noise without any SYNC within the bound is a transport error, not a crash.
        noise = type('Noise', (), {'write': lambda self, f: None, 'read': lambda self, n: b'\x5a' * n})()
        with self.assertRaises(loader.LoaderError):
            loader.Session(noise, retries=1, retry_pause=0).ping()

    def test_mismatched_echo_is_never_accepted(self):
        device = FakeDevice()
        device.held = True
        session = self.session(device, retries=1)
        rewrites = (lambda c, e: (loader.CMD_READ, e),                       # command
                    lambda c, e: (c, bytes([e[0] ^ 1]) + e[1:]),             # address low byte
                    lambda c, e: (c, e[:2] + bytes([e[2] ^ 0x20]) + e[3:]),  # RAM bit
                    lambda c, e: (c, e[:3] + bytes([e[3] ^ 1])))             # length
        for rewrite in rewrites:
            with self.subTest(rewrite=rewrite):
                device.requests.clear()
                device.echo_next = rewrite
                session.write(0x40, b'\x99\x98')
                self.assertEqual(len(device.requests), 2, 'the mismatched echo was discarded and the write retried')
        # Every reply mismatching exhausts the bounded retries.
        always = FakeDevice()
        always.held = True
        original = always.write

        def write(frame):
            always.echo_next = rewrites[1]
            original(frame)
        always.write = write
        with self.assertRaises(loader.LoaderError):
            self.session(always).write(0x40, b'\x01')
        self.assertEqual(len(always.requests), 3)
        # A BAD_CRC answer's echo is exempt: it is reported, then retried.
        bad = loader.encode_request(loader.CMD_PING)
        self.assertEqual(self.session(FakeDevice()).exchange(bad[:-1] + bytes([bad[-1] ^ 1])).code,
                         loader.STATUS_BAD_CRC)

    def test_stale_write_ack_does_not_complete_a_later_write(self):
        device = FakeDevice()
        device.held = True
        session = self.session(device)
        # The first WRITE's reply is delayed past its timeout: the retry
        # resends it and both replies arrive; the duplicate stays queued.
        device.delay_next = True
        session.write(0, b'A')
        self.assertEqual(len(device.requests), 2)
        self.assertTrue(device.pending, 'a duplicate ACK for offset 0 is still queued')
        # The next WRITE reads that stale ACK first; it must be discarded and
        # the real reply for offset 256 awaited.
        session.write(256, b'B')
        self.assertEqual((device.rom[0], device.rom[256]), (ord('A'), ord('B')))
        self.assertEqual(device.pending, b'')
        # A stale ACK with the real reply lost: never reported as success
        # without a retry that actually reaches the device.
        device.inject(device.frame_for(0xC0, loader.CMD_WRITE, loader.address_field(0).to_bytes(3, 'little') + b'\x00'))
        device.drop_next = True
        before = len(device.requests)
        session.write(512, b'C')
        self.assertEqual(device.rom[512], ord('C'))
        self.assertEqual(len(device.requests), before + 2)
        device.inject(device.frame_for(0xC0, loader.CMD_WRITE, loader.address_field(0).to_bytes(3, 'little') + b'\x00'))
        device.drop_next = True
        with self.assertRaises(loader.LoaderError):
            self.session(device, retries=0).write(768, b'D')

    def test_hold_does_not_accept_a_ping_reply(self):
        device = FakeDevice()
        session = self.session(device)
        device.inject(device.frame_for(0x40, loader.CMD_PING, b'\x00\x00\x00\x00'))
        response = session.hold()
        self.assertEqual(response.command, loader.CMD_HOLD)
        self.assertTrue(response.held and device.held)
        device.held = False
        device.inject(device.frame_for(0x40, loader.CMD_PING, b'\x00\x00\x00\x00'))
        device.drop_next = True
        with self.assertRaises(loader.LoaderError):
            self.session(device, retries=0).hold()

    def test_stale_frames_are_bounded(self):
        device = FakeDevice()
        stale = device.frame_for(0x40, loader.CMD_PING, b'\x00\x00\x00\x00')
        device.inject(stale * (loader.STALE_FRAME_LIMIT + 1))
        with self.assertRaises(loader.LoaderError):
            self.session(device, retries=0).hold()
        # The next attempt drains the leftovers and recovers.
        device.inject(stale * 3)
        self.assertTrue(self.session(device).hold().held)

    def test_dump_transport_and_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            image, dump = Path(directory, 'game.gb'), Path(directory, 'frames.bin')
            image.write_bytes(bytes(300))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(loader.main(['--image', str(image), '--dump', str(dump), '--no-verify']), 0)
            frames = dump.read_bytes()
            self.assertEqual(frames[:2], bytes([0xA5, loader.CMD_HOLD]))
            self.assertEqual(frames.count(bytes([0xA5, loader.CMD_WRITE])), 2)
            self.assertTrue(frames.endswith(loader.encode_request(loader.CMD_RELEASE)))
            self.assertIn('300 bytes loaded', out.getvalue())
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(loader.main(['--image', str(image), '--port', '/dev/null/none']), 2)
                with self.assertRaises(SystemExit):
                    loader.main(['--image', str(image)])


if __name__ == '__main__':
    unittest.main()
