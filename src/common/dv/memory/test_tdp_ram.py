"""Contract test for the common same-clock true-dual-port RAM.

The oracle is the common memory HAS cycle contract, not either backend: every
rising edge each port captures the pre-edge word at its own address, then the
enabled writes of both ports land, so a same-edge collision on one port
(same-port) or across the two ports (mixed-port) returns the complete old
word. Two enabled writes to one address in one edge are forbidden by the
contract and never driven here. Widths come from the public ports so one
testcase covers the default geometry, the byte-plane geometry fixture and the
Intel wrapper driven through the DV port stub.
"""
import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge


EXPECTED_CLOCK_HZ = 25_000_000


async def _cycle(dut, *, a_address: int, b_address: int,
                 a_write: bool = False, a_data: int = 0,
                 b_write: bool = False, b_data: int = 0) -> tuple[int, int]:
    await FallingEdge(dut.clk_i)
    dut.a_address_i.value = a_address
    dut.a_write_enable_i.value = int(a_write)
    dut.a_write_data_i.value = a_data
    dut.b_address_i.value = b_address
    dut.b_write_enable_i.value = int(b_write)
    dut.b_write_data_i.value = b_data
    await RisingEdge(dut.clk_i)
    await ReadOnly()
    return int(dut.a_read_data_o.value), int(dut.b_read_data_o.value)


@cocotb.test(timeout_time=40, timeout_unit="us")
async def tdp_ram_contract(dut) -> None:
    seed = int(os.environ["DV_SEED"])
    clock_hz = int(os.environ["DV_CLOCK_HZ"])
    max_cycles = int(os.environ["DV_MAX_CYCLES"])
    assert seed >= 0, f"seed must be non-negative: {seed}"
    assert clock_hz == EXPECTED_CLOCK_HZ, (
        f"clock contract mismatch: expected={EXPECTED_CLOCK_HZ} observed={clock_hz}"
    )
    assert max_cycles >= 24, f"cycle budget too small: {max_cycles}"
    clock_period_ns = 1_000_000_000 // clock_hz

    data_width = len(dut.a_write_data_i)
    address_width = len(dut.a_address_i)
    assert len(dut.b_write_data_i) == data_width
    assert len(dut.b_address_i) == address_width
    assert len(dut.a_read_data_o) == data_width
    assert len(dut.b_read_data_o) == data_width
    depth = 1 << address_width
    data_mask = (1 << data_width) - 1
    cocotb.log.info(
        "tdp_ram seed=%d clock_hz=%d max_cycles=%d geometry=%dx%d",
        seed, clock_hz, max_cycles, depth, data_width,
    )

    # Why: patterns differ in every byte lane, so a swapped or stuck lane fails.
    def pattern(index: int) -> int:
        value = 0
        for lane in range(0, data_width, 8):
            value |= ((0xA5 + 0x37 * (lane // 8) + 0x59 * index) & 0xFF) << lane
        return value & data_mask

    model: dict[int, int] = {}
    cycle = 0

    def check(label: str, observed: int, expected: int, address: int) -> None:
        assert observed == expected, (
            f"cycle={cycle} {label} address={address} "
            f"expected=0x{expected:x} observed=0x{observed:x}"
        )

    a_min, a_max, a_one = 0, depth - 1, 1 % depth

    dut.a_address_i.value = 0
    dut.a_write_enable_i.value = 0
    dut.a_write_data_i.value = 0
    dut.b_address_i.value = 0
    dut.b_write_enable_i.value = 0
    dut.b_write_data_i.value = 0
    cocotb.start_soon(Clock(dut.clk_i, clock_period_ns, unit="ns").start())

    for index, address in enumerate((a_min, a_max, a_one)):
        other = a_max if address != a_max else a_min
        _, b_word = await _cycle(dut, a_address=address, a_write=True, a_data=pattern(index),
                                 b_address=other)
        cycle += 1
        if other in model:
            check("B read beside A init", b_word, model[other], other)
        model[address] = pattern(index)

    for a_address, b_address in ((a_min, a_max), (a_max, a_one), (a_one, a_min), (a_min, a_one)):
        a_word, b_word = await _cycle(dut, a_address=a_address, b_address=b_address)
        cycle += 1
        check("A registered read", a_word, model[a_address], a_address)
        check("B registered read", b_word, model[b_address], b_address)

    old_value = model[a_one]
    new_value = pattern(4) ^ data_mask
    assert new_value != old_value
    a_word, b_word = await _cycle(dut, a_address=a_one, a_write=True, a_data=new_value,
                                  b_address=a_max)
    cycle += 1
    check("A same-port collision", a_word, old_value, a_one)
    check("B read beside A collision", b_word, model[a_max], a_max)
    model[a_one] = new_value
    a_word, b_word = await _cycle(dut, a_address=a_one, b_address=a_one)
    cycle += 1
    check("A post-collision read", a_word, model[a_one], a_one)
    check("B post-collision read", b_word, model[a_one], a_one)

    old_value = model[a_max]
    new_value = pattern(5) ^ data_mask
    assert new_value != old_value
    a_word, b_word = await _cycle(dut, a_address=a_min,
                                  b_address=a_max, b_write=True, b_data=new_value)
    cycle += 1
    check("B same-port collision", b_word, old_value, a_max)
    check("A read beside B collision", a_word, model[a_min], a_min)
    model[a_max] = new_value
    a_word, b_word = await _cycle(dut, a_address=a_max, b_address=a_max)
    cycle += 1
    check("A post-B-collision read", a_word, model[a_max], a_max)
    check("B post-B-collision read", b_word, model[a_max], a_max)

    old_value = model[a_min]
    new_value = pattern(6) ^ data_mask
    assert new_value != old_value
    a_word, b_word = await _cycle(dut, a_address=a_min, a_write=True, a_data=new_value,
                                  b_address=a_min)
    cycle += 1
    check("A same-port mixed collision", a_word, old_value, a_min)
    check("B mixed-port collision (A writes)", b_word, old_value, a_min)
    model[a_min] = new_value
    old_value = model[a_one]
    new_value = pattern(7) ^ data_mask
    assert new_value != old_value
    a_word, b_word = await _cycle(dut, a_address=a_one,
                                  b_address=a_one, b_write=True, b_data=new_value)
    cycle += 1
    check("A mixed-port collision (B writes)", a_word, old_value, a_one)
    check("B same-port mixed collision", b_word, old_value, a_one)
    model[a_one] = new_value
    a_word, b_word = await _cycle(dut, a_address=a_min, b_address=a_one)
    cycle += 1
    check("A after mixed collisions", a_word, model[a_min], a_min)
    check("B after mixed collisions", b_word, model[a_one], a_one)

    a_word, b_word = await _cycle(dut, a_address=a_max, a_write=False, a_data=pattern(8),
                                  b_address=a_min, b_write=False, b_data=pattern(9))
    cycle += 1
    check("A read during disabled write", a_word, model[a_max], a_max)
    check("B read during disabled write", b_word, model[a_min], a_min)
    a_word, b_word = await _cycle(dut, a_address=a_min, b_address=a_max)
    cycle += 1
    check("A disabled write ignored", a_word, model[a_min], a_min)
    check("B disabled write ignored", b_word, model[a_max], a_max)

    a_new, b_new = pattern(10), pattern(11)
    a_old, b_old = model[a_min], model[a_max]
    a_word, b_word = await _cycle(dut, a_address=a_min, a_write=True, a_data=a_new,
                                  b_address=a_max, b_write=True, b_data=b_new)
    cycle += 1
    check("A read-first beside B write", a_word, a_old, a_min)
    check("B read-first beside A write", b_word, b_old, a_max)
    model[a_min], model[a_max] = a_new, b_new
    a_word, b_word = await _cycle(dut, a_address=a_max, b_address=a_min)
    cycle += 1
    check("A sees B's write", a_word, model[a_max], a_max)
    check("B sees A's write", b_word, model[a_min], a_min)

    # Why: with no read enable a held output would look the same as a fresh
    # read, so writes on the other port prove each edge reads again.
    for a_address, b_address in ((a_one, a_min), (a_min, a_one), (a_one, a_max)):
        a_word, b_word = await _cycle(dut, a_address=a_address, b_address=b_address)
        cycle += 1
        check("A every-edge read", a_word, model[a_address], a_address)
        check("B every-edge read", b_word, model[b_address], b_address)
    for index in range(2):
        a_word, b_word = await _cycle(dut, a_address=a_max,
                                      b_address=a_min, b_write=True, b_data=pattern(12 + index))
        cycle += 1
        check("A stable address", a_word, model[a_max], a_max)
        check("B stable address read-first", b_word, model[a_min], a_min)
        model[a_min] = pattern(12 + index)
    a_word, b_word = await _cycle(dut, a_address=a_min, b_address=a_min)
    cycle += 1
    check("A last unrelated write", a_word, model[a_min], a_min)
    check("B last unrelated write", b_word, model[a_min], a_min)

    assert cycle <= max_cycles, f"cycle budget exceeded: {cycle}>{max_cycles}"
