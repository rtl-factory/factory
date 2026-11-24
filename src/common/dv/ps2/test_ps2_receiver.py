"""Contract of the common PS/2 line receiver (wiki/src/common/dv/ps2/dv-plan.md):
good frames at non-harmonic pin timing become one byte each, bad start,
parity and stop bits and a stalled frame each give their one-cycle error and
no byte, a disabled receiver ignores the line, and an abort abandons a frame,
even on its stop edge, without a byte, an error or a later timeout."""

from __future__ import annotations

import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ReadOnly, RisingEdge, Timer

PERIOD_NS = 40
FRAME_TIMEOUT_CYCLES = 5000
ERROR_PARITY, ERROR_FRAME, ERROR_TIMEOUT = 4, 2, 1


def odd_parity_bit(value: int) -> int:
    return 0 if value.bit_count() & 1 else 1


class Monitor:
    """Every cycle's outputs, sampled once they settle after the edge."""

    def __init__(self, dut) -> None:
        self.dut = dut
        self.bytes: list[int] = []
        self.errors: list[int] = []

    async def run(self) -> None:
        while True:
            await RisingEdge(self.dut.clk_i)
            await ReadOnly()
            valid = int(self.dut.byte_valid_o.value)
            errors = int(self.dut.errors_o.value)
            assert not (valid and errors), "byte and error on one cycle"
            if valid:
                self.bytes.append(int(self.dut.byte_o.value))
            if errors:
                self.errors.append(errors)

    def take(self) -> tuple[list[int], list[int]]:
        result = (self.bytes, self.errors)
        self.bytes, self.errors = [], []
        return result


async def drive_bit(dut, bit: int, half_ns: int) -> None:
    dut.ps2_data_i.value = bit
    await Timer(half_ns, unit="ns")
    dut.ps2_clk_i.value = 0
    await Timer(half_ns, unit="ns")
    dut.ps2_clk_i.value = 1


async def send_frame(dut, value: int, half_ns: int, *, start: int = 0,
                     parity: int | None = None, stop: int = 1) -> None:
    parity_bit = odd_parity_bit(value) if parity is None else parity
    for bit in [start, *(value >> i & 1 for i in range(8)), parity_bit, stop]:
        await drive_bit(dut, bit, half_ns)
    dut.ps2_data_i.value = 1
    await Timer(half_ns, unit="ns")


async def send_through_parity(dut, value: int, half_ns: int) -> None:
    for bit in [0, *(value >> i & 1 for i in range(8)), odd_parity_bit(value)]:
        await drive_bit(dut, bit, half_ns)


async def stop_edge_with_abort(dut, half_ns: int) -> None:
    """Hold abort_i on the clock that sees the synchronized stop edge."""

    dut.ps2_data_i.value = 1
    await Timer(half_ns, unit="ns")
    dut.ps2_clk_i.value = 0
    await RisingEdge(dut.clk_i)
    await RisingEdge(dut.clk_i)
    dut.abort_i.value = 1
    await RisingEdge(dut.clk_i)
    dut.abort_i.value = 0
    await Timer(half_ns, unit="ns")
    dut.ps2_clk_i.value = 1


async def idle(dut, cycles: int) -> None:
    for _ in range(cycles):
        await RisingEdge(dut.clk_i)


@cocotb.test()
async def ps2_receiver_contract(dut) -> None:
    """Frames to bytes, every discard reason, enable and abort."""

    seed = int(os.environ.get("N2A_SEED", "11"))
    print(f"seed={seed}")
    rng = random.Random(seed)
    cocotb.start_soon(Clock(dut.clk_i, PERIOD_NS, unit="ns").start())
    dut.ps2_clk_i.value = 1
    dut.ps2_data_i.value = 1
    dut.enable_i.value = 1
    dut.abort_i.value = 0
    dut.reset_i.value = 1
    await idle(dut, 4)
    dut.reset_i.value = 0
    await idle(dut, 4)
    monitor = Monitor(dut)
    cocotb.start_soon(monitor.run())

    # Ref: PS/2 devices clock at 10 to 16.7 kHz, a 30 to 50 us half period;
    # shorter, non-harmonic halves keep the run short and still span several
    # system clocks per half.
    sent = [rng.randrange(256) for _ in range(24)] + [0x00, 0xFF, 0xE0, 0xF0]
    for value in sent:
        await send_frame(dut, value, rng.randrange(1000, 2000))
    await idle(dut, 4)
    assert monitor.take() == (sent, [])

    await send_frame(dut, 0x22, 1370, parity=1 - odd_parity_bit(0x22))
    await idle(dut, 4)
    assert monitor.take() == ([], [ERROR_PARITY])
    await send_frame(dut, 0x22, 1370, stop=0)
    await idle(dut, 4)
    assert monitor.take() == ([], [ERROR_FRAME])
    await send_frame(dut, 0x23, 1370, parity=1 - odd_parity_bit(0x23), stop=0)
    await idle(dut, 4)
    assert monitor.take() == ([], [ERROR_PARITY | ERROR_FRAME])

    # Why: a lone idle-high falling edge is a bad start; driving the rest of
    # a frame would contain later low bits that are legal fresh starts.
    await drive_bit(dut, 1, 1370)
    await idle(dut, 4)
    assert monitor.take() == ([], [ERROR_FRAME])

    await drive_bit(dut, 0, 1370)
    await drive_bit(dut, 1, 1370)
    await idle(dut, FRAME_TIMEOUT_CYCLES - 200)
    assert monitor.take() == ([], [])
    await idle(dut, 200)
    assert monitor.take() == ([], [ERROR_TIMEOUT])
    await send_frame(dut, 0x1C, 1370)
    await idle(dut, 4)
    assert monitor.take() == ([0x1C], [])

    dut.enable_i.value = 0
    await send_frame(dut, 0x2D, 1370)
    await send_frame(dut, 0x2D, 1370, stop=0)
    await drive_bit(dut, 1, 1370)
    await idle(dut, FRAME_TIMEOUT_CYCLES + 10)
    assert monitor.take() == ([], [])
    dut.enable_i.value = 1
    await send_frame(dut, 0x2E, 1370)
    await idle(dut, 4)
    assert monitor.take() == ([0x2E], [])

    # Why: an abort on the stop edge must drop the byte and leave the
    # receiver idle, or a ghost timeout follows.
    await send_through_parity(dut, 0x33, 1370)
    await stop_edge_with_abort(dut, 1370)
    await idle(dut, FRAME_TIMEOUT_CYCLES + 10)
    assert monitor.take() == ([], [])
    await send_frame(dut, 0x34, 1370)
    await idle(dut, 4)
    assert monitor.take() == ([0x34], [])

    await drive_bit(dut, 0, 1370)
    await drive_bit(dut, 1, 1370)
    dut.abort_i.value = 1
    await RisingEdge(dut.clk_i)
    dut.abort_i.value = 0
    await idle(dut, FRAME_TIMEOUT_CYCLES + 10)
    assert monitor.take() == ([], [])
    await send_frame(dut, 0x35, 1370)
    await idle(dut, 4)
    assert monitor.take() == ([0x35], [])

    print(f"PS/2 receiver contract PASS: {len(sent)} frames, every discard, enable, abort")
