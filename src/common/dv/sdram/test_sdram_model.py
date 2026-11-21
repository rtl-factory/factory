"""Contract of the common SDRAM device model (wiki/src/common/dv/sdram/dv-plan.md).

The bench drives the device pins one command per clock, from a falling edge,
and samples DQ after each rising edge. The positive tests check data, masks,
burst order and preload against a shadow memory; each negative test commits
exactly one violation and its target expects the model's fatal message."""
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge

POWERUP = 8
NOP, ACT, READ, WRITE, PRE, REF, MRS = 0b111, 0b011, 0b101, 0b100, 0b010, 0b001, 0b000
MODE = {1: 0x020, 8: 0x023}


class Bench:
    def __init__(self, dut):
        self.d = dut
        self.beats = int(dut.BURST_BEATS.value)
        self.xor = bool(int(dut.ROW_XOR_BANK.value))

    async def start(self):
        d = self.d
        cocotb.start_soon(Clock(d.clk_i, 40, unit="ns").start())
        d.cke_i.value = 1
        d.cs_n_i.value = 0
        self.drive_pins(NOP)
        d.preload_valid_i.value = 0
        d.preload_address_i.value = 0
        d.preload_data_i.value = 0
        d.preload_byte_enable_i.value = 0
        await FallingEdge(d.clk_i)

    def drive_pins(self, command, bank=0, address=0, dqm=0, data=0, drive=False):
        d = self.d
        d.ras_n_i.value = (command >> 2) & 1
        d.cas_n_i.value = (command >> 1) & 1
        d.we_n_i.value = command & 1
        d.ba_i.value = bank
        d.addr_i.value = address
        d.dqm_i.value = dqm
        d.dq_i.value = data
        d.dq_oe_i.value = int(drive)

    async def cmd(self, command=NOP, **kwargs):
        """Present one command for one clock; return DQ sampled after the clock's ending edge."""
        self.drive_pins(command, **kwargs)
        await RisingEdge(self.d.clk_i)
        await ReadOnly()
        value = int(self.d.dq_o.value)
        await FallingEdge(self.d.clk_i)
        self.drive_pins(NOP)
        return value

    async def nops(self, count):
        for _ in range(count):
            await self.cmd(NOP)

    async def init(self, refreshes=2, mode=None):
        await self.nops(POWERUP + 1)
        await self.cmd(PRE, address=1 << 10)
        await self.nops(1)
        for _ in range(refreshes):
            await self.cmd(REF)
            await self.nops(2)
        await self.cmd(MRS, address=MODE[self.beats] if mode is None else mode)
        await self.nops(2)

    async def write(self, bank, row, column, words, masks=None):
        masks = masks or [0] * len(words)
        await self.cmd(ACT, bank=bank, address=row)
        await self.nops(1)
        for beat, (word, mask) in enumerate(zip(words, masks)):
            await self.cmd(WRITE if beat == 0 else NOP, bank=bank, address=column, dqm=mask,
                           data=word, drive=True)
        await self.nops(1)
        await self.cmd(PRE, bank=bank)
        await self.nops(1)

    async def read(self, bank, row, column):
        await self.cmd(ACT, bank=bank, address=row)
        await self.nops(1)
        words = [await self.cmd(READ, bank=bank, address=column)]
        for _ in range(self.beats - 1):
            words.append(await self.cmd(NOP))
        await self.nops(1)
        await self.cmd(PRE, bank=bank)
        await self.nops(1)
        return words

    def word_of(self, byte_address):
        if self.xor:
            bank = ((byte_address >> 11) ^ (byte_address >> 13)) & 3
            row = (byte_address >> 13) & 0x1FFF
        else:
            bank = (byte_address >> 24) & 3
            row = (byte_address >> 11) & 0x1FFF
        return bank, row, (byte_address >> 1) & 0x3FF


@cocotb.test()
async def sdram_model_contract(dut):
    """Writes with byte masks, reads in burst order, and preloaded bytes read back."""
    b = Bench(dut)
    rng = random.Random(int(cocotb.RANDOM_SEED))
    await b.start()
    preload = bytes(rng.randrange(256) for _ in range(64))
    base = 0x0123_4580
    dut.preload_valid_i.value = 1
    dut.preload_address_i.value = base
    dut.preload_data_i.value = int.from_bytes(preload, "little")
    dut.preload_byte_enable_i.value = (1 << len(preload)) - 1
    await b.nops(1)
    dut.preload_valid_i.value = 0
    await b.init()
    for group in range(0, len(preload), 2 * b.beats):
        bank, row, column = b.word_of(base + group)
        got = await b.read(bank, row, column)
        expected = [preload[group + 2 * k] | preload[group + 2 * k + 1] << 8 for k in range(b.beats)]
        assert got == expected, f"preload group {group}: {got} != {expected}"
        await b.cmd(REF)
        await b.nops(2)
    for _ in range(4):
        bank, row, column = rng.randrange(4), rng.randrange(8192), rng.randrange(128) * 8
        old = await b.read(bank, row, column)
        words = [rng.randrange(1 << 16) for _ in range(b.beats)]
        masks = [rng.randrange(4) for _ in range(b.beats)]
        await b.write(bank, row, column, words, masks)
        expected = [(o & (0x00FF if m & 1 else 0) | o & (0xFF00 if m & 2 else 0)) |
                    (w & (0 if m & 1 else 0x00FF) | w & (0 if m & 2 else 0xFF00))
                    for o, w, m in zip(old, words, masks)]
        assert await b.read(bank, row, column) == expected
        await b.cmd(REF)
        await b.nops(2)
    if b.beats == 8:
        # Why: a burst that starts inside the group would wrap; the model must refuse
        # it (negative test), so the positive test checks the order of an aligned one.
        bank, row = 1, 77
        await b.write(bank, row, 8, list(range(100, 108)))
        assert await b.read(bank, row, 8) == list(range(100, 108))


async def ready(dut):
    b = Bench(dut)
    await b.start()
    await b.init()
    return b


@cocotb.test()
async def read_burst_interrupted(dut):
    b = await ready(dut)
    await b.cmd(ACT, address=5)
    await b.nops(1)
    await b.cmd(READ)
    await b.cmd(PRE)


@cocotb.test()
async def write_burst_interrupted(dut):
    b = await ready(dut)
    await b.cmd(ACT, address=5)
    await b.nops(1)
    await b.cmd(WRITE, drive=True)
    await b.cmd(PRE, drive=True)


@cocotb.test()
async def write_beat_undriven(dut):
    b = await ready(dut)
    await b.cmd(ACT, address=5)
    await b.nops(1)
    await b.cmd(WRITE, drive=True)
    await b.cmd(NOP, drive=False)


@cocotb.test()
async def dq_outside_write(dut):
    b = await ready(dut)
    await b.cmd(NOP, drive=True)


@cocotb.test()
async def dq_turnaround(dut):
    b = await ready(dut)
    await b.cmd(ACT, address=5)
    await b.nops(1)
    await b.cmd(READ)
    # Why: one idle clock after the read beat, so only the one-idle-clock
    # turnaround term can fire.
    await b.nops(1)
    await b.cmd(WRITE, drive=True)


@cocotb.test()
async def command_in_powerup(dut):
    b = Bench(dut)
    await b.start()
    await b.cmd(PRE, address=1 << 10)


@cocotb.test()
async def first_command_not_precharge_all(dut):
    b = Bench(dut)
    await b.start()
    await b.nops(POWERUP + 1)
    await b.cmd(REF)


@cocotb.test()
async def activate_before_mode(dut):
    b = Bench(dut)
    await b.start()
    await b.nops(POWERUP + 1)
    await b.cmd(PRE, address=1 << 10)
    await b.nops(1)
    await b.cmd(ACT)


@cocotb.test()
async def activate_open_bank(dut):
    b = await ready(dut)
    await b.cmd(ACT, address=5)
    await b.nops(2)
    await b.cmd(ACT, address=6)


@cocotb.test()
async def activate_after_refresh(dut):
    b = await ready(dut)
    await b.cmd(REF)
    await b.cmd(ACT)


@cocotb.test()
async def activate_after_mode(dut):
    b = Bench(dut)
    await b.start()
    await b.init()
    await b.cmd(MRS, address=MODE[b.beats])
    await b.cmd(ACT)


@cocotb.test()
async def column_before_mode(dut):
    b = Bench(dut)
    await b.start()
    await b.nops(POWERUP + 1)
    await b.cmd(PRE, address=1 << 10)
    await b.nops(1)
    await b.cmd(READ)


@cocotb.test()
async def column_without_row(dut):
    b = await ready(dut)
    await b.cmd(READ)


@cocotb.test()
async def column_auto_precharge(dut):
    b = await ready(dut)
    await b.cmd(ACT, address=5)
    await b.nops(1)
    await b.cmd(READ, address=1 << 10)


@cocotb.test()
async def burst_unaligned(dut):
    b = await ready(dut)
    await b.cmd(ACT, address=5)
    await b.nops(1)
    await b.cmd(READ, address=3)


@cocotb.test()
async def read_masked(dut):
    b = await ready(dut)
    await b.cmd(ACT, address=5)
    await b.nops(1)
    await b.cmd(READ, dqm=1)


@cocotb.test()
async def write_undriven(dut):
    b = await ready(dut)
    await b.cmd(ACT, address=5)
    await b.nops(1)
    await b.cmd(WRITE)


@cocotb.test()
async def precharge_before_tras(dut):
    b = await ready(dut)
    await b.cmd(ACT, address=5)
    await b.cmd(PRE)


@cocotb.test()
async def precharge_before_twr(dut):
    b = await ready(dut)
    await b.cmd(ACT, address=5)
    await b.nops(1)
    await b.cmd(WRITE, drive=True)
    await b.cmd(PRE)


@cocotb.test()
async def burst_precharge_before_twr(dut):
    b = await ready(dut)
    await b.cmd(ACT, address=5)
    await b.nops(1)
    await b.cmd(WRITE, drive=True)
    for _ in range(7):
        await b.cmd(NOP, drive=True)
    await b.cmd(PRE)


@cocotb.test()
async def refresh_open_row(dut):
    b = await ready(dut)
    await b.cmd(ACT, address=5)
    await b.nops(2)
    await b.cmd(REF)


@cocotb.test()
async def refresh_back_to_back(dut):
    b = await ready(dut)
    await b.cmd(REF)
    await b.cmd(REF)


@cocotb.test()
async def refresh_interval(dut):
    b = await ready(dut)
    await b.nops(200)


@cocotb.test()
async def mode_open_row(dut):
    b = await ready(dut)
    await b.cmd(ACT, address=5)
    await b.nops(2)
    await b.cmd(MRS, address=MODE[b.beats])


@cocotb.test()
async def mode_before_refreshes(dut):
    b = Bench(dut)
    await b.start()
    await b.init(refreshes=1)


@cocotb.test()
async def mode_after_refresh(dut):
    b = Bench(dut)
    await b.start()
    await b.nops(POWERUP + 1)
    await b.cmd(PRE, address=1 << 10)
    await b.nops(1)
    await b.cmd(REF)
    await b.nops(2)
    await b.cmd(REF)
    await b.cmd(MRS, address=MODE[b.beats])


@cocotb.test()
async def mode_value(dut):
    b = Bench(dut)
    await b.start()
    await b.init(mode=MODE[9 - b.beats])
