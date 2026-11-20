"""Absolute-cycle oracle and timestamp stimulus for the production C clock."""
from dataclasses import dataclass

MASK = (1 << 32) - 1
CYCLES_PER_MS = 25_000


@dataclass(frozen=True)
class Trace:
    name: str
    origin: int
    elapsed: tuple[int, ...]
    initial_cycles: int = 0

    @property
    def expected(self) -> tuple[tuple[int, int], ...]:
        # Absolute integers, not modular deltas or the implementation's carry.
        return tuple(((self.initial_cycles + elapsed) // CYCLES_PER_MS & MASK,
                      (self.initial_cycles + elapsed) % CYCLES_PER_MS)
                     for elapsed in self.elapsed)


def traces() -> tuple[Trace, ...]:
    period = 1 << 32
    return (
        Trace("nonzero_origin_fraction", 0x12345678,
              (0, 1, 12345, 24999, 25000, 25001, 49999, 50000)),
        Trace("hardware_zero", MASK - 10000, (0, 9999, 10000, 10001, 24999, 25000)),
        Trace("whole_period_alias", 123456789,
              (0, period - 1, period, 172 * 25_000_000, 2 * period - 1,
               2 * period, 3 * period - 1, 3 * period, 3 * period + 24999)),
        Trace("maximum_delta_with_fraction", 79,
              (0, 24999, 24999 + MASK, 24999 + 2 * MASK, 25000 + 2 * MASK)),
        # Reach the modulo-ms boundary without executing 25,000 hardware periods
        # on RTL. The seed is a valid accumulator state after this absolute age.
        Trace("millisecond_rollover", MASK - 5,
              (0, 1, 24999, 25000, 25001, 50000, MASK),
              MASK * CYCLES_PER_MS + 24999),
    )


def old_clock_fails() -> None:
    trace = traces()[2]
    assert all(((elapsed & MASK) // CYCLES_PER_MS) == expected[0]
               for elapsed, expected in zip(trace.elapsed[:2], trace.expected[:2]))
    assert ((trace.elapsed[2] & MASK) // CYCLES_PER_MS) != trace.expected[2][0]


# The runtime probe returns (elapsed ms, timestamp reads). Sleep timestamps
# advance by step at each read; expected durations here are absolute cycle math.
RUNTIME_CASES = (
    # origin, pre-sleep age, tick step, sleep milliseconds
    (MASK - 10000, 0, 5000, 0),
    (MASK - 10000, 24999, 5000, 1),
    (MASK - 50000, 0, 12500, 3),
    (0x98765432, 0, 25_000_000, 172000),
)
