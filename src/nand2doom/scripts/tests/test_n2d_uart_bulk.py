"""Independent CRC32 packets and stop-and-wait bulk transport boundaries."""
import struct
import sys
from pathlib import Path
import unittest
import zlib
import tempfile
from unittest.mock import Mock, patch
from contextlib import redirect_stdout, redirect_stderr
import io

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import n2d_uart as host
import n2d_uart_debug_host as wire


def reply(command, sequence, address, completed, payload=b"", status=0):
    body = struct.pack("<BBBBIHH", 2, command | 128, sequence, status,
                       address, len(payload), completed) + payload
    return b"\x6a" + body + struct.pack("<I", zlib.crc32(body))


class Transport:
    def __init__(self):
        self.memory = bytearray(131072)
        self.requests = []
        self.pending = b""
        self.change = lambda packet: packet
        self.short = False
        self.now = 0.0
        self.halted = False

    def write(self, packet):
        assert not self.pending
        self.requests.append(packet)
        self.now += len(packet) * 10 / 1_000_000 + .016
        command, sequence = packet[2:4]
        if packet[0] == 0xa5:
            address = int.from_bytes(packet[4:8], "little")
            words = ()
            if command == 5:
                words = (0x3242324e, 4096, 3)
            elif command == 1:
                words = (0x3144324e, int(self.halted))
            elif command == 2:
                self.halted = True
            elif command == 3:
                self.halted = False
            elif command == 0x10:
                words = tuple(int.from_bytes(self.memory[i:i+4], "little")
                              for i in range(address, address+packet[8]*4, 4))
            elif command in (0x11, 0x12):
                for i, byte in enumerate(packet[10:-1]):
                    if packet[9] & (1 << (i%4)):
                        self.memory[address+i] = byte
            else:
                assert command == 4
            body = bytes((1, command | 128, sequence, 0, len(words)))
            body += b"".join(struct.pack("<I", word) for word in words)
            response = b"\x5a" + body + bytes((wire.crc8(body),))
        else:
            assert packet[0:2] == b"\xa6\x02"
            assert zlib.crc32(packet[1:-4]) == int.from_bytes(packet[-4:], "little")
            address, length = struct.unpack("<IH", packet[4:10])
            if command == 0x31:
                assert len(packet[10:-4]) == length
                self.memory[address:address+length] = packet[10:-4]
                payload = b""
            else:
                offset = address-0x10000000 if address >= 0x10000000 else address
                payload = bytes(self.memory[offset:offset+length])
            response = reply(command, sequence, address, length, payload)
        self.pending = self.change(response)
        return len(packet) - self.short

    def read(self, count):
        data, self.pending = self.pending[:min(count, 23)], self.pending[min(count, 23):]
        self.now += len(data) * 10 / 1_000_000
        return data

    def close(self):
        pass


class BulkTests(unittest.TestCase):
    def client(self):
        transport = Transport()
        client = host.Client(transport, 1, clock=lambda: transport.now,
                             sleep=lambda delay: setattr(transport, "now", transport.now + delay))
        client.capabilities()
        return client, transport

    def test_capability_once_roundtrip_wrap_and_latency(self):
        client, transport = self.client()
        self.assertEqual(client.capabilities(), (0x3242324e, 4096, 3))
        self.assertEqual(len(transport.requests), 1)
        client.sequence = 254
        for address in (0, 4096):
            data = bytes((address//4096 + i*37) % 256 for i in range(4096))
            client.bulk_write(address, data)
            self.assertEqual(client.bulk_read(address, 4096), data)
        self.assertEqual([p[3] for p in transport.requests[1:]], [254, 255, 0, 1])
        self.assertEqual(len(transport.requests[1:]), 4)
        self.assertLess(transport.now, .3)

    def test_range_payload_boundaries_before_write(self):
        client, transport = self.client()
        cases = [(-4, 4), (1, 4), (0, 0), (0, 3), (0, 4100),
                 (0x3fffffc, 8), (0x1000fffc, 8), (0xfffffffc, 8)]
        for address, length in cases:
            with self.subTest(address=address, length=length), self.assertRaises(ValueError):
                client.bulk_read(address, length)
        with self.assertRaises(ValueError):
            client.bulk_write(0x10000000, b"1234")
        self.assertEqual(len(transport.requests), 1)
        self.assertFalse(client.failed)

    def test_capability_required_and_identity_mismatch(self):
        transport = Transport()
        client = host.Client(transport, 1)
        with self.assertRaises(ValueError):
            client.bulk_read(0, 4)
        self.assertFalse(transport.requests)
        transport.change = lambda _: b""
        # Finite synthetic time; absent/old endpoint never causes legacy fallback.
        client = host.Client(transport, .01, clock=lambda: transport.now,
                             sleep=lambda t: setattr(transport, "now", transport.now+t))
        with self.assertRaises(host.HostError):
            client.capabilities()
        self.assertTrue(client.failed)
        self.assertEqual(len(transport.requests), 1)

    def test_every_header_crc_partial_and_lost_ack_poison(self):
        def altered(index, value):
            return lambda packet: packet[:index] + bytes((value,)) + packet[index+1:]
        changes = [altered(0, 0), altered(1, 1), altered(2, 0xb1), altered(3, 77),
                   altered(4, 14), altered(5, 4), altered(9, 0), altered(11, 3),
                   altered(11, 8), lambda p: p[:-1] + bytes((p[-1]^1,)),
                   lambda p: p[:15], lambda p: b""]
        for change in changes:
            client, transport = self.client()
            transport.change = change
            with self.subTest(change=change), self.assertRaises(host.BulkError) as error:
                client.bulk_read(0, 4)
            self.assertIsNone(error.exception.completed)
            self.assertTrue(client.failed)
            with self.assertRaises(host.HostError):
                client.bulk_read(0, 4)
            self.assertEqual(len(transport.requests), 2)

    def test_checked_error_prefix_not_uncertain_and_never_partial_read(self):
        for command in (wire.CMD_BULK_WRITE, wire.CMD_BULK_READ):
            client, transport = self.client()
            transport.change = lambda p: reply(command, 1, 0, 32, status=9)
            with self.assertRaises(host.BulkError) as error:
                client._bulk(command, 0, 64, b"X"*64 if command == wire.CMD_BULK_WRITE else b"")
            self.assertEqual(error.exception.completed, 32)
            self.assertTrue(client.failed)

    def test_whole_budget_reserves_transaction_before_write(self):
        client, transport = self.client()
        client.operation_deadline = transport.now + .9
        with self.assertRaisesRegex(host.HostError, "budget"):
            client.bulk_read(0, 4)
        self.assertEqual(len(transport.requests), 1)

    def test_every_tail_verified_and_explicit_resume_after_reset(self):
        for length in (1, 2, 3, 4, 31, 32, 33, 4095, 4096, 4097, 8195):
            client, transport = self.client()
            transport.memory[:] = b"\xcc"*len(transport.memory)
            data = bytes(i%251 for i in range(length))
            manifest = host.Manifest(0, (host.Segment(0, data, "fixture"),))
            host.load(client, manifest, bulk=True, verify=True, report=lambda _: None)
            self.assertEqual(transport.memory[:length], data)
            self.assertEqual(transport.memory[length:length+4], b"\xcc"*4)
            self.assertTrue(transport.halted)
            self.assertEqual(transport.requests[-1][2], 4)
            host.load(client, manifest, bulk=True, verify=True, resume=True, report=lambda _: None)
            self.assertEqual([p[2] for p in transport.requests[-2:]], [4, 3])
            self.assertFalse(transport.halted)

    def test_frame_and_shadow_seventeen_data_exchanges(self):
        client, transport = self.client()
        transport.halted = True
        transport.memory[:] = bytes(i%251 for i in range(len(transport.memory)))
        frame = host.dump(client, 0x10000000, 64000)
        shadow = host.dump(client, 0x10000, 1024)
        self.assertEqual(frame, transport.memory[:64000])
        self.assertEqual(shadow, transport.memory[65536:66560])
        self.assertEqual(sum(p[0] == 0xa6 for p in transport.requests), 17)
        self.assertLess(transport.now, 1.1)

    def test_output_all_ancestors_leafs_and_no_serial_for_invalid_plan(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root/"workdir").mkdir()
            (root/"workdir/file").write_bytes(b"keep")
            (root/"outside").mkdir()
            (root/"workdir/link").symlink_to(root/"outside", target_is_directory=True)
            for name in ("/tmp/escape", "../escape", "workdir/../escape", "workdir/file",
                         "workdir/file/deep/output", "workdir/link/output", "workdir/link"):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    host.output_file(name, root)
            self.assertEqual(host.output_file("workdir/new/frame.bin", root), root/"workdir/new/frame.bin")
            self.assertEqual((root/"workdir/file").read_bytes(), b"keep")
        for argv in (["--dry-run", "dump", "0x1000fffc", "8", "--bulk", "--output", "workdir/out"],
                     ["--port", "FAKE", "dump", "0", "4", "--bulk", "--output", "../escape"],
                     ["--port", "FAKE", "load", "missing", "--bulk"]):
            opener = Mock()
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(host.main(argv, opener=opener), 2)
            opener.assert_not_called()

    def test_short_write_error_prefix_and_failed_dump_not_published(self):
        client, transport = self.client()
        transport.short = True
        with self.assertRaises(host.BulkError) as error:
            client.bulk_write(0, b"1234")
        self.assertIsNone(error.exception.completed)
        self.assertEqual(len(transport.requests), 2)
        transport = Transport()
        transport.halted = True
        original = transport.write
        def failing(packet):
            if packet[0] == 0xa6:
                transport.change = lambda _: reply(0x32, packet[3], 0, 0, status=9)
            return original(packet)
        transport.write = failing
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)/"out.bin"
            with patch.object(host, "output_file", return_value=target), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(host.main(["--port", "FAKE", "dump", "0", "4", "--bulk",
                                             "--output", "workdir/out"], opener=lambda *_: transport), 3)
            self.assertFalse(target.exists())

    def test_bulk_load_mismatch_poison_no_reset_resume(self):
        client, transport = self.client()
        transport.change = lambda p: reply(0x32, p[3], 0, 4, b"BAD!") if p[:3] == b"\x6a\x02\xb2" else p
        with self.assertRaisesRegex(host.HostError, "readback mismatch"):
            host.load(client, host.Manifest(0, (host.Segment(0, b"good", "fixture"),)),
                      bulk=True, verify=True, resume=True, report=lambda _: None)
        self.assertTrue(client.failed)
        self.assertNotIn(3, [p[2] for p in transport.requests])
        self.assertNotIn(4, [p[2] for p in transport.requests])


if __name__ == "__main__":
    unittest.main()
