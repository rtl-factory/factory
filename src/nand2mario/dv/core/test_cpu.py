"""Public-port CPU checks with independent instruction and memory oracles."""
import os
import random
from pathlib import Path

import cocotb
from cocotb.triggers import Timer

from .cpu_model import CPU, ILLEGAL

FIELDS = [('valid', 1), ('is_interrupt', 1), ('pc_before', 16), ('pc_after', 16),
          ('opcode', 24), ('opcode_length', 2)] + [(r, 8) for r in 'afbcdehl'] + [
          ('sp', 16), ('ime', 1), ('halted', 1), ('stopped', 1)]


def unpack(value):
    result = {}
    for name, bits in reversed(FIELDS):
        result[name] = value & ((1 << bits)-1)
        value >>= bits
    assert value == 0
    return result


class Bench:
    def __init__(self, dut, seed):
        self.dut, self.rng = dut, random.Random(seed)
        self.cycles = 0
        self.bound = int(os.environ['DV_MAX_CYCLES'])
        self.mem = bytearray(65536)
        self.writes, self.reads, self.acks = [], [], []
        self.period = 3
        self.delay = 0
        self.pending = None
        self.enables = 0
        self.fault_response = False
        self.fault_on_write = False
        self.divider_resets = 0
        self.map_interrupt_registers = False
        self.events = []

    async def edge(self, enable=None):
        d = self.dut
        self.cycles += 1
        assert self.cycles <= self.bound, 'finite simulation bound exceeded'
        d.clk_i.value = 0
        ce = self.cycles % self.period == 0 if enable is None else enable
        d.machine_cycle_enable_i.value = int(ce)
        d.response_i.value = 0
        await Timer(10, unit='ns')
        request = int(d.request_o.value)
        valid, write = (request >> 27) & 1, (request >> 26) & 1
        address, data = (request >> 8) & 65535, request & 255
        kind = (request >> 24) & 3
        ie_write = None
        if valid:
            assert kind != 3 and (not write or kind in (1, 2)), 'invalid bus access kind'
            if self.pending is None:
                self.pending = request
                self.remaining = self.delay
            assert self.pending == request, 'request changed during bus stall'
            if self.remaining == 0:
                fault = self.fault_response or (self.fault_on_write and write)
                d.response_i.value = 512 | (256 if fault else 0) | self.mem[address]
                if not fault:
                    if write:
                        self.mem[address] = data
                        self.writes.append((address, data))
                        self.events.append((self.cycles, self.enables + int(ce), 'write', address, data))
                        if self.map_interrupt_registers and address == 0xFFFF:
                            ie_write = data & 31
                    else:
                        self.reads.append(address)
                        self.events.append((self.cycles, self.enables + int(ce), 'read', address, self.mem[address]))
                self.pending = None
            else:
                self.remaining -= 1
        else:
            assert self.pending is None, 'request withdrawn before response'
        await Timer(10, unit='ns')
        ack = int(d.interrupt_ack_o.value)
        if ack:
            self.acks.append(ack)
            self.events.append((self.cycles, self.enables + int(ce), 'ack', ack))
        self.divider_resets += int(d.divider_reset_o.value)
        if ce: self.enables += 1
        d.clk_i.value = 1
        await Timer(20, unit='ns')
        # Why: model synchronous IE/IF writes: the CPU sees the old value on the
        # response edge and the new value on the next edge, as in the system.
        if self.map_interrupt_registers:
            if ie_write is not None: d.interrupt_enable_i.value = ie_write
            if ack: d.interrupt_flags_i.value = int(d.interrupt_flags_i.value) & ~ack
        record = unpack(int(d.retirement_o.value))
        if record['valid']:
            self.events.append((self.cycles, self.enables, 'retire', record['pc_before'],
                                record['pc_after'], record['is_interrupt']))
        return record if record['valid'] else None

    async def reset(self, mem):
        d = self.dut
        d.reset_i.value = 1
        d.interrupt_enable_i.value = 0
        d.interrupt_flags_i.value = 0
        d.joyp_selected_active_i.value = 0
        d.wake_i.value = 0
        self.pending = None
        for _ in range(2): await self.edge(False)
        self.mem = bytearray(mem)
        self.writes, self.reads, self.acks = [], [], []
        self.enables = 0
        self.divider_resets = 0
        self.map_interrupt_registers = False
        self.events = []
        self.delay = 0
        self.fault_response = False
        self.fault_on_write = False
        d.reset_i.value = 0

    async def retire(self, bound=300):
        for _ in range(bound):
            record = await self.edge()
            if record: return record
            assert not int(self.dut.fault_o.value), 'unexpected CPU bus fault'
            assert not int(self.dut.locked_o.value), 'unexpected CPU illegal lock'
        raise AssertionError('retirement timeout')

    async def check(self, model, count, label, timing=True):
        last = None
        expected_writes = []
        for index in range(count):
            expected, cycles, writes = model.step()
            expected_writes.extend(writes)
            record = await self.retire()
            assert {k: record[k] for k in expected} == expected, (label, index, expected, record)
            if timing and last is not None:
                assert self.enables-last == cycles, (label, index, 'cycles', cycles, self.enables-last)
            last = self.enables
            assert self.writes == expected_writes, (label, index, 'writes', expected_writes, self.writes)


def setup_program(rng):
    a, f = rng.randrange(256), rng.randrange(16) << 4
    # Why: AF is loadable only through POP, so the setup pushes BC and pops it into AF.
    return bytes([0x31, 0x00, 0xD0, 0x01, f, a, 0xC5, 0xF1,
                  0x01, rng.randrange(256), rng.randrange(256),
                  0x11, 0x10, 0xC1, 0x21, 0x23, 0xC1])


@cocotb.test()
async def cpu_contract(dut):
    seed = int(os.environ['DV_SEED'])
    assert int(os.environ['DV_CLOCK_HZ']) == 25_000_000
    bench = Bench(dut, seed)
    rng = random.Random(seed)
    cocotb.log.info('CPU oracle seed=%d', seed)
    # Why: both flag polarity sets drive every conditional path, with varied operands.
    for trial in range(2):
        for op in range(256):
            if op in ILLEGAL or op in (0xCB, 0x10, 0x76): continue
            mem = bytearray(65536)
            prefix = bytearray(setup_program(rng))
            prefix[4] = 0xF0 if trial else 0
            program = prefix + bytes([op, 0x80, 0xC2, 0, 0])
            mem[0x100:0x100+len(program)] = program
            mem[0xC123] = rng.randrange(256)
            mem[0xD000:0xD002] = bytes([0x00, 0x03])
            await bench.reset(mem)
            await bench.check(CPU(bytearray(mem)), 8, f'base={op:02x} trial={trial}')
    for op in range(256):
        mem = bytearray(65536)
        program = setup_program(rng) + bytes([0xCB, op, 0])
        mem[0x100:0x100+len(program)] = program
        mem[0xC123] = rng.randrange(256)
        await bench.reset(mem)
        await bench.check(CPU(bytearray(mem)), 8, f'CB={op:02x}')
    for trial in range(8):
        program = bytearray(setup_program(rng))
        for _ in range(40):
            program.extend([0x3E, rng.randrange(256), 0xC6, rng.randrange(256),
                            0x27, 0xCB, rng.randrange(256), 0x34, 0x35])
        mem = bytearray(65536); mem[0x100:0x100+len(program)] = program
        await bench.reset(mem)
        bench.delay = trial % 5
        await bench.check(CPU(bytearray(mem)), 7+40*6, f'generated={trial}', timing=bench.delay == 0)
    for op in ILLEGAL:
        mem = bytearray(65536); mem[0x100] = op
        await bench.reset(mem)
        for _ in range(30): assert await bench.edge() is None
        assert int(dut.locked_o.value) and not bench.writes
    await bench.reset(bytearray(65536)); bench.fault_response = True
    for _ in range(20): assert await bench.edge() is None
    assert int(dut.fault_o.value) and not int(dut.request_o.value) >> 27
    mem = bytearray(65536); mem[0x100:0x104] = bytes([0xEA, 0, 0xC0, 0])
    await bench.reset(mem); bench.fault_on_write = True
    for _ in range(40): assert await bench.edge() is None
    assert int(dut.fault_o.value) and not bench.writes
    await sleep_interrupt_protocol(bench)
    await wake_sampling_boundaries(bench)
    await interrupt_stack_selection(bench)
    await interrupt_selection_hold(bench)
    await interrupt_selection_adjacent(bench)
    await interrupt_wake_timing(bench)
    await halt_ime0_resume_timing(bench)
    await di_interrupt_boundary(bench)
    await original_image(bench)
    cocotb.log.info('Base/CB/original generated/illegal/fault checks passed cycles=%d', bench.cycles)


async def sleep_interrupt_protocol(b):
    d = b.dut
    for program in (bytes([0xFB, 0, 0xF3, 0]), bytes([0xFB, 0xFB, 0, 0xF3]),
                    bytes([0xFB, 0xF3, 0, 0])):
        mem = bytearray(65536); mem[0x100:0x104] = program
        await b.reset(mem)
        await b.check(CPU(bytearray(mem)), 4, 'EI delay/DI cancellation')
    for pending in (1, 2, 4, 8, 16, 31, 24):
        mem = bytearray(65536); mem[0x100:0x104] = bytes([0xFB, 0, 0, 0x76])
        bit = (pending & -pending).bit_length()-1
        vector = 0x40+8*bit
        mem[vector] = 0xD9
        await b.reset(mem)
        d.interrupt_enable_i.value = 31
        d.interrupt_flags_i.value = pending
        model = CPU(bytearray(mem))
        for _ in range(2):
            expected, _, _ = model.step()
            got = await b.retire()
            assert all(got[k] == v for k, v in expected.items()), (expected, got)
        previous = b.enables
        got = await b.retire()
        assert b.enables-previous == 5
        assert got['is_interrupt'] == 1 and got['opcode_length'] == 0 and got['opcode'] == 0
        assert (got['pc_before'], got['pc_after'], got['sp'], got['ime']) == (0x102, vector, 0xFFFC, 0)
        assert b.writes == [(0xFFFD, 1), (0xFFFC, 2)]
        assert b.acks == [1 << bit]
        d.interrupt_flags_i.value = 0
        model.push(0x102); model.pc = vector; model.ime = False
        expected, cycles, _ = model.step()
        previous = b.enables
        got = await b.retire()
        assert all(got[k] == v for k, v in expected.items()), (expected, got)
        assert b.enables-previous == cycles == 4
    # Ref: Pan Docs, interrupt dispatch: an IRQ withdrawn after entry recognition vectors to $0000 without ack;
    # stack effects and the five-cycle entry still complete.
    mem = bytearray(65536); mem[0x100:0x103] = bytes([0xFB, 0, 0])
    await b.reset(mem); d.interrupt_enable_i.value = 1; d.interrupt_flags_i.value = 1
    await b.retire(); await b.retire()
    d.interrupt_flags_i.value = 0
    previous = b.enables
    got = await b.retire()
    assert got['is_interrupt'] and got['pc_after'] == 0 and not b.acks
    assert b.enables-previous == 5 and b.writes == [(0xFFFD, 1), (0xFFFC, 2)]
    mem[0x100:0x104] = bytes([0xFB, 0, 0x76, 0])
    await b.reset(mem)
    await b.retire(); await b.retire(); got = await b.retire()
    assert got['halted'] and got['ime']
    for _ in range(12): assert await b.edge() is None
    d.interrupt_enable_i.value = 4; d.interrupt_flags_i.value = 4
    got = await b.retire()
    assert got['is_interrupt'] and got['pc_before'] == 0x103 and got['pc_after'] == 0x50
    assert b.writes == [(0xFFFD, 1), (0xFFFC, 3)]
    # Ref: Pan Docs, HALT behaviour: the no-IME wake and the HALT bug, which fetches the next byte twice.
    mem = bytearray(65536); mem[0x100:0x105] = bytes([0x76, 0x3E, 0x42, 0, 0])
    await b.reset(mem)
    got = await b.retire()
    assert got['halted'] and got['pc_after'] == 0x101
    reads = list(b.reads)
    for _ in range(30): assert await b.edge() is None
    assert b.reads == reads and not b.writes
    d.interrupt_enable_i.value = 1; d.interrupt_flags_i.value = 1
    got = await b.retire()
    assert (got['pc_before'], got['pc_after'], got['a']) == (0x101, 0x103, 0x42)
    assert not got['halted'] and not b.acks
    await b.reset(mem)
    await b.retire()
    b.delay = 8
    d.interrupt_enable_i.value = 1; d.interrupt_flags_i.value = 1
    for _ in range(b.period-1): await b.edge(False)
    await b.edge(True)
    assert b.pending is not None
    d.interrupt_flags_i.value = 0
    got = await b.retire()
    assert (got['pc_before'], got['a']) == (0x101, 0x42)
    await b.reset(mem)
    d.interrupt_enable_i.value = 1; d.interrupt_flags_i.value = 1
    model = CPU(bytearray(mem))
    for _ in range(2):
        expected, _, _ = model.step(pending=1)
        got = await b.retire()
        assert all(got[k] == v for k, v in expected.items()), ('HALT bug', expected, got)
    assert got['a'] == 0x3E and got['opcode'] == 0x3E3E
    # Ref: Pan Docs, HALT behaviour: EI then HALT with an IRQ already pending returns to the HALT, not the next opcode.
    mem[0x100:0x104] = bytes([0xFB, 0x76, 0, 0])
    await b.reset(mem); d.interrupt_enable_i.value = 1; d.interrupt_flags_i.value = 1
    await b.retire(); got = await b.retire()
    assert got['pc_after'] == 0x101 and got['ime'] and not got['halted']
    got = await b.retire()
    assert got['is_interrupt'] and got['pc_before'] == 0x101
    for selected in (0, 1):
        for pending in (0, 1):
            mem = bytearray(65536); mem[0x100:0x104] = bytes([0x10, 0, 0, 0])
            await b.reset(mem)
            d.joyp_selected_active_i.value = selected
            d.interrupt_enable_i.value = pending; d.interrupt_flags_i.value = pending
            model = CPU(bytearray(mem)); expected, _, _ = model.step(pending, selected)
            got = await b.retire()
            assert all(got[k] == v for k, v in expected.items()), ('STOP', selected, pending, expected, got)
            assert b.divider_resets == (not selected)
            if not selected:
                reads = list(b.reads)
                for _ in range(12): assert await b.edge() is None
                assert b.reads == reads
                d.wake_i.value = 1
                await b.edge(False); d.wake_i.value = 0
                got = await b.retire()
                assert got['pc_before'] == 0x101+(not pending) and not got['stopped']
    mem = bytearray(65536); mem[0x100:0x104] = bytes([0xEA, 0, 0xC0, 0])
    await b.reset(mem)
    for _ in range(12):
        await b.edge()
        if b.writes: break
    assert b.writes == [(0xC000, 1)]
    for _ in range(15): assert await b.edge(False) is None
    assert b.writes == [(0xC000, 1)]
    got = await b.retire()
    assert got['pc_before'] == 0x100 and b.writes == [(0xC000, 1)]
    await b.reset(mem); b.delay = 100
    await b.edge()
    assert b.pending is not None
    await b.reset(mem)
    got = await b.retire()
    assert got['pc_before'] == 0x100 and not got['is_interrupt']


async def wake_sampling_boundaries(b):
    """Qualified one-edge STOP wake and machine-boundary-only HALT admission."""
    d = b.dut
    b.period = 24
    for coincident in (False, True):
        mem = bytearray(65536)
        mem[0x100:0x104] = bytes([0x10, 0, 0x76, 0])
        await b.reset(mem)
        got = await b.retire()
        assert got['stopped'] and got['pc_after'] == 0x102
        for _ in range(23 if coincident else 22): await b.edge(False)
        d.wake_i.value = 1
        assert await b.edge(coincident) is None
        d.wake_i.value = 0
        got = await b.retire()
        assert got['pc_before'] == 0x102 and got['halted'] and not got['stopped']
        assert b.divider_resets == 1

    mem = bytearray(65536)
    mem[0x100:0x104] = bytes([0x76, 0x3E, 0x42, 0x76])
    await b.reset(mem)
    got = await b.retire()
    assert got['halted']
    reads = list(b.reads)
    d.interrupt_enable_i.value = 1
    d.interrupt_flags_i.value = 1
    assert await b.edge(False) is None
    d.interrupt_flags_i.value = 0
    for _ in range(120): assert await b.edge() is None
    assert int(d.halted_o.value) and b.reads == reads and not b.writes

    await b.reset(mem)
    await b.retire()
    b.delay = 30
    for _ in range(23): await b.edge(False)
    d.interrupt_enable_i.value = 1
    d.interrupt_flags_i.value = 1
    assert await b.edge(True) is None
    assert b.pending is not None
    d.interrupt_flags_i.value = 0
    got = await b.retire()
    assert (got['pc_before'], got['pc_after'], got['a']) == (0x101, 0x103, 0x42)
    assert b.reads.count(0x101) == 2  # Why: the discarded pre-HALT fetch, then the fresh wake fetch.
    assert not b.acks and not b.writes
    b.period = 3


async def interrupt_stack_selection(b):
    """IE changes by the upper push affect selection; lower push is too late."""
    d = b.dut
    b.period = 24
    # Why: independent expected vectors/ack masks come from the specified push
    # ordering, not the implementation's microsteps. PC at dispatch is $0205.
    for delay in (0, 23, 29):
        for sp, pending, vector, ack, remaining in (
                (0x0000, 4, 0x0000, 0, 4),
                (0x0000, 3, 0x0048, 2, 1),
                (0x0001, 8, 0x0058, 8, 0)):
            mem = bytearray(65536)
            mem[0x100:0x103] = bytes([0xC3, 0x00, 0x02])
            mem[0x200:0x206] = bytes([0x31, sp & 255, sp >> 8, 0xFB, 0, 0])
            await b.reset(mem)
            b.map_interrupt_registers = True
            d.interrupt_enable_i.value = pending
            d.interrupt_flags_i.value = pending
            for _ in range(4): await b.retire()
            b.delay = delay
            got = await b.retire(bound=400)
            label = ('IE stack selection', sp, pending, delay, b.events)
            assert (got['is_interrupt'], got['pc_before'], got['pc_after'], got['ime']) == (1, 0x205, vector, 0), label
            assert b.writes == [((sp-1) & 65535, 2), ((sp-2) & 65535, 5)], label
            assert b.acks == ([ack] if ack else []), label
            assert int(d.interrupt_flags_i.value) == remaining, label
            assert int(d.interrupt_enable_i.value) == (2 if sp == 0 else 5), label
            cocotb.log.info('interrupt stack boundary sp=%04x pending=%02x delay=%d events=%s',
                            sp, pending, delay, b.events)
            d.interrupt_enable_i.value = 16
            d.interrupt_flags_i.value = 16
            got = await b.retire(bound=400)
            assert not got['is_interrupt'] and not got['ime'], label
    b.period = 3


async def interrupt_selection_adjacent(b):
    """Selection and low-push completion may share the next system edge."""
    d = b.dut
    b.period = 1
    for bit, vector in enumerate((0x40, 0x48, 0x50, 0x58, 0x60)):
        mem = bytearray(65536)
        mem[0x100:0x103] = bytes([0xFB, 0, 0])
        await b.reset(mem)
        b.map_interrupt_registers = True
        d.interrupt_enable_i.value = 31
        d.interrupt_flags_i.value = 1 << bit
        await b.retire(); await b.retire()
        got = await b.retire()
        writes = [e for e in b.events if e[2] == 'write']
        acks = [e for e in b.events if e[2] == 'ack']
        assert (got['is_interrupt'], got['pc_after'], got['ime']) == (1, vector, 0), b.events
        assert b.writes == [(0xFFFD, 1), (0xFFFC, 2)], b.events
        assert b.acks == [1 << bit] and int(d.interrupt_flags_i.value) == 0, b.events
        assert writes[1][0] == writes[0][0] + 1 == acks[0][0], b.events
        cocotb.log.info('interrupt adjacent vector=%04x events=%s', vector, b.events)
    for action in ('cancel', 'fault', 'reset'):
        mem = bytearray(65536)
        mem[0x100:0x106] = bytes([0x31, 0, 0, 0xFB, 0, 0])
        await b.reset(mem)
        b.map_interrupt_registers = True
        d.interrupt_enable_i.value = 4 if action == 'cancel' else 1
        d.interrupt_flags_i.value = 4 if action == 'cancel' else 1
        for _ in range(3): await b.retire()
        for _ in range(20):
            await b.edge()
            if b.writes: break
        else: raise AssertionError('upper push did not complete')
        assert b.writes == [(0xFFFF, 1)], b.events
        upper_cycle = b.cycles
        if action == 'cancel':
            got = await b.retire()
            assert (got['is_interrupt'], got['pc_after'], got['ime']) == (1, 0, 0), b.events
            assert b.writes == [(0xFFFF, 1), (0xFFFE, 5)], b.events
            assert [e[0] for e in b.events if e[2] == 'write'] == [upper_cycle, upper_cycle + 1], b.events
            assert int(d.interrupt_flags_i.value) == 4, b.events
        else:
            if action == 'fault': b.fault_on_write = True
            else: d.reset_i.value = 1
            for _ in range(3): assert await b.edge() is None, b.events
            assert b.writes == [(0xFFFF, 1)], b.events
            assert int(d.fault_o.value) == (action == 'fault'), b.events
            assert not int(d.request_o.value) >> 27, b.events
        assert not b.acks, b.events
        cocotb.log.info('interrupt adjacent action=%s events=%s', action, b.events)
    b.period = 3


async def interrupt_wake_timing(b):
    """HALT with IME=1 has the same dispatch boundary as waiting with NOPs."""
    d = b.dut
    b.period = 24
    for delay in (1, 4):
        elapsed = []
        for sleeping in (False, True):
            mem = bytearray(65536)
            mem[0x100:0x105] = bytes([0xFB, 0, 0x76 if sleeping else 0, 0, 0])
            await b.reset(mem)
            b.delay = delay
            await b.retire(); await b.retire(); got = await b.retire()
            assert got['halted'] == sleeping and got['ime']
            for _ in range(delay+1): await b.edge(False)
            d.interrupt_enable_i.value = 1
            d.interrupt_flags_i.value = 1
            before = b.enables
            await b.edge(True)
            got = await b.retire(bound=400)
            assert got['is_interrupt'] and got['pc_after'] == 0x40, b.events
            elapsed.append(b.enables-before)
            cocotb.log.info('interrupt wake sleeping=%s delay=%d machine_cycles=%d events=%s',
                            sleeping, delay, elapsed[-1], b.events)
        assert elapsed == [6, 6], ('HALT adds a dispatch cycle', delay, elapsed)
    b.period = 3


async def halt_ime0_resume_timing(b):
    """A qualified IME=0 wake fetch must not cost another decode boundary."""
    d = b.dut
    b.period = 24
    for delay in (0, 1, 4, 23, 24, 29, 48):
        mem = bytearray(65536)
        mem[0x100] = 0x76
        await b.reset(mem)
        got = await b.retire()
        assert got['halted'] and not got['ime'] and b.reads.count(0x101) == 1
        # Why: the pre-HALT pipeline byte was NOP. Wake must fetch the changed
        # INC B, rather than silently using the stale prefetch to save time.
        b.mem[0x101] = 0x04
        for _ in range(23): assert await b.edge(False) is None
        d.interrupt_enable_i.value = 1
        d.interrupt_flags_i.value = 1
        b.delay = delay
        response_at = None
        before = b.enables
        for offset in range(100):
            got = await b.edge(offset % 24 == 0)
            if b.reads.count(0x101) == 2 and response_at is None:
                response_at = offset
                # Why: isolate wake-response latency; following accesses are
                # unstalled, so retirement belongs to the next boundary.
                b.delay = 0
            if offset == 0:
                d.interrupt_flags_i.value = 0
            if got:
                break
        else:
            raise AssertionError(('IME=0 wake did not retire', delay, b.events))
        label = ('IME=0 wake phase', delay, response_at, offset, b.events)
        assert response_at == delay, label
        assert offset == (delay // 24 + 1) * 24, label
        assert (got['pc_before'], got['pc_after'], got['opcode'], got['opcode_length'],
                got['b'], got['sp'], got['ime'], got['halted'], got['is_interrupt']) == (
                    0x101, 0x102, 0x04, 1, 1, 0xFFFE, 0, 0, 0), label
        assert b.reads.count(0x101) == 2 and not b.acks and not b.writes, label
        cocotb.log.info('IME0 wake delay=%d response_offset=%d retire_offset=%d machine_cycles=%d events=%s',
                        delay, response_at, offset, b.enables-before, b.events)

    await b.reset(mem)
    await b.retire()
    b.delay = 4
    b.fault_response = True
    d.interrupt_enable_i.value = 1
    d.interrupt_flags_i.value = 1
    for offset in range(72):
        assert await b.edge(offset % 24 == 0) is None
        if offset == 0: d.interrupt_flags_i.value = 0
    assert int(d.fault_o.value) and not int(d.request_o.value) >> 27
    assert b.reads.count(0x101) == 1 and not b.acks and not b.writes

    await b.reset(mem)
    await b.retire()
    b.delay = 29
    d.interrupt_enable_i.value = 1
    d.interrupt_flags_i.value = 1
    assert await b.edge(True) is None and b.pending is not None
    mem[0x100] = 0
    await b.reset(mem)
    got = await b.retire()
    assert got['pc_before'] == 0x100 and got['opcode'] == 0 and not got['halted']
    assert not int(d.fault_o.value) and not b.acks and not b.writes
    b.period = 3


async def interrupt_selection_hold(b):
    """IF changes after selection cannot cancel or reprioritize a held push."""
    d = b.dut
    b.period = 24
    for late_flags in (0, 9):
        mem = bytearray(65536)
        mem[0x100:0x103] = bytes([0xFB, 0, 0])
        await b.reset(mem)
        b.map_interrupt_registers = True
        d.interrupt_enable_i.value = 31
        d.interrupt_flags_i.value = 8
        await b.retire(); await b.retire()
        b.delay = 29
        for _ in range(200):
            await b.edge()
            if b.writes: break
        else: raise AssertionError('upper push did not complete')
        while b.cycles % b.period: await b.edge()
        await b.edge(False)
        assert b.pending is not None and len(b.writes) == 1, b.events
        d.interrupt_flags_i.value = late_flags
        got = await b.retire(bound=400)
        assert (got['is_interrupt'], got['pc_after']) == (1, 0x58), b.events
        assert b.acks == [8] and int(d.interrupt_flags_i.value) == late_flags & ~8, b.events
        assert b.writes == [(0xFFFD, 1), (0xFFFC, 2)], b.events
        cocotb.log.info('interrupt frozen selection late_flags=%02x events=%s', late_flags, b.events)
    b.period = 3


@cocotb.test()
async def interrupt_timing_probe(dut):
    """Focused development entry; every check also runs in cpu_contract."""
    bench = Bench(dut, int(os.environ['DV_SEED']))
    await interrupt_stack_selection(bench)
    await interrupt_selection_hold(bench)
    await interrupt_selection_adjacent(bench)
    await interrupt_wake_timing(bench)
    await halt_ime0_resume_timing(bench)
    await di_interrupt_boundary(bench)


@cocotb.test()
async def interrupt_wake_probe(dut):
    await interrupt_wake_timing(Bench(dut, int(os.environ['DV_SEED'])))


@cocotb.test()
async def halt_ime0_resume_probe(dut):
    await halt_ime0_resume_timing(Bench(dut, int(os.environ['DV_SEED'])))


async def di_interrupt_boundary(b):
    """An IRQ before DI preempts it; an IRQ at DI completion is suppressed."""
    d = b.dut
    b.period = 24
    for before_di in (True, False):
        mem = bytearray(65536)
        mem[0x100:0x105] = bytes([0xFB, 0, 0, 0xF3, 0])
        await b.reset(mem)
        b.delay = 4
        await b.retire(); await b.retire()
        if not before_di: await b.retire()
        d.interrupt_enable_i.value = 1
        d.interrupt_flags_i.value = 1
        got = await b.retire()
        if before_di:
            assert got['pc_before'] == 0x102 and got['ime'], b.events
            got = await b.retire()
            assert got['is_interrupt'] and got['pc_before'] == 0x103, b.events
            assert b.writes == [(0xFFFD, 1), (0xFFFC, 3)], b.events
        else:
            assert got['pc_before'] == 0x103 and not got['ime'], b.events
            got = await b.retire()
            assert got['pc_before'] == 0x104 and not got['is_interrupt'], b.events
            assert not b.writes and not b.acks, b.events
        cocotb.log.info('DI pending before_di=%s events=%s', before_di, b.events)
    b.period = 3


async def original_image(b):
    root = Path(__file__).resolve().parents[4]
    image_arg = os.environ.get('N2M_CPU_IMAGE')
    assert image_arg, 'set N2M_CPU_IMAGE to the documented locally built smoke image'
    image = (root / image_arg).resolve()
    assert image.is_relative_to(root / 'workdir') and image.is_file(), 'CPU image must exist below repository workdir'
    mem = bytearray(65536); mem[:32768] = image.read_bytes()
    await b.reset(mem)
    b.period = 24
    model = CPU(bytearray(mem))
    expected_writes = []
    for index in range(100):
        expected, _, writes = model.step()
        expected_writes.extend(writes)
        got = await b.retire(bound=240)
        assert all(got[k] == v for k, v in expected.items()), ('local image', index, expected, got)
        assert b.writes == expected_writes
        if got['halted']: break
    else:
        raise AssertionError('original image did not finish within 100 instructions')
    assert b.mem[0xC000:0xC002] == bytes([110, 0xA5])
    b.period = 3
