"""Reject stale witnesses, unknown counters and frames predating queue drainage."""
import unittest

from src.nand2doom.dv.software.doom_gameplay import (
    BREAK, COMPLETION, GameplayTrace, MAKE, TURN, frame_statistics, parse_symbols,
)

SYMBOLS = {"gametic": 0x100, "maketic": 0x104, "ticdup": 0x108}


class GameplayTests(unittest.TestCase):
    def store(self, trace, tick, address, data, mask=15, **flags):
        trace.retirement(trace.last_order + 1, tick, address, data, mask, **flags)

    def initialized(self):
        trace = GameplayTrace(SYMBOLS)
        for name, address in SYMBOLS.items():
            for lane in range(4):
                # Why: disabled lanes hold poison, not zero, so a reader that ignores the mask fails.
                self.store(trace, 0, address + lane,
                           0xFFFFFFFF ^ (0xFF << (8 * lane)), 1 << lane)
                if lane < 3:
                    self.assertNotIn(name, trace.counters)
        self.store(trace, 1, SYMBOLS["ticdup"], 1)
        self.store(trace, 2, SYMBOLS["maketic"], 1)
        self.store(trace, 3, SYMBOLS["maketic"], 2)
        return trace

    def trace(self):
        trace = self.initialized()
        trace.begin_phase(0, 10)
        for tick, marker in ((11, MAKE), (12, TURN), (13, BREAK),
                             (14, 0xD00D5FEC)):
            self.store(trace, tick, COMPLETION, marker)
        return trace

    def drain(self, trace):
        self.store(trace, 15, SYMBOLS["maketic"], 3)
        self.store(trace, 16, SYMBOLS["gametic"], 1)
        self.store(trace, 17, SYMBOLS["gametic"], 2)
        self.assertIsNone(trace.barriers[0])
        self.store(trace, 18, SYMBOLS["gametic"], 3)
        self.assertEqual(18, trace.barriers[0])

    def test_neutral_slot_must_be_applied_before_uart(self):
        trace = self.trace()
        trace.frame(15, 16, 123)
        self.assertIsNone(trace.settled_frame(0))
        with self.assertRaisesRegex(AssertionError, "not drained"):
            trace.begin_phase(1, 17)
        self.drain(trace)
        trace.frame(17, 20, 124)
        self.assertIsNone(trace.settled_frame(0))
        trace.frame(19, 20, 125)
        self.assertEqual(125, trace.settled_frame(0)["crc"])
        trace.begin_phase(1, 21)
        with self.assertRaisesRegex(AssertionError, "missing phase make"):
            self.store(trace, 22, COMPLETION, 0xD00D5FD8)

    def test_two_ordered_turns_and_final_monitoring(self):
        trace = self.trace()
        self.drain(trace)
        trace.frame(19, 20, 125)
        trace.begin_phase(1, 21)
        for tick, marker in ((22, MAKE), (23, TURN), (24, BREAK),
                             (25, 0xD00D5FD8)):
            self.store(trace, tick, COMPLETION, marker)
        self.store(trace, 26, SYMBOLS["maketic"], 4)
        self.store(trace, 27, SYMBOLS["gametic"], 4)
        trace.frame(28, 29, 126)
        trace.check_final()
        with self.assertRaisesRegex(AssertionError, "extra or out-of-phase angle"):
            self.store(trace, 30, COMPLETION, 0xD00D5FC4)

    def test_frame_statistics_count_colors_nonblack_pixels_and_rows(self):
        self.assertEqual((1, 0, 0), frame_statistics(bytes(320 * 200)))
        frame = bytearray(320 * 200)
        frame[0] = 7
        frame[320 * 5 + 3] = 7
        frame[320 * 199 + 319] = 200
        self.assertEqual((3, 3, 3), frame_statistics(bytes(frame)))
        self.assertEqual((3, 2, 2), frame_statistics(b"\x00\x01\x00\x02", width=2))
        with self.assertRaises(AssertionError):
            frame_statistics(b"\x00\x01\x00", width=2)

    def test_repeated_equal_command_stores_are_not_collapsed(self):
        trace = self.trace()
        with self.assertRaisesRegex(AssertionError, "extra or out-of-phase turn"):
            self.store(trace, 15, COMPLETION, TURN)

    def test_counter_initialization_masks_and_increments_fail_closed(self):
        with self.assertRaisesRegex(AssertionError, "unobserved initial counters"):
            GameplayTrace(SYMBOLS).begin_phase(0, 0)
        for name, value, mask, message in (
            ("gametic", 1, 3, "partial post-init"),
            ("gametic", 3, 15, "reset/decreased/jumped"),
            ("maketic", 1, 15, "reset/decreased/jumped"),
            ("ticdup", 2, 15, "ticdup must be one"),
            ("gametic", 0xFFFFFFFF, 15, "negative/wrapped"),
        ):
            with self.subTest(name=name, value=value):
                with self.assertRaisesRegex(AssertionError, message):
                    self.store(self.initialized(), 4, SYMBOLS[name], value, mask)
        with self.assertRaisesRegex(AssertionError, "missed BSS initialization"):
            self.store(GameplayTrace(SYMBOLS), 0, SYMBOLS["gametic"], 7)

    def test_only_successful_ordered_retired_stores_count(self):
        trace = self.initialized()
        saved = trace.last_order
        for flags in ({"valid": False}, {"memory_valid": False},
                      {"write": False}, {"trap": True}):
            self.store(trace, 4, SYMBOLS["gametic"], 9, **flags)
        self.assertEqual(saved, trace.last_order)
        self.assertEqual(0, trace.counters["gametic"])
        with self.assertRaisesRegex(AssertionError, "duplicate/out-of-order"):
            trace.retirement(saved, 5, 0x100, 1, 15)

    def test_counter_cannot_overtake_producer_and_completion_is_full_width(self):
        trace = self.initialized()
        self.store(trace, 4, SYMBOLS["gametic"], 1)
        self.store(trace, 5, SYMBOLS["gametic"], 2)
        with self.assertRaises(AssertionError):
            self.store(trace, 6, SYMBOLS["gametic"], 3)
        trace = self.initialized()
        trace.begin_phase(0, 10)
        with self.assertRaisesRegex(AssertionError, "partial completion"):
            self.store(trace, 11, COMPLETION, MAKE, 3)

    def test_elf_symbols_are_unambiguous_word_bss_objects(self):
        text = "00000100 00000004 B gametic\n00000104 00000004 b maketic\n00000108 00000004 B ticdup\n"
        self.assertEqual(SYMBOLS, parse_symbols(text))
        for invalid in (text + text, text.replace(" b maketic", " D maketic"),
                        text.replace("00000104", "00000103"),
                        text.replace("00000004", "00000008"),
                        text.replace("ticdup", "other")):
            with self.assertRaises(AssertionError):
                parse_symbols(invalid)
