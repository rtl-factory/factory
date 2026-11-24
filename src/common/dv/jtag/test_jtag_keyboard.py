"""Wire/status oracle of the common JTAG keyboard endpoint over a test-owned byte sink.

The Bench drives the neutral scan ports as the Intel hub presents them; its
`scan(pad=...)` models the hub's late TDI. Consumers compose this Bench with
their own sink (nand2linux: n2l_ps2 FIFO and MMIO; nand2tetris: hack_ps2_keys)."""
import cocotb
import os
from cocotb.triggers import Timer

from src.common.scripts.jtag.n2a_jtag_keyboard import COMMAND_MAGIC, STATUS_MAGIC, Result

MASK64 = (1 << 64) - 1


def packet(seq, data):
    """A command of any length, including ones the host library refuses to build."""
    return (COMMAND_MAGIC << 56) | (len(data) << 48) | (seq << 40) | int.from_bytes(data, 'little')


def fields(word):
    assert word >> 56 == STATUS_MAGIC, hex(word)
    assert (word >> 40) & 255 == 0, hex(word)
    return ((word >> 55) & 1, (word >> 54) & 1, (word >> 51) & 7,
            (word >> 48) & 7, (word >> 32) & 255, word & 0xffffffff)


def first_64_bits(word, pad):
    """The command a node capturing the FIRST 64 shifted bits would see when the
    hub inserts `pad` junk (one) bits before the host word: the pre-fix RTL."""
    return ((word << pad) | ((1 << pad) - 1)) & MASK64


class Bench:
    READY = 'inject_ready_i'      # Why: the bare endpoint's sink is its own input; a consumer bench overrides it
    EXTRA_INPUTS = ()             # Why: the bare endpoint has no consumer inputs; a consumer bench adds its own

    def __init__(self, d):
        self.d = d
        self.half_ns = 500_000_000 // int(os.environ.get("DV_CLOCK_HZ", "25000000"))
        self.cycles = 0
        self.accepted = []
        self.limit = None

    async def tick(self, count=1):
        d = self.d
        for _ in range(count):
            d.clk_i.value = 0
            if self.READY == 'inject_ready_i':
                d.inject_ready_i.value = 1 if self.limit is None else int(len(self.accepted) < self.limit)
            await Timer(self.half_ns, unit='ns')
            if int(d.inject_valid_o.value) and int(getattr(d, self.READY).value):
                self.accepted.append(int(d.inject_byte_o.value))
            d.clk_i.value = 1
            await Timer(self.half_ns, unit='ns')
            self.cycles += 1
            assert self.cycles < 500000

    async def reset(self):
        d = self.d
        for name in ('clk_i', 'tck_i', 'capture_i', 'shift_i', 'update_i', 'update_ir_i',
                     'tdi_i', 'ir_i', *self.EXTRA_INPUTS):
            getattr(d, name).value = 0
        if self.READY == 'inject_ready_i':
            d.inject_ready_i.value = 1
        d.reset_i.value = 1
        await self.tick(4)
        d.reset_i.value = 0
        for _ in range(5):
            await self.tck()
        self.accepted.clear()

    async def tck(self, **signals):
        d = self.d
        d.tck_i.value = 0
        for k, v in signals.items():
            getattr(d, k + '_i').value = v
        await self.tick(2)
        bit = int(d.tdo_o.value)
        d.tck_i.value = 1
        await self.tick(2)
        d.tck_i.value = 0
        return bit

    async def select(self, ir):
        await self.tck(ir=ir, update_ir=1, capture=0, shift=0, update=0)
        await self.tck(update_ir=0)

    async def scan(self, ir, word=0, bits=64, update=True, pad=0):
        """`pad` models the Intel hub (seven on the DE2-115): the node sees
        `pad` junk TDI bits before the host's bits and `pad` extra Shift-DR
        cycles, while the host still reads the first `bits` TDO bits."""
        await self.select(ir)
        await self.tck(capture=1)
        result = 0
        for i in range(bits + pad):
            tdi = 1 if i < pad else (word >> (i - pad)) & 1
            out = await self.tck(capture=0, shift=1, tdi=tdi)
            if i < bits:
                result |= out << i
        await self.tck(capture=0, shift=0, update=int(update))
        await self.tck(update=0)
        return result

    async def launch_stopped(self, seq, data):
        """Submit then stop TCK immediately, leaving system execution observable."""
        await self.scan(1, packet(seq, data), update=False)
        await self.tck(update=1)
        self.d.update_i.value = 0

    async def completion(self):
        for _ in range(4):
            result = await self.status()
            if not result[0]:
                return result
        raise AssertionError('completion did not cross to resumed TCK')

    async def status(self):
        return fields(await self.scan(2))

    async def send(self, seq, data):
        await self.scan(1, packet(seq, data))
        for _ in range(4):
            status = await self.status()
            if not status[0]:
                return status
        raise AssertionError('completion did not cross to TCK')


@cocotb.test()
async def jtag_keyboard_contract(dut):
    b = Bench(dut)
    await b.reset()
    assert await b.status() == (0, 0, 0, 0, 0, 0)
    # Why: the second identical send proves an exact replay never reinjects.
    data = bytes.fromhex('1c f0 1c e0')
    assert await b.send(0, data) == (0, 0, 1, 4, 0, 4)
    assert await b.send(0, data) == (0, 0, 1, 4, 0, 4)
    assert b.accepted == list(data)
    # Why: a limit is the only way to make the sink refuse mid-batch, which is
    # what cuts a batch at an exact prefix.
    b.accepted.clear()
    b.limit = 1
    assert await b.send(1, b'abcd') == (0, 0, 2, 1, 1, 5)
    assert await b.send(1, b'abcd') == (0, 0, 2, 1, 1, 5)
    b.limit = 0
    assert await b.send(2, b'efg') == (0, 0, 2, 0, 2, 5)
    b.limit = 4
    assert await b.send(3, b'bcd') == (0, 0, 1, 3, 3, 8)
    assert b.accepted == list(b'abcd')
    b.limit = None
    # Ref: wiki/src/common/scripts/jtag/spec.md "Wire format": only IR 1 carries commands.
    for ir in (0, 2, 3):
        await b.scan(ir, packet(4, b'z'))
    assert await b.status() == (0, 0, 1, 3, 3, 8)
    # Why: an IR change mid-scan leaves a partial command, which must never be admitted.
    await b.scan(1, packet(4, b'z'), bits=31, update=False)
    assert await b.status() == (0, 0, 1, 3, 3, 8)
    assert await b.send(4, b'z') == (0, 0, 1, 1, 4, 9)
    assert b.accepted[-1] == ord('z')
    # Why: a short scan may have shifted a partial command, so the endpoint must
    # fail closed rather than guess.
    for bits in (0, 1, 63):
        await b.reset()
        await b.scan(1, packet(0, b'x'), bits=bits)
        assert (await b.status())[1] == 1
        await b.scan(1, packet(0, b'y'))
        assert b.accepted == []
    # Why: the real hub's late TDI and extra Shift-DR cycles: the command is the
    # last 64 bits shifted in (>= 64 shifts accepted) and the status readout is
    # unchanged. The pre-fix capture of the FIRST 64 bits is a malformed command
    # for every pad, so that RTL fails here exactly as it failed on the board.
    for pad in (1, 7, 9):
        await b.reset()
        assert first_64_bits(packet(0, b'x'), pad) >> 56 != COMMAND_MAGIC, pad
        await b.scan(1, packet(0, b'x'), pad=pad)
        status = await b.status()
        if status[0]: status = await b.status()
        assert status[1:] == (0, 1, 1, 0, 1), status
        assert await b.scan(2, pad=pad) >> 56 == STATUS_MAGIC
        assert b.accepted == [ord('x')]
    # Why: after a malformed command or a wrong sequence the host cannot know
    # which later bytes are safe, so the first failure latches.
    for command, code in ((packet(0, b''), Result.MALFORMED), (packet(0, b'12345'), Result.MALFORMED),
                          (packet(0, b'x') ^ (1 << 56), Result.MALFORMED),
                          (packet(0, b'x') | (1 << 32), Result.MALFORMED), (packet(1, b'x'), Result.SEQUENCE)):
        await b.reset()
        await b.scan(1, command)
        status = await b.status()
        if status[0]: status = await b.status()
        assert status[2] == code and status[3] == status[5] == 0, status
        await b.scan(1, packet(0,b'z'))
        assert b.accepted == []
        assert (await b.status())[2] == code
    # Why: the host may stop TCK at any time, so delivery must not depend on it.
    await b.reset()
    await b.launch_stopped(0, b'abcd')
    await b.tick(32)
    assert b.accepted == list(b'abcd')
    assert await b.completion() == (0, 0, 1, 4, 0, 4)
    # Why: a reset can land at any point of a batch, so each staged/prefix point
    # is tried with TCK stopped.
    for prefix in range(4):
        await b.reset()
        await b.launch_stopped(0, b'abcd')
        for _ in range(20):
            if int(dut.inject_valid_o.value) and len(b.accepted) == prefix: break
            await b.tick()
        dut.reset_i.value = 1
        await b.tick(3)
        dut.reset_i.value = 0
        await b.tick(20)
        assert not int(dut.inject_valid_o.value)
        b.accepted.clear()
        for _ in range(4):
            await b.tck(capture=0, shift=0, update=0, update_ir=0)
        assert await b.completion() == (0, 0, 0, 0, 0, 0)
        assert await b.send(0, b'z') == (0, 0, 1, 1, 0, 1)
        assert b.accepted == [ord('z')]
    # Why: pausing only the system clock lets two complete scans occur before
    # the first command is consumed; the busy submit must not overwrite the
    # held mailbox.
    await b.reset()
    async def scan_without_system(word):
        async def edge(**signals):
            dut.tck_i.value = 0
            for name, value in signals.items():
                getattr(dut, name + '_i').value = value
            await Timer(50, unit='ns')
            dut.tck_i.value = 1
            await Timer(50, unit='ns')
        await edge(ir=1, update_ir=1, capture=0, shift=0, update=0)
        await edge(update_ir=0)
        await edge(capture=1)
        for bit in range(64):
            await edge(capture=0, shift=1, tdi=(word >> bit) & 1)
        await edge(shift=0, update=1)
        await edge(update=0)
        dut.tck_i.value = 0
    await scan_without_system(packet(0, b'a'))
    await scan_without_system(packet(1, b'b'))
    await b.tick(32)
    assert b.accepted == [ord('a')]
    assert await b.completion() == (0, 1, 1, 1, 0, 1)
    await b.reset()
    for seq in range(257):
        assert await b.send(seq & 255, b'x') == (0, 0, 1, 1, seq & 255, seq + 1)
    assert b.accepted == [ord('x')] * 257
    dut._log.info('JTAG_KEYBOARD PASS cycles=%d', b.cycles)
