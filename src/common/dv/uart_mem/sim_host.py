"""The common UART memory host library on a simulated UART line
(wiki/src/common/dv/uart_mem/dv-plan.md).

A bench runs the host library's own code (uart_mem.Client, uart_mem.load)
against a board top or system, instead of re-implementing the load
transaction. The library is blocking, so it runs in a cocotb bridge thread;
its transport and clock are simulated: bytes go out and come back through a
UartWire, and command deadlines count simulated seconds.
"""
from __future__ import annotations

from typing import Callable, TypeVar

import cocotb
from cocotb.task import bridge, resume
from cocotb.triggers import Timer
from cocotb.utils import get_sim_time

from src.common.dv.uart.uart_wire import UartWire
from src.common.scripts.uart_mem import uart_mem as um

T = TypeVar("T")


def sim_seconds() -> float:
    return get_sim_time(unit="ns") / 1e9


class SimTransport:
    """uart_mem.Transport on a bench UartWire: write sends the bytes on the
    DUT's rx pin, read returns what the background receiver collected from
    its tx pin, polling every poll_ps until something arrives or the
    simulated deadline passes. A malformed received frame is raised from
    the next read, in the library call, so the test fails with it there."""

    def __init__(self, wire: UartWire, poll_ps: int):
        self.wire = wire
        self.poll_ps = poll_ps
        self.received = bytearray()
        self.error: AssertionError | None = None
        cocotb.start_soon(self._receive())

    async def _receive(self) -> None:
        # Why: a receiver task that failed on its own would end the test while
        # the bridge thread waits in a read, and cocotb then reports the
        # cancelled read as a second, misleading error.
        try:
            while True:
                self.received.append(await self.wire.receive_byte())
        except AssertionError as error:
            self.error = error

    def write(self, data: bytes) -> None:
        resume(self.wire.send)(data)

    def read(self, size: int, deadline: float) -> bytes:
        return resume(self._read)(size, deadline)

    async def _read(self, size: int, deadline: float) -> bytes:
        while not self.received and self.error is None and sim_seconds() < deadline:
            await Timer(self.poll_ps, unit="ps")
        if self.error is not None:
            raise self.error
        out = bytes(self.received[:size])
        del self.received[:len(out)]
        return out


def client(wire: UartWire, *, poll_ps: int, timeout_s: float) -> um.Client:
    """A host-library client on the wire, with deadlines in simulated time."""
    return um.Client(SimTransport(wire, poll_ps), timeout=timeout_s, clock=sim_seconds)


async def run(func: Callable[..., T], *args, **kwargs) -> T:
    """Run one blocking host-library call (uart_mem.load, Client.read, ...)
    while the simulation advances."""
    return await bridge(func)(*args, **kwargs)
