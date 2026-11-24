"""Independent nonzero-slot architectural stimuli for the real input ledger."""
import copy
import json
import unittest
from pathlib import Path

from src.nand2doom.dv.software.doom_gameplay import GameplayTrace, MAKE, BREAK, TURN, COMPLETION
from src.nand2doom.dv.software.input_witness import SEAMS_RV32_GCC, InputWitness

from src.nand2doom.dv.software.input_witness_vectors import abi_fixture, initialized_trace, rows


def exercise(records, a=None, slot=7):
    a = a or abi_fixture()
    t = initialized_trace(a, slot)
    events = []
    w = InputWitness(a, t, lambda: events.append('ready'), lambda: events.append('boundary'))
    w.arm(t.phase_start + 1)
    for index, row in enumerate(records):
        r = dict(row, order=t.last_order + 1 if row.get('order') is None else row['order'],
                 tick=t.phase_start + 10 + index * 10)
        # Why: all retirements share one monotonic order, including non-stores.
        if row.get('order') is None:
            r['order'] = max(t.last_order, w.order) + 1
        if r['memory_valid'] and r['memory_write']:
            t.retirement(r['order'], r['tick'], r['address'], r['write_data'], r['write_mask'],
                         valid=r['valid'], trap=r['trap'])
        w.accept(r)
        if events and w.make_start_tick is None:
            w.start_make(r['tick'] + 1)
            w.ack_make(r['tick'] + 2)
        if w.ready_tick is not None and w.break_start_tick is None:
            w.start_break(r['tick'] + 3)
            w.break_ack_tick = r['tick'] + 4
    w.check_final()
    return w, t, events


REPO_ROOT = Path(__file__).resolve().parents[4]


class InputWitnessTests(unittest.TestCase):
    def test_seams_match_the_pinned_compiler(self):
        pin = json.loads((REPO_ROOT / "cfg/toolchain.json").read_text())["rv32_gcc"]["version"]
        self.assertEqual(pin, SEAMS_RV32_GCC,
                         "rv32_gcc pin changed: re-derive input_witness SEAMS from the new "
                         "Doom image's disassembly")

    def test_real_status_make_empty_and_direct_break_nonzero_slots(self):
        for slot in (3, 7, 17):
            with self.subTest(slot=slot):
                w, t, events = exercise(rows(abi_fixture(), slot), slot=slot)
                self.assertEqual([slot, slot + 1], [i['slot'] for i in w.history])
                self.assertEqual(['boundary', 'ready'], events)
                self.assertEqual([1, 0], [i['turns'] for i in w.history])
                self.assertEqual(slot + 2, t.counters['maketic'])
                self.assertLess(w.empty_tick, w.ready_tick)

    def test_neutral_iterations_not_rebased_and_event_latched(self):
        w, _, events = exercise(rows(abi_fixture(), neutral_before=2))
        self.assertEqual([7, 8, 9, 10], [i['slot'] for i in w.history])
        self.assertEqual(2, w.make['index'])
        self.assertEqual(1, events.count('ready'))

    def test_mid_invocation_prepare_waits_clean_entry(self):
        a = abi_fixture()
        # Why: only partial neutral seams, no MAKE yet, so prepare must wait for a clean entry.
        partial = [dict(rows(a)[0], seam='slot', pc=a.pc['slot'],
                        instruction=a.instructions[a.pc['slot']], next_pc=a.pc['slot'] + 4, memory_valid=True,
                        address=a.symbols['maketic'], read_mask=15, read_data=7,
                        rd_write=True, rd=11, rd_data=7)]
        w, _, _ = exercise(partial + rows(a), a)
        self.assertTrue(w.records[0]['pre_boundary'])
        self.assertEqual(7, w.history[0]['slot'])
        publication = next(x for x in rows(a) if x['seam'] == 'publish')
        publication = dict(publication, write_data=8)
        w, _, _ = exercise(partial + [publication] + rows(a, slot=8), a)
        self.assertEqual(8, w.history[0]['slot'])
        for change in ({'rd': 15}, {'read_data': 99, 'rd_data': 99}):
            with self.subTest(change=change):
                with self.assertRaises(AssertionError):
                    exercise([dict(partial[0], **change)] + rows(a), a)

    def test_negative_real_status_fields(self):
        for value in (9, 0x1001, 0x1089, 0x1109, 0x100a, 0x1041, 0x1000):
            with self.subTest(value=value):
                r = rows(abi_fixture())
                next(x for x in r if x['seam'] == 'status').update(read_data=value, rd_data=value)
                with self.assertRaises(AssertionError):
                    exercise(r)

    def test_negative_masks_returns_registers_callbacks_data(self):
        cases = [('status', {'valid': False}), ('status', {'trap': True}),
                 ('status', {'read_mask': 7}), ('status', {'rd': 11}),
                 ('status', {'rd_data': 0}), ('status', {'write_mask': 1}),
                 ('status', {'address': 0x20002000}), ('status', {'instruction': 0x13}),
                 ('data', {'read_data': 0x274, 'rd_data': 0x274}),
                 ('data', {'read_data': 0x80000674, 'rd_data': 0x80000674}),
                 ('zero', {'rd_data': 1}), ('empty_return', {'next_pc': 0}),
                 ('event_return', {'next_pc': 0}), ('process_call', {'next_pc': 0}),
                 ('command_call', {'rd_data': 0}), ('slot', {'rd': 15}),
                 ('slot', {'read_data': 99, 'rd_data': 99}), ('slot', {'read_mask': 3}),
                 ('publish', {'write_mask': 3}), ('publish', {'write_data': 99})]
        for seam, mutation in cases:
            with self.subTest(seam=seam, mutation=mutation):
                r = rows(abi_fixture())
                next(x for x in r if x['seam'] == seam).update(mutation)
                with self.assertRaises(AssertionError):
                    exercise(r)

    def test_missing_chain_aborted_make_and_missing_entry(self):
        for seam in ('entry', 'zero', 'empty_return', 'input_call', 'start_tail',
                     'key_call', 'slot', 'publish', 'command_call'):
            with self.subTest(seam=seam):
                r = rows(abi_fixture())
                r.pop(next(i for i, x in enumerate(r) if x['seam'] == seam))
                with self.assertRaises(AssertionError):
                    exercise(r)

    def test_break_cannot_use_make_empty_path(self):
        a = abi_fixture()
        r = rows(a)
        idx = next(i for i, x in enumerate(r) if x['seam'] == 'key_marker' and x['write_data'] == BREAK)
        extra = copy.deepcopy(next(x for x in r if x['seam'] == 'key_call'))
        r.insert(idx + 2, extra)
        with self.assertRaises(AssertionError):
            exercise(r)

    def test_slot_argument_overwrite_and_extra_turn(self):
        a = abi_fixture()
        for case in ('overwrite', 'extra_turn', 'neutral_turn'):
            with self.subTest(case=case):
                r = rows(a)
                if case == 'overwrite':
                    idx = next(i for i, x in enumerate(r) if x['seam'] == 'command_call')
                    extra = dict(r[idx], pc=0x1234, instruction=0x13, rd=11, rd_data=99)
                else:
                    indices = [i for i, x in enumerate(r) if x['seam'] == 'publish']
                    idx = indices[0 if case == 'extra_turn' else 1]
                    extra = copy.deepcopy(next(x for x in r if x['seam'] == 'turn'))
                r.insert(idx, extra)
                with self.assertRaises(AssertionError):
                    exercise(r)

    def test_order_and_adjacent_equal_reads_not_deduplicated(self):
        a = abi_fixture()
        r = rows(a)
        idx = next(i for i, x in enumerate(r) if x['seam'] == 'slot')
        unrelated = dict(r[idx], pc=0x4000, instruction=0x2083, rd=7)
        r[idx:idx] = [dict(unrelated), dict(unrelated)]
        w, _, _ = exercise(r)
        self.assertEqual(7, w.history[0]['slot'])
        r[3]['order'] = 0
        with self.assertRaisesRegex(AssertionError, 'out-of-order'):
            exercise(r)

    def test_uninitialized_counter_and_preboundary_marker_fail(self):
        a = abi_fixture()
        t = GameplayTrace({n: a.symbols[n] for n in ('gametic', 'maketic', 'ticdup')})
        t.phase = 1
        with self.assertRaises(AssertionError):
            InputWitness(a, t, lambda: None, lambda: None).arm(1)
        r = rows(a)
        marker = next(x for x in r if x['seam'] == 'key_marker')
        with self.assertRaises(AssertionError):
            exercise([marker] + r)


if __name__ == '__main__':
    unittest.main()
