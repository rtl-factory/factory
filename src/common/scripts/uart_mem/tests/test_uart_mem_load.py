"""uart_mem_load against the library's fake endpoint: the dry-run plan opens
nothing, a load lands at the first writable window with the advertised
entry and releases, a project mismatch or a failed readback leaves the CPU
held and exits 1."""
import io
import json
from pathlib import Path
import tempfile
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

    def test_project_ids(self):
        self.assertEqual(tool.project_id("n2m "), 0x206D326E)
        self.assertEqual(tool.project_id("0x1234"), 0x1234)
        with self.assertRaises(Exception):
            tool.project_id("toolong")


if __name__ == "__main__":
    unittest.main()
