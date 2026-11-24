"""DrivenInputs writes each input once at bind time and then only on a change."""
import unittest
from types import SimpleNamespace

from src.common.dv.driven_inputs import DrivenInputs


class Handle:
    def __init__(self):
        self.writes = []

    @property
    def value(self):
        return self.writes[-1]

    @value.setter
    def value(self, value):
        self.writes.append(value)


class DrivenInputsTests(unittest.TestCase):
    def test_writes_initial_then_only_changes(self):
        dut = SimpleNamespace(ready=Handle(), valid=Handle())
        pins = DrivenInputs(dut, ready=1, valid=0)
        pins.drive(ready=1, valid=0)
        pins.drive(valid=1)
        pins.drive(valid=1, ready=0)
        self.assertEqual([1, 0], dut.ready.writes)
        self.assertEqual([0, 1], dut.valid.writes)
        self.assertEqual((0, 1), (pins["ready"], pins["valid"]))

    def test_same_cycle_override_keeps_the_last_value(self):
        dut = SimpleNamespace(valid=Handle())
        pins = DrivenInputs(dut, valid=1)
        pins.drive(valid=0)
        pins.drive(valid=1)
        self.assertEqual(1, dut.valid.writes[-1])

    def test_unknown_name_fails(self):
        pins = DrivenInputs(SimpleNamespace(valid=Handle()), valid=0)
        with self.assertRaises(KeyError):
            pins.drive(ready=1)


if __name__ == "__main__":
    unittest.main()
