"""DUT inputs that a per-clock cocotb model drives, each written only when its value changes."""
from __future__ import annotations


class DrivenInputs:
    """Bound input handles and the last value written to each.

    Why: a model that wakes every clock and rewrites unchanged inputs through
    `dut.<name>` spends most of a test's wall time in handle lookups and
    writes (#230); binding once and skipping unchanged writes keeps the same
    pin values for a fraction of the cost.

    A name reads back the last value written, which is also the value the DUT
    saw at the current edge until this cycle drives it again, because cocotb
    applies writes after the edge.
    """

    def __init__(self, dut, **initial: int):
        self._handles = {name: getattr(dut, name) for name in initial}
        self._driven = {}
        for name, value in initial.items():
            self._handles[name].value = value
            self._driven[name] = value

    def drive(self, **values: int) -> None:
        for name, value in values.items():
            if self._driven[name] != value:
                self._handles[name].value = value
                self._driven[name] = value

    def __getitem__(self, name: str) -> int:
        return self._driven[name]
