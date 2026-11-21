"""Hardware-free contract of the UART memory endpoint host library against an
independent fake endpoint: framing, identity, chunking, the load transaction
(one hold, verify before entry, one release, failures leave the CPU held),
readback, and shared-UART project bytes."""
import unittest
import zlib

from src.common.scripts.uart_mem import uart_mem as um

PROJECT = int.from_bytes(b"n2d ", "little")


def crc8(data):
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 7) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


class FakeEndpoint:
    """Device model written from the HAS tables, independent of the codec."""

    def __init__(self, windows=((0, 0x10000, 3), (0x20000, 0x1000, 7)), max_length=256,
                 entry=0):
        self.windows, self.max_length, self.entry_addr = windows, max_length, entry
        self.memory = {}
        self.hold = False
        self.log = []
        self.out = bytearray()
        self.inbox = bytearray()
        self.corrupt = set()
        self.fail_command = {}
        self.prints = b""
        self.drop_response = False
        self.chunk = 4096

    def write(self, data):
        self.inbox += data
        self._parse()

    def read(self, size, deadline):
        size = min(size, self.chunk)
        data, self.out = bytes(self.out[:size]), self.out[size:]
        return data

    def _parse(self):
        while len(self.inbox) >= 11:
            assert self.inbox[0] == 0xA8, "host sent a byte outside a frame"
            header = bytes(self.inbox[1:10])
            assert crc8(header) == self.inbox[10]
            version, command, seq = header[0], header[1], header[2]
            address = int.from_bytes(header[3:7], "little")
            length = int.from_bytes(header[7:9], "little")
            payload = b""
            size = 11
            if command == 0x10:
                size += length + 4
                if len(self.inbox) < size:
                    return
                payload = bytes(self.inbox[11:11 + length])
                assert zlib.crc32(payload) == int.from_bytes(self.inbox[11 + length:size], "little")
            del self.inbox[:size]
            assert version == 1
            self.log.append((command, address, length))
            self._respond(command, seq, *self._execute(command, address, length, payload))

    def _allowed(self, address, length, bit):
        return any(b <= address and address + length <= b + s and a & bit
                   for b, s, a in self.windows)

    def _execute(self, command, address, length, payload):
        if command in self.fail_command:
            return b"", self.fail_command.pop(command)
        if command == 0x01:
            words = [0x4D41324E, PROJECT, (self.max_length << 16) | (len(self.windows) << 8)
                     | (int(self.hold) << 1) | int(self.hold), self.entry_addr, 64]
            for window in self.windows:
                words += list(window)
            return b"".join(w.to_bytes(4, "little") for w in words), 0
        if command == 0x02:
            self.hold = True
            return b"", 0
        if command == 0x03:
            self.hold = False
            return b"", 0
        if command == 0x04:
            return b"", 0 if self.hold and address == self.entry_addr else 5
        if length == 0 or length % 4 or length > self.max_length:
            return b"", 6
        if command == 0x10:
            if not self._allowed(address, length, 1):
                return b"", 5
            if not self.hold:
                return b"", 7
            for i, byte in enumerate(payload):
                self.memory[address + i] = byte ^ (0xFF if address + i in self.corrupt else 0)
            return b"", 0
        if command == 0x11:
            if not self._allowed(address, length, 2 if self.hold else 4):
                return b"", 7 if not self.hold and self._allowed(address, length, 2) else 5
            return bytes(self.memory.get(address + i, 0) for i in range(length)), 0
        return b"", 2

    def _respond(self, command, seq, payload, status):
        if self.drop_response:
            return
        body = bytes([1, command | 0x80, seq]) + len(payload).to_bytes(2, "little") + \
            payload + bytes([status])
        self.out += self.prints + bytes([0x8A]) + body + zlib.crc32(body).to_bytes(4, "little")


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        self.now += 0.01
        return self.now


def client(fake, **kwargs):
    return um.Client(fake, timeout=0.5, clock=FakeClock(), **kwargs)


class CodecTests(unittest.TestCase):
    def test_request_layout(self):
        frame = um.encode_request(um.WRITE, 7, 0x1234, 4, b"\x01\x02\x03\x04")
        self.assertEqual(frame[:10], bytes([0xA8, 1, 0x10, 7, 0x34, 0x12, 0, 0, 4, 0]))
        self.assertEqual(frame[10], crc8(frame[1:10]))
        self.assertEqual(frame[15:], zlib.crc32(b"\x01\x02\x03\x04").to_bytes(4, "little"))
        self.assertEqual(len(um.encode_request(um.READ, 1, 0, 64)), 11)

    def test_crc8_check_value(self):
        self.assertEqual(um.crc8(b"123456789"), 0xF4)

    def test_request_rejects_bad_fields(self):
        with self.assertRaises(ValueError):
            um.encode_request(um.READ, 1, 0, 4, b"abcd")
        with self.assertRaises(ValueError):
            um.encode_request(um.READ, 256)

    def test_response_round_trip(self):
        frame = um.encode_response(um.READ, 3, b"abcd", 0)
        self.assertEqual(frame[0], 0x8A)
        self.assertEqual(zlib.crc32(frame[1:-4]), int.from_bytes(frame[-4:], "little"))


class ClientTests(unittest.TestCase):
    def test_identify(self):
        fake = FakeEndpoint()
        identity = client(fake).identify()
        self.assertEqual(identity.project, "n2d ")
        self.assertEqual(identity.max_length, 256)
        self.assertEqual(identity.live_gap_cycles, 64)
        self.assertEqual(identity.windows[1], um.Window(0x20000, 0x1000, 7))
        self.assertFalse(identity.held)

    def test_bad_magic_and_mismatched_project(self):
        with self.assertRaises(um.EndpointError):
            um.parse_identity(bytes(20))
        fake = FakeEndpoint()
        with self.assertRaises(um.EndpointError):
            um.load(client(fake), [um.Segment(0, b"abcd")], 0, project_id=0x1234)
        self.assertFalse(any(c == 0x02 for c, _, _ in fake.log), "held a mismatched image")

    def test_read_chunks_by_max_length(self):
        fake = FakeEndpoint()
        fake.memory = {0x20000 + i: i & 0xFF for i in range(1000)}
        c = client(fake)
        self.assertEqual(c.read(0x20000, 1000), bytes(i & 0xFF for i in range(1000)))
        self.assertEqual([l for cmd, _, l in fake.log if cmd == 0x11], [256, 256, 256, 232])

    def test_status_error_poisons(self):
        fake = FakeEndpoint()
        c = client(fake)
        with self.assertRaises(um.StatusError) as caught:
            c.read(0, 4)  # Why: the program window is not live-readable
        self.assertEqual(caught.exception.status, 7)
        with self.assertRaises(um.EndpointError):
            c.hold()
        self.assertNotIn(0x02, [cmd for cmd, _, _ in fake.log])

    def test_project_bytes_before_response(self):
        fake = FakeEndpoint()
        seen = []
        c = client(fake, on_project_bytes=seen.append)
        fake.prints = b"hello "
        c.identify()
        self.assertEqual(b"".join(seen), b"hello ")

    def test_false_sofs_before_a_valid_response(self):
        # Why: a bare 0x8a, a huge bogus length and a bad-CRC candidate are each
        # project data and must not end the search for the real reply.
        bogus = bytes([0x8A, 0x00, 0x00, 0x00, 0xFF, 0xFF])
        fake_header = bytearray(um.encode_response(um.HOLD, 1, b"", 0))
        fake_header[-1] ^= 0xFF
        for chunk in (4096, 1):
            fake = FakeEndpoint()
            fake.chunk = chunk
            fake.prints = b"a\x8az" + bogus + bytes(fake_header) + b"tail"
            seen = []
            c = client(fake, on_project_bytes=seen.append)
            c.timeout = 100.0
            c.hold()
            self.assertTrue(fake.hold)
            self.assertEqual(b"".join(seen), fake.prints, f"chunk {chunk}")

    def test_incomplete_plausible_candidate_before_identify(self):
        # Why: these bytes form a plausible Identify header (version 1, 0x81,
        # sequence 1, length 116) that never completes; it must not swallow the
        # real reply that follows.
        for chunk in (4096, 1):
            fake = FakeEndpoint()
            fake.chunk = chunk
            fake.prints = bytes([0x8A, 0x01, 0x81, 0x01, 0x74, 0x00])
            seen = []
            c = client(fake, on_project_bytes=seen.append)
            c.timeout = 100.0
            identity = c.identify()
            self.assertEqual(identity.project, "n2d ")
            self.assertEqual(b"".join(seen), fake.prints, f"chunk {chunk}")

    def test_nested_response_in_a_fragmented_read_payload(self):
        # Why: memory can hold any bytes, so a response nested in a read payload
        # must stay data, with and without a false incomplete prefix before it.
        nested = um.encode_response(um.READ, 2, b"abcd", 0)
        block = (nested + bytes(64))[:64]
        for prefix in (b"", bytes([0x8A, 0x01, 0x91, 0x02, 0x40, 0x00])):
            fake = FakeEndpoint()
            fake.chunk = 1
            fake.memory = {0x20000 + i: b for i, b in enumerate(block)}
            seen = []
            c = client(fake, on_project_bytes=seen.append)
            c.timeout = 1000.0
            c.identify()
            fake.prints = prefix
            self.assertEqual(c.read(0x20000, 64), block, prefix.hex())
            self.assertEqual(b"".join(seen), prefix)

    def test_corrupt_response_and_timeout(self):
        fake = FakeEndpoint()
        fake.drop_response = True
        c = client(fake)
        frame = bytearray(um.encode_response(um.IDENTIFY, 1, b"", 0))
        frame[-1] ^= 1
        fake.out += frame
        with self.assertRaisesRegex(um.EndpointError, "bad CRC"):
            c.identify()
        fake = FakeEndpoint()
        fake.drop_response = True
        c = client(fake)
        with self.assertRaisesRegex(um.EndpointError, "timeout"):
            c.hold()
        with self.assertRaisesRegex(um.EndpointError, "poisoned"):
            c.release()

    def test_sequence_mismatch(self):
        fake = FakeEndpoint()
        fake.drop_response = True
        c = client(fake)
        fake.out += um.encode_response(um.HOLD, 9, b"", 0)
        # Why: a stale reply must never be taken for this request's, so it is
        # skipped as project data and the request times out.
        with self.assertRaisesRegex(um.EndpointError, "timeout"):
            c.hold()


class LoadTests(unittest.TestCase):
    def segments(self):
        return [um.Segment(0x0000, bytes(range(256)) * 3),
                um.Segment(0x8000, b"tail-7b"),
                um.Segment(0x20000, b"\x55" * 64)]

    def test_multi_segment_load(self):
        fake = FakeEndpoint()
        fake.memory[0x8007] = 0xEE  # Why: the tail's last word is written whole, so this byte checks it survives
        um.load(client(fake), self.segments(), 0, project_id=PROJECT)
        commands = [c for c, _, _ in fake.log]
        self.assertEqual(commands.count(0x02), 1)
        self.assertEqual(commands.count(0x03), 1)
        self.assertEqual(commands[-2:], [0x04, 0x03], "entry then release, last")
        hold = commands.index(0x02)
        writes = [i for i, c in enumerate(commands) if c == 0x10]
        self.assertTrue(all(i > hold for i in writes))
        for segment in self.segments():
            got = bytes(fake.memory[segment.address + i] for i in range(len(segment.data)))
            self.assertEqual(got, segment.data)
        self.assertEqual(fake.memory[0x8007], 0xEE)
        self.assertFalse(fake.hold)

    def test_every_segment_verified_before_entry(self):
        fake = FakeEndpoint()
        um.load(client(fake), self.segments(), 0)
        entry = [c for c, _, _ in fake.log].index(0x04)
        for segment in self.segments():
            reads = [i for i, (c, a, _) in enumerate(fake.log)
                     if c == 0x11 and a == segment.address]
            self.assertTrue(reads and reads[-1] < entry)

    def test_later_segment_verify_failure_leaves_cpu_held(self):
        fake = FakeEndpoint()
        fake.corrupt = {0x20010}
        with self.assertRaises(um.LoadError) as caught:
            um.load(client(fake), self.segments(), 0)
        self.assertEqual(caught.exception.segment, 2)
        commands = [c for c, _, _ in fake.log]
        self.assertNotIn(0x03, commands)
        self.assertNotIn(0x04, commands)
        self.assertTrue(fake.hold)

    def test_device_error_mid_load_names_segment(self):
        fake = FakeEndpoint()
        original = fake._execute
        writes = []

        def failing(command, address, length, payload):
            if command == 0x10:
                writes.append(address)
                if len(writes) == 4:  # Why: segment 1 starts after three segment-0 chunks
                    return b"", 3
            return original(command, address, length, payload)
        fake._execute = failing
        with self.assertRaises(um.LoadError) as caught:
            um.load(client(fake), self.segments(), 0)
        self.assertEqual(caught.exception.segment, 1)
        self.assertIn("checksum", str(caught.exception))
        self.assertTrue(fake.hold)
        self.assertNotIn(0x03, [c for c, _, _ in fake.log])

    def test_segment_outside_writable_window_is_refused_before_hold(self):
        fake = FakeEndpoint()
        with self.assertRaises(um.LoadError):
            um.load(client(fake), [um.Segment(0x30000, b"abcd")], 0)
        self.assertEqual([c for c, _, _ in fake.log], [0x01])

    def test_live_readback_needs_no_hold(self):
        fake = FakeEndpoint()
        fake.memory = {0x20000 + i: 7 for i in range(64)}
        c = client(fake)
        self.assertEqual(um.read_window(c, 0x20000, 64), b"\x07" * 64)
        self.assertNotIn(0x02, [cmd for cmd, _, _ in fake.log])


if __name__ == "__main__":
    unittest.main()
