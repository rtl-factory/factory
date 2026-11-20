from __future__ import annotations

import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, RisingEdge

from src.nand2doom.scripts.n2d_uart_debug_host import (
    CMD_HALT,
    CMD_INPUT,
    CMD_LOAD,
    CMD_PEEK,
    CMD_PING,
    CMD_POKE,
    CMD_RESET_PC,
    CMD_RESUME,
    INPUT_ADDRESS,
    ST_ALIGNMENT,
    ST_BUSY,
    ST_CHECKSUM,
    ST_COMMAND,
    ST_FRAMING,
    ST_GRANT,
    ST_LENGTH,
    ST_MEMORY,
    ST_NOT_HALTED,
    ST_OK,
    ST_RANGE,
    ST_TIMEOUT,
    ST_VERSION,
    decode_response,
    input_request,
    request,
    response_length_from_header,
)


CLKS_PER_BIT = 25
MAX_CYCLES = int(os.environ.get("N2A_MAX_CYCLES", "220000"))
SEED = int(os.environ.get("N2A_SEED", "33"))


async def _uart_send_byte(dut, value: int, *, stop: int = 1) -> None:
    dut.uart_rx_i.value = 0
    await ClockCycles(dut.clk_i, CLKS_PER_BIT)
    for bit in range(8):
        dut.uart_rx_i.value = (value >> bit) & 1
        await ClockCycles(dut.clk_i, CLKS_PER_BIT)
    dut.uart_rx_i.value = stop
    await ClockCycles(dut.clk_i, CLKS_PER_BIT)
    dut.uart_rx_i.value = 1


async def _uart_send(dut, packet: bytes) -> None:
    for byte in packet:
        await _uart_send_byte(dut, byte)


async def _uart_receive_byte(dut) -> int:
    await FallingEdge(dut.uart_tx_o)
    await ClockCycles(dut.clk_i, CLKS_PER_BIT + (CLKS_PER_BIT // 2))
    value = 0
    for bit in range(8):
        value |= int(dut.uart_tx_o.value) << bit
        await ClockCycles(dut.clk_i, CLKS_PER_BIT)
    assert int(dut.uart_tx_o.value) == 1, "response UART stop bit was low"
    await ClockCycles(dut.clk_i, CLKS_PER_BIT // 2)
    return value


async def _uart_receive(dut):
    raw = bytearray()
    while len(raw) < 6:
        raw.append(await _uart_receive_byte(dut))
    length = response_length_from_header(bytes(raw))
    while len(raw) < length:
        raw.append(await _uart_receive_byte(dut))
    return decode_response(bytes(raw))


async def _exchange(dut, packet: bytes):
    receive = cocotb.start_soon(_uart_receive(dut))
    await _uart_send(dut, packet)
    return await receive


async def _memory_model(
    dut,
    memory: dict[int, int],
    controls: dict[str, object],
    stats: dict[str, int],
) -> None:
    staged: dict[int, tuple[int, int]] = {}
    pending: tuple[bool, int, int, int] | None = None
    dut.mem_req_ready_i.value = 1
    dut.mem_rsp_valid_i.value = 0
    dut.mem_rsp_rdata_i.value = 0
    dut.mem_rsp_error_i.value = 0
    dut.mem_txn_done_i.value = 0
    dut.mem_txn_error_i.value = 0

    while True:
        await RisingEdge(dut.clk_i)
        dut.mem_req_ready_i.value = int(controls["ready"])
        dut.mem_rsp_valid_i.value = 0
        dut.mem_rsp_error_i.value = 0
        dut.mem_txn_done_i.value = 0
        dut.mem_txn_error_i.value = 0

        if int(dut.mem_txn_begin_o.value):
            staged = {}
            stats["begin"] += 1
        if int(dut.mem_txn_abort_o.value):
            staged = {}
            pending = None
            stats["abort"] += 1
            dut.mem_txn_done_i.value = 1
        if int(dut.mem_txn_commit_o.value):
            for address, (data, strobes) in staged.items():
                original = memory.get(address, 0)
                merged = original
                for lane in range(4):
                    if strobes & (1 << lane):
                        merged &= ~(0xFF << (lane * 8))
                        merged |= ((data >> (lane * 8)) & 0xFF) << (lane * 8)
                memory[address] = merged
            staged = {}
            stats["commit"] += 1
            dut.mem_txn_done_i.value = 1

        if (
            pending is not None
            and controls["respond"]
            and pending[1] != controls["stall_address"]
        ):
            write, address, data, strobes = pending
            dut.mem_rsp_valid_i.value = 1
            if controls["error"] or address == controls["error_address"]:
                dut.mem_rsp_error_i.value = 1
            elif write:
                staged[address] = (data, strobes)
                stats["staged"] += 1
            else:
                dut.mem_rsp_rdata_i.value = memory.get(address, 0)
            pending = None
        elif (
            int(dut.mem_req_valid_o.value)
            and int(dut.mem_req_ready_i.value)
            and pending is None
        ):
            pending = (
                bool(dut.mem_req_write_o.value),
                int(dut.mem_req_addr_o.value),
                int(dut.mem_req_wdata_o.value),
                int(dut.mem_req_wstrb_o.value),
            )


@cocotb.test()
async def serial_debug_protocol_contract(dut) -> None:
    cocotb.log.info("seed=%d max_cycles=%d", SEED, MAX_CYCLES)
    clock = Clock(dut.clk_i, 40, unit="ns")
    cocotb.start_soon(clock.start())

    dut.reset_i.value = 1
    dut.uart_rx_i.value = 1
    dut.halt_ack_i.value = 1
    dut.cache_maint_done_i.value = 1
    dut.arb_grant_i.value = 1
    dut.input_event_ready_i.value = 1
    dut.mem_txn_done_i.value = 0
    dut.mem_txn_error_i.value = 0
    controls: dict[str, object] = {
        "ready": True,
        "respond": True,
        "error": False,
        "error_address": None,
        "stall_address": None,
    }
    stats = {"begin": 0, "commit": 0, "abort": 0, "staged": 0}
    memory = {
        0x00000100: 0xDEADBEEF,
        0x00000104: 0xCAFE1234,
        0x00000108: 0xA5A5A5A5,
        0x00000180: 0x01020304,
        0x00000184: 0x11121314,
        0x00000188: 0x21222324,
        0x0000018C: 0x31323334,
    }
    cocotb.start_soon(_memory_model(dut, memory, controls, stats))
    await ClockCycles(dut.clk_i, 4)
    dut.reset_i.value = 0
    await ClockCycles(dut.clk_i, 4)

    ping = await _exchange(dut, request(CMD_PING, 1))
    assert ping.status == ST_OK and ping.sequence == 1
    assert ping.words == (0x3144324E, 1), f"unexpected ping payload {ping.words!r}"

    # A complete overlapping frame is rejected after an active memory command,
    # without changing or replacing that transaction and response.
    cocotb.log.info("starting overlapping BUSY contract")
    async def receive_active_and_busy():
        return await _uart_receive(dut), await _uart_receive(dut)

    controls["respond"] = False
    begin_before_busy = stats["begin"]
    commit_before_busy = stats["commit"]
    abort_before_busy = stats["abort"]
    response_stream = cocotb.start_soon(receive_active_and_busy())
    await _uart_send(dut, request(CMD_PEEK, 19, address=0x100, count=1))
    for _ in range(64):
        await RisingEdge(dut.clk_i)
        if int(dut.arb_req_o.value):
            break
    else:
        raise AssertionError("active peek never reached the arbiter")

    async def release_active_peek():
        await ClockCycles(dut.clk_i, 500)
        controls["respond"] = True

    release = cocotb.start_soon(release_active_peek())
    overlapping_request = request(
        CMD_POKE,
        20,
        address=0x108,
        words=(0xFFFFFFFF,),
        byte_enable=0xF,
    )
    await _uart_send(dut, overlapping_request)
    await release
    active_peek, busy = await response_stream
    cocotb.log.info("active peek and BUSY responses received")
    assert active_peek.status == ST_OK and active_peek.sequence == 19
    assert active_peek.words == (0xDEADBEEF,)
    assert busy.status == ST_BUSY and busy.sequence == 20
    assert busy.command == (CMD_POKE | 0x80)
    assert stats["begin"] == begin_before_busy + 1
    assert stats["commit"] == commit_before_busy + 1
    assert stats["abort"] == abort_before_busy
    assert memory[0x108] == 0xA5A5A5A5

    load_words = (0x11223344, 0x55667788)
    loaded = await _exchange(
        dut,
        request(
            CMD_LOAD,
            2,
            address=0x100,
            words=load_words,
            byte_enable=0xF,
        ),
    )
    assert loaded.status == ST_OK
    assert memory[0x100] == 0x11223344 and memory[0x104] == 0x55667788
    assert memory[0x108] == 0xA5A5A5A5, "load corrupted unrelated memory"

    peek = await _exchange(
        dut, request(CMD_PEEK, 3, address=0x100, count=3)
    )
    assert peek.status == ST_OK and peek.words == (*load_words, 0xA5A5A5A5)

    poke = await _exchange(
        dut,
        request(
            CMD_POKE,
            4,
            address=0x104,
            words=(0xAABBCCDD,),
            byte_enable=0b0101,
        ),
    )
    assert poke.status == ST_OK
    assert memory[0x104] == 0x55BB77DD
    assert memory[0x100] == 0x11223344 and memory[0x108] == 0xA5A5A5A5

    saw_invalidate = False

    async def observe_invalidate():
        nonlocal saw_invalidate
        for _ in range(6000):
            await RisingEdge(dut.clk_i)
            saw_invalidate |= bool(dut.cache_invalidate_o.value)
            if saw_invalidate:
                return

    observer = cocotb.start_soon(observe_invalidate())

    async def assert_invalidate_precedes_response():
        await FallingEdge(dut.uart_tx_o)
        assert saw_invalidate, "resume response started before cache invalidation"

    ordering = cocotb.start_soon(assert_invalidate_precedes_response())
    resumed = await _exchange(dut, request(CMD_RESUME, 5))
    await observer
    await ordering
    assert resumed.status == ST_OK and saw_invalidate
    dut.halt_ack_i.value = 0
    dut.cache_maint_done_i.value = 0

    # CMD_INPUT is legal while the CPU runs. It holds one exact Set-2 event
    # under backpressure and does not enter halt, maintenance, arbitration, or
    # debug-memory traffic.
    dut.input_event_ready_i.value = 0
    input_receive = cocotb.start_soon(_uart_receive(dut))
    await _uart_send(dut, input_request(40, 0x375))
    for _ in range(64):
        await RisingEdge(dut.clk_i)
        if int(dut.input_event_valid_o.value):
            break
    else:
        raise AssertionError("CMD_INPUT did not present a FIFO event")
    stats_before_input = dict(stats)
    for _ in range(24):
        assert int(dut.input_event_valid_o.value)
        assert int(dut.input_event_o.value) == 0x375
        assert not int(dut.halt_req_o.value)
        assert not int(dut.cache_invalidate_o.value)
        assert not int(dut.arb_req_o.value)
        assert not int(dut.mem_req_valid_o.value)
        await RisingEdge(dut.clk_i)
    dut.input_event_ready_i.value = 1
    await RisingEdge(dut.clk_i)
    accepted_input = await input_receive
    assert accepted_input.status == ST_OK and accepted_input.sequence == 40
    assert stats == stats_before_input

    # A FIFO that remains full produces a bounded BUSY result without a false
    # event acceptance or any debug-memory transaction.
    dut.input_event_ready_i.value = 0
    stats_before_busy_input = dict(stats)
    busy_input = await _exchange(dut, input_request(41, 0x11D))
    assert busy_input.status == ST_BUSY and busy_input.sequence == 41
    assert stats == stats_before_busy_input
    dut.input_event_ready_i.value = 1

    malformed_inputs = (
        request(CMD_INPUT, 42, address=INPUT_ADDRESS + 2,
                words=(0x1C,), byte_enable=0xF),
        request(CMD_INPUT, 43, address=INPUT_ADDRESS + 4,
                words=(0x1C,), byte_enable=0xF),
        request(CMD_INPUT, 44, address=INPUT_ADDRESS,
                count=0, byte_enable=0xF),
        request(CMD_INPUT, 45, address=INPUT_ADDRESS,
                words=(0x1C,), byte_enable=0x3),
        request(CMD_INPUT, 46, address=INPUT_ADDRESS,
                words=(0x400,), byte_enable=0xF),
    )
    malformed_statuses = (ST_ALIGNMENT, ST_RANGE, ST_LENGTH, ST_LENGTH, ST_LENGTH)
    for packet, expected_status in zip(malformed_inputs, malformed_statuses):
        rejected_input = await _exchange(dut, packet)
        assert rejected_input.status == expected_status
    assert stats == stats_before_busy_input

    denied = await _exchange(dut, request(CMD_PEEK, 6, address=0x100, count=1))
    assert denied.status == ST_NOT_HALTED

    halt_receive = cocotb.start_soon(_uart_receive(dut))
    await _uart_send(dut, request(CMD_HALT, 7))
    await ClockCycles(dut.clk_i, 20)
    assert int(dut.halt_req_o.value) == 1 and not halt_receive.done()
    dut.halt_ack_i.value = 1
    await ClockCycles(dut.clk_i, 7)
    assert not halt_receive.done(), "halt response preceded cache maintenance"
    dut.cache_maint_done_i.value = 1
    halted = await halt_receive
    assert halted.status == ST_OK

    reset_pc_seen = False

    async def observe_reset_pc():
        nonlocal reset_pc_seen
        while not reset_pc_seen:
            await RisingEdge(dut.clk_i)
            if int(dut.reset_pc_valid_o.value):
                assert int(dut.reset_pc_o.value) == 0x200
                reset_pc_seen = True

    reset_observer = cocotb.start_soon(observe_reset_pc())
    reset_pc = await _exchange(
        dut, request(CMD_RESET_PC, 8, address=0x200)
    )
    await reset_observer
    assert reset_pc.status == ST_OK and reset_pc_seen

    malformed = bytearray(
        request(CMD_POKE, 9, address=0x108, words=(0,), byte_enable=0xF)
    )
    malformed[-1] ^= 0x80
    checksum = await _exchange(dut, bytes(malformed))
    assert checksum.status == ST_CHECKSUM and memory[0x108] == 0xA5A5A5A5
    unaligned = await _exchange(
        dut, request(CMD_PEEK, 10, address=0x102, count=1)
    )
    out_of_range = await _exchange(
        dut, request(CMD_PEEK, 11, address=0x04000000, count=1)
    )
    zero_length = await _exchange(
        dut, request(CMD_PEEK, 12, address=0x100, count=0)
    )
    assert unaligned.status == ST_ALIGNMENT
    assert out_of_range.status == ST_RANGE
    assert zero_length.status == ST_LENGTH
    oversized = await _exchange(
        dut,
        request(
            CMD_LOAD,
            13,
            address=0x100,
            words=tuple(range(9)),
            byte_enable=0xF,
        ),
    )
    assert oversized.status == ST_LENGTH
    assert memory[0x100] == 0x11223344, "oversized load changed memory"

    memory_before_errors = dict(memory)
    begin_before_errors = stats["begin"]
    bad_version = await _exchange(
        dut, request(CMD_PING, 30, version=2)
    )
    unsupported = await _exchange(dut, request(0x7E, 31))
    bad_load_strobes = await _exchange(
        dut,
        request(
            CMD_LOAD,
            32,
            address=0x108,
            words=(0xFFFFFFFF,),
            byte_enable=0x7,
        ),
    )
    zero_poke_strobes = await _exchange(
        dut,
        request(
            CMD_POKE,
            33,
            address=0x108,
            words=(0xFFFFFFFF,),
            byte_enable=0,
        ),
    )
    assert (bad_version.status, bad_version.sequence, bad_version.command) == (
        ST_VERSION,
        30,
        CMD_PING | 0x80,
    )
    assert (unsupported.status, unsupported.sequence, unsupported.command) == (
        ST_COMMAND,
        31,
        0xFE,
    )
    assert (
        bad_load_strobes.status,
        bad_load_strobes.sequence,
        bad_load_strobes.command,
    ) == (ST_LENGTH, 32, CMD_LOAD | 0x80)
    assert (
        zero_poke_strobes.status,
        zero_poke_strobes.sequence,
        zero_poke_strobes.command,
    ) == (ST_LENGTH, 33, CMD_POKE | 0x80)
    assert memory == memory_before_errors
    assert stats["begin"] == begin_before_errors

    atomic_addresses = (0x180, 0x184, 0x188)
    atomic_original = tuple(memory[address] for address in atomic_addresses)
    atomic_sentinel = memory[0x18C]
    replacement = (0xA0A1A2A3, 0xB0B1B2B3, 0xC0C1C2C3)

    controls["error_address"] = 0x184
    before = dict(stats)
    later_error = await _exchange(
        dut,
        request(
            CMD_LOAD,
            34,
            address=0x180,
            words=replacement,
            byte_enable=0xF,
        ),
    )
    controls["error_address"] = None
    assert later_error.status == ST_MEMORY and later_error.sequence == 34
    assert stats["begin"] == before["begin"] + 1
    assert stats["staged"] >= before["staged"] + 1
    assert stats["abort"] == before["abort"] + 1
    assert stats["commit"] == before["commit"]
    assert tuple(memory[address] for address in atomic_addresses) == atomic_original
    assert memory[0x18C] == atomic_sentinel

    controls["stall_address"] = 0x184
    before = dict(stats)
    later_timeout = await _exchange(
        dut,
        request(
            CMD_LOAD,
            35,
            address=0x180,
            words=replacement,
            byte_enable=0xF,
        ),
    )
    controls["stall_address"] = None
    assert later_timeout.status == ST_TIMEOUT and later_timeout.sequence == 35
    assert stats["begin"] == before["begin"] + 1
    assert stats["staged"] >= before["staged"] + 1
    assert stats["abort"] == before["abort"] + 1
    assert stats["commit"] == before["commit"]
    assert tuple(memory[address] for address in atomic_addresses) == atomic_original
    assert memory[0x18C] == atomic_sentinel

    async def revoke_grant_on_later_word():
        while True:
            await RisingEdge(dut.clk_i)
            if (
                int(dut.mem_req_valid_o.value)
                and int(dut.mem_req_addr_o.value) == 0x184
            ):
                dut.arb_grant_i.value = 0
                return

    before = dict(stats)
    grant_revoker = cocotb.start_soon(revoke_grant_on_later_word())
    later_grant_loss = await _exchange(
        dut,
        request(
            CMD_LOAD,
            36,
            address=0x180,
            words=replacement,
            byte_enable=0xF,
        ),
    )
    await grant_revoker
    dut.arb_grant_i.value = 1
    assert later_grant_loss.status == ST_GRANT
    assert later_grant_loss.sequence == 36
    assert stats["begin"] == before["begin"] + 1
    assert stats["staged"] >= before["staged"] + 1
    assert stats["abort"] == before["abort"] + 1
    assert stats["commit"] == before["commit"]
    assert tuple(memory[address] for address in atomic_addresses) == atomic_original
    assert memory[0x18C] == atomic_sentinel

    before = dict(stats)
    recovery_load = await _exchange(
        dut,
        request(
            CMD_LOAD,
            37,
            address=0x180,
            words=replacement,
            byte_enable=0xF,
        ),
    )
    recovery_peek = await _exchange(
        dut, request(CMD_PEEK, 38, address=0x180, count=3)
    )
    assert recovery_load.status == ST_OK
    assert recovery_peek.status == ST_OK and recovery_peek.words == replacement
    assert stats["begin"] == before["begin"] + 2
    assert stats["commit"] == before["commit"] + 2
    assert stats["abort"] == before["abort"]
    assert memory[0x18C] == atomic_sentinel

    dut.arb_grant_i.value = 0
    grant = await _exchange(dut, request(CMD_PEEK, 14, address=0x100, count=1))
    assert grant.status == ST_GRANT
    assert memory[0x100] == 0x11223344
    dut.arb_grant_i.value = 1

    controls["ready"] = False
    timeout = await _exchange(
        dut,
        request(
            CMD_POKE,
            15,
            address=0x108,
            words=(0xFFFFFFFF,),
            byte_enable=0xF,
        ),
    )
    assert timeout.status == ST_TIMEOUT
    assert memory[0x108] == 0xA5A5A5A5, "timed-out poke changed memory"
    controls["ready"] = True

    controls["error"] = True
    memory_error = await _exchange(
        dut,
        request(
            CMD_POKE,
            16,
            address=0x108,
            words=(0xFFFFFFFF,),
            byte_enable=0xF,
        ),
    )
    assert memory_error.status == ST_MEMORY
    assert memory[0x108] == 0xA5A5A5A5, "memory-error poke changed memory"
    controls["error"] = False

    # A partial header must time out and return the captured command/sequence.
    timeout_receive = cocotb.start_soon(_uart_receive(dut))
    await _uart_send(dut, bytes((0xA5, 1, CMD_PING, 17)))
    interbyte = await timeout_receive
    assert interbyte.status == ST_TIMEOUT and interbyte.sequence == 17

    # A bad physical stop bit gets a deterministic framing response.
    framing_receive = cocotb.start_soon(_uart_receive(dut))
    await _uart_send_byte(dut, 0xA5, stop=0)
    framing = await framing_receive
    assert framing.status == ST_FRAMING

    final = await _exchange(dut, request(CMD_PEEK, 18, address=0x100, count=3))
    assert final.words == (0x11223344, 0x55BB77DD, 0xA5A5A5A5)
