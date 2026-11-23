"""Contract test for the common write-through single-port RAM (n2a_sync_ram_wt).

The oracle is the write-through cycle contract of wiki/src/common/rtl/memory/mas.md, not a
backend: every rising edge returns the word at the address one edge later; a
write edge stores the full word and returns the NEW word (write-through);
write_enable low leaves storage unchanged. Widths come from the public ports,
so one testcase covers the portable default geometry and the IHP banked
backend driven through the DV macro model, including a read beside a write in
another bank.
"""
import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge


EXPECTED_CLOCK_HZ = 25_000_000


async def _cycle(dut, *, address: int, write_enable: bool = False, write_data: int = 0) -> int:
    await FallingEdge(dut.clk_i)
    dut.address_i.value = address
    dut.write_enable_i.value = int(write_enable)
    dut.write_data_i.value = write_data
    await RisingEdge(dut.clk_i)
    await ReadOnly()
    return int(dut.read_data_o.value)


@cocotb.test(timeout_time=40, timeout_unit="us")
async def sync_ram_wt_contract(dut) -> None:
    seed = int(os.environ["DV_SEED"])
    clock_hz = int(os.environ["DV_CLOCK_HZ"])
    max_cycles = int(os.environ["DV_MAX_CYCLES"])
    assert seed >= 0, f"seed must be non-negative: {seed}"
    assert clock_hz == EXPECTED_CLOCK_HZ, (
        f"clock contract mismatch: expected={EXPECTED_CLOCK_HZ} observed={clock_hz}"
    )
    assert max_cycles >= 24, f"cycle budget too small: {max_cycles}"
    clock_period_ns = 1_000_000_000 // clock_hz

    data_width = len(dut.write_data_i)
    address_width = len(dut.address_i)
    assert len(dut.read_data_o) == data_width
    depth = 1 << address_width
    data_mask = (1 << data_width) - 1
    cocotb.log.info("sync_ram_wt seed=%d clock_hz=%d max_cycles=%d geometry=%dx%d",
                    seed, clock_hz, max_cycles, depth, data_width)

    def pattern(index: int) -> int:
        value = 0
        for lane in range(0, data_width, 8):
            value |= ((0x3C + 0x29 * (lane // 8) + 0x61 * index) & 0xFF) << lane
        return value & data_mask

    model: dict[int, int] = {}
    cycle = 0

    def check(label: str, observed: int, expected: int, address: int) -> None:
        assert observed == expected, (
            f"cycle={cycle} {label} address={address} "
            f"expected=0x{expected:x} observed=0x{observed:x}"
        )

    # Why: the middle word lies in another bank when the RAM is banked, so the
    # reads below switch banks.
    a_min, a_max, a_one, a_mid = 0, depth - 1, 1 % depth, depth // 2

    dut.address_i.value = 0
    dut.write_enable_i.value = 0
    dut.write_data_i.value = 0
    cocotb.start_soon(Clock(dut.clk_i, clock_period_ns, unit="ns").start())

    for index, address in enumerate((a_min, a_max, a_one, a_mid)):
        observed = await _cycle(dut, address=address, write_enable=True,
                                write_data=pattern(index))
        cycle += 1
        model[address] = pattern(index)
        check("write-through", observed, model[address], address)

    for address in (a_mid, a_min, a_max, a_one, a_mid, a_min):
        observed = await _cycle(dut, address=address)
        cycle += 1
        check("registered read", observed, model[address], address)

    new_value = model[a_one] ^ data_mask
    observed = await _cycle(dut, address=a_one, write_enable=True, write_data=new_value)
    cycle += 1
    model[a_one] = new_value
    check("write-through overwrite", observed, new_value, a_one)
    observed = await _cycle(dut, address=a_one)
    cycle += 1
    check("post-overwrite read", observed, model[a_one], a_one)

    observed = await _cycle(dut, address=a_max, write_enable=False, write_data=pattern(9))
    cycle += 1
    check("disabled write ignored", observed, model[a_max], a_max)

    observed = await _cycle(dut, address=a_mid, write_enable=True, write_data=pattern(5))
    cycle += 1
    model[a_mid] = pattern(5)
    check("write-through mid", observed, model[a_mid], a_mid)
    for address in (a_min, a_mid):
        observed = await _cycle(dut, address=address)
        cycle += 1
        check("read after bank switch", observed, model[address], address)

    assert cycle <= max_cycles, f"cycle budget exceeded: {cycle}>{max_cycles}"
