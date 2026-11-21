"""ASIC backend of n2m_dual_port_ram, shared single-port path (8 KiB byte
store, over either n2a_sync_ram_wt backend): every edge's outputs equal an independent model of the portable
two-port contract (port A write-first on a same-port collision, port B
read-first against port A, valid registers, data holds), under the sharing
rule the backend documents: port A never reads on a port-B read edge or
while a write is posted, and never writes while one is posted; a port-A
write on a port-B read edge is posted until the first edge without a port-B
read. Directed: a write posted under a PPU-like read burst, read-first on
the write edge, forwarding of the posted word to later burst reads of its
address, retirement, a posted write kept through a port-B reset with
boot-clear-like writes and through a port-A reset under a port-B burst,
then a seeded random phase."""
import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge

CLOCK_PERIOD_NS = 40
DEPTH = 8192


class Store:
    """Backdoor to the shared store's bytes in either n2a_sync_ram_wt backend:
    the portable byte array, or the IHP backend's 4096 x 16 macro model rows
    holding two bytes each (address bit 0 picks the lane)."""

    def __init__(self, store):
        try:
            self.words = store.g_bank[0].u_sram.memory
            self.packed = True
        except AttributeError:
            self.words = store.memory
            self.packed = False

    def write_all(self, data):
        if self.packed:
            for row in range(len(data) // 2):
                self.words[row].value = data[2 * row] | data[2 * row + 1] << 8
        else:
            for index, value in enumerate(data):
                self.words[index].value = value

    def read(self, address):
        if self.packed:
            return (int(self.words[address >> 1].value) >> (8 * (address & 1))) & 0xFF
        return int(self.words[address].value)


class Model:
    """The portable two-port contract on one clock."""

    def __init__(self, memory):
        self.memory = memory
        self.valid_a = self.valid_b = 0
        self.data_a = self.data_b = 0
        self.pending = False

    def edge(self, read_a, write_a, address_a, data_a, read_b, address_b, reset_a=0, reset_b=0):
        old_b = self.memory[address_b]
        old_a = self.memory[address_a]
        write_a = write_a and not reset_a
        read_a = read_a and not reset_a
        read_b = read_b and not reset_b
        if read_a:
            self.data_a = data_a if write_a else old_a
        if read_b:
            self.data_b = old_b
        self.valid_a, self.valid_b = int(read_a), int(read_b)
        if write_a:
            self.memory[address_a] = data_a
        self.pending = (write_a and (read_b or self.pending)) or (self.pending and read_b)


async def drive(dut, model, read_a=0, write_a=0, address_a=0, data_a=0, read_b=0, address_b=0,
                reset_a=0, reset_b=0):
    await FallingEdge(dut.clk_i)
    dut.reset_a_i.value = reset_a
    dut.reset_b_i.value = reset_b
    dut.read_a_i.value = read_a
    dut.write_a_i.value = write_a
    dut.address_a_i.value = address_a
    dut.write_data_a_i.value = data_a
    dut.read_b_i.value = read_b
    dut.address_b_i.value = address_b
    await RisingEdge(dut.clk_i)
    model.edge(read_a, write_a, address_a, data_a, read_b, address_b, reset_a, reset_b)
    await ReadOnly()
    observed = (int(dut.read_valid_a_o.value), int(dut.read_data_a_o.value),
                int(dut.read_valid_b_o.value), int(dut.read_data_b_o.value))
    expected = (model.valid_a, model.data_a, model.valid_b, model.data_b)
    assert observed == expected, (observed, expected, (read_a, write_a, address_a, read_b, address_b))


@cocotb.test(timeout_time=20, timeout_unit='ms')
async def shared_sram_contract(dut):
    seed = int(os.environ['DV_SEED'])
    bound = int(os.environ['DV_MAX_CYCLES'])
    assert int(os.environ['DV_CLOCK_HZ']) == 25_000_000
    rng = random.Random(seed)
    cocotb.log.info('n2m_dual_port_ram shared SRAM seed=%d bound=%d', seed, bound)
    memory = [rng.randrange(256) for _ in range(DEPTH)]
    store = Store(dut.dut.g_shared_sram.u_store)
    store.write_all(memory)
    model = Model(memory)
    for name in ('reset_a_i', 'reset_b_i', 'read_a_i', 'write_a_i', 'address_a_i', 'write_data_a_i',
                 'read_b_i', 'address_b_i'):
        getattr(dut, name).value = 0
    cocotb.start_soon(Clock(dut.clk_i, CLOCK_PERIOD_NS, unit='ns').start())
    edges = 0

    await drive(dut, model, read_a=1, address_a=0x1234)
    old = model.data_a
    await drive(dut, model, write_a=1, address_a=0x1234, data_a=0x3C, read_b=1, address_b=0x1234)
    assert model.data_b == old and model.pending
    for address in (0x1234, 0x0042, 0x1234):
        await drive(dut, model, read_b=1, address_b=address)
    assert model.data_b == 0x3C and model.pending
    await drive(dut, model)
    await drive(dut, model, read_a=1, address_a=0x1234)
    assert model.data_a == 0x3C and not model.pending
    await drive(dut, model, read_a=1, write_a=1, address_a=0x0007, data_a=0xA5)
    await drive(dut, model, read_b=1, address_b=0x0007)
    for _ in range(3):
        await drive(dut, model)
    edges += 12

    await drive(dut, model, write_a=1, address_a=0x0300, data_a=0x5A, read_b=1, address_b=0x0200)
    assert model.pending
    for address in range(4):
        await drive(dut, model, write_a=1, address_a=address, data_a=0, read_b=1, address_b=0x0200,
                    reset_b=1)
    await drive(dut, model)
    assert [store.read(a) for a in (0x0300, 0, 1, 2, 3)] == [0x5A, 0, 0, 0, 0], 'reset_b kept writes'
    await drive(dut, model, write_a=1, address_a=0x0301, data_a=0xC3, read_b=1, address_b=0x0301)
    for _ in range(3):
        await drive(dut, model, read_b=1, address_b=0x0301, reset_a=1)
    assert model.data_b == 0xC3
    await drive(dut, model, reset_a=1)
    await drive(dut, model)
    assert store.read(0x0301) == 0xC3, 'reset_a kept the posted write'
    edges += 13

    burst = 0
    hot = [rng.randrange(DEPTH) for _ in range(8)]
    while edges < bound:
        if burst == 0 and rng.random() < 0.15:
            burst = rng.randrange(1, 6)
        read_b = int(burst > 0)
        burst = max(0, burst - 1)
        address_b = rng.choice(hot)
        address_a = rng.choice(hot)
        read_a = int(not read_b and not model.pending and rng.random() < 0.4)
        write_a = int(not model.pending and rng.random() < 0.3)
        await drive(dut, model, read_a, write_a, address_a, rng.randrange(256), read_b, address_b)
        edges += 1
    await drive(dut, model)                                   # Why: retires a last posted write.
    observed = [store.read(a) for a in hot]
    assert observed == [model.memory[a] for a in hot], 'final store contents'
    cocotb.log.info('shared SRAM contract passed: edges=%d', edges)
