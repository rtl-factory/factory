import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge


EXPECTED_CLOCK_HZ = 25_000_000


async def _cycle(dut, *, address: int, write_enable: bool, write_data: int) -> int:
    await FallingEdge(dut.clk_i)
    dut.address_i.value = address
    dut.write_enable_i.value = int(write_enable)
    dut.write_data_i.value = write_data
    await RisingEdge(dut.clk_i)
    await ReadOnly()
    return int(dut.read_data_o.value)


@cocotb.test(timeout_time=20, timeout_unit="us")
async def sync_ram_contract(dut) -> None:
    seed = int(os.environ["DV_SEED"])
    clock_hz = int(os.environ["DV_CLOCK_HZ"])
    max_cycles = int(os.environ["DV_MAX_CYCLES"])
    assert seed >= 0, f"seed must be non-negative: {seed}"
    assert clock_hz == EXPECTED_CLOCK_HZ, (
        f"clock contract mismatch: expected={EXPECTED_CLOCK_HZ} "
        f"observed={clock_hz}"
    )
    assert max_cycles >= 16, f"cycle budget too small: {max_cycles}"
    clock_period_ns = 1_000_000_000 // clock_hz
    cocotb.log.info(
        "sync_ram seed=%d clock_hz=%d max_cycles=%d",
        seed,
        clock_hz,
        max_cycles,
    )

    data_width = len(dut.write_data_i)
    address_width = len(dut.address_i)
    depth = 1 << address_width
    data_mask = (1 << data_width) - 1
    model: dict[int, int] = {}
    cycle = 0

    dut.address_i.value = 0
    dut.write_enable_i.value = 0
    dut.write_data_i.value = 0
    cocotb.start_soon(Clock(dut.clk_i, clock_period_ns, unit="ns").start())

    async def write_then_read(address: int, value: int) -> None:
        nonlocal cycle
        await _cycle(
            dut, address=address, write_enable=True, write_data=value & data_mask
        )
        cycle += 1
        model[address] = value & data_mask
        observed = await _cycle(
            dut, address=address, write_enable=False, write_data=0
        )
        cycle += 1
        assert observed == model[address], (
            f"cycle={cycle} read address={address} "
            f"expected=0x{model[address]:x} observed=0x{observed:x}"
        )

    await write_then_read(0, 0xA5A5_5A5A)
    await write_then_read(depth - 1, 0x5A5A_A5A5)
    await write_then_read(1, 0xFFFF_0000)

    observed = await _cycle(
        dut, address=0, write_enable=False, write_data=0
    )
    cycle += 1
    assert observed == model[0], (
        f"cycle={cycle} independent address=0 expected=0x{model[0]:x} "
        f"observed=0x{observed:x}"
    )

    old_value = model[0]
    new_value = 0x1234_5678 & data_mask
    observed = await _cycle(
        dut, address=0, write_enable=True, write_data=new_value
    )
    cycle += 1
    assert observed == old_value, (
        f"cycle={cycle} read-first collision address=0 expected=0x{old_value:x} "
        f"observed=0x{observed:x}"
    )
    model[0] = new_value

    observed = await _cycle(
        dut, address=0, write_enable=False, write_data=0
    )
    cycle += 1
    assert observed == model[0], (
        f"cycle={cycle} overwrite address=0 expected=0x{model[0]:x} "
        f"observed=0x{observed:x}"
    )
    assert cycle <= max_cycles, f"cycle budget exceeded: {cycle}>{max_cycles}"
