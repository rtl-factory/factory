"""JTAG chain helpers: cable selection from jtagconfig, device-name resolution
by IDCODE from a quartus_stp listing, and the programming command."""
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.board import jtag_chain as chain  # noqa: E402

DE2 = 0x020F70DD
JTAGCONFIG = "1) USB-Blaster [1-1]\n  020F70DD   EP4CE115\n"
BANNER = "Info: *******************************************************************\nInfo: Running Quartus Prime Signal Tap\n"
# Why: quartus_stp names the same DE2-115 IDCODE differently on different installs.
LISTING_EP4 = BANNER + "N2A_DEVICE @1: EP4CE115 (0x020F70DD)\nInfo: Quartus Prime Signal Tap was successful.\n"
LISTING_10CL = BANNER + "N2A_DEVICE @1: 10CL120(Y|Z)/EP3C120/.. (0x020F70DD)\n"


class CableTests(unittest.TestCase):
    def test_one_cable_with_exactly_the_board_is_selected(self):
        for idcode in ("020F70DD", "020f70dd", DE2):
            with self.subTest(idcode=idcode):
                self.assertEqual(chain.select_cable(chain.parse_jtagconfig(JTAGCONFIG), idcode),
                                 "USB-Blaster [1-1]")

    def test_zero_two_or_wrong_cables_are_refused(self):
        two = JTAGCONFIG + "2) USB-Blaster [1-2]\n  031050DD   10M50DA(.|ES)/10M50DC\n"
        wrong = "1) USB-Blaster [1-1]\n  031050DD   10M50DA(.|ES)/10M50DC\n"
        for text in ("", two, wrong, "1) USB-Blaster [1-1]\n"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                chain.select_cable(chain.parse_jtagconfig(text), DE2)

    def test_find_cable_runs_jtagconfig_once_and_reports_failures(self):
        calls = []
        def runner(argv, timeout):
            calls.append((argv, timeout))
            return 0, JTAGCONFIG
        self.assertEqual(chain.find_cable(DE2, "/q/bin", runner=runner), "USB-Blaster [1-1]")
        self.assertEqual(calls, [(["/q/bin/jtagconfig"], chain.LIST_TIMEOUT)])
        with self.assertRaisesRegex(RuntimeError, "jtagconfig exited 1"):
            chain.find_cable(DE2, runner=lambda a, t: (1, "no server"))


class DeviceTests(unittest.TestCase):
    def test_both_listing_forms_resolve_by_idcode(self):
        for text, name in ((LISTING_EP4, "@1: EP4CE115 (0x020F70DD)"),
                           (LISTING_10CL, "@1: 10CL120(Y|Z)/EP3C120/.. (0x020F70DD)")):
            with self.subTest(name=name):
                self.assertEqual(chain.parse_devices(text), [name])
                self.assertEqual(chain.select_device(chain.parse_devices(text), DE2), name)
        self.assertEqual(chain.select_device(["@1: 10M50DA (0x031050DD)", "@2: x (0x020f70dd) "], DE2),
                         "@2: x (0x020f70dd) ")
        self.assertEqual(chain.select_device(["@1: 10M50DA (0x031050DD)"], "031050DD"),
                         "@1: 10M50DA (0x031050DD)")

    def test_zero_or_ambiguous_matches_fail_closed(self):
        for names in ([], ["@1: 10M50DA (0x031050DD)"], ["@1: EP4CE115"],
                      ["@1: EP4CE115 (0x020F70DD)", "@2: 10CL120(Y|Z)/EP3C120/.. (0x020F70DD)"]):
            with self.subTest(names=names), self.assertRaisesRegex(ValueError, "exactly one .*0x020F70DD"):
                chain.select_device(names, DE2)

    def test_listing_runs_one_bounded_quartus_stp_script(self):
        calls = []
        def runner(argv, timeout):
            calls.append((argv, timeout))
            return 0, LISTING_10CL
        self.assertEqual(chain.resolve("USB-Blaster [1-1]", "/q/bin", DE2, runner=runner),
                         "@1: 10CL120(Y|Z)/EP3C120/.. (0x020F70DD)")
        self.assertEqual(calls, [(["/q/bin/quartus_stp", "-t", str(chain.SCRIPT), "USB-Blaster [1-1]"],
                                  chain.LIST_TIMEOUT)])
        self.assertTrue(chain.SCRIPT.is_file())

    def test_listing_failure_and_timeout_are_errors(self):
        with self.assertRaisesRegex(RuntimeError, "exited 1: N2A_ERROR no such cable"):
            chain.list_devices("x", runner=lambda a, t: (1, "Info: banner\nN2A_ERROR no such cable\n"))
        def slow(argv, timeout):
            raise subprocess.TimeoutExpired(argv, timeout)
        with self.assertRaisesRegex(RuntimeError, "timed out"):
            chain.list_devices("x", runner=slow)


class ProgramTests(unittest.TestCase):
    def test_command_and_success_line(self):
        self.assertEqual(chain.program_command("/w/a.sof", "USB-Blaster [1-1]"),
                         ["quartus_pgm", "-c", "USB-Blaster [1-1]", "-m", "jtag", "-o", "p;/w/a.sof@1"])
        self.assertEqual(chain.program_command("a.sof", "c", "/q")[0], "/q/quartus_pgm")
        self.assertTrue(chain.program_succeeded(0, "Info: Programmer was successful. 0 errors"))
        self.assertFalse(chain.program_succeeded(0, "Error: no cable"))
        self.assertFalse(chain.program_succeeded(2, "Programmer was successful"))


if __name__ == "__main__":
    unittest.main()
