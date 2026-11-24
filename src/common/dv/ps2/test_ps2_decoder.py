"""Contract of the common Set-2 event decoder (wiki/src/common/dv/ps2/dv-plan.md):
directed make, break, extended and extended-break sequences, then seeded
random byte streams with random event back-pressure and clears, checked
every clock against a reference model written from the MAS."""

from __future__ import annotations

import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ReadOnly, RisingEdge

PERIOD_NS = 40
# Ref: PS/2 scan code set 2 prefixes.
PREFIX_EXTENDED, PREFIX_BREAK = 0xE0, 0xF0


class Reference:
    """The pending qualifiers of the MAS and the outputs they imply."""

    def __init__(self) -> None:
        self.extended = False
        self.key_break = False

    def outputs(self, valid: bool, value: int, ready: bool) -> tuple[int, int, int]:
        prefix = value in (PREFIX_EXTENDED, PREFIX_BREAK)
        event_valid = int(valid and not prefix)
        event = (int(self.extended) << 9) | (int(self.key_break) << 8) | value
        return event_valid, event, int(prefix or ready)

    def step(self, valid: bool, value: int, ready: bool, clear: bool) -> None:
        if valid and value == PREFIX_EXTENDED:
            self.extended = True
        elif valid and value == PREFIX_BREAK:
            self.key_break = True
        elif valid and ready:
            self.extended = self.key_break = False
        if clear:
            self.extended = self.key_break = False


class Bench:
    def __init__(self, dut) -> None:
        self.dut = dut
        self.reference = Reference()
        self.events: list[int] = []
        self.checked = 0

    async def cycle(self, *, valid: bool = False, value: int = 0,
                    ready: bool = True, clear: bool = False) -> None:
        dut = self.dut
        dut.byte_valid_i.value = int(valid)
        dut.byte_i.value = value
        dut.event_ready_i.value = int(ready)
        dut.clear_i.value = int(clear)
        await ReadOnly()
        expected = self.reference.outputs(valid, value, ready)
        observed = (int(dut.event_valid_o.value), int(dut.event_o.value),
                    int(dut.byte_ready_o.value))
        if expected[0]:
            assert observed == expected, f"byte {value:#04x}: {observed} != {expected}"
        else:
            assert observed[0] == 0 and observed[2] == expected[2], (
                f"byte {value:#04x} valid={int(valid)}: {observed} != {expected}")
        if expected[0] and ready:
            self.events.append(observed[1])
        self.checked += 1
        self.reference.step(valid, value, ready, clear)
        await RisingEdge(dut.clk_i)

    async def stream(self, data: list[int]) -> None:
        for value in data:
            await self.cycle(valid=True, value=value)

    def take(self) -> list[int]:
        events, self.events = self.events, []
        return events


@cocotb.test()
async def ps2_decoder_contract(dut) -> None:
    """Prefix sequences, back-pressure and clears against the reference."""

    seed = int(os.environ.get("N2A_SEED", "23"))
    print(f"seed={seed}")
    rng = random.Random(seed)
    cocotb.start_soon(Clock(dut.clk_i, PERIOD_NS, unit="ns").start())
    dut.byte_valid_i.value = 0
    dut.byte_i.value = 0
    dut.event_ready_i.value = 1
    dut.clear_i.value = 0
    dut.reset_i.value = 1
    for _ in range(4):
        await RisingEdge(dut.clk_i)
    dut.reset_i.value = 0
    await RisingEdge(dut.clk_i)
    bench = Bench(dut)

    # Ref: PS/2 scan code set 2: 1D is W, E0 75 is up arrow, F0 prefixes a
    # break. Idle cycles between bytes must not change the qualifiers.
    await bench.stream([0x1D, PREFIX_BREAK, 0x1D, PREFIX_EXTENDED, 0x75])
    await bench.cycle()
    await bench.stream([PREFIX_EXTENDED])
    await bench.cycle()
    await bench.stream([PREFIX_BREAK, 0x75])
    assert bench.take() == [0x01D, 0x11D, 0x275, 0x375]

    # Why: a refused byte keeps its qualifiers so the producer can retry it.
    await bench.stream([PREFIX_EXTENDED, PREFIX_BREAK])
    await bench.cycle(valid=True, value=0x6B, ready=False)
    await bench.cycle(valid=True, value=0x6B, ready=False)
    await bench.cycle(valid=True, value=0x6B)
    await bench.cycle(valid=True, value=0x6B)
    assert bench.take() == [0x36B, 0x06B]

    # Why: a clear drops pending qualifiers, so the next code is plain.
    await bench.stream([PREFIX_EXTENDED])
    await bench.cycle(clear=True)
    await bench.stream([0x75])
    await bench.stream([PREFIX_BREAK])
    await bench.cycle(valid=True, value=0x1C, clear=True)
    await bench.stream([0x1C])
    assert bench.take() == [0x075, 0x11C, 0x01C]

    # Ref: prefixes and a few codes weighted up, so long prefix runs, refusals
    # and clears between them all occur.
    choices = [PREFIX_EXTENDED, PREFIX_BREAK, 0x1D, 0x75, 0x00, 0xFF]
    for _ in range(4000):
        valid = rng.random() < 0.7
        value = rng.choice(choices) if rng.random() < 0.6 else rng.randrange(256)
        await bench.cycle(valid=valid, value=value, ready=rng.random() < 0.7,
                          clear=rng.random() < 0.05)
    events = bench.take()
    assert len(events) > 100
    kinds = {event >> 8 for event in events}
    assert kinds == {0, 1, 2, 3}, f"missing qualifier combinations: {kinds}"
    print(f"PS/2 decoder contract PASS: {bench.checked} cycles checked, {len(events)} random events")
