"""Self-test of the common UART wire helper (wiki/src/common/dv/uart/dv-plan.md)
against the common UART PHY at 12.5 clocks per bit: bytes the helper sends
arrive at the receiver, bytes the transmitter sends arrive at the helper, a
low stop bit is a framing error at the receiver, and a bad frame on tx makes
the helper raise. Runs in clock mode and in time mode."""
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, RisingEdge, Timer, with_timeout

from src.common.dv.uart.uart_wire import UartWire

PERIOD_NS = 40
CLOCKS_PER_BIT = 12.5


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
    clocked = UartWire(rx=dut.rx_i, tx=dut.tx_o, clock=dut.clk_i, clocks_per_bit=CLOCKS_PER_BIT)
    # Why: time mode starts a quarter clock off the edges, as a host UART is not
    # synchronous to the board clock.
    timed = UartWire(rx=dut.rx_i, tx=dut.tx_o, bit_ps=CLOCKS_PER_BIT * PERIOD_NS * 1000,
                     sample_window_ps=100_000)
    return clocked, timed


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


async def framing_error(dut):
    received, errors = [], []
    cocotb.start_soon(collect_rx(dut, received, errors))
    clocked, _ = wires(dut)
    await clocked.send(b"\x5a", stop=0)
    await clocked.idle(12)
    assert errors, "a low stop bit was not a framing error at the receiver"


async def rejects_bad_frames(dut):
    forced = UartWire(rx=dut.force_level_i, clock=dut.clk_i, clocks_per_bit=CLOCKS_PER_BIT)
    dut.force_i.value = 1
    for wire in wires(dut):
        for levels, expect in (([0] * 10, None), ([0] + [1] * 9, 0xFF)):
            dut.force_level_i.value = 1
            await ClockCycles(dut.clk_i, 30)
            cocotb.start_soon(forced.send_levels(levels))
            try:
                value = await wire.receive_byte()
            except AssertionError as error:
                assert expect is None and "stop bit" in str(error), str(error)
            else:
                assert value == expect, f"helper accepted a bad frame as {value:#04x}"
            dut.force_level_i.value = 1
            await ClockCycles(dut.clk_i, 30)
    dut.force_i.value = 0


@cocotb.test()
async def uart_wire_contract(dut):
    await setup(dut)
    await loopback(dut)
    await framing_error(dut)
    await rejects_bad_frames(dut)
