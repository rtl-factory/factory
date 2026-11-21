"""The one cocotb UART wire driver and receiver for every bench
(wiki/src/common/dv/uart/dv-plan.md).

A frame is 8N1: a start bit, eight data bits LSB first, a stop bit. Bit
timing comes in one of two forms:

- clock mode, `clock` and `clocks_per_bit`: bit k of a frame starts at edge
  floor(k * clocks_per_bit) of `clock` after the frame's first edge, so a
  fractional rate such as 12.5 clocks per bit (2 Mbaud at 25 MHz) alternates
  12 and 13 clocks. Levels change just after the chosen edge, the way a
  synchronous bench drives a pin.
- time mode, `bit_ps`: bit k starts at round(k * bit_ps) picoseconds after the
  frame starts, independent of any clock, the way a host UART drives a board.

Received bits are sampled at the centre of each bit, floor((k + 0.5) *
clocks_per_bit) edges or round((k + 0.5) * bit_ps) ps after the start edge,
and a frame whose start bit is not low or whose stop bit is not high at its
centre raises AssertionError.
"""
from __future__ import annotations

import math

import cocotb
from cocotb.triggers import FallingEdge, RisingEdge, Timer
from cocotb.utils import get_sim_time


def frame_bits(value: int, *, stop: int = 1) -> list[int]:
    """The ten line levels of one 8N1 frame; stop=0 makes a framing error."""
    return [0, *((value >> bit) & 1 for bit in range(8)), stop]


class UartWire:
    def __init__(self, *, rx=None, tx=None, clock=None, clocks_per_bit: float | None = None,
                 bit_ps: float | None = None, drive_edge: str = "rising",
                 sample_window_ps: int = 0, stop_bits: int = 1):
        """rx is the DUT input this bench drives; tx is the DUT output it receives.

        drive_edge "falling" drives each level after a falling edge of clock
        (the bench then starts a frame on the next falling edge) instead of
        just after the rising edge the caller is at. sample_window_ps > 0, in
        time mode, samples each received bit at its centre and that far either
        side and requires all three to agree. stop_bits is the idle line time
        after each sent frame, in bits.
        """
        if (clocks_per_bit is None) == (bit_ps is None):
            raise ValueError("give exactly one of clocks_per_bit (with clock) or bit_ps")
        if clocks_per_bit is not None and clock is None:
            raise ValueError("clocks_per_bit needs the clock it counts")
        if drive_edge not in ("rising", "falling"):
            raise ValueError("drive_edge is 'rising' or 'falling'")
        self.rx, self.tx, self.clock = rx, tx, clock
        self.clocks_per_bit, self.bit_ps = clocks_per_bit, bit_ps
        self.drive_edge, self.sample_window_ps, self.stop_bits = drive_edge, sample_window_ps, stop_bits

    def _edge(self, falling: bool = False):
        return FallingEdge(self.clock) if falling else RisingEdge(self.clock)

    async def send_levels(self, levels) -> None:
        """Drive raw line levels, one bit time each, then leave the line where the last level put it."""
        levels = list(levels)
        if self.bit_ps is not None:
            start = get_sim_time(unit="ps")
            for index, level in enumerate(levels):
                self.rx.value = level
                await Timer(start + round((index + 1) * self.bit_ps) - get_sim_time(unit="ps"), unit="ps")
            return
        falling = self.drive_edge == "falling"
        if falling:
            await self._edge(True)
        edges = 0
        for index, level in enumerate(levels):
            self.rx.value = level
            target = math.floor((index + 1) * self.clocks_per_bit)
            while edges < target:
                await self._edge(falling)
                edges += 1

    async def send_byte(self, value: int, *, stop: int = 1) -> None:
        await self.send(bytes([value]), stop=stop)

    async def send(self, data: bytes, *, stop: int = 1) -> None:
        """Send bytes back to back as 8N1 frames; stop=0 sends every frame with a low stop bit."""
        levels: list[int] = []
        for value in data:
            levels += frame_bits(value, stop=stop) + [1] * (self.stop_bits - 1)
        await self.send_levels(levels)
        self.rx.value = 1

    async def idle(self, bits: float) -> None:
        """Hold the line high for a number of bit times."""
        self.rx.value = 1
        if self.bit_ps is not None:
            await Timer(round(bits * self.bit_ps), unit="ps")
        else:
            for _ in range(math.floor(bits * self.clocks_per_bit)):
                await self._edge(self.drive_edge == "falling")

    async def _sample_at(self, start: int, bit: int) -> int:
        centre = start + round((bit + 0.5) * self.bit_ps)
        samples = []
        offsets = (-self.sample_window_ps, 0, self.sample_window_ps) if self.sample_window_ps else (0,)
        for offset in offsets:
            await Timer(centre + offset - get_sim_time(unit="ps"), unit="ps")
            samples.append(int(self.tx.value))
        assert len(set(samples)) == 1, \
            f"UART TX bit {bit} unstable within +/-{self.sample_window_ps} ps of its centre"
        return samples[0]

    async def receive_byte(self, *, on_start=None) -> int:
        """Wait for the next start edge on tx and return the byte of that frame.

        on_start, if given, is called at the start edge, before any sample.
        """
        await FallingEdge(self.tx)
        if on_start is not None:
            on_start()
        value = 0
        if self.bit_ps is not None:
            start = get_sim_time(unit="ps")
            levels = [await self._sample_at(start, bit) for bit in range(10)]
        else:
            levels, edges = [], 0
            for bit in range(10):
                target = math.floor((bit + 0.5) * self.clocks_per_bit)
                while edges < target:
                    await self._edge()
                    edges += 1
                levels.append(int(self.tx.value))
        assert levels[0] == 0, "UART start bit not low at its centre"
        assert levels[9] == 1, "UART stop bit not high at its centre"
        for bit in range(8):
            value |= levels[bit + 1] << bit
        return value

    async def receive(self, count: int) -> bytes:
        return bytes([await self.receive_byte() for _ in range(count)])

    def start_receiver(self, sink: bytearray | None = None) -> bytearray:
        """Collect every byte from tx into sink, in the background, and return sink."""
        sink = bytearray() if sink is None else sink

        async def run():
            while True:
                sink.append(await self.receive_byte())

        cocotb.start_soon(run())
        return sink

