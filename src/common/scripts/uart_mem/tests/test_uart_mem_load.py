"""uart_mem_load against the library's fake endpoint: the dry-run plan opens
nothing, a load lands at the first writable window with the advertised
entry and releases, a project mismatch or a failed readback leaves the CPU
held and exits 1, and a segment manifest (nand2doom's shape) loads every
segment at its address with its entry, skipping unchanged data segments when
asked."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import time
import unittest

from src.common.scripts.uart_mem import uart_mem_load as tool
from src.common.scripts.uart_mem.tests.test_uart_mem import FakeEndpoint


class LoadScriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.image = Path(self.tmp.name) / "game.gb"
        self.data = bytes(range(256)) * 2 + b"\x11\x22"
        self.image.write_bytes(self.data)

    def tearDown(self):
        self.tmp.cleanup()

    def run_tool(self, *argv, fake=None):
        out = io.StringIO()
        rc = tool.run(tool.parser().parse_args([str(self.image), *argv]), transport=fake, out=out)
        return rc, out.getvalue()

    def test_dry_run_prints_the_plan_and_opens_nothing(self):
        rc, text = self.run_tool("--port", "/dev/null-port", "--project", "n2m ")
        plan = json.loads(text)
        self.assertEqual((rc, plan["dry_run"], plan["bytes"], plan["baud"], plan["project"]),
                         (0, True, len(self.data), 2_000_000, 0x206D326E))

    def test_load_writes_the_first_writable_window_and_releases(self):
        fake = FakeEndpoint(entry=0x100)
        rc, text = self.run_tool("--project", "n2d ", "--yes", fake=fake)
        self.assertEqual(rc, 0, text)
        self.assertEqual(bytes(fake.memory[i] for i in range(len(self.data))), self.data)
        commands = [c for c, _, _ in fake.log]
        self.assertEqual((commands.count(0x02), commands[-2:]), (1, [0x04, 0x03]))
        self.assertIn((0x04, 0x100, 0), fake.log)
        self.assertFalse(fake.hold)
        self.assertIn("entry 0x100", text)

    def test_explicit_address_and_entry(self):
        fake = FakeEndpoint(entry=0x40)
        rc, _ = self.run_tool("--address", "0x40", "--entry", "0x40", "--yes", fake=fake)
        self.assertEqual(rc, 0)
        self.assertEqual(fake.memory[0x40], self.data[0])

    def test_project_mismatch_sends_no_hold(self):
        fake = FakeEndpoint()
        rc, _ = self.run_tool("--project", "n2m ", "--yes", fake=fake)
        self.assertEqual(rc, 1)
        self.assertNotIn(0x02, [c for c, _, _ in fake.log])

    def test_failed_readback_leaves_the_cpu_held(self):
        fake = FakeEndpoint()
        fake.corrupt = {5}
        rc, _ = self.run_tool("--yes", fake=fake)
        self.assertEqual(rc, 1)
        self.assertTrue(fake.hold)
        self.assertNotIn(0x03, [c for c, _, _ in fake.log])

    def test_serial_read_returns_what_is_waiting_without_the_deadline(self):
        class Port:
            def __init__(self):
                self.waiting = bytearray(b"\x8a" * 11)

            @property
            def in_waiting(self):
                return len(self.waiting)

            def read(self, size):
                data, self.waiting[:] = bytes(self.waiting[:size]), self.waiting[size:]
                return data

        transport = tool.SerialTransport.__new__(tool.SerialTransport)
        transport.serial = Port()
        start = time.monotonic()
        data = transport.read(4096, start + 5.0)
        self.assertEqual((data, time.monotonic() - start < 1.0), (b"\x8a" * 11, True))
        self.assertEqual(transport.read(4096, time.monotonic() + 0.05), b"")

    def write_manifest(self, **overrides):
        program, wad = bytes(range(64)), bytes(range(255, 155, -1))
        folder = Path(self.tmp.name)
        (folder / "program.bin").write_bytes(program)
        (folder / "doom1.wad").write_bytes(wad)
        manifest = {"schema_version": 1, "entry_address": 0x40, "segments": [
            {"address": 0x40, "byte_length": len(program), "file": "program.bin",
             "sha256": hashlib.sha256(program).hexdigest()},
            {"address": 0x1000, "byte_length": len(wad), "file": "doom1.wad",
             "sha256": hashlib.sha256(wad).hexdigest()}]}
        manifest.update(overrides)
        self.image = folder / "manifest.json"
        self.image.write_text(json.dumps(manifest))
        return program, wad

    def test_manifest_loads_every_segment_with_its_entry(self):
        program, wad = self.write_manifest()
        fake = FakeEndpoint(entry=0x40, digest=True)
        rc, text = self.run_tool("--project", "n2d ", "--skip-unchanged", "--yes", fake=fake)
        self.assertEqual(rc, 0, text)
        self.assertEqual(bytes(fake.memory[0x40 + i] for i in range(len(program))), program)
        self.assertEqual(bytes(fake.memory[0x1000 + i] for i in range(len(wad))), wad)
        self.assertIn((0x04, 0x40, 0), fake.log)
        self.assertNotIn(0x11, [c for c, _, _ in fake.log], "verified by Digest, not readback")
        self.assertIn("0x1000 100 bytes: written", text)

    def test_manifest_reload_skips_the_unchanged_data_segment(self):
        self.write_manifest()
        fake = FakeEndpoint(entry=0x40, digest=True)
        self.assertEqual(self.run_tool("--skip-unchanged", "--yes", fake=fake)[0], 0)
        fake.log.clear()
        rc, text = self.run_tool("--skip-unchanged", "--yes", fake=fake)
        self.assertEqual(rc, 0, text)
        self.assertEqual({a for c, a, _ in fake.log if c == 0x10}, {0x40}, "only the program is rewritten")
        self.assertIn("0x1000 100 bytes: skipped", text)
        self.assertIn("0x40 64 bytes: written", text)

    def test_manifest_plan_lists_the_segments(self):
        self.write_manifest()
        rc, text = self.run_tool("--port", "/dev/null-port")
        plan = json.loads(text)
        self.assertEqual((rc, plan["entry"], plan["bytes"]), (0, 0x40, 164))
        self.assertEqual(plan["segments"], [{"address": 0x40, "bytes": 64}, {"address": 0x1000, "bytes": 100}])

    def test_bad_manifests_stop_before_any_port(self):
        program, _ = self.write_manifest()
        for overrides in ({"segments": []}, {"entry_address": "0x40"},
                          {"segments": [{"address": 0, "file": "../escape.bin"}]},
                          {"segments": [{"address": 0, "file": "program.bin", "byte_length": 3}]},
                          {"segments": [{"address": 0, "file": "program.bin", "sha256": "00"}]}):
            self.write_manifest(**overrides)
            fake = FakeEndpoint(digest=True)
            with self.assertRaises(SystemExit, msg=overrides):
                self.run_tool("--yes", fake=fake)
            self.assertEqual(fake.log, [], overrides)

    def test_project_ids(self):
        self.assertEqual(tool.project_id("n2m "), 0x206D326E)
        self.assertEqual(tool.project_id("0x1234"), 0x1234)
        with self.assertRaises(Exception):
            tool.project_id("toolong")


if __name__ == "__main__":
    unittest.main()
