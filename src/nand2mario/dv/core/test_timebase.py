import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge, Timer
from cocotb.utils import get_sim_time


CLOCK_HZ = 25_000_000
CLOCK_PERIOD_NS = 40
PHASE_INCREMENT = 16_384
DOT_INCREMENT = 65_536            # Why: 4,194,304 / 25,000,000 reduced by 64.
PHASE_MODULUS = 390_625
PULSES_PER_PERIOD = PHASE_INCREMENT
DOTS_PER_PERIOD = DOT_INCREMENT
FINAL_CONSUMER_EDGE = PHASE_MODULUS
REQUIRED_CYCLES = FINAL_CONSUMER_EDGE + 24


def advance_phase(phase: int) -> tuple[int, int]:
    total = phase + PHASE_INCREMENT
    pulse = int(total >= PHASE_MODULUS)
    return total - (PHASE_MODULUS if pulse else 0), pulse


def advance_dot_phase(phase: int) -> tuple[int, int]:
    total = phase + DOT_INCREMENT
    pulse = int(total >= PHASE_MODULUS)
    return total - (PHASE_MODULUS if pulse else 0), pulse


def consumer_edge_for_pulse(pulse_number: int) -> int:
    return (
        pulse_number * PHASE_MODULUS + PHASE_INCREMENT - 1
    ) // PHASE_INCREMENT


def required_environment() -> tuple[int, int, int]:
    clock_hz = int(os.environ["DV_CLOCK_HZ"])
    max_cycles = int(os.environ["DV_MAX_CYCLES"])
    seed = int(os.environ["DV_SEED"])
    assert clock_hz == CLOCK_HZ, (
        f"clock contract mismatch: expected={CLOCK_HZ} observed={clock_hz}"
    )
    assert max_cycles >= REQUIRED_CYCLES, (
        f"cycle bound too small: required={REQUIRED_CYCLES} "
        f"observed={max_cycles}"
    )
    return clock_hz, max_cycles, seed


async def reset_to_phase_zero(dut) -> None:
    await FallingEdge(dut.clk_i)
    dut.reset_i.value = 1
    await Timer(1, unit="ns")
    assert int(dut.machine_cycle_enable_o.value) == 0, (
        "machine-cycle enable remained asserted during reset"
    )
    assert int(dut.dot_enable_o.value) == 0, "dot enable remained asserted during reset"
    await RisingEdge(dut.clk_i)
    await RisingEdge(dut.clk_i)
    await FallingEdge(dut.clk_i)
    dut.reset_i.value = 0


@cocotb.test(timeout_time=20, timeout_unit="ms")
async def machine_cycle_enable_contract(dut) -> None:
    _, max_cycles, seed = required_environment()
    cocotb.log.info(
        "n2m timebase seed=%d max_cycles=%d ratio=%d/%d",
        seed,
        max_cycles,
        PHASE_INCREMENT,
        PHASE_MODULUS,
    )

    dut.reset_i.value = 1
    cocotb.start_soon(Clock(dut.clk_i, CLOCK_PERIOD_NS, unit="ns").start())
    await reset_to_phase_zero(dut)

    phase = dot_phase = 0
    pulse_edges: list[int] = []
    dot_edges: list[int] = []
    dots_since_machine = 0
    for cycle in range(1, 129):
        next_phase, expected = advance_phase(phase)
        next_dot_phase, expected_dot = advance_dot_phase(dot_phase)
        observed = int(dut.machine_cycle_enable_o.value)
        observed_dot = int(dut.dot_enable_o.value)
        assert observed == expected, (
            f"cycle={cycle} phase={phase} expected_enable={expected} "
            f"observed_enable={observed}"
        )
        assert observed_dot == expected_dot, (
            f"cycle={cycle} dot_phase={dot_phase} expected_dot={expected_dot} "
            f"observed_dot={observed_dot}"
        )
        if observed_dot:
            dot_edges.append(cycle)
            dots_since_machine += 1
        if observed:
            pulse_edges.append(cycle)
            assert observed_dot, f"cycle={cycle}: machine pulse without a dot pulse"
            assert dots_since_machine == 4, (
                f"cycle={cycle}: {dots_since_machine} dots since the previous machine pulse"
            )
            dots_since_machine = 0
        await RisingEdge(dut.clk_i)
        await ReadOnly()
        phase = next_phase
        dot_phase = next_dot_phase

    assert pulse_edges[:5] == [24, 48, 72, 96, 120], (
        f"unexpected initial pulse edges: observed={pulse_edges[:5]}"
    )
    assert dot_edges[:8] == [6, 12, 18, 24, 30, 36, 42, 48], (
        f"unexpected initial dot edges: observed={dot_edges[:8]}"
    )

    await reset_to_phase_zero(dut)
    await RisingEdge(dut.clk_i)
    first_active_edge_ns = int(get_sim_time(unit="ns"))
    dot_pulses = 0

    async def count_dots():
        nonlocal dot_pulses
        while True:
            await RisingEdge(dut.dot_enable_o)
            dot_pulses += 1

    counter = cocotb.start_soon(count_dots())

    previous_consumer_edge = 0
    for pulse_number in range(1, PULSES_PER_PERIOD + 1):
        expected_consumer_edge = consumer_edge_for_pulse(pulse_number)
        await RisingEdge(dut.machine_cycle_enable_o)
        observed_rise_ns = int(get_sim_time(unit="ns"))
        expected_rise_ns = (
            first_active_edge_ns
            + (expected_consumer_edge - 2) * CLOCK_PERIOD_NS
        )
        assert observed_rise_ns == expected_rise_ns, (
            f"pulse={pulse_number} expected_consumer_edge="
            f"{expected_consumer_edge} expected_rise_ns={expected_rise_ns} "
            f"observed_rise_ns={observed_rise_ns}"
        )
        await FallingEdge(dut.machine_cycle_enable_o)
        observed_fall_ns = int(get_sim_time(unit="ns"))
        expected_fall_ns = expected_rise_ns + CLOCK_PERIOD_NS
        assert observed_fall_ns == expected_fall_ns, (
            f"pulse={pulse_number} was not exactly one system cycle: "
            f"expected_fall_ns={expected_fall_ns} "
            f"observed_fall_ns={observed_fall_ns}"
        )
        if previous_consumer_edge:
            gap = expected_consumer_edge - previous_consumer_edge
            assert gap in (23, 24), (
                f"pulse={pulse_number} unexpected_gap={gap}"
            )
        previous_consumer_edge = expected_consumer_edge

    assert previous_consumer_edge == FINAL_CONSUMER_EDGE
    assert int(dut.machine_cycle_enable_o.value) == 0, (
        "enable did not clear when the exact accumulator period wrapped"
    )
    counter.cancel()
    assert dot_pulses == DOTS_PER_PERIOD, (
        f"dot pulses in one reduced-ratio period: expected={DOTS_PER_PERIOD} observed={dot_pulses}"
    )
    assert dot_pulses == 4 * PULSES_PER_PERIOD

    await RisingEdge(dut.machine_cycle_enable_o)
    next_rise_ns = int(get_sim_time(unit="ns"))
    expected_next_rise_ns = (
        first_active_edge_ns
        + (FINAL_CONSUMER_EDGE + 24 - 2) * CLOCK_PERIOD_NS
    )
    assert next_rise_ns == expected_next_rise_ns, (
        f"post-period phase drift: expected_rise_ns={expected_next_rise_ns} "
        f"observed_rise_ns={next_rise_ns}"
    )
