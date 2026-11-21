"""Hardware-free byte transport and pre-open validation tests."""

from contextlib import redirect_stderr, redirect_stdout
import copy
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[5]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from src.nand2doom.scripts.host import n2d_uart as host  # noqa: E402
from src.nand2doom.scripts.host import n2d_uart_debug_host as wire  # noqa: E402


def response(command, sequence, words=(), status=0):
    body = bytes((1, command | 128, sequence, status, len(words)))
    body += b"".join(w.to_bytes(4, "little") for w in words)
    return b"\x5a" + body + bytes((wire.crc8(body),))


class FakeTransport:
    def __init__(self, *, fragment=1, corrupt=None, short=False, fail_command=None):
        self.fragment, self.corrupt, self.short = fragment, corrupt, short
        self.fail_command = fail_command
        self.requests, self.pending = [], bytearray()
        self.memory = bytearray(b"\xcc" * 256)
        self.closed = False
        self.halted = False

    def write(self, packet):
        assert not self.pending, "more than one outstanding transaction"
        assert packet[0] == 0xA5 and wire.crc8(packet[1:-1]) == packet[-1]
        self.requests.append(packet)
        command, sequence = packet[2:4]
        address = int.from_bytes(packet[4:8], "little")
        count, enables = packet[8:10]
        words = ()
        status = wire.ST_MEMORY if command == self.fail_command else 0
        if not status:
            if command == wire.CMD_PING:
                words = (0x3144324E, int(self.halted))
            elif command == wire.CMD_HALT:
                self.halted = True
            elif command == wire.CMD_RESUME:
                self.halted = False
            elif command == wire.CMD_PEEK:
                words = tuple(int.from_bytes(self.memory[i:i + 4], "little")
                              for i in range(address, address + count * 4, 4))
            elif command in (wire.CMD_LOAD, wire.CMD_POKE):
                for i, value in enumerate(packet[10:-1]):
                    if enables & (1 << (i % 4)):
                        self.memory[address + i] = value
        result = response(command, sequence, words, status)
        self.pending.extend(self.corrupt(result) if self.corrupt else result)
        return len(packet) - 1 if self.short else len(packet)

    def read(self, count):
        count = min(count, self.fragment)
        data = bytes(self.pending[:count])
        del self.pending[:count]
        return data

    def close(self):
        self.closed = True


class UartTests(unittest.TestCase):
    def run_main(self, argv, transport=None):
        opener = Mock(return_value=transport or FakeTransport())
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            status = host.main(argv, opener=opener)
        return status, opener

    def test_fragmentation_all_commands_and_sequence_wrap(self):
        fake = FakeTransport()
        client = host.Client(fake, 1)
        client.sequence = 255
        self.assertEqual(client.command(wire.CMD_PING), (0x3144324E, 0))
        client.command(wire.CMD_HALT)
        client.command(wire.CMD_LOAD, address=0, words=(0x44332211,), byte_enable=15)
        client.command(wire.CMD_POKE, address=0, words=(0xFF00,), byte_enable=2)
        self.assertEqual(client.command(wire.CMD_PEEK, address=0, count=1), (0x4433FF11,))
        client.command(wire.CMD_RESET_PC, address=0)
        client.command(wire.CMD_INPUT, address=wire.INPUT_ADDRESS, words=(0x31C,), byte_enable=15)
        client.command(wire.CMD_RESUME)
        self.assertEqual([p[3] for p in fake.requests], [255, 0, 1, 2, 3, 4, 5, 6])
        self.assertEqual(fake.requests[6], wire.input_request(5, 0x31C))

    def test_crc_valid_halt_timeout_never_retries_or_resumes(self):
        fake = FakeTransport(corrupt=lambda p: response(wire.CMD_HALT, p[3], status=wire.ST_TIMEOUT))
        client = host.Client(fake, 1)
        with self.assertRaisesRegex(host.HostError, "device status 0x08; no retry"):
            client.command(wire.CMD_HALT)
        self.assertTrue(client.failed)
        # Why: even if the device later stops, no caller can repair a failed session by
        # Ping, re-Halt or Resume, because clock history stays unknown.
        self.assertTrue(fake.halted)
        for command in (wire.CMD_PING, wire.CMD_HALT, wire.CMD_RESUME):
            with self.assertRaisesRegex(host.HostError, "session failed"):
                client.command(command)
        self.assertEqual(len(fake.requests), 1)

    def test_malformed_headers_crc_status_and_identity_poison_session(self):
        def replace(index, value):
            return lambda packet: packet[:index] + bytes((value,)) + packet[index + 1:]
        cases = [replace(0, 0), replace(1, 2), replace(2, 0x82), replace(3, 99),
                 replace(4, 255), replace(5, 255), replace(5, 1),
                 lambda p: p[:-1] + bytes((p[-1] ^ 1,))]
        cases += [lambda p: response(wire.CMD_PING, 0, (0, 0)),
                  lambda p: response(wire.CMD_PING, 0, (0x3144324E, 2)),
                  lambda p: response(wire.CMD_PING, 0, (), wire.ST_BUSY)]
        for corrupt in cases:
            with self.subTest(corrupt=corrupt):
                fake = FakeTransport(corrupt=corrupt)
                client = host.Client(fake, 0.01)
                with self.assertRaises(host.HostError):
                    client.command(wire.CMD_PING)
                with self.assertRaisesRegex(host.HostError, "session failed"):
                    client.command(wire.CMD_HALT)
                self.assertEqual(len(fake.requests), 1)

    def test_short_write_and_empty_or_truncated_response_never_retry(self):
        for fake in (FakeTransport(short=True), FakeTransport(corrupt=lambda p: b""),
                     FakeTransport(corrupt=lambda p: p[:6])):
            now = [0.0]
            def sleep(seconds):
                now[0] += seconds
            client = host.Client(fake, 0.01, clock=lambda: now[0], sleep=sleep)
            with self.assertRaises(host.HostError):
                client.command(wire.CMD_POKE, words=(1,), byte_enable=15)
            self.assertEqual(len(fake.requests), 1)
            self.assertTrue(client.failed)

    def test_late_write_or_read_and_oserror(self):
        for operation in ("write", "read"):
            fake = FakeTransport()
            now = [0.0]
            original = getattr(fake, operation)
            def late(*args):
                result = original(*args)
                now[0] = 2
                return result
            setattr(fake, operation, late)
            with self.assertRaisesRegex(host.HostError, "deadline"):
                host.Client(fake, 1, clock=lambda: now[0]).command(wire.CMD_PING)
        fake = FakeTransport()
        fake.write = Mock(side_effect=OSError("disconnected"))
        status, _ = self.run_main(["--port", "FAKE", "halt"], fake)
        self.assertEqual(status, 3)
        self.assertTrue(fake.closed)

    def test_identity_before_mutation_and_owned_handle_cleanup(self):
        fake = FakeTransport(corrupt=lambda p: response(wire.CMD_PING, 0, (0x314C324E, 0)))
        status, _ = self.run_main(["--port", "FAKE", "halt"], fake)
        self.assertEqual(status, 3)
        self.assertEqual([p[2] for p in fake.requests], [wire.CMD_PING])
        self.assertTrue(fake.closed)
        fake = FakeTransport()
        status, _ = self.run_main(["--port", "FAKE", "halt"], fake)
        self.assertEqual(status, 0)
        self.assertEqual([p[2] for p in fake.requests], [wire.CMD_PING, wire.CMD_HALT])
        self.assertTrue(fake.closed)

    def test_multiple_segments_and_every_tail_with_readback(self):
        for size in range(4, 70):
            first = bytes(range(size))
            second = b"other"
            manifest = host.Manifest(0, (host.Segment(0, first, "first"), host.Segment(128, second, "second")))
            fake = FakeTransport()
            host.load(host.Client(fake, 1), manifest, verify=True, report=lambda _: None)
            self.assertEqual(fake.memory[:size], first)
            self.assertEqual(fake.memory[size:128], b"\xcc" * (128 - size))
            self.assertEqual(fake.memory[128:133], second)
            self.assertEqual(fake.memory[133:], b"\xcc" * 123)
            self.assertTrue(fake.halted)
            self.assertNotIn(wire.CMD_RESUME, [p[2] for p in fake.requests])
            self.assertTrue(all(p[8] <= 8 for p in fake.requests))
            self.assertEqual(fake.requests[-1][2], wire.CMD_RESET_PC)

    def test_explicit_resume_only_after_success_and_no_commands_after_error(self):
        manifest = host.Manifest(0, (host.Segment(0, bytes(range(37)), "image"),))
        for failed in (wire.CMD_HALT, wire.CMD_LOAD, wire.CMD_POKE, wire.CMD_PEEK, wire.CMD_RESET_PC):
            fake = FakeTransport(fail_command=failed)
            with self.assertRaises(host.HostError):
                host.load(host.Client(fake, 1), manifest, verify=True, resume=True, report=lambda _: None)
            self.assertEqual(fake.requests[-1][2], failed)
            self.assertNotIn(wire.CMD_RESUME, [p[2] for p in fake.requests])
        fake = FakeTransport()
        host.load(host.Client(fake, 1), manifest, resume=True, report=lambda _: None)
        self.assertEqual(fake.requests[-1][2], wire.CMD_RESUME)
        self.assertFalse(fake.halted)

    def test_readback_mismatch_stops_before_reset_or_resume(self):
        fake = FakeTransport()
        client = host.Client(fake, 1)
        original = client.command
        def command(cmd, **kwargs):
            result = original(cmd, **kwargs)
            return (0,) if cmd == wire.CMD_PEEK else result
        client.command = command
        with self.assertRaisesRegex(host.HostError, "readback mismatch"):
            host.load(client, host.Manifest(0, (host.Segment(0, b"abcd", "image"),)),
                      verify=True, resume=True, report=lambda _: None)
        self.assertEqual(fake.requests[-1][2], wire.CMD_PEEK)

    def test_argument_validation_before_open(self):
        cases = [["--timeout", "nan", "ping"], ["--timeout", "inf", "ping"],
                 ["--timeout", "0", "ping"], ["peek", "-4", "1"],
                 ["peek", "0", "9"], ["peek", "0x3fffffc", "2"],
                 ["peek", "0x1000fffc", "2"], ["peek", "0x20000000", "1"],
                 ["poke", "0", "0x100000000"], ["poke", "0", "0", "--byte-enable", "0"],
                 ["reset-pc", "2"], ["reset-pc", "0x4000000"], ["input", "1024"]]
        for case in cases:
            with self.subTest(case=case):
                status, opener = self.run_main(["--port", "FAKE", *case])
                self.assertEqual(status, 2)
                opener.assert_not_called()

    def test_help_and_all_dry_commands_without_site_packages(self):
        cases = [["--help"], ["--dry-run", "ping"], ["--dry-run", "halt"],
                 ["--dry-run", "resume"], ["--dry-run", "peek", "0", "8"],
                 ["--dry-run", "poke", "0", "0xffffffff"],
                 ["--dry-run", "reset-pc", "0"], ["--dry-run", "input", "0x31c"]]
        for argv in cases:
            result = subprocess.run([sys.executable, "-B", "-S", str(ROOT / "src/nand2doom/scripts/host/n2d_uart.py"), *argv],
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_serial_adapter_configuration_and_open_failure_cleanup(self):
        connection = Mock()
        serial = Mock()
        serial.Serial.return_value = connection
        with patch.dict(sys.modules, {"serial": serial}):
            self.assertIs(host.open_serial("EXPLICIT", 0.5), connection)
        self.assertEqual(serial.Serial.call_args.kwargs, dict(port=None, baudrate=2000000,
                         bytesize=8, parity="N", stopbits=1, timeout=0, write_timeout=0.5,
                         xonxoff=False, rtscts=False, dsrdtr=False))
        self.assertFalse(connection.dtr)
        self.assertFalse(connection.rts)
        self.assertEqual(connection.port, "EXPLICIT")
        connection.open.side_effect = OSError("busy")
        with patch.dict(sys.modules, {"serial": serial}), self.assertRaises(OSError):
            host.open_serial("EXPLICIT", 0.5)
        connection.close.assert_called_once()


class ManifestTests(unittest.TestCase):
    run_main = UartTests.run_main
    def setUp(self):
        parent = ROOT / "workdir/unit-tests"
        parent.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        (self.folder / "image.bin").write_bytes(b"abcde")
        self.manifest = dict(schema_version=1, architecture="rv32im", abi="ilp32",
                             endianness="little", entry_address=0, memory_map_revision="n2d-platform-v1",
                             segments=[dict(address=0, file="image.bin", byte_length=5,
                                            sha256=hashlib.sha256(b"abcde").hexdigest())])
        self.path = self.folder / "manifest.json"

    def write_manifest(self, manifest):
        self.path.write_text(json.dumps(manifest), encoding="utf-8")

    def test_both_builder_spellings_snapshot_and_live_load(self):
        for alternative in (False, True):
            manifest = copy.deepcopy(self.manifest)
            if alternative:
                manifest["entry"] = manifest.pop("entry_address")
                segment = manifest["segments"][0]
                segment["path"] = segment.pop("file")
            self.write_manifest(manifest)
            validated = host.validate_manifest(self.path)
            self.assertEqual(validated.segments[0].data, b"abcde")
            status, opener = self.run_main(["--dry-run", "load", str(self.path)])
            self.assertEqual(status, 0)
            opener.assert_not_called()
            fake = FakeTransport()
            status, _ = self.run_main(["--port", "FAKE", "load", str(self.path), "--verify"], fake)
            self.assertEqual(status, 0)
            self.assertTrue(fake.closed)
            self.assertTrue(fake.halted)

    def test_manifest_failures_never_open_transport(self):
        mutations = [lambda m: m.update(architecture="rv64im"), lambda m: m.update(abi="lp64"),
                     lambda m: m.update(schema_version=True), lambda m: m.update(endianness="big"),
                     lambda m: m.update(memory_map_revision="unknown"), lambda m: m.update(entry_address=4),
                     lambda m: m.update(entry=8), lambda m: m.update(segments=[]),
                     lambda m: m["segments"].append(dict(m["segments"][0])),
                     lambda m: m["segments"][0].update(address=2),
                     lambda m: m["segments"][0].update(address=0x3fffffc),
                     lambda m: m["segments"][0].update(byte_length=4),
                     lambda m: m["segments"][0].update(sha256="0" * 64),
                     lambda m: m["segments"][0].update(file="../image.bin"),
                     lambda m: m["segments"][0].update(file="C:\\image.bin"),
                     lambda m: m["segments"][0].update(path="different.bin"),
                     lambda m: m.update(engine={}), lambda m: m.update(game_data={}),
                     lambda m: m["segments"].append(dict(m["segments"][0], address=0x2000000))]
        for mutate in mutations:
            manifest = copy.deepcopy(self.manifest)
            mutate(manifest)
            self.write_manifest(manifest)
            status, opener = self.run_main(["--port", "FAKE", "load", str(self.path), "--resume"])
            self.assertEqual(status, 2, manifest)
            opener.assert_not_called()

    def test_duplicate_keys_and_nonobjects_rejected(self):
        for text in ('{"schema_version":1,"schema_version":1}', '[]', 'null', '{'):
            self.path.write_text(text)
            status, opener = self.run_main(["--port", "FAKE", "load", str(self.path)])
            self.assertEqual(status, 2)
            opener.assert_not_called()

    def test_pinned_doom_metadata_and_asset_match(self):
        from src.nand2doom.scripts.sw_build import n2d_doom as doom
        data = b"test WAD fixture, not game content"
        digest = hashlib.sha256(data).hexdigest()
        (self.folder / "fixture.wad").write_bytes(data)
        manifest = copy.deepcopy(self.manifest)
        manifest["engine"] = dict(name="doomgeneric", revision=doom.ENGINE_REVISION,
                                  archive_sha256=doom.ENGINE_SHA256,
                                  license=doom.ENGINE_LICENSE, url=doom.ENGINE_URL)
        manifest["game_data"] = dict(name="Freedoom Phase 1", version=doom.FREEDOOM_VERSION,
                                     archive_sha256=doom.FREEDOOM_SHA256, wad_sha256=digest,
                                     license="BSD-3-Clause", url=doom.FREEDOOM_URL,
                                     byte_length=len(data), address=doom.WAD_ADDRESS)
        manifest["segments"].append(dict(address=doom.WAD_ADDRESS, file="fixture.wad",
                                         byte_length=len(data), sha256=digest))
        # Why: only the fixture's byte length and digest are patched, so every
        # provenance field still comes from the production recipe, with no downloads
        # or real assets.
        with patch.object(doom, "WAD_SHA256", digest), patch.object(doom, "WAD_BYTE_LENGTH", len(data)):
            self.write_manifest(manifest)
            self.assertEqual(len(host.validate_manifest(self.path).segments), 2)
            for key in manifest["engine"]:
                bad = copy.deepcopy(manifest)
                bad["engine"][key] = "wrong"
                self.write_manifest(bad)
                with self.assertRaisesRegex(ValueError, "provenance"):
                    host.validate_manifest(self.path)
            for key in manifest["game_data"]:
                bad = copy.deepcopy(manifest)
                bad["game_data"][key] = "wrong"
                self.write_manifest(bad)
                with self.assertRaisesRegex(ValueError, "provenance"):
                    host.validate_manifest(self.path)
            bad = copy.deepcopy(manifest)
            bad["segments"][1]["address"] += 4
            self.write_manifest(bad)
            with self.assertRaisesRegex(ValueError, "asset segment"):
                host.validate_manifest(self.path)


if __name__ == "__main__":
    unittest.main()
