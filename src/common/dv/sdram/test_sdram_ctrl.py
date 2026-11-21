"""Contract of the common SDRAM controller (wiki/src/common/rtl/sdram/mas.md).

Random reads and writes, aligned and misaligned, over a few rows of every bank
so the open-row configuration sees hits, closed banks and misses, and both
configurations cross many refreshes. A byte shadow of every written lane
checks read data; the latency from acceptance to the response pulse is
checked against the MAS table of the configuration (row state taken from the
command pins); a misaligned request must answer with error on the next clock
and issue no command. The common device model checks every pin rule."""
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge
from cocotb.utils import get_sim_time

CLOSE_READ, CLOSE_WRITE = 9, 10
# Ref: wiki/src/common/rtl/sdram/mas.md latency table, open row: (read, write)
# by row state at acceptance.
OPEN_LATENCY = {"hit": (10, 9), "closed": (11, 10), "miss": (12, 11)}


class Bench:
    def __init__(self, dut):
        self.d = dut
        self.line = bool(int(dut.LINE.value))
        self.beats = 8 if self.line else 1
        self.lanes = 2 * self.beats
        self.shadow = {}
        self.open_rows = {}
        self.activates = 0
        self.refreshes = 0

    @property
    def cycle(self):
        return int(get_sim_time("ns")) // 40

    def bank_row(self, address):
        if self.line:
            return ((address >> 11) ^ (address >> 13)) & 3, (address >> 13) & 0x1FFF
        return (address >> 24) & 3, (address >> 11) & 0x1FFF

    async def monitor(self):
        d = self.d
        while True:
            # Why: the command of a clock is stable at its falling edge.
            await FallingEdge(d.clk_i)
            if int(d.reset_i.value):
                continue
            ras, cas, we = (int(d.u_ctrl.sdram_ras_n_o.value), int(d.u_ctrl.sdram_cas_n_o.value),
                            int(d.u_ctrl.sdram_we_n_o.value))
            bank, address = int(d.u_ctrl.sdram_bank_o.value), int(d.u_ctrl.sdram_address_o.value)
            command = (ras, cas, we)
            if command == (0, 1, 1):
                self.activates += 1
                self.open_rows[bank] = address
            elif command == (0, 1, 0):
                if address & (1 << 10):
                    self.open_rows.clear()
                else:
                    self.open_rows.pop(bank, None)
            elif command == (0, 0, 1):
                self.refreshes += 1

    def sample(self):
        """(cycle, error, data) when the response pulses in this clock, else None."""
        d = self.d
        if not int(d.response_valid_o.value):
            return None
        return self.cycle, int(d.response_error_o.value), int(d.response_data_o.value)

    async def transaction(self, rng):
        d = self.d
        write = rng.random() < 0.5
        misaligned = rng.random() < 0.05
        bank = rng.randrange(4)
        row = rng.choice((3, 4, 0x1ABC))
        column = rng.randrange(1024 // self.beats) * self.beats
        if self.line:
            address = (row << 13) | (((bank ^ row) & 3) << 11) | (column << 1)
        else:
            address = (bank << 24) | (row << 11) | (column << 1)
        if misaligned:
            address |= rng.randrange(1, self.lanes)
        data = rng.getrandbits(16 * self.beats)
        enables = rng.getrandbits(self.lanes)
        d.request_valid_i.value = 1
        d.request_write_i.value = int(write)
        d.request_address_i.value = address
        d.request_write_data_i.value = data
        d.request_byte_enable_i.value = enables
        # Why: the bench drives from a falling edge, so ready seen there is the ready
        # that the next rising edge samples.
        await ReadOnly()
        while not int(d.request_ready_o.value):
            await FallingEdge(d.clk_i)
            await ReadOnly()
        await RisingEdge(d.clk_i)
        accepted = self.cycle
        state = None
        if not misaligned:
            b, r = self.bank_row(address)
            state = "closed" if b not in self.open_rows else "hit" if self.open_rows[b] == r else "miss"
        activates = self.activates
        await ReadOnly()
        response = self.sample()
        await FallingEdge(d.clk_i)
        d.request_valid_i.value = 0
        while response is None:
            await RisingEdge(d.clk_i)
            await ReadOnly()
            response = self.sample()
            assert self.cycle - accepted < 40, "no response"
        cycle, error, got = response
        # Why: latency counts the clocks after the accepting edge up to and
        # including the clock in which the response pulses.
        latency = cycle - accepted + 1
        if misaligned:
            assert error == 1 and latency == 1, f"misaligned request: error {error}, latency {latency}"
            assert self.activates == activates, "a misaligned request opened a row"
        else:
            assert error == 0, "aligned request answered with error"
            if self.line:
                expected = OPEN_LATENCY[state][1 if write else 0]
            else:
                expected = CLOSE_WRITE if write else CLOSE_READ
            assert latency == expected, f"{'write' if write else 'read'} {state}: latency {latency} != {expected}"
            if write:
                for lane in range(self.lanes):
                    if enables >> lane & 1:
                        self.shadow[address + lane] = data >> (8 * lane) & 0xFF
            else:
                for lane in range(self.lanes):
                    if address + lane in self.shadow:
                        byte = got >> (8 * lane) & 0xFF
                        assert byte == self.shadow[address + lane], \
                            f"read {address + lane:#x}: {byte:#04x} != {self.shadow[address + lane]:#04x}"
            if not self.line:
                await FallingEdge(d.clk_i)
                await ReadOnly()
                assert not int(d.idle_o.value), "close page: idle before the clock after the response"
                await RisingEdge(d.clk_i)
                await ReadOnly()
                assert int(d.idle_o.value), "close page: not idle in the clock after the response"
        await FallingEdge(d.clk_i)
        for _ in range(rng.choice((0, 0, 0, 1, 2, 7))):
            await FallingEdge(d.clk_i)


@cocotb.test()
async def sdram_ctrl_contract(dut):
    b = Bench(dut)
    rng = random.Random(int(cocotb.RANDOM_SEED))
    cocotb.start_soon(Clock(dut.clk_i, 40, unit="ns").start())
    cocotb.start_soon(b.monitor())
    dut.reset_i.value = 1
    dut.request_valid_i.value = 0
    dut.request_write_i.value = 0
    dut.request_address_i.value = 0
    dut.request_write_data_i.value = 0
    dut.request_byte_enable_i.value = 0
    for _ in range(4):
        await FallingEdge(dut.clk_i)
    dut.reset_i.value = 0
    while not int(dut.init_done_o.value):
        await FallingEdge(dut.clk_i)
    for _ in range(600):
        await b.transaction(rng)
    assert b.refreshes >= 8 + 10, f"only {b.refreshes} refreshes"
    dut._log.info("%d clocks, %d refreshes, %d shadow bytes", b.cycle, b.refreshes, len(b.shadow))
