"""Contract test for the common RAMs with read requests.

The oracle is the request contract of n2a_sync_ram_enabled and
n2a_tdp_ram_enabled (wiki/src/common/rtl/memory/mas.md), not either backend.
On every rising edge each port, outside its reset, with its read enable high
presents the pre-edge word at its address with valid on the next cycle; with
the read enable low or under its reset valid is low and the port's word holds
its last enabled read. Then the enabled writes land, except a write under its
port's reset. So a same-edge read and write of one word returns the old word
on the same port and across the ports. Two writes to one word in one edge are
forbidden and never driven by the contract test; the negative test drives one
and expects the RTL assertion to stop it. Widths come from the public ports,
so one testcase covers every geometry and the Intel backends driven through
the DV port stub.
"""
import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge


EXPECTED_CLOCK_HZ = 25_000_000


class Port:
    """One request port (prefix "" for the single-port RAM, "a_"/"b_" for the
    true-dual-port RAM) and its model of valid and the held word."""

    def __init__(self, dut, prefix: str):
        self.name = prefix.rstrip("_") or "port"
        self.reset = getattr(dut, f"{prefix}reset_i")
        self.read = getattr(dut, f"{prefix}read_enable_i")
        self.write = getattr(dut, f"{prefix}write_enable_i")
        self.address = getattr(dut, f"{prefix}address_i")
        self.write_data = getattr(dut, f"{prefix}write_data_i")
        self.read_data = getattr(dut, f"{prefix}read_data_o")
        self.read_valid = getattr(dut, f"{prefix}read_valid_o")
        self.data_width = len(self.write_data)
        self.depth = 1 << len(self.address)
        self.request = dict(reset=0, read=0, write=0, address=0, data=0)
        self.valid = 0
        # Why: the held word is unknown until the first enabled read.
        self.word = None

    def drive(self, *, reset=0, read=0, write=0, address=0, data=0) -> None:
        self.request = dict(reset=reset, read=read, write=write, address=address, data=data)
        self.reset.value = reset
        self.read.value = read
        self.write.value = write
        self.address.value = address
        self.write_data.value = data

    def sample(self, memory: dict) -> None:
        """Model this port's registers for the edge (before the writes land)."""
        request = self.request
        self.valid = int(request["read"] and not request["reset"])
        if self.valid:
            self.word = memory.get(request["address"])

    def check(self, cycle: int, label: str) -> None:
        valid = int(self.read_valid.value)
        assert valid == self.valid, (
            f"cycle={cycle} {label} {self.name} valid expected={self.valid} observed={valid}"
        )
        if self.word is not None:
            observed = int(self.read_data.value)
            assert observed == self.word, (
                f"cycle={cycle} {label} {self.name} data expected=0x{self.word:x} "
                f"observed=0x{observed:x} request={self.request}"
            )


def _environment(minimum_cycles: int) -> tuple[int, int]:
    seed = int(os.environ["DV_SEED"])
    clock_hz = int(os.environ["DV_CLOCK_HZ"])
    max_cycles = int(os.environ["DV_MAX_CYCLES"])
    assert seed >= 0, f"seed must be non-negative: {seed}"
    assert clock_hz == EXPECTED_CLOCK_HZ, (
        f"clock contract mismatch: expected={EXPECTED_CLOCK_HZ} observed={clock_hz}"
    )
    assert max_cycles >= minimum_cycles, f"cycle budget too small: {max_cycles}"
    cocotb.log.info("ram_enabled seed=%d clock_hz=%d max_cycles=%d", seed, clock_hz, max_cycles)
    return seed, max_cycles


async def _edge(dut, ports: list[Port], memory: dict, cycle: int, label: str) -> None:
    await RisingEdge(dut.clk_i)
    for port in ports:
        port.sample(memory)
    for port in ports:
        request = port.request
        if request["write"] and not request["reset"]:
            memory[request["address"]] = request["data"]
    await ReadOnly()
    for port in ports:
        port.check(cycle, label)
    await FallingEdge(dut.clk_i)


async def _run(dut, ports: list[Port], minimum_cycles: int) -> None:
    seed, max_cycles = _environment(minimum_cycles)
    rng = random.Random(seed)
    clock_period_ns = 1_000_000_000 // EXPECTED_CLOCK_HZ
    first = ports[0]
    data_mask = (1 << first.data_width) - 1
    # Why: the corner addresses and a middle one, so collisions are frequent and
    # address decoding is still exercised at both ends.
    addresses = sorted({0, 1 % first.depth, first.depth // 2, first.depth - 1})
    memory: dict[int, int] = {}
    seen = set()
    for port in ports:
        port.drive()
    cocotb.start_soon(Clock(dut.clk_i, clock_period_ns, unit="ns").start())
    await FallingEdge(dut.clk_i)
    cycle = 0

    # Why: every port starts under reset with requests presented, so the first
    # edges prove a reset drops a write and keeps valid low.
    for port in ports:
        port.drive(reset=1, read=1, write=1, address=addresses[0], data=data_mask)
    await _edge(dut, ports, memory, cycle, "reset")
    cycle += 1
    assert not memory, "the model must drop writes under reset"

    for index, address in enumerate(addresses):
        for port in ports:
            port.drive()
        first.drive(write=1, address=address, data=rng.randrange(1 << first.data_width))
        await _edge(dut, ports, memory, cycle, f"init {index}")
        cycle += 1

    while cycle < max_cycles:
        written = set()
        for port in ports:
            reset = int(rng.random() < 0.08)
            read = int(rng.random() < 0.6)
            write = int(rng.random() < 0.4)
            address = rng.choice(addresses)
            if write and address in written:
                write = 0
            if write and not reset:
                written.add(address)
            port.drive(reset=reset, read=read, write=write, address=address,
                       data=rng.randrange(1 << port.data_width))
        for port in ports:
            request = port.request
            if request["reset"] and (request["read"] or request["write"]):
                seen.add("request under reset")
            if not request["reset"] and request["read"] and request["write"]:
                seen.add("same-port read and write")
            if not request["reset"] and not request["read"] and port.word is not None:
                seen.add("hold")
        if len(ports) == 2:
            a, b = (port.request for port in ports)
            if (a["address"] == b["address"] and not a["reset"] and not b["reset"] and
                    ((a["write"] and b["read"]) or (b["write"] and a["read"]))):
                seen.add("mixed-port read and write")
            if a["reset"] != b["reset"]:
                seen.add("one port in reset")
        await _edge(dut, ports, memory, cycle, "random")
        cycle += 1

    expected = {"request under reset", "same-port read and write", "hold"}
    if len(ports) == 2:
        expected |= {"mixed-port read and write", "one port in reset"}
    assert expected <= seen, f"cases not reached: {sorted(expected - seen)}"


@cocotb.test(timeout_time=200, timeout_unit="us")
async def sync_ram_enabled_contract(dut) -> None:
    await _run(dut, [Port(dut, "")], 64)


@cocotb.test(timeout_time=200, timeout_unit="us")
async def tdp_ram_enabled_contract(dut) -> None:
    ports = [Port(dut, "a_"), Port(dut, "b_")]
    assert ports[0].data_width == ports[1].data_width
    assert ports[0].depth == ports[1].depth
    await _run(dut, ports, 64)


@cocotb.test(timeout_time=10, timeout_unit="us")
async def tdp_ram_enabled_write_collision_is_rejected(dut) -> None:
    _environment(4)
    a, b = Port(dut, "a_"), Port(dut, "b_")
    a.drive()
    b.drive()
    cocotb.start_soon(Clock(dut.clk_i, 1_000_000_000 // EXPECTED_CLOCK_HZ, unit="ns").start())
    await FallingEdge(dut.clk_i)
    a.drive(write=1, address=3, data=1)
    b.drive(write=1, address=3, data=2)
    # Why: the RTL assertion must stop this deliberately forbidden collision.
    await RisingEdge(dut.clk_i)
    await RisingEdge(dut.clk_i)
    assert False, "two writes to one word in one edge did not fire"
