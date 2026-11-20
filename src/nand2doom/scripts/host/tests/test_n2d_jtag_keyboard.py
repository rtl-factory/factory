"""Hardware-free contract of the nand2doom JTAG keyboard CLI: Set-2 translation
of Doom actions, raw events and text, the inert dry-run, and delivery through
the real common Session against the common test's independent wire peer."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[5]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from src.nand2doom.scripts.host import n2d_jtag_device as resolver  # noqa: E402
from src.nand2doom.scripts.host import n2d_jtag_keyboard as tool  # noqa: E402
from src.nand2doom.scripts.host.n2d_control import ACTIONS  # noqa: E402
from src.common.scripts.jtag import n2a_jtag_keyboard as jtag  # noqa: E402
from src.common.scripts.jtag.tests.test_n2a_jtag_keyboard import Device  # noqa: E402

CABLE = ["--cable", "USB-Blaster [1-1]", "--device", "@1: EP4CE115 (0x020F70DD)"]
# quartus_stp names the same DE2-115 IDCODE differently on different installs.
BANNER = "Info: *******************************************************************\nInfo: Running Quartus Prime Signal Tap\n"
LISTING_EP4 = BANNER + "N2D_DEVICE @1: EP4CE115 (0x020F70DD)\nInfo: Quartus Prime Signal Tap was successful.\n"
LISTING_10CL = BANNER + "N2D_DEVICE @1: 10CL120(Y|Z)/EP3C120/.. (0x020F70DD)\n"


def decode(stream: bytes) -> list[int]:
    """Independent Set-2 fold: E0/F0 qualify the next byte (io/HAS.md)."""
    events, extended, release = [], 0, 0
    for byte in stream:
        if byte == 0xE0:
            extended = 1
        elif byte == 0xF0:
            release = 1
        else:
            events.append(extended << 9 | release << 8 | byte)
            extended = release = 0
    return events


class Peer(Device):
    """The common peer, refusing every other submit after its first byte so the
    proven-suffix resubmission path carries every step."""
    def __init__(self):
        super().__init__(limit=4)
        self.submits = 0

    def scan(self, ir, value=0, *, deadline=None):
        if ir == 1:
            self.submits += 1
            self.limit = 1 if self.submits % 2 else 4
        return super().scan(ir, value, deadline=deadline)


class Fake:
    """jtag module stand-in: real settings/translation/session, peer transport."""
    def __init__(self, peer=None):
        self.peer, self.opened = peer or Peer(), []
        self.settings, self.key_strokes = jtag.settings, jtag.key_strokes
        self.Session, self.send_all, self.TransferError = jtag.Session, jtag.send_all, jtag.TransferError

    def open_transport(self, data):
        self.opened.append(data)
        return self.peer


def no_listing(cable, quartus_bin):
    raise AssertionError("the device listing must not run")


def listing(text, calls=None, rc=0):
    """A resolve() bound to a fake quartus_stp runner returning `text`."""
    def runner(argv, timeout):
        if calls is not None:
            calls.append(argv)
        return rc, text
    return lambda cable, quartus_bin: resolver.resolve(cable, quartus_bin, runner=runner)


def run(argv, fake=None, sleeps=None, resolve=no_listing):
    args = tool.parser().parse_args(argv)
    return tool.run(args, jtag=fake or Fake(), resolve=resolve,
                    sleep=(sleeps.append if sleeps is not None else lambda s: None))


class DeviceResolutionTests(unittest.TestCase):
    def test_both_listing_forms_resolve_by_idcode(self):
        for text, name in ((LISTING_EP4, "@1: EP4CE115 (0x020F70DD)"),
                           (LISTING_10CL, "@1: 10CL120(Y|Z)/EP3C120/.. (0x020F70DD)")):
            with self.subTest(name=name):
                self.assertEqual(resolver.parse_devices(text), [name])
                self.assertEqual(resolver.select_device(resolver.parse_devices(text)), name)
        self.assertEqual(resolver.select_device(["@1: 10M50DA (0x031050DD)", "@2: x (0x020f70dd) "]),
                         "@2: x (0x020f70dd) ")

    def test_zero_or_ambiguous_matches_fail_closed(self):
        for names in ([], ["@1: 10M50DA (0x031050DD)"], ["@1: EP4CE115"],
                      ["@1: EP4CE115 (0x020F70DD)", "@2: 10CL120(Y|Z)/EP3C120/.. (0x020F70DD)"]):
            with self.subTest(names=names), self.assertRaisesRegex(ValueError, "exactly one .*0x020F70DD"):
                resolver.select_device(names)

    def test_listing_runs_one_bounded_quartus_stp_script(self):
        calls = []
        def runner(argv, timeout):
            calls.append((argv, timeout))
            return 0, LISTING_10CL
        self.assertEqual(resolver.resolve("USB-Blaster [1-1]", "/q/bin", runner=runner),
                         "@1: 10CL120(Y|Z)/EP3C120/.. (0x020F70DD)")
        self.assertEqual(calls, [(["/q/bin/quartus_stp", "-t", str(resolver.SCRIPT), "USB-Blaster [1-1]"],
                                  resolver.LIST_TIMEOUT)])
        self.assertTrue(resolver.SCRIPT.is_file())

    def test_listing_failure_and_timeout_are_errors(self):
        with self.assertRaisesRegex(RuntimeError, "exited 1: N2D_ERROR no such cable"):
            resolver.list_devices("x", runner=lambda a, t: (1, "Info: banner\nN2D_ERROR no such cable\n"))
        def slow(argv, timeout):
            raise subprocess.TimeoutExpired(argv, timeout)
        with self.assertRaisesRegex(RuntimeError, "timed out"):
            resolver.list_devices("x", runner=slow)

    def test_dry_run_shows_the_resolved_device_and_opens_no_transport(self):
        fake, calls = Fake(), []
        code, result = run(["--cable", "USB-Blaster [1-1]", "key", "Fire"], fake,
                           resolve=listing(LISTING_10CL, calls))
        self.assertEqual(code, 0)
        self.assertEqual(result["jtag_device"], "@1: 10CL120(Y|Z)/EP3C120/.. (0x020F70DD)")
        self.assertEqual(result["jtag_device_source"], "IDCODE 0x020F70DD")
        self.assertIn("dry_run", result)
        self.assertEqual((len(calls), fake.opened), (1, []))

    def test_send_uses_the_resolved_device_and_override_skips_listing(self):
        fake = Fake()
        code, _ = run(["--cable", "USB-Blaster [1-1]", "--yes", "key", "Fire"], fake,
                      resolve=listing(LISTING_10CL))
        self.assertEqual(code, 0)
        self.assertEqual(fake.opened[0]["jtag_device"], "@1: 10CL120(Y|Z)/EP3C120/.. (0x020F70DD)")
        code, result = run([*CABLE, "--yes", "key", "Fire"], fake)   # no_listing: override wins
        self.assertEqual((code, result["jtag_device_source"]), (0, "--device"))
        self.assertEqual(fake.opened[1]["jtag_device"], "@1: EP4CE115 (0x020F70DD)")

    def test_unresolvable_device_refuses_before_opening(self):
        fake, err = Fake(), io.StringIO()
        ambiguous = LISTING_EP4 + "N2D_DEVICE @2: EP4CE115 (0x020F70DD)\n"
        for resolve in (listing(BANNER), listing(ambiguous), listing("Error: no cable", rc=3)):
            with self.subTest(), redirect_stderr(err):
                self.assertEqual(tool.main(["--cable", "USB-Blaster [1-1]", "--yes", "key", "Fire"],
                                           jtag=fake, resolve=resolve), 2)
        self.assertEqual(fake.opened, [])
        self.assertIn("found 2", err.getvalue())


class TranslationTests(unittest.TestCase):
    def test_event_codes_cover_all_qualifiers(self):
        self.assertEqual(tool.event_codes(0x01C), bytes([0x1C]))
        self.assertEqual(tool.event_codes(0x11C), bytes([0xF0, 0x1C]))
        self.assertEqual(tool.event_codes(0x275), bytes([0xE0, 0x75]))
        self.assertEqual(tool.event_codes(0x375), bytes([0xE0, 0xF0, 0x75]))
        for event in range(1024):
            if event & 0xFF not in (0xE0, 0xF0):
                self.assertEqual(decode(tool.event_codes(event)), [event])
        for bad in (-1, 1024, 0x0E0, 0x3F0, True, "1"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                tool.event_codes(bad)

    def test_every_doom_action_round_trips(self):
        for name, event in ACTIONS.items():
            with self.subTest(name=name):
                make, brk = tool.key_steps([name], "tap")
                self.assertEqual(decode(make), [event])
                self.assertEqual(decode(brk), [event | 0x100])
        self.assertEqual(tool.action_event("turn-LEFT"), ACTIONS["Turn left"])
        self.assertEqual(tool.action_event(" turn_right "), ACTIONS["Turn right"])
        with self.assertRaises(ValueError):
            tool.action_event("jump")

    def test_chord_makes_in_order_and_breaks_in_reverse(self):
        make, brk = tool.key_steps(["Run", "Forward"], "tap")
        self.assertEqual(decode(make), [0x012, 0x275])
        self.assertEqual(decode(brk), [0x375, 0x112])
        self.assertEqual(tool.key_steps(["Fire"], "press"), [bytes([0x14])])
        self.assertEqual(tool.key_steps(["Fire"], "release"), [bytes([0xF0, 0x14])])

    def test_plan_batches_by_four(self):
        self.assertEqual(tool.plan([bytes(range(1, 7))]),
                         {"steps": [{"set2": "01 02 03 04 05 06", "batches": ["01 02 03 04", "05 06"]}],
                          "bytes": 6})


class DeliveryTests(unittest.TestCase):
    def test_dry_run_opens_nothing_and_needs_no_cable(self):
        fake = Fake()
        code, result = run(["key", "Fire"], fake)
        self.assertEqual(code, 0)
        self.assertEqual(fake.opened, [])
        self.assertIn("dry_run", result)
        self.assertEqual(result["steps"][0]["set2"], "14")
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(tool.main(["send", "0x275", "0x375"], jtag=fake), 0)
        self.assertEqual(json.loads(out.getvalue())["steps"], [{"set2": "e0 75 e0 f0 75",
                                                               "batches": ["e0 75 e0 f0", "75"]}])
        self.assertEqual(fake.opened, [])

    def test_cli_help_and_dry_run_subprocess_are_inert(self):
        script = ROOT / "src/nand2doom/scripts/host/n2d_jtag_keyboard.py"
        for argv in (["--help"], ["type", "idkfa"], ["key", "Turn left", "--hold", "0.5"]):
            with self.subTest(argv=argv):
                done = subprocess.run([sys.executable, "-B", str(script), *argv], cwd=ROOT,
                                      capture_output=True, text=True, timeout=30, check=False)
                self.assertEqual(done.returncode, 0, done.stderr)

    def test_send_requires_names_and_rejects_bad_input_before_opening(self):
        fake = Fake()
        with self.assertRaises(ValueError):
            run(["--yes", "key", "Fire"], fake)
        with self.assertRaises(ValueError):
            run(["--device", "@1: EP4CE115 (0x020F70DD)", "key", "Fire"], fake)   # device without cable
        with self.assertRaises(ValueError):
            run([*CABLE, "--yes", "send", "0x0f0"], fake)
        with self.assertRaises(ValueError):
            run([*CABLE, "--yes", "type", "\x01\x02\x00"], fake)        # no scan code for NUL
        self.assertEqual(fake.opened, [])
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(tool.main(["--yes", "key", "Jump"], jtag=fake), 2)
        self.assertIn("unknown action", err.getvalue())

    def test_key_tap_delivers_exact_events_with_refused_suffixes(self):
        fake, sleeps = Fake(), []
        code, result = run([*CABLE, "--yes", "key", "Run", "Turn left", "--hold", "0.25"], fake, sleeps)
        self.assertEqual((code, result["status"], result["close"]), (0, "ok", "closed"))
        self.assertEqual(decode(bytes(fake.peer.injected)), [0x012, 0x26B, 0x36B, 0x112])
        self.assertEqual(result["sent"], len(fake.peer.injected))
        self.assertIn(0.25, sleeps)
        self.assertEqual(fake.opened[0]["jtag_cable"], "USB-Blaster [1-1]")

    def test_send_and_type_deliver_through_common_session(self):
        fake = Fake()
        events = [0x275, 0x014, 0x114, 0x375]
        code, _ = run([*CABLE, "--yes", "send", *map(hex, events)], fake)
        self.assertEqual(code, 0)
        self.assertEqual(decode(bytes(fake.peer.injected)), events)
        fake, sleeps = Fake(), []
        code, result = run([*CABLE, "--yes", "type", "iD", "--hold", "0.2", "--gap", "0.3"], fake, sleeps)
        self.assertEqual(code, 0)
        self.assertEqual(decode(bytes(fake.peer.injected)),
                         [0x043, 0x143, 0x012, 0x023, 0x123, 0x112])
        self.assertEqual(sleeps.count(0.2), 2)
        self.assertEqual(sleeps.count(0.3), 1)

    def test_uncertain_delivery_reports_error_and_still_closes(self):
        fake = Fake(Device(defect="lost"))
        code, result = run([*CABLE, "--yes", "key", "Fire"], fake)
        self.assertEqual(code, 1)
        self.assertEqual(result["status"], "error")
        self.assertIn("no replay", result["error"])
        self.assertEqual(result["close"], "closed")
        fake = Fake(Device(defect="close"))
        code, result = run([*CABLE, "--yes", "key", "Fire"], fake)
        self.assertEqual((code, result["status"]), (1, "error"))

    def test_delivery_bound_is_whole_request(self):
        # A target that refuses every byte (input FIFO never drains) exhausts
        # the bound instead of retrying forever; nothing was delivered.
        fake = Fake(Device(limit=0))
        code, result = run([*CABLE, "--yes", "--seconds", "0.05", "key", "Fire"], fake)
        self.assertEqual((code, result["sent"], result["close"]), (1, 0, "closed"))
        self.assertRegex(result["error"], "unsent|deadline")
        self.assertEqual(bytes(fake.peer.injected), b"")


if __name__ == "__main__":
    unittest.main()
