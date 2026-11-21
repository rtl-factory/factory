import unittest

from src.nand2doom.scripts.perf.n2d_sw_profile import gameplay_windows, profile, symbols


def retirement(cycle, pc, instruction=0x13):
    return f"{cycle} R pc={pc:08x} insn={instruction:08x}\n"


class SoftwareProfileTests(unittest.TestCase):
    def test_resume_origin_is_applied_to_absolute_trace(self):
        report = {"result": {"resumed": 130574, "frames": [
            {"start": 0, "end": 10, "after_scene": 0},
            {"start": 10, "end": 20, "after_scene": 1}]}}
        self.assertEqual(gameplay_windows(report), [(130584, 130594)])

    def test_windows_exclude_startup_and_clip_function_intervals(self):
        entries = symbols("00000100 00000010 T a\n00000110 00000010 T b")
        result = profile([retirement(0, 0x100), retirement(7, 0x110, 3),
                          retirement(11, 0x118, 35), retirement(20, 0x100)],
                         entries, [(5, 15)])
        self.assertEqual(result["windows"][0]["cycles"], 10)
        self.assertEqual(result["windows"][0]["instructions"], 2)
        self.assertEqual(result["functions"]["a"]["cycles"], 2)
        self.assertEqual(result["functions"]["b"]["cycles"], 8)
        self.assertEqual(result["functions"]["b"]["loads"], 1)
        self.assertEqual(result["functions"]["b"]["stores"], 1)

    def test_symbol_end_is_exclusive(self):
        result = profile([retirement(0, 0x100), retirement(1, 0x110), retirement(2, 0x100)],
                         [(0x100, 0x110, "a")], [(0, 2)])
        self.assertEqual(result["functions"]["<unknown>"]["instructions"], 1)

    def test_swap_poll_is_separate_from_useful_copy_in_same_function(self):
        result = profile([retirement(0, 0x100), retirement(1, 0x104),
                          retirement(4, 0x108), retirement(6, 0x110), retirement(8, 0x100)],
                         [(0x100, 0x120, "acquire")], [(0, 8)], [(0x104, 0x110)])
        self.assertEqual(result["windows"][0]["wait_cycles"], 5)
        self.assertEqual(result["functions"]["acquire"]["cycles"], 3)

    def test_sleep_descendant_and_return_preserve_wait_context(self):
        # jal ra, then ret: the child of Sleep is waiting, a later independent
        # call to the same clock helper is useful work.
        result = profile([retirement(0, 0x100, 0xEF), retirement(1, 0x200, 0x8067),
                          retirement(2, 0x104, 0x8067), retirement(3, 0x300, 0xEF),
                          retirement(4, 0x200, 0x8067), retirement(5, 0x304)],
                         [(0x100, 0x110, "DG_SleepMs"), (0x200, 0x210, "clock"),
                          (0x300, 0x310, "game")], [(0, 5)])
        self.assertEqual(result["windows"][0]["wait_cycles"], 3)
        self.assertEqual(result["functions"]["clock"]["instructions"], 1)

    def test_partial_or_reordered_trace_fails_closed(self):
        for lines in ([retirement(1, 0x100), retirement(5, 0x100)],
                      [retirement(0, 0x100), retirement(4, 0x100)],
                      [retirement(0, 0x100), retirement(0, 0x104), retirement(5, 0x100)]):
            with self.assertRaises(ValueError):
                profile(lines, [(0x100, 0x110, "a")], [(0, 5)])


if __name__ == "__main__":
    unittest.main()
