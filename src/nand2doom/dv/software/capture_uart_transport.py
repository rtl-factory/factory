"""Byte transport for the synchronous production host, on public UART pins.

Only cocotb's scheduler thread touches handles or simulation time. The host
bridge thread blocks on resume-decorated methods; it never runs alongside RTL.
No command/response decoding or capture state machine lives in this adapter.
"""
import cocotb
from cocotb.task import resume
from cocotb.triggers import FallingEdge, Timer
from cocotb.utils import get_sim_time
from src.nand2doom.dv.io.test_n2d_uart_debug import _uart_send, _uart_receive_byte


class CaptureUartTransport:
    def __init__(self, dut, before_write):
        self.dut = dut
        self.before_write = before_write
        self.received = bytearray()
        self.receiver = cocotb.start_soon(self._receive())

    async def _receive(self):
        while True:
            self.received.append(await _uart_receive_byte(self.dut))

    @resume
    async def clock(self):
        return float(get_sim_time(unit='sec'))

    @resume
    async def sleep(self, seconds):
        await Timer(max(seconds, 40e-9), unit='sec', round_mode='ceil')

    @resume
    async def write(self, packet):
        assert not self.received, 'unexpected unread response bytes'
        await self.before_write(packet)
        await FallingEdge(self.dut.clk_i)
        await _uart_send(self.dut, packet)
        return len(packet)

    @resume
    async def read(self, count):
        # Why: deliberately fragment reads so the production Client's framing,
        # exact lengths, CRC, deadlines and poison/no-retry behavior are
        # exercised, not bypassed.
        count = min(count, len(self.received), 257)
        data = bytes(self.received[:count])
        del self.received[:count]
        return data

    def stop(self):
        self.receiver.cancel()
