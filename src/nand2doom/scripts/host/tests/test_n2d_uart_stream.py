"""Hardware-free tests of the streaming load host session and its plan."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
import zlib

ROOT = Path(__file__).resolve().parents[5]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from src.nand2doom.scripts.sw_build import n2d_doom as doom  # noqa: E402
from src.nand2doom.scripts.host import n2d_uart as host  # noqa: E402
from src.nand2doom.scripts.host import n2d_uart_debug_host as wire  # noqa: E402


def legacy_response(command, sequence, words=(), status=0):
    body = bytes((1, command | 128, sequence, status, len(words)))
    body += b"".join(w.to_bytes(4, "little") for w in words)
    return b"\x5a" + body + bytes((wire.crc8(body),))


def reply(kind, sequence, status=0, value=0):
    body = bytes((kind | 0x80,)) + sequence.to_bytes(2, "little") + bytes((status,))
    body += value.to_bytes(4, "little")
    return bytes((wire.STREAM_REPLY_SOF,)) + body + bytes((wire.crc8(body),))


class StreamDevice:
    """Independent device model: in-order replies, idempotent stored writes.

    Fault hooks act on the ordinal of each received write frame: `drop` loses
    the frame silently, `checksum` answers CRC failure, `status` answers a
    chosen status, `lose_reply` withholds a reply, `noise` precedes a reply
    with garbage bytes.
    """

    def __init__(self, *, drop=(), checksum=(), status=None, lose_reply=(), noise=(),
                 corrupt_memory=None, fragment=7):
        self.memory = bytearray(b"\xcc" * 0x10000)
        self.drop, self.checksum, self.lose_reply, self.noise = set(drop), set(checksum), set(lose_reply), set(noise)
        self.status = status or {}
        self.corrupt_memory = corrupt_memory
        self.fragment = fragment
        self.pending = bytearray()
        self.legacy, self.frames = [], []
        self.writes = 0
        self.committed = 0
        self.halted = False
        self.closed = False

    def write(self, packet):
        if packet[0] == wire.REQUEST_SOF:
            command, sequence = packet[2], packet[3]
            self.legacy.append(command)
            words = ()
            if command == wire.CMD_PING:
                words = (0x3144324E, int(self.halted))
            elif command == wire.CMD_HALT:
                self.halted = True
            elif command == wire.CMD_RESUME:
                self.halted = False
            self.pending += legacy_response(command, sequence, words)
            return len(packet)
        assert packet[0] == wire.STREAM_SOF
        kind = packet[1]
        sequence = int.from_bytes(packet[2:4], "little")
        address = int.from_bytes(packet[4:8], "little")
        length = int.from_bytes(packet[8:12], "little")
        assert wire.crc8(packet[1:12]) == packet[12]
        assert zlib.crc32(packet[1:-4]) == int.from_bytes(packet[-4:], "little")
        payload = packet[13:-4]
        self.frames.append(kind)
        if kind == wire.STREAM_HELLO:
            self.pending += reply(kind, sequence, value=wire.STREAM_IDENTITY)
        elif kind == wire.STREAM_WRITE:
            ordinal = self.writes
            self.writes += 1
            if ordinal in self.drop:
                return len(packet)
            status = wire.ST_CHECKSUM if ordinal in self.checksum else self.status.get(ordinal, 0)
            if not status:
                assert self.halted and len(payload) == length
                self.memory[address:address + length] = payload
                self.committed += 1
            if ordinal in self.noise:
                self.pending += b"\x7a\x00\x13"
            if ordinal not in self.lose_reply:
                self.pending += reply(kind, sequence, status, address)
        elif kind == wire.STREAM_DIGEST:
            if self.corrupt_memory is not None:
                self.memory[self.corrupt_memory] ^= 1
                self.corrupt_memory = None
            seed = int.from_bytes(payload, "little")
            value = wire.stream_digest(bytes(self.memory[address:address + length]), seed)
            self.pending += reply(kind, sequence, value=value)
        elif kind == wire.STREAM_END:
            self.pending += reply(kind, sequence, value=self.committed)
        return len(packet)

    def read(self, count):
        count = min(count, self.fragment)
        data = bytes(self.pending[:count])
        del self.pending[:count]
        return data

    def close(self):
        self.closed = True


def make_client(device):
    now = [0.0]

    def sleep(seconds):
        now[0] += max(seconds, 1e-4)
    return host.Client(device, 1, clock=lambda: now[0], sleep=sleep)


MANIFEST = host.Manifest(0x100, (
    host.Segment(0x0100, bytes((i * 7) & 0xFF for i in range(2500)), "code"),
    host.Segment(0x2000, bytes((i * 13 + 1) & 0xFF for i in range(4099)), "data"),
    host.Segment(0x8000, b"tail!", "tail"),
))


class StreamHostTests(unittest.TestCase):
    def load(self, device, **kwargs):
        client = make_client(device)
        host.stream_load(client, MANIFEST, baud=3_000_000, report=lambda _: None, **kwargs)
        return client

    def assert_image(self, device):
        for segment in MANIFEST.segments:
            self.assertEqual(bytes(device.memory[segment.address:segment.address + len(segment.data)]),
                             segment.data)
        self.assertEqual(device.memory[0x8005:0x8010], b"\xcc" * 11)

    def test_frames_are_aligned_bounded_and_cover_every_byte(self):
        chunks = host.stream_chunks(MANIFEST)
        self.assertEqual(sum(len(data) for _, data in chunks), sum(len(s.data) for s in MANIFEST.segments))
        self.assertTrue(all(address % 4 == 0 and 1 <= len(data) <= 1024 for address, data in chunks))
        self.assertEqual(len(chunks), 3 + 5 + 1)

    def test_clean_load_order_digest_and_explicit_resume(self):
        device = StreamDevice()
        self.load(device, resume=True)
        self.assert_image(device)
        self.assertEqual(device.legacy, [wire.CMD_HALT, wire.CMD_RESET_PC, wire.CMD_RESUME])
        self.assertEqual(device.frames[0], wire.STREAM_HELLO)
        self.assertEqual(device.frames[-1], wire.STREAM_END)
        self.assertEqual(device.frames.count(wire.STREAM_DIGEST), 3)
        self.assertEqual(device.frames.count(wire.STREAM_WRITE), 9)

    def test_link_faults_are_recovered_by_idempotent_resend(self):
        device = StreamDevice(drop={1}, checksum={3}, lose_reply={5}, noise={2, 6},
                              status={7: wire.ST_MEMORY})
        self.load(device)
        self.assert_image(device)
        self.assertGreater(device.writes, 9)
        self.assertNotIn(wire.CMD_RESUME, device.legacy)

    def test_lost_tail_reply_resends_after_window_timeout(self):
        device = StreamDevice(lose_reply={8})
        self.load(device)
        self.assert_image(device)
        self.assertEqual(device.writes, 10)

    def test_protocol_rejection_stops_without_reset_or_resume(self):
        device = StreamDevice(status={2: wire.ST_RANGE})
        client = make_client(device)
        with self.assertRaisesRegex(host.StreamError, "status 0x05"):
            host.stream_load(client, MANIFEST, baud=3_000_000, resume=True, report=lambda _: None)
        self.assertTrue(client.failed)
        self.assertEqual(device.legacy, [wire.CMD_HALT])

    def test_retry_bound_and_stored_digest_mismatch_fail(self):
        device = StreamDevice(checksum=set(range(100)))
        with self.assertRaisesRegex(host.StreamError, "failed 8 times"):
            self.load(device)
        device = StreamDevice(corrupt_memory=0x2000 + 77)
        with self.assertRaisesRegex(host.StreamError, "stored digest mismatch in segment data"):
            self.load(device)
        self.assertEqual(device.legacy, [wire.CMD_HALT])

    def test_wrong_identity_is_refused(self):
        device = StreamDevice()
        original = device.write

        def impostor(packet):
            result = original(packet)
            if packet[0] == wire.STREAM_SOF and packet[1] == wire.STREAM_HELLO:
                del device.pending[-10:]
                device.pending += reply(wire.STREAM_HELLO, int.from_bytes(packet[2:4], "little"),
                                        value=0x12345678)
            return result
        device.write = impostor
        with self.assertRaisesRegex(host.StreamError, "N2S1"):
            self.load(device)

    def test_codec_rejects_malformed_frames_and_replies(self):
        with self.assertRaises(ValueError):
            wire.stream_write_frame(0, 2, b"abcd")
        with self.assertRaises(ValueError):
            wire.stream_write_frame(0, 0, bytes(1025))
        with self.assertRaises(ValueError):
            wire.stream_write_frame(0, 0x03FFFFFC, bytes(8))
        with self.assertRaises(ValueError):
            wire.stream_frame(wire.STREAM_END, 0, length=1)
        with self.assertRaises(ValueError):
            wire.stream_frame(0x99, 0)
        good = reply(wire.STREAM_WRITE, 3, 0, 0x40)
        self.assertEqual(wire.decode_stream_reply(good), wire.StreamReply(wire.STREAM_WRITE, 3, 0, 0x40))
        with self.assertRaises(ValueError):
            wire.decode_stream_reply(good[:-1] + bytes((good[-1] ^ 1,)))

    def test_chained_digest_equals_whole_image_crc32(self):
        image = b"".join(s.data for s in MANIFEST.segments)
        seed = 0xFFFFFFFF
        for segment in MANIFEST.segments:
            seed = wire.stream_digest(segment.data, seed)
        self.assertEqual(seed ^ 0xFFFFFFFF, zlib.crc32(image))

    def test_doom_manifest_wire_time_model(self):
        firmware = 512 * 1024
        size = doom.WAD_BYTE_LENGTH + firmware
        frames = -(-doom.WAD_BYTE_LENGTH // 1024) + firmware // 1024
        seconds = wire.stream_wire_seconds(size, 3_000_000, frames=frames)
        self.assertAlmostEqual(seconds, (size + frames * 17) * 10 / 3_000_000)
        self.assertLess(seconds, 100.0)
        self.assertGreater(wire.stream_wire_seconds(size, 1_000_000, frames=frames), 290.0)

    def test_stream_plan_and_live_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "image.bin").write_bytes(bytes(range(256)) * 9)
            import hashlib
            data = (path / "image.bin").read_bytes()
            manifest = {"schema_version": 1, "architecture": host.image.ARCH, "abi": host.image.ABI,
                        "endianness": "little", "memory_map_revision": host.image.MEMORY_MAP_REVISION,
                        "entry_address": 0, "segments": [{"address": 0, "byte_length": len(data),
                        "file": "image.bin", "sha256": hashlib.sha256(data).hexdigest()}]}
            (path / "manifest.json").write_text(json.dumps(manifest))
            output = subprocess.run(
                [sys.executable, "-S", "-B", str(ROOT / "src/nand2doom/scripts/host/n2d_uart.py"),
                 "--baud", "3000000", "--dry-run", "load", str(path / "manifest.json"), "--stream"],
                capture_output=True, text=True, check=True).stdout
            expected = wire.stream_wire_seconds(len(data), 3_000_000, frames=3)
            self.assertIn(f"stream wire time {expected:.1f}s at 3000000 baud", output)
            self.assertIn("3 stream frames", output)
            for bad in (["--stream", "--bulk", "--verify"], ["--stream", "--verify"]):
                status = subprocess.run(
                    [sys.executable, "-S", "-B", str(ROOT / "src/nand2doom/scripts/host/n2d_uart.py"),
                     "--dry-run", "load", str(path / "manifest.json"), *bad],
                    capture_output=True, text=True).returncode
                self.assertEqual(status, 2)
            device = StreamDevice()
            opened = []

            def opener(port, timeout, baud):
                opened.append(baud)
                return device
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                status = host.main(["--port", "FAKE", "--baud", "3000000", "load",
                                    str(path / "manifest.json"), "--stream"], opener=opener)
            self.assertEqual((status, opened), (0, [3_000_000]))
            self.assertEqual(bytes(device.memory[:len(data)]), data)
            self.assertEqual(device.legacy, [wire.CMD_PING, wire.CMD_HALT, wire.CMD_RESET_PC])
            self.assertTrue(device.closed)


class BaudOptionTests(unittest.TestCase):
    """Every n2d host tool opens the port at the loaded image's rate."""

    def test_board_names_and_integers(self):
        self.assertEqual(host.baud_value("de2_115"), 3_000_000)
        self.assertEqual(host.baud_value("de10_lite"), 1_000_000)
        self.assertEqual(host.baud_value("3000000"), 3_000_000)
        for bad in ("fast", "100", "0x10000000"):
            with self.assertRaises(Exception):
                host.baud_value(bad)
        self.assertEqual(host.CONFIG["board_baudrates"]["de2_115"], 3_000_000)

    def test_control_and_capture_pass_the_rate_to_the_opener(self):
        from src.nand2doom.scripts.host import n2d_control as control
        from src.nand2doom.scripts.host import n2d_capture as capture
        opened = []

        def opener(port, timeout, baud):
            opened.append(baud)
            raise OSError("fixture stops after open")
        session = control.Session("PORT", .01, 3_000_000, opener=opener)
        with self.assertRaises(Exception):
            session.connect()
        self.assertEqual(opened, [3_000_000])
        worker_args = []
        control.Worker("PORT", .01, 3_000_000, factory=lambda *args: worker_args.append(args))
        plan = subprocess.run([sys.executable, "-S", "-B", str(ROOT / "src/nand2doom/scripts/host/n2d_control.py"),
                               "--baud", "de2_115", "--plan"], capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(plan.stdout)["baud"], 3_000_000)
        args = capture.parser().parse_args(["--manifest", "m", "--output", "o", "--hardware-image", "h",
                                            "--baud", "de2_115"])
        self.assertEqual(args.baud, 3_000_000)
        self.assertEqual(capture.parser().parse_args(
            ["--manifest", "m", "--output", "o", "--hardware-image", "h"]).baud, 1_000_000)


class OwnedHandleTests(unittest.TestCase):
    """The CLI closes its handle once; a close failure exits 3."""

    def run_ping(self, device):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return host.main(["--port", "FAKE", "ping"], opener=lambda *args: device)

    def test_close_failure_overrides_success_and_interrupt_propagates(self):
        device = StreamDevice()
        self.assertEqual(self.run_ping(device), 0)
        self.assertTrue(device.closed)
        device = StreamDevice()
        device.close = unittest.mock.Mock(side_effect=OSError("close fixture"))
        self.assertEqual(self.run_ping(device), 3)
        device.close.assert_called_once()
        device = StreamDevice()
        device.write = unittest.mock.Mock(side_effect=KeyboardInterrupt)
        with self.assertRaises(KeyboardInterrupt):
            self.run_ping(device)
        self.assertTrue(device.closed)
        device = StreamDevice()
        device.write = unittest.mock.Mock(side_effect=KeyboardInterrupt)
        device.close = unittest.mock.Mock(side_effect=OSError("close fixture"))
        self.assertEqual(self.run_ping(device), 3)


if __name__ == "__main__":
    unittest.main()
