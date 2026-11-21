"""Host keyboard through a nand2doom board top: the common Virtual JTAG
wrapper (on its Verilator stub) and the common endpoint inside n2d_system feed
the Set-2 input FIFO (wiki/src/nand2doom/rtl/io/mas.md#jtag-keyboard).

The host plays the hub through the stub's scan variables; a monitor records
every input-FIFO write. With PS/2 idle, no UART input event and the CPU
halted, the JTAG endpoint is the only producer, so the written events must be
exactly the submitted Set-2 bytes decoded to `{extended, break, code}`."""
import cocotb
from cocotb.triggers import ReadOnly, RisingEdge

from src.common.dv.jtag.vjtag_stub_host import StubHost

# Ref: PS/2 scan code set 2: W make/break and Up (extended) make/break, split
# across two 4-byte batches.
BATCHES = (bytes([0x1D, 0xF0, 0x1D, 0xE0]), bytes([0x75, 0xE0, 0xF0, 0x75]))
EVENTS = [0x01D, 0x11D, 0x275, 0x375]


async def check_jtag_keys(board, clock_period_ns=40):
    """Send BATCHES over the hub and require EVENTS in the input FIFO."""
    keyboard = board.u_system.u_keyboard
    written = []

    async def monitor():
        while True:
            await RisingEdge(board.clk_25)
            await ReadOnly()
            if int(keyboard.fifo_write.value):
                written.append(int(keyboard.fifo_push_data.value))

    task = cocotb.start_soon(monitor())
    host = StubHost(board.u_jtag_virtual.u_vjtag, half_tck_ns=2 * clock_period_ns)
    await host.idle(5)
    assert await host.status() == (0, 0, 0, 0, 0, 0)
    for batch in BATCHES:
        await host.send(batch)
    for _ in range(8):
        await RisingEdge(board.clk_25)
    task.cancel()
    assert written == EVENTS, [hex(event) for event in written]
