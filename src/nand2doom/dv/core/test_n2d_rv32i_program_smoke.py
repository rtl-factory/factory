import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge

from .observation import read_observation
from .rv32i_smoke_model import expected_smoke, smoke_program


CLOCK_HZ = 25_000_000
CLOCK_PERIOD_NS = 40


@cocotb.test(timeout_time=30, timeout_unit="us")
async def rv32i_program_smoke(dut) -> None:
    assert int(os.environ["DV_CLOCK_HZ"]) == CLOCK_HZ
    maximum = int(os.environ["DV_MAX_CYCLES"])
    seed = int(os.environ["DV_SEED"])
    expected = expected_smoke()
    program = smoke_program()
    cocotb.log.info(
        "RV32I program smoke seed=%d words=%d retirements=%d max_cycles=%d",
        seed,
        len(program),
        len(expected),
        maximum,
    )

    dut.reset_i.value = 1
    dut.run_i.value = 0
    dut.program_write_enable_i.value = 0
    dut.program_write_address_i.value = 0
    dut.program_write_data_i.value = 0
    dut.data_write_enable_i.value = 0
    dut.data_write_address_i.value = 0
    dut.data_write_data_i.value = 0
    dut.debug_data_address_i.value = 0
    cocotb.start_soon(Clock(dut.clk_i, CLOCK_PERIOD_NS, unit="ns").start())

    for address, instruction in enumerate(program):
        await FallingEdge(dut.clk_i)
        dut.program_write_enable_i.value = 1
        dut.program_write_address_i.value = address
        dut.program_write_data_i.value = instruction
        await RisingEdge(dut.clk_i)
    await FallingEdge(dut.clk_i)
    dut.program_write_enable_i.value = 0
    await RisingEdge(dut.clk_i)
    await FallingEdge(dut.clk_i)
    dut.reset_i.value = 0
    dut.run_i.value = 1

    observed = []
    for cycle in range(maximum):
        await RisingEdge(dut.clk_i)
        await ReadOnly()
        if int(dut.observation_valid_o.value):
            item = read_observation(dut)
            assert not item.trap, (
                f"cycle={cycle} unexpected trap cause={item.cause} "
                f"pc=0x{item.pc:08x} insn=0x{item.instruction:08x}"
            )
            assert len(observed) < len(expected), (
                f"cycle={cycle} unexpected extra retirement {item}"
            )
            assert item == expected[len(observed)], (
                f"cycle={cycle} retirement={len(observed)} "
                f"expected={expected[len(observed)]} observed={item}"
            )
            observed.append(item)
            if len(observed) == len(expected):
                break
    else:
        raise AssertionError(
            f"timeout after {maximum} cycles: observed {len(observed)}/{len(expected)} retirements"
        )

    await FallingEdge(dut.clk_i)
    dut.run_i.value = 0
    dut.debug_data_address_i.value = 1
    await RisingEdge(dut.clk_i)
    await ReadOnly()
    assert int(dut.debug_data_o.value) == 36, (
        "final signature mismatch: expected=0x00000024 "
        f"observed=0x{int(dut.debug_data_o.value):08x}"
    )
    assert not int(dut.halted_o.value)
