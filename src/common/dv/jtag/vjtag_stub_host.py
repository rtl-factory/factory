"""Host side of the Intel Virtual JTAG hub over the board-top stub.

A board top elaborated with `sld_virtual_jtag_stub.sv` exposes the hub's scan
outputs as bench variables (`tck_q`, `tdi_q`, `capture_q`, `shift_q`,
`update_q`, `update_ir_q`, `ir_q`) and reads `tdo`. `StubHost` drives them with
the common endpoint's IR1 command / IR2 status scans, padding each DR scan with
the hub's late-TDI bits (wiki/src/common/scripts/jtag/spec.md#wire-format). Each
consumer's board test checks where the accepted bytes land."""
from cocotb.triggers import Timer

from src.common.dv.jtag.test_jtag_keyboard import fields, packet

HUB_PAD = 7  # Ref: wiki/src/nand2doom/rtl/io/mas.md "JTAG keyboard composition" (late TDI); seven on the DE2-115
SIGNALS = ('tck', 'tdi', 'capture', 'shift', 'update', 'update_ir', 'ir')


async def writable():
    """Leave a ReadOnly phase before driving inputs."""
    await Timer(1, unit='ns')


class StubHost:
    def __init__(self, stub, half_tck_ns):
        self.stub = stub
        self.half_tck_ns = half_tck_ns
        self.seq = 0
        self.total = 0
        for name in SIGNALS:
            getattr(stub, name + '_q').value = 0

    async def tck(self, **signals):
        self.stub.tck_q.value = 0
        for name, value in signals.items():
            getattr(self.stub, name + '_q').value = value
        await Timer(self.half_tck_ns, unit='ns')
        bit = int(self.stub.tdo.value)
        self.stub.tck_q.value = 1
        await Timer(self.half_tck_ns, unit='ns')
        self.stub.tck_q.value = 0
        return bit

    async def idle(self, count):
        """Clock the scan domain out of reset with every strobe low."""
        await writable()
        for _ in range(count):
            await self.tck()

    async def select(self, ir):
        await self.tck(ir=ir, update_ir=1, capture=0, shift=0, update=0)
        await self.tck(update_ir=0)

    async def scan(self, ir, word=0, bits=64, pad=HUB_PAD):
        """One DR scan as the hub presents it: `pad` junk TDI bits before the
        host word while the host reads the first `bits` TDO bits."""
        await writable()
        await self.select(ir)
        await self.tck(capture=1)
        result = 0
        for index in range(bits + pad):
            tdi = 1 if index < pad else (word >> (index - pad)) & 1
            out = await self.tck(capture=0, shift=1, tdi=tdi)
            if index < bits:
                result |= out << index
        await self.tck(capture=0, shift=0, update=1)
        await self.tck(update=0)
        return result

    async def status(self):
        """(busy, error, code, accepted, seq, total) from one IR2 scan."""
        return fields(await self.scan(2))

    async def send(self, data):
        """Submit one 1..4 byte command and require full acceptance."""
        await self.scan(1, packet(self.seq, bytes(data)))
        for _ in range(8):
            status = await self.status()
            if not status[0]:
                break
        self.total += len(data)
        assert status == (0, 0, 1, len(data), self.seq, self.total & 0xffffffff), status
        self.seq = (self.seq + 1) & 255
        return status
