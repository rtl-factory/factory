"""Public-port checks of the silent APU register block against the model."""
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge

from .apu_model import NR52, REGISTER_START, WAVE_END, WAVE_START, ApuModel

CLOCK_PERIOD_NS = 40
DATA_KIND = 1
UNCLAIMED = (0xFF00, 0xFF0F, 0xFF40, 0xFF46, 0xFF7F, 0xFE9F, 0x0000, 0xFFFF)


def request_word(valid, write, address, data):
    return (valid << 27) | (write << 26) | (DATA_KIND << 24) | (address << 8) | data


def response_fields(word):
    return (word >> 9) & 1, (word >> 8) & 1, word & 0xFF


class Bench:
    def __init__(self, dut):
        self.dut = dut
        self.model = ApuModel()
        self.bit = 0
        self.ticks = 0

    async def cycle(self, write=None, address=None, data=0, tick=False):
        """One clock: optional access, optional frame-sequencer step on its edge."""
        d = self.dut
        await FallingEdge(d.clk_i)
        if tick:
            # Falling edge of the sequencer bit is seen on this rising edge.
            if not self.bit:
                d.sequencer_bit_i.value = 1
                await RisingEdge(d.clk_i)
                await FallingEdge(d.clk_i)
            d.sequencer_bit_i.value = 0
            self.bit = 0
        d.io_request_i.value = 0 if address is None else request_word(1, write, address, data)
        await ReadOnly()
        read = None
        if address is not None:
            valid, fault, read = response_fields(int(d.io_response_o.value))
            want = self.model.selected(address)
            assert int(d.io_selected_o.value) == want, hex(address)
            if want:
                assert valid == 1 and fault == 0, hex(address)
                if not write:
                    expect = self.model.read(address)
                    assert read == expect, (hex(address), hex(read), hex(expect), self.ticks)
            else:
                assert valid == 0, (hex(address), 'unclaimed address answered')
        await RisingEdge(d.clk_i)
        if tick:
            self.model.sequencer_tick()
            self.ticks += 1
        if write and address is not None and self.model.selected(address):
            self.model.write(address, data)
        return read

    async def read(self, address):
        return await self.cycle(0, address)

    async def write(self, address, data, tick=False):
        await self.cycle(1, address, data, tick)

    async def tick(self, count=1):
        for _ in range(count):
            await self.cycle(tick=True)

    async def read_all(self):
        for address in range(REGISTER_START, WAVE_END + 1):
            await self.read(address)


async def start(dut):
    bench = Bench(dut)
    dut.io_request_i.value = 0
    dut.sequencer_bit_i.value = 0
    dut.reset_i.value = 1
    for _ in range(2):
        await RisingEdge(dut.clk_i)
    await FallingEdge(dut.clk_i)
    dut.reset_i.value = 0
    return bench


async def apu_power_and_masks(dut):
    b = await start(dut)
    # Post-boot state, then power off.
    await b.read_all()
    assert await b.read(NR52) == 0xF1
    assert [await b.read(a) for a in (0xFF11, 0xFF12, 0xFF24, 0xFF25)] == [0xBF, 0xF3, 0x77, 0xF3]
    await b.write(NR52, 0x00)
    await b.read_all()
    assert await b.read(NR52) == 0x70
    # Power off: register writes ignored, length loads still accepted.
    for address in range(REGISTER_START, NR52):
        await b.write(address, 0xFF)
    await b.read_all()
    await b.write(NR52, 0x80)
    assert await b.read(NR52) == 0xF0
    for address in range(REGISTER_START, NR52):
        await b.write(address, 0x00 if address in (0xFF14, 0xFF19, 0xFF1E, 0xFF23) else 0xFF)
    await b.read_all()
    # Power off clears NR10-NR51 and channel status; wave RAM survives.
    for i in range(16):
        await b.write(WAVE_START + i, i * 0x11)
    await b.write(0xFF12, 0xF0)
    await b.write(0xFF14, 0x80)
    assert await b.read(NR52) == 0xF1
    await b.write(NR52, 0x00)
    await b.read_all()
    assert await b.read(WAVE_START + 5) == 0x55
    for address in UNCLAIMED:
        await b.cycle(1, address, 0x5A)


async def apu_length_and_status(dut):
    b = await start(dut)
    await b.write(NR52, 0x80)
    # Channel 2: DAC on, length 4, length enabled, trigger.
    await b.write(0xFF17, 0xF0)
    await b.write(0xFF16, 64 - 4)
    await b.write(0xFF19, 0xC0)
    for _ in range(12):
        await b.read(NR52)
        await b.tick()
    assert await b.read(NR52) & 0x02 == 0
    # DAC off clears status at once; trigger with DAC off stays off.
    await b.write(0xFF17, 0xF0)
    await b.write(0xFF19, 0x80)
    assert await b.read(NR52) & 0x02
    await b.write(0xFF17, 0x07)
    assert await b.read(NR52) & 0x02 == 0
    await b.write(0xFF19, 0x80)
    assert await b.read(NR52) & 0x02 == 0
    # Channel 3 length 256 and NR30 DAC gate.
    await b.write(0xFF1A, 0x80)
    await b.write(0xFF1B, 0xFE)
    await b.write(0xFF1E, 0xC0)
    assert await b.read(NR52) & 0x04
    await b.tick(5)
    assert await b.read(NR52) & 0x04 == 0
    # Extra length clock: enable length in the first half with length 1.
    await b.write(0xFF21, 0xF0)
    await b.write(0xFF20, 63)
    await b.write(0xFF23, 0x80)
    while b.model.step % 2 == 0:
        await b.tick()
    await b.write(0xFF23, 0x40)
    assert await b.read(NR52) & 0x08 == 0


async def apu_sweep_overflow(dut):
    b = await start(dut)
    await b.write(NR52, 0x80)
    await b.write(0xFF12, 0xF0)
    # Trigger-time overflow: freq $7FF, shift 1, add.
    await b.write(0xFF10, 0x11)
    await b.write(0xFF13, 0xFF)
    await b.write(0xFF14, 0x87)
    assert await b.read(NR52) & 0x01 == 0
    # Sweep-step overflow after several steps from a lower frequency.
    await b.write(0xFF13, 0x00)
    await b.write(0xFF14, 0x86)
    for _ in range(40):
        await b.read(NR52)
        await b.tick()
    assert await b.read(NR52) & 0x01 == 0
    # Negate never overflows.
    await b.write(0xFF10, 0x19)
    await b.write(0xFF14, 0x87)
    await b.tick(32)
    assert await b.read(NR52) & 0x01


async def apu_random_traffic(dut):
    rng = random.Random(int(cocotb.RANDOM_SEED))
    b = await start(dut)
    hot = [0xFF10, 0xFF11, 0xFF12, 0xFF13, 0xFF14, 0xFF16, 0xFF17, 0xFF19, 0xFF1A,
           0xFF1B, 0xFF1E, 0xFF20, 0xFF21, 0xFF23, NR52]
    for _ in range(6000):
        r = rng.random()
        tick = rng.random() < 0.15
        if r < 0.45:
            address = rng.choice(hot)
            data = rng.randrange(256)
            if address == NR52 and rng.random() < 0.8:
                data |= 0x80
            if address in (0xFF12, 0xFF17, 0xFF21) and rng.random() < 0.7:
                data |= 0x80
            await b.write(address, data, tick)
        elif r < 0.55:
            await b.write(rng.randrange(REGISTER_START, WAVE_END + 1), rng.randrange(256), tick)
        elif r < 0.9:
            await b.cycle(0, rng.choice(hot + [rng.randrange(REGISTER_START, WAVE_END + 1)]), tick=tick)
        else:
            await b.cycle(tick=tick)
    await b.read_all()


@cocotb.test()
async def apu_contract(dut):
    """Each scenario starts from reset; the target names this single test."""
    cocotb.start_soon(Clock(dut.clk_i, CLOCK_PERIOD_NS, unit='ns').start())
    for scenario in (apu_power_and_masks, apu_length_and_status, apu_sweep_overflow,
                     apu_random_traffic):
        await scenario(dut)
