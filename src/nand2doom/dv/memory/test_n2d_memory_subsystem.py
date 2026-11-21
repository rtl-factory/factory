from __future__ import annotations

import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge


MAX_CYCLES = int(os.environ.get("MAX_CYCLES", "4096"))


def _pack(values: list[int], width: int) -> int:
    return sum(value << (index * width) for index, value in enumerate(values))


@cocotb.test()
async def memory_arbiter_fast_model_contract(dut):
    seed = int(os.environ.get("SEED", "31"))
    cocotb.log.info("seed=%d max_cycles=%d", seed, MAX_CYCLES)
    cocotb.start_soon(Clock(dut.clk_i, 40, unit="ns").start())
    dut.reset_i.value = 1
    dut.force_error_i.value = 0
    dut.master_request_valid_i.value = 0
    dut.master_request_prefetch_i.value = 0
    dut.master_request_write_i.value = 0
    dut.master_request_address_i.value = 0
    dut.master_request_write_data_i.value = 0
    dut.master_request_byte_enable_i.value = 0
    for _ in range(3):
        await RisingEdge(dut.clk_i)
    dut.reset_i.value = 0

    expected: dict[int, int] = {}
    completions = [0, 0, 0]
    accepted = [False, False, False]
    operations = 6
    cycles = 0

    async def phase(write: bool) -> None:
        nonlocal cycles, accepted
        accepted = [False, False, False]
        starts = list(completions)
        while any(completions[m] < starts[m] + operations for m in range(3)):
            valid = []
            addresses = []
            data = []
            enables = []
            for master in range(3):
                active = not accepted[master] and completions[master] < starts[master] + operations
                index = completions[master] - starts[master]
                address = 0x1000 + master * 0x400 + index * 0x10
                value = int.from_bytes(bytes(((seed + master * 37 + index * 11 + b) & 0xFF) for b in range(16)), "little")
                mask = 0xFFFF if index != 3 else (0x0F0F if master == 2 else 0xFFFF)
                valid.append(int(active))
                addresses.append(address)
                data.append(value)
                enables.append(mask)
                if write and active:
                    previous = expected.get(address, 0)
                    merged = previous
                    for lane in range(16):
                        if mask & (1 << lane):
                            merged = (merged & ~(0xFF << (lane * 8))) | (value & (0xFF << (lane * 8)))
                    expected[address] = merged
            dut.master_request_valid_i.value = _pack(valid, 1)
            dut.master_request_write_i.value = 0x7 if write else 0
            dut.master_request_address_i.value = _pack(addresses, 26)
            dut.master_request_write_data_i.value = _pack(data, 128)
            dut.master_request_byte_enable_i.value = _pack(enables if write else [0, 0, 0], 16)
            await RisingEdge(dut.clk_i)
            ready = int(dut.master_request_ready_o.value)
            responses = int(dut.master_response_valid_o.value)
            for master in range(3):
                if valid[master] and ((ready >> master) & 1):
                    assert not accepted[master]
                    accepted[master] = True
                if (responses >> master) & 1:
                    assert accepted[master], f"response without accepted request master={master}"
                    index = completions[master] - starts[master]
                    address = 0x1000 + master * 0x400 + index * 0x10
                    assert not ((int(dut.master_response_error_o.value) >> master) & 1)
                    if not write:
                        observed = (int(dut.master_response_data_o.value) >> (master * 128)) & ((1 << 128) - 1)
                        assert observed == expected[address], f"master={master} address={address:#x} expected={expected[address]:032x} observed={observed:032x}"
                    completions[master] += 1
                    accepted[master] = False
            cycles += 1
            assert cycles < MAX_CYCLES

    await phase(True)
    write_completions = list(completions)
    await phase(False)
    assert write_completions == [operations] * 3
    assert completions == [operations * 2] * 3

    async def grant_order(valid: list[int], prefetch: list[int]) -> list[int]:
        """Present the masters' reads together, each held until accepted,
        and return the acceptance order."""
        nonlocal cycles
        pending = list(valid)
        order: list[int] = []
        busy = False
        while any(pending) or busy:
            dut.master_request_valid_i.value = _pack(pending, 1)
            dut.master_request_prefetch_i.value = _pack(prefetch, 1)
            dut.master_request_write_i.value = 0
            dut.master_request_address_i.value = _pack([0x1000, 0x1400, 0x1800], 26)
            await RisingEdge(dut.clk_i)
            ready = int(dut.master_request_ready_o.value)
            for master in range(3):
                if pending[master] and (ready >> master) & 1:
                    order.append(master)
                    pending[master] = 0
                    busy = True
            if int(dut.master_response_valid_o.value):
                busy = False
            cycles += 1
            assert cycles < MAX_CYCLES
        dut.master_request_valid_i.value = 0
        dut.master_request_prefetch_i.value = 0
        return order

    # Why: a prefetch must never delay demand, so a prefetch-marked request
    # loses to every demand request whatever the round-robin pointer, and is
    # granted once no demand is valid. The D-cache (master 1) is the only
    # prefetching master in the product.
    for rounds in range(3):
        assert (await grant_order([1, 1, 0], [0, 1, 0]))[0] == 0
        assert (await grant_order([0, 1, 1], [0, 1, 0]))[0] == 2
        order = await grant_order([1, 1, 1], [0, 1, 0])
        assert order[-1] == 1, order
        assert await grant_order([0, 1, 0], [0, 1, 0]) == [1]
    cocotb.log.info("PASS cycles=%d completions=%s", cycles, completions)


@cocotb.test()
async def debug_transaction_atomic_contract(dut):
    seed = int(os.environ.get("SEED", "31"))
    cocotb.log.info("seed=%d max_cycles=%d", seed, MAX_CYCLES)
    cocotb.start_soon(Clock(dut.clk_i, 40, unit="ns").start())
    for name in ("transaction_begin_i", "transaction_commit_i", "transaction_abort_i", "word_request_valid_i", "word_request_write_i"):
        getattr(dut, name).value = 0
    dut.word_request_address_i.value = 0
    dut.word_request_write_data_i.value = 0
    dut.word_request_byte_enable_i.value = 0
    dut.reset_i.value = 1
    for _ in range(3): await RisingEdge(dut.clk_i)
    dut.reset_i.value = 0

    async def pulse(name: str):
        getattr(dut, name).value = 1
        await RisingEdge(dut.clk_i)
        getattr(dut, name).value = 0

    async def word(write: bool, address: int, data: int = 0, mask: int = 0, expect_error: bool = False):
        dut.word_request_write_i.value = write
        dut.word_request_address_i.value = address
        dut.word_request_write_data_i.value = data
        dut.word_request_byte_enable_i.value = mask
        dut.word_request_valid_i.value = 1
        for _ in range(MAX_CYCLES):
            await RisingEdge(dut.clk_i)
            if int(dut.word_request_ready_o.value): break
        else: raise AssertionError("word request did not become ready")
        dut.word_request_valid_i.value = 0
        for _ in range(MAX_CYCLES):
            await RisingEdge(dut.clk_i)
            if int(dut.word_response_valid_o.value):
                assert bool(int(dut.word_response_error_o.value)) == expect_error
                return int(dut.word_response_data_o.value)
        raise AssertionError("word response timeout")

    async def wait_transaction():
        for _ in range(MAX_CYCLES):
            await RisingEdge(dut.clk_i)
            if int(dut.transaction_done_o.value):
                assert not int(dut.transaction_error_o.value)
                return
        raise AssertionError("transaction completion timeout")

    await pulse("transaction_begin_i")
    await word(True, 0x200, 0x11223344, 0xF)
    await word(True, 0x204, 0x55667788, 0xF)
    await pulse("transaction_commit_i")
    await wait_transaction()
    assert await word(False, 0x200) == 0x11223344
    assert await word(False, 0x204) == 0x55667788

    # Why: a later rejected word followed by the UART bridge's abort must not
    # publish the earlier successfully staged word, or a failed transaction
    # would be half applied.
    await pulse("transaction_begin_i")
    await word(True, 0x200, 0x01020304, 0xF)
    await word(True, 0x203, 0xDEADBEEF, 0xF, expect_error=True)
    await pulse("transaction_abort_i")
    assert await word(False, 0x200) == 0x11223344
    assert await word(False, 0x204) == 0x55667788

    await pulse("transaction_begin_i")
    await word(True, 0x200, 0xDEADBEEF, 0xF)
    await word(True, 0x204, 0xCAFEBABE, 0xF)
    await pulse("transaction_abort_i")
    assert await word(False, 0x200) == 0x11223344
    assert await word(False, 0x204) == 0x55667788

    await pulse("transaction_begin_i")
    await word(True, 0x200, 0xAABBCCDD, 0x3)
    await pulse("transaction_commit_i")
    await wait_transaction()
    assert await word(False, 0x200) == 0x1122CCDD
    cocotb.log.info("PASS committed, late-error aborted, grant-loss abort equivalent, and masked transactions")
