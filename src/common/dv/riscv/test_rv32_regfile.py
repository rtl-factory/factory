import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge, Timer


REGISTER_COUNT = 32
WORD_MASK = 0xFFFF_FFFF
RANDOM_OPERATIONS = 128


class RegisterFileModel:
    def __init__(self) -> None:
        self.registers = [0] * REGISTER_COUNT

    def reset(self) -> None:
        self.registers = [0] * REGISTER_COUNT

    def read(self, index: int) -> int:
        return 0 if index == 0 else self.registers[index]

    def write(self, enable: int, index: int, data: int) -> None:
        if enable and index != 0:
            self.registers[index] = data & WORD_MASK


def _required_nonnegative_integer(name: str) -> int:
    value = int(os.environ[name])
    assert value >= 0, f"{name} must be non-negative, observed={value}"
    return value


async def _check_reads(dut, model: RegisterFileModel, index_a: int, index_b: int) -> None:
    dut.read_a_index_i.value = index_a
    dut.read_b_index_i.value = index_b
    await Timer(1, unit="ps")
    observed_a = int(dut.read_a_data_o.value)
    observed_b = int(dut.read_b_data_o.value)
    expected_a = model.read(index_a)
    expected_b = model.read(index_b)
    assert observed_a == expected_a, (
        f"read A mismatch index={index_a} expected=0x{expected_a:08x} "
        f"observed=0x{observed_a:08x}"
    )
    assert observed_b == expected_b, (
        f"read B mismatch index={index_b} expected=0x{expected_b:08x} "
        f"observed=0x{observed_b:08x}"
    )


@cocotb.test(timeout_time=25, timeout_unit="us")
async def register_file_contract(dut) -> None:
    clock_hz = _required_nonnegative_integer("DV_CLOCK_HZ")
    max_cycles = _required_nonnegative_integer("DV_MAX_CYCLES")
    seed = _required_nonnegative_integer("DV_SEED")
    assert clock_hz == 25_000_000, (
        f"clock contract mismatch: expected=25000000 observed={clock_hz}"
    )

    model = RegisterFileModel()
    rng = random.Random(seed)
    cycles = 0

    async def active_edge() -> None:
        nonlocal cycles
        cycles += 1
        assert cycles <= max_cycles, (
            f"cycle bound exceeded: cycle={cycles} maximum={max_cycles}"
        )
        await RisingEdge(dut.clk_i)
        await ReadOnly()
        await Timer(1, unit="ps")

    cocotb.start_soon(Clock(dut.clk_i, 40, unit="ns").start())
    dut.rst_i.value = 1
    dut.read_a_index_i.value = 0
    dut.read_b_index_i.value = 0
    dut.write_enable_i.value = 0
    dut.write_index_i.value = 0
    dut.write_data_i.value = 0
    await active_edge()
    await active_edge()
    model.reset()
    await FallingEdge(dut.clk_i)
    dut.rst_i.value = 0

    for index_a in range(REGISTER_COUNT):
        for index_b in range(REGISTER_COUNT):
            await _check_reads(dut, model, index_a, index_b)

    for value in (0, 1, 0x8000_0000, WORD_MASK, 0xA5A5_5A5A):
        dut.write_enable_i.value = 1
        dut.write_index_i.value = 0
        dut.write_data_i.value = value
        await active_edge()
        model.write(1, 0, value)
        await _check_reads(dut, model, 0, 31)

    for index in range(1, REGISTER_COUNT):
        value = ((index * 0x1020_4081) ^ 0xA5A5_5A5A) & WORD_MASK
        dut.write_enable_i.value = 1
        dut.write_index_i.value = index
        dut.write_data_i.value = value
        await active_edge()
        model.write(1, index, value)
    dut.write_enable_i.value = 0
    for index_a in range(REGISTER_COUNT):
        for index_b in range(REGISTER_COUNT):
            await _check_reads(dut, model, index_a, index_b)

    target_index = 7
    old_value = model.read(target_index)
    new_value = old_value ^ WORD_MASK
    await FallingEdge(dut.clk_i)
    dut.read_a_index_i.value = target_index
    dut.read_b_index_i.value = target_index
    dut.write_enable_i.value = 1
    dut.write_index_i.value = target_index
    dut.write_data_i.value = new_value
    await Timer(1, unit="ns")
    assert int(dut.read_a_data_o.value) == old_value
    assert int(dut.read_b_data_o.value) == old_value
    await active_edge()
    model.write(1, target_index, new_value)
    assert int(dut.read_a_data_o.value) == new_value
    assert int(dut.read_b_data_o.value) == new_value

    await FallingEdge(dut.clk_i)
    dut.rst_i.value = 1
    dut.write_enable_i.value = 1
    dut.write_index_i.value = 31
    dut.write_data_i.value = WORD_MASK
    await active_edge()
    model.reset()
    for index_a in range(REGISTER_COUNT):
        for index_b in range(REGISTER_COUNT):
            await _check_reads(dut, model, index_a, index_b)
    await FallingEdge(dut.clk_i)
    dut.rst_i.value = 0

    for operation in range(RANDOM_OPERATIONS):
        enable = rng.randrange(2)
        write_index = rng.randrange(REGISTER_COUNT)
        write_data = rng.getrandbits(32)
        index_a = rng.randrange(REGISTER_COUNT)
        index_b = rng.randrange(REGISTER_COUNT)
        dut.write_enable_i.value = enable
        dut.write_index_i.value = write_index
        dut.write_data_i.value = write_data
        await _check_reads(dut, model, index_a, index_b)
        await active_edge()
        model.write(enable, write_index, write_data)
        observed_a = int(dut.read_a_data_o.value)
        observed_b = int(dut.read_b_data_o.value)
        assert observed_a == model.read(index_a), (
            f"random post-edge A mismatch operation={operation} seed={seed}"
        )
        assert observed_b == model.read(index_b), (
            f"random post-edge B mismatch operation={operation} seed={seed}"
        )

    assert cycles <= max_cycles
    cocotb.log.info(
        "register-file contract passed seed=%d cycles=%d random_operations=%d",
        seed,
        cycles,
        RANDOM_OPERATIONS,
    )
