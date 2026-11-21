"""Self-test of the common UART wire helper (wiki/src/common/dv/uart/dv-plan.md)
against the common UART PHY at 12.5 clocks per bit: bytes the helper sends
arrive at the receiver, bytes the transmitter sends arrive at the helper, a
low stop bit is a framing error at the receiver, every bit the helper drives
starts on its exact edge or picosecond, the helper samples each received bit
at its centre (and, with a window, either side), and a bad frame on tx makes
the helper raise. Runs in clock mode (rising and falling drive) and in time
mode."""
import math
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, RisingEdge, Timer, with_timeout
from cocotb.utils import get_sim_time

from src.common.dv.uart.uart_wire import UartWire, frame_bits

PERIOD_NS = 40
CLOCKS_PER_BIT = 12.5
BIT_PS = CLOCKS_PER_BIT * PERIOD_NS * 1000
WINDOW_PS = 100_000
# Why: 0x55 makes every bit boundary of the frame a transition.
TIMING_BYTE = 0x55


async def setup(dut):
    cocotb.start_soon(Clock(dut.clk_i, PERIOD_NS, unit="ns").start())
    dut.reset_i.value = 1
    dut.rx_i.value = 1
    dut.tx_valid_i.value = 0
    dut.tx_byte_i.value = 0
    dut.force_i.value = 0
    dut.force_level_i.value = 1
    await ClockCycles(dut.clk_i, 4)
    dut.reset_i.value = 0
    await ClockCycles(dut.clk_i, 4)


def wires(dut):
    return (UartWire(rx=dut.rx_i, tx=dut.tx_o, clock=dut.clk_i, clocks_per_bit=CLOCKS_PER_BIT),
            UartWire(rx=dut.rx_i, tx=dut.tx_o, clock=dut.clk_i, clocks_per_bit=CLOCKS_PER_BIT,
                     drive_edge="falling"),
            UartWire(rx=dut.rx_i, tx=dut.tx_o, bit_ps=BIT_PS, sample_window_ps=WINDOW_PS))


async def collect_rx(dut, sink, errors):
    while True:
        await RisingEdge(dut.clk_i)
        if int(dut.rx_valid_o.value):
            sink.append(int(dut.rx_byte_o.value))
        if int(dut.rx_framing_error_o.value):
            errors.append(len(sink))


async def transmit(dut, data):
    for value in data:
        dut.tx_byte_i.value = value
        dut.tx_valid_i.value = 1
        while True:
            await RisingEdge(dut.clk_i)
            if int(dut.tx_ready_o.value):
                break
        await FallingEdge(dut.clk_i)
        dut.tx_valid_i.value = 0


async def loopback(dut):
    rng = random.Random(int(cocotb.RANDOM_SEED))
    received, errors = [], []
    cocotb.start_soon(collect_rx(dut, received, errors))
    for wire in wires(dut):
        if wire.bit_ps is not None:
            # Why: time mode starts a quarter clock off the edges, as a host UART
            # is not synchronous to the board clock.
            await Timer(PERIOD_NS // 4, unit="ns")
        data = bytes(rng.getrandbits(8) for _ in range(40))
        received.clear()
        await wire.send(data)
        await ClockCycles(dut.clk_i, 20)
        assert bytes(received) == data, "helper to receiver"
        out = bytes(rng.getrandbits(8) for _ in range(40))
        receiving = cocotb.start_soon(wire.receive(len(out)))
        await transmit(dut, out)
        assert await with_timeout(receiving, 50, "us") == out, "transmitter to helper"
    assert not errors, "framing error on good frames"


def bit_at_edge(edges: int) -> int:
    """The frame bit that holds the line after this many drive edges of the frame."""
    return max(k for k in range(10) if math.floor(k * CLOCKS_PER_BIT) <= edges)


async def check_clock_send(dut, wire):
    """Sample rx_i between drive edges and check bit k starts at edge floor(k*12.5)."""
    falling = wire.drive_edge == "falling"
    levels = frame_bits(TIMING_BYTE)
    sending = cocotb.start_soon(wire.send_byte(TIMING_BYTE))
    if falling:
        await FallingEdge(dut.clk_i)
    for edges in range(math.floor(10 * CLOCKS_PER_BIT)):
        await (RisingEdge(dut.clk_i) if falling else FallingEdge(dut.clk_i))
        expect = levels[bit_at_edge(edges)]
        assert int(dut.rx_i.value) == expect, \
            f"{wire.drive_edge} drive: rx_i {int(dut.rx_i.value)} after {edges} edges, bit {bit_at_edge(edges)} is {expect}"
    await sending


async def check_time_send(dut, wire):
    """Check the line 1 ns after each bit starts and 1 ns before it ends."""
    levels = frame_bits(TIMING_BYTE)
    start = get_sim_time(unit="ps")
    sending = cocotb.start_soon(wire.send_byte(TIMING_BYTE))
    for bit, level in enumerate(levels):
        for at in (round(bit * BIT_PS) + 1000, round((bit + 1) * BIT_PS) - 1000):
            await Timer(start + at - get_sim_time(unit="ps"), unit="ps")
            assert int(dut.rx_i.value) == level, f"time drive: bit {bit} wrong at {at} ps"
    await sending


async def send_timing(dut):
    for wire in wires(dut):
        await ClockCycles(dut.clk_i, 20)
        await (check_time_send(dut, wire) if wire.bit_ps is not None else check_clock_send(dut, wire))


async def force_clocked(dut, per_clock):
    """Put one level on tx for each clock, changed 1 ns after each rising edge."""
    for level in per_clock:
        dut.force_level_i.value = level
        await RisingEdge(dut.clk_i)
        # Why: the helper samples tx at the rising edge; changing it there would race.
        await Timer(1, unit="ns")
    dut.force_level_i.value = 1


async def force_timed(dut, segments):
    """Put (until_ps, level) segments on tx, from now."""
    start = get_sim_time(unit="ps")
    for until, level in segments:
        dut.force_level_i.value = level
        await Timer(start + until - get_sim_time(unit="ps"), unit="ps")
    dut.force_level_i.value = 1


def centre_only_clocked(value):
    """A frame whose bits hold their true level only in the clock the helper samples."""
    levels = frame_bits(value)
    sampled = {math.floor((bit + 0.5) * CLOCKS_PER_BIT) - 1: level for bit, level in enumerate(levels)}
    per_clock = []
    for clock in range(math.floor(10 * CLOCKS_PER_BIT)):
        owner = levels[min(9, math.floor(clock / CLOCKS_PER_BIT))]
        per_clock.append(0 if clock == 0 else sampled.get(clock, 1 - owner))
    return per_clock


def centre_only_timed(value, half_ps):
    """A frame whose bits hold their true level only within half_ps of their centre."""
    segments = []
    for bit, level in enumerate(frame_bits(value)):
        centre = round((bit + 0.5) * BIT_PS)
        wrong = 0 if bit == 0 else 1 - level
        segments += [(centre - half_ps, wrong), (centre + half_ps, level),
                     (round((bit + 1) * BIT_PS), wrong if bit < 9 else level)]
    return segments


async def receive_forced(dut, wire, force):
    dut.force_level_i.value = 1
    await ClockCycles(dut.clk_i, 30)
    cocotb.start_soon(force)
    try:
        return await wire.receive_byte()
    except AssertionError as error:
        return str(error)
    finally:
        dut.force_level_i.value = 1
        await ClockCycles(dut.clk_i, 30)


async def sample_points(dut):
    """The helper reads the centre of each bit, and nothing either side of it."""
    clocked, _, timed = wires(dut)
    dut.force_i.value = 1
    for value in (0xA5, 0x3C):
        got = await receive_forced(dut, clocked, force_clocked(dut, centre_only_clocked(value)))
        assert got == value, f"clock mode read {got!r} from a centre-only frame of {value:#04x}"
        got = await receive_forced(dut, timed, force_timed(dut, centre_only_timed(value, WINDOW_PS + 20_000)))
        assert got == value, f"time mode read {got!r} from a centre-only frame of {value:#04x}"
    dut.force_i.value = 0


async def rejects_bad_frames(dut):
    """A low stop bit, a start pulse under half a bit and a glitch inside the window raise."""
    clocked, _, timed = wires(dut)
    half_bit = math.floor(CLOCKS_PER_BIT / 2)
    short_start_clocked = [0] * (half_bit - 1) + [1] * 120
    short_start_timed = [(round(BIT_PS / 2) - WINDOW_PS - 50_000, 0), (round(10 * BIT_PS), 1)]
    low_stop = [0] * 10
    ones = [0] + [1] * 9
    # Why: an all-ones byte with a 40 ns low glitch over the late window sample of bit 3.
    late = round(3.5 * BIT_PS) + WINDOW_PS
    glitch = [(round(BIT_PS), 0), (late - 20_000, 1), (late + 20_000, 0), (round(10 * BIT_PS), 1)]
    line = UartWire(rx=dut.force_level_i, clock=dut.clk_i, clocks_per_bit=CLOCKS_PER_BIT)
    cases = (
        (clocked, line.send_levels(low_stop), "stop bit"),
        (timed, line.send_levels(low_stop), "stop bit"),
        (clocked, line.send_levels(ones), 0xFF),
        (timed, line.send_levels(ones), 0xFF),
        (clocked, force_clocked(dut, short_start_clocked), "start bit"),
        (timed, force_timed(dut, short_start_timed), "start bit"),
        (timed, force_timed(dut, glitch), "unstable"),
    )
    dut.force_i.value = 1
    for wire, force, expect in cases:
        got = await receive_forced(dut, wire, force)
        if isinstance(expect, str):
            assert isinstance(got, str) and expect in got, f"expected '{expect}', got {got!r}"
        else:
            assert got == expect, f"helper read {got!r}, expected {expect:#04x}"
    dut.force_i.value = 0


@cocotb.test()
async def uart_wire_contract(dut):
    await setup(dut)
    await loopback(dut)
    await send_timing(dut)
    await framing_error(dut)
    await sample_points(dut)
    await rejects_bad_frames(dut)


async def framing_error(dut):
    received, errors = [], []
    cocotb.start_soon(collect_rx(dut, received, errors))
    clocked = wires(dut)[0]
    await clocked.send(b"\x5a", stop=0)
    await clocked.idle(12)
    assert errors, "a low stop bit was not a framing error at the receiver"
