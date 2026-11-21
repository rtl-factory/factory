import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge

from .observation import read_observation
from .rv32i_smoke_model import Observation, expected_smoke, smoke_program


CLOCK_HZ = 25_000_000
CLOCK_PERIOD_NS = 40


@cocotb.test(timeout_time=30, timeout_unit="us")
async def rv32i_core_step_contract(dut) -> None:
    assert int(os.environ["DV_CLOCK_HZ"]) == CLOCK_HZ
    maximum = int(os.environ["DV_MAX_CYCLES"])
    seed = int(os.environ["DV_SEED"])
    program = smoke_program()
    expected = expected_smoke()
    memory: dict[int, int] = {}
    cocotb.log.info(
        "RV32I core-step seed=%d expected_retirements=%d max_cycles=%d",
        seed,
        len(expected),
        maximum,
    )

    dut.reset_i.value = 1
    dut.enable_i.value = 1
    dut.instruction_request_ready_i.value = 1
    dut.instruction_response_valid_i.value = 0
    dut.instruction_response_data_i.value = 0
    dut.instruction_response_error_i.value = 0
    dut.data_request_ready_i.value = 1
    dut.data_response_valid_i.value = 0
    dut.data_response_read_data_i.value = 0
    dut.data_response_error_i.value = 0
    cocotb.start_soon(Clock(dut.clk_i, CLOCK_PERIOD_NS, unit="ns").start())
    await RisingEdge(dut.clk_i)
    await FallingEdge(dut.clk_i)
    dut.reset_i.value = 0

    pending_instruction = None
    pending_data = None
    observed = []
    saw_illegal_trap = False

    for cycle in range(maximum):
        dut.instruction_response_valid_i.value = int(pending_instruction is not None)
        dut.instruction_response_error_i.value = 0
        if pending_instruction is not None:
            address = pending_instruction
            word = address >> 2
            dut.instruction_response_data_i.value = (
                program[word] if word < len(program) else 0
            )

        dut.data_response_valid_i.value = int(pending_data is not None)
        dut.data_response_error_i.value = 0
        if pending_data is not None:
            write, address, write_data = pending_data
            dut.data_response_read_data_i.value = memory.get(address >> 2, 0)
        else:
            write = False
            address = 0
            write_data = 0

        next_instruction = None
        if int(dut.instruction_request_valid_o.value):
            next_instruction = int(dut.instruction_request_address_o.value)

        next_data = None
        if int(dut.data_request_valid_o.value):
            next_data = (
                bool(dut.data_request_write_o.value),
                int(dut.data_request_address_o.value),
                int(dut.data_request_write_data_o.value),
            )

        await RisingEdge(dut.clk_i)
        await ReadOnly()
        if pending_data is not None and write:
            memory[address >> 2] = write_data
        if int(dut.observation_valid_o.value):
            item = read_observation(dut)
            if len(observed) < len(expected):
                assert item == expected[len(observed)], (
                    f"cycle={cycle} retirement={len(observed)} "
                    f"expected={expected[len(observed)]} observed={item}"
                )
                observed.append(item)
            else:
                expected_trap = Observation(
                    order=len(expected),
                    pc=44,
                    instruction=0,
                    next_pc=44,
                    trap=1,
                    cause=2,
                    trap_value=0,
                )
                assert item == expected_trap, (
                    f"cycle={cycle} illegal trap mismatch expected={expected_trap} observed={item}"
                )
                saw_illegal_trap = True

        if saw_illegal_trap:
            assert int(dut.halted_o.value)
            break

        pending_instruction = next_instruction
        pending_data = next_data
        await FallingEdge(dut.clk_i)
    else:
        raise AssertionError(
            f"timeout after {maximum} cycles: retirements={len(observed)} "
            f"illegal_trap={saw_illegal_trap}"
        )

    assert tuple(observed) == expected
    assert memory == {0: 12, 1: 36}
