"""JTAG keyboard bytes as the third producer of the nand2doom input FIFO.

The common endpoint Bench drives the neutral Virtual JTAG scan ports (with the
Intel hub's late-TDI padding where noted) on a free-running system clock while
the physical PS/2 pins, the UART input-event port and the CPU MMIO channel run
concurrently. Events are checked only through the public DATA/STATUS registers
and the endpoint's wire status; a white-box monitor checks every offer against
the documented arbitration rule (wiki/src/nand2doom/rtl/io/mas.md)."""
from __future__ import annotations

import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, Timer

from src.common.dv.jtag.test_jtag_keyboard import Bench, packet
from src.nand2doom.dv.io.test_n2d_ps2_keyboard import (
    BIT_VALID, CONTROL, DATA, INJECT, STATUS, inject_event, mmio, send_frame,
    send_through_parity)
from src.nand2doom.scripts import n2d_jtag_keyboard as host
from src.common.scripts.jtag.n2a_jtag_keyboard import key_strokes

OVERFLOW = 1 << 8
PS2_HALF_NS = 1370


def set2(event: int) -> list[int]:
    """Independent event -> Set-2 bytes (io/HAS.md event format)."""
    return [0xE0] * (event >> 9 & 1) + [0xF0] * (event >> 8 & 1) + [event & 0xFF]


class InputBench(Bench):
    """Common TAP driver on a free-running clock; the sink is the n2d FIFO."""

    def __init__(self, dut):
        super().__init__(dut)
        self.seq = 0

    async def tick(self, count=1):
        for _ in range(count):
            await RisingEdge(self.d.clk_i)
            self.cycles += 1
            assert self.cycles < 2_000_000

    async def reset(self):
        d = self.d
        for name in ('tck_i', 'capture_i', 'shift_i', 'update_i', 'update_ir_i', 'tdi_i', 'ir_i',
                     'inject_valid_i', 'inject_event_i', 'mmio_request_valid_i',
                     'mmio_request_write_i', 'mmio_request_address_i',
                     'mmio_request_write_data_i', 'mmio_request_write_strobe_i'):
            getattr(d, name).value = 0
        d.ps2_clk_i.value = 1
        d.ps2_data_i.value = 1
        d.mmio_response_ready_i.value = 1
        d.reset_i.value = 1
        await self.tick(4)
        d.reset_i.value = 0
        for _ in range(5):
            await self.tck()
        self.seq = 0

    async def submit(self, codes) -> tuple:
        """One host command (1..4 bytes) with the next sequence number."""
        status = await self.send(self.seq & 255, bytes(codes))
        busy, error, code, accepted, seq, _ = status
        assert (busy, error, seq) == (0, 0, self.seq & 255), status
        assert code in (1, 2) and (code == 1) == (accepted == len(codes)), status
        self.seq += 1
        return status

    async def deliver(self, codes, chunk=4, rng=None):
        """Host send_all: batches, resubmitting only the proven refused suffix."""
        codes = list(codes)
        refusals = 0
        while codes:
            size = min(len(codes), rng.randint(1, 4) if rng else chunk)
            accepted = (await self.submit(codes[:size]))[3]
            codes = codes[accepted:]
            if accepted < size:
                refusals += 1
                await self.tick(20)
        return refusals


async def offer_monitor(dut, log):
    """Record every offer with the competing sources visible on that cycle."""
    keyboard = dut.u_keyboard
    while True:
        await FallingEdge(dut.clk_i)
        if int(dut.reset_i.value) or not int(dut.scan_inject_valid_o.value):
            continue
        byte = int(dut.scan_inject_byte_o.value)
        log.append(dict(
            byte=byte, ready=int(dut.scan_inject_ready_o.value),
            prefix=byte in (0xE0, 0xF0),
            physical=int(keyboard.physical_event_push.value),
            uart=int(dut.inject_valid_i.value),
            mmio=int(keyboard.mmio_inject_accept.value),
            control=int(keyboard.control_write_accept.value),
            full=int(not int(keyboard.fifo_has_push_space.value))))


def check_offers(log) -> dict:
    """Prefix bytes are always taken; a completing byte is taken exactly when
    no CONTROL, physical, UART or CPU-INJECT push owns the edge and a slot is
    free."""
    reasons = dict(physical=0, uart=0, mmio=0, control=0, full=0, accepted=0, prefix=0)
    for offer in log:
        if offer['prefix']:
            assert offer['ready'], offer
            reasons['prefix'] += 1
            continue
        blocked = [k for k in ('control', 'physical', 'uart', 'mmio', 'full') if offer[k]]
        assert offer['ready'] == int(not blocked), offer
        for key in blocked or ['accepted']:
            reasons[key] += 1
    return reasons


async def pop_all(dut) -> list[int]:
    events = []
    while True:
        value, error = await mmio(dut, DATA)
        assert error == 0
        if not value & BIT_VALID:
            return events
        events.append(value & 0x3FF)


async def status(dut) -> int:
    value, error = await mmio(dut, STATUS)
    assert error == 0
    return value


async def ps2_event(dut, event):
    for byte in set2(event):
        await send_frame(dut, byte, half_period_ns=PS2_HALF_NS)


async def stop_edge(dut):
    """Finish a frame left after its parity bit (stop data already high): the
    stop bit's falling edge now, then the idle-high clock."""
    dut.ps2_clk_i.value = 0
    await Timer(PS2_HALF_NS, unit='ns')
    dut.ps2_clk_i.value = 1


async def host_translation(dut, b):
    # Every Doom action through the n2d host tool, then text through the
    # shared translation: each make/break step lands as its exact events.
    for name, event in host.ACTIONS.items():
        make, brk = host.key_steps([name], 'tap')
        await b.deliver(make)
        assert await pop_all(dut) == [event], name
        await b.deliver(brk)
        assert await pop_all(dut) == [event | 0x100], name
    expected = [0x043, 0x143, 0x012, 0x023, 0x123, 0x112, 0x275, 0x375]
    for make, brk in key_strokes(b'iD\x1b[A'):
        await b.deliver(make)
        await b.deliver(brk)
    assert await pop_all(dut) == expected
    # One command carrying a whole extended break across the padded hub scan.
    await b.scan(1, packet(b.seq, bytes([0xE0, 0xF0, 0x6B])), pad=7)
    result = await b.completion()
    assert result[2:5] == (1, 3, b.seq), result
    b.seq += 1
    assert await pop_all(dut) == [0x36B]


async def full_fifo_and_prefix_retention(dut, b):
    for index in range(8):
        _, error = await mmio(dut, INJECT, write=True, data=0x070 + index)
        assert error == 0
    assert (await status(dut) >> 3) & 0xF == 8
    # Both prefixes are taken while full; the completing byte is refused and
    # the host resubmits exactly that suffix once a slot frees.
    assert (await b.submit([0xE0, 0xF0, 0x74]))[2:4] == (2, 2)
    assert (await b.submit([0x74]))[2:4] == (2, 0)
    value, _ = await mmio(dut, DATA)
    assert value == BIT_VALID | 0x070
    assert (await b.submit([0x74]))[2:4] == (1, 1)
    assert await pop_all(dut) == [0x071 + i for i in range(7)] + [0x374]
    assert not await status(dut) & OVERFLOW


async def competitor_sweep(dut, b, log, competitor):
    """Align one competing operation with the JTAG offer cycle by sweeping its
    start relative to a launch with TCK stopped. A refusal while slots are free
    can only be the competitor owning the edge; the competitor always lands,
    and the host's resubmitted byte follows it."""
    refused = 0
    for delay in range(16):
        await b.reset()
        if competitor == 'physical':
            await send_through_parity(dut, 0x29, PS2_HALF_NS)
            dut.ps2_data_i.value = 1
        await b.launch_stopped(0, bytes([0x1C]))

        async def fire():
            await b.tick(delay)
            if competitor == 'uart':
                await inject_event(dut, 0x03A)
            elif competitor == 'mmio':
                assert (await mmio(dut, INJECT, write=True, data=0x05A))[1] == 0
            elif competitor == 'control':
                assert (await mmio(dut, CONTROL, write=True, data=1, strobe=1))[1] == 0
            else:
                await stop_edge(dut)
        task = cocotb.start_soon(fire())
        await b.tick(40)
        await task
        result = await b.completion()
        assert result[2] in (1, 2) and result[4] == 0, result
        b.seq = 1
        if result[3] == 0:
            refused += 1
            assert (await b.submit([0x1C]))[2:4] == (1, 1)
        other = {'uart': [0x03A], 'mmio': [0x05A], 'control': [], 'physical': [0x029]}[competitor]
        events = await pop_all(dut)
        assert sorted(events) == sorted(other + [0x01C]), (competitor, delay, events)
        if result[3] == 0:
            assert events == other + [0x01C], (competitor, delay, events)
        assert not await status(dut) & OVERFLOW
    assert refused, f'{competitor} never coincided with a JTAG offer'
    dut._log.info('sweep %s: %d of 16 alignments refused the JTAG byte', competitor, refused)


async def mixed_streams(dut, b, rng):
    """Physical, UART, CPU INJECT and JTAG producers at once with a CPU reader.
    Disjoint scan-code ranges identify each source; every source's sequence must
    arrive complete, once and in order."""
    def events(low, high, count):
        return [rng.choice((0, 0x100, 0x200, 0x300)) | rng.randrange(low, high) for _ in range(count)]
    sent = {'physical': events(0x10, 0x30, 10), 'uart': events(0x30, 0x50, 24),
            'mmio': events(0x50, 0x60, 10), 'jtag': events(0x60, 0x80, 30)}
    source = lambda e: ('physical' if (e & 0xFF) < 0x30 else 'uart' if (e & 0xFF) < 0x50
                        else 'mmio' if (e & 0xFF) < 0x60 else 'jtag')
    done = set()

    async def physical():
        for event in sent['physical']:
            await Timer(rng.randrange(0, 20_000), unit='ns')
            await ps2_event(dut, event)
        done.add('physical')

    async def uart():
        for event in sent['uart']:
            await b.tick(rng.choice((0, 0, 1, 2, rng.randrange(0, 600))))
            await inject_event(dut, event)
        done.add('uart')

    async def jtag():
        stream = [byte for event in sent['jtag'] for byte in set2(event)]
        b.refusals = await b.deliver(stream, rng=rng)
        done.add('jtag')

    popped, injected = [], list(sent['mmio'])
    tasks = [cocotb.start_soon(c()) for c in (physical, uart, jtag)]
    while len(done) < 3 or injected:
        await b.tick(rng.randrange(0, 40))
        if injected and rng.random() < 0.08:
            assert (await mmio(dut, INJECT, write=True, data=injected.pop(0)))[1] == 0
        else:
            value, error = await mmio(dut, DATA)
            assert error == 0
            if value & BIT_VALID:
                popped.append(value & 0x3FF)
    for task in tasks:
        await task
    popped += await pop_all(dut)
    for name, sequence in sent.items():
        assert [e for e in popped if source(e) == name] == sequence, name
    assert len(popped) == sum(map(len, sent.values()))
    assert not await status(dut) & OVERFLOW


@cocotb.test()
async def jtag_input_mixed_contract(dut):
    seed = int(os.environ.get('N2A_SEED', '128'))
    print(f'seed={seed}')
    rng = random.Random(seed)
    cocotb.start_soon(Clock(dut.clk_i, 40, unit='ns').start())
    log = []
    cocotb.start_soon(offer_monitor(dut, log))
    b = InputBench(dut)
    await b.reset()
    assert await b.status() == (0, 0, 0, 0, 0, 0)
    await host_translation(dut, b)
    await full_fifo_and_prefix_retention(dut, b)
    for competitor in ('control', 'physical', 'uart', 'mmio'):
        await competitor_sweep(dut, b, log, competitor)
    await b.reset()
    await mixed_streams(dut, b, rng)
    reasons = check_offers(log)
    for key in ('physical', 'uart', 'mmio', 'control', 'full', 'accepted', 'prefix'):
        assert reasons[key], (key, reasons)
    dut._log.info('JTAG_INPUT PASS cycles=%d offers=%s host_refusals=%d', b.cycles, reasons,
                  getattr(b, 'refusals', 0))
