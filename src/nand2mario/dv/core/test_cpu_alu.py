import itertools
import os
import random

import cocotb
from cocotb.triggers import Timer


ADD, ADC, SUB, SBC, AND, XOR, OR, CP, INC, DEC = range(10)
OPERATIONS = (ADD, ADC, SUB, SBC, AND, XOR, OR, CP, INC, DEC)
BINARY_OPERATIONS = (ADD, ADC, SUB, SBC, AND, XOR, OR, CP)
DIRECTED_VALUES = (
    0x00, 0x01, 0x0F, 0x10, 0x3F, 0x55, 0x7F,
    0x80, 0x81, 0xAA, 0xF0, 0xFE, 0xFF,
)
RANDOM_CASES_PER_OPERATION = 256
INVALID_OPERATIONS = tuple(range(10, 32))
DIRECTED_TRANSACTIONS = (
    len(BINARY_OPERATIONS) * len(DIRECTED_VALUES) ** 2 * 2
    + 2 * len(DIRECTED_VALUES) * 2
)
TRANSACTION_COUNT = (
    DIRECTED_TRANSACTIONS
    + len(OPERATIONS) * RANDOM_CASES_PER_OPERATION
    + len(INVALID_OPERATIONS)
)


def alu_reference(
    operation: int, lhs: int, rhs: int, flags_in: int
) -> tuple[int, int, int]:
    carry_in = (flags_in >> 4) & 1
    preserved_flags = flags_in & 0xF0

    if operation in (ADD, ADC):
        carry = carry_in if operation == ADC else 0
        total = lhs + rhs + carry
        value = total & 0xFF
        half_carry = ((lhs & 0x0F) + (rhs & 0x0F) + carry) > 0x0F
        flags = (int(value == 0) << 7) | (int(half_carry) << 5)
        flags |= int(total > 0xFF) << 4
        return value, flags, 1

    if operation in (SUB, SBC, CP):
        carry = carry_in if operation == SBC else 0
        difference = lhs - rhs - carry
        result = difference & 0xFF
        half_borrow = (lhs & 0x0F) < ((rhs & 0x0F) + carry)
        borrow = lhs < (rhs + carry)
        flags = (int(result == 0) << 7) | (1 << 6)
        flags |= (int(half_borrow) << 5) | (int(borrow) << 4)
        value = lhs if operation == CP else result
        return value, flags, 1

    if operation in (AND, XOR, OR):
        if operation == AND:
            value = lhs & rhs
        elif operation == XOR:
            value = lhs ^ rhs
        else:
            value = lhs | rhs
        flags = (int(value == 0) << 7) | (int(operation == AND) << 5)
        return value, flags, 1

    if operation == INC:
        value = (lhs + 1) & 0xFF
        flags = (int(value == 0) << 7) | (int((lhs & 0x0F) == 0x0F) << 5)
        flags |= carry_in << 4
        return value, flags, 1

    if operation == DEC:
        value = (lhs - 1) & 0xFF
        flags = (int(value == 0) << 7) | (1 << 6)
        flags |= (int((lhs & 0x0F) == 0) << 5) | (carry_in << 4)
        return value, flags, 1

    return lhs, preserved_flags, 0


def _required_nonnegative_integer(name: str) -> int:
    value = int(os.environ[name])
    assert value >= 0, f"{name} must be non-negative, observed={value}"
    return value


@cocotb.test(timeout_time=25, timeout_unit="us")
async def cpu_alu_contract(dut) -> None:
    seed = _required_nonnegative_integer("DV_SEED")
    clock_hz = _required_nonnegative_integer("DV_CLOCK_HZ")
    max_transactions = _required_nonnegative_integer("DV_MAX_CYCLES")
    assert clock_hz == 25_000_000, (
        f"clock contract mismatch: expected=25000000 observed={clock_hz}"
    )
    assert max_transactions >= TRANSACTION_COUNT, (
        f"transaction bound too small: required={TRANSACTION_COUNT} "
        f"observed={max_transactions}"
    )
    rng = random.Random(seed)
    transaction = 0

    async def check(operation: int, lhs: int, rhs: int, flags: int, label: str) -> None:
        nonlocal transaction
        transaction += 1
        assert transaction <= max_transactions
        dut.operation_i.value = operation
        dut.lhs_i.value = lhs
        dut.rhs_i.value = rhs
        dut.flags_i.value = flags
        await Timer(1, unit="ns")
        expected = alu_reference(operation, lhs, rhs, flags)
        observed = (
            int(dut.value_o.value),
            int(dut.flags_o.value),
            int(dut.valid_o.value),
        )
        assert observed == expected, (
            f"transaction={transaction} seed={seed} label={label} "
            f"operation={operation} lhs=0x{lhs:02x} rhs=0x{rhs:02x} "
            f"flags=0x{flags:02x} expected={expected} observed={observed}"
        )

    for operation in BINARY_OPERATIONS:
        for lhs, rhs, carry in itertools.product(
            DIRECTED_VALUES, DIRECTED_VALUES, (0, 1)
        ):
            await check(operation, lhs, rhs, carry << 4, "directed-binary")

    for operation in (INC, DEC):
        for lhs, carry in itertools.product(DIRECTED_VALUES, (0, 1)):
            await check(operation, lhs, 0xA5, carry << 4, "directed-unary")

    for operation in OPERATIONS:
        for random_index in range(RANDOM_CASES_PER_OPERATION):
            await check(
                operation,
                rng.getrandbits(8),
                rng.getrandbits(8),
                rng.getrandbits(4) << 4,
                f"random-{random_index}",
            )

    for operation in INVALID_OPERATIONS:
        await check(operation, 0x81, 0x7F, 0xB0, "invalid-operation")

    assert transaction == TRANSACTION_COUNT
    cocotb.log.info(
        "nand2mario ALU contract passed seed=%d transactions=%d",
        seed,
        transaction,
    )
