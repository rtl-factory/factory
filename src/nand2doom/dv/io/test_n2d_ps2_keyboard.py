"""Serial-pin and MMIO contract for the nand2doom PS/2 keyboard."""

from __future__ import annotations

import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, Timer

DATA = 0x000
STATUS = 0x004
CONTROL = 0x008
INJECT = 0x00C
BIT_VALID = 1 << 31


async def reset_dut(dut) -> None:
    dut.ps2_clk_i.value = 1
    dut.ps2_data_i.value = 1
    dut.inject_valid_i.value = 0
    dut.inject_event_i.value = 0
    dut.mmio_request_valid_i.value = 0
    dut.mmio_request_write_i.value = 0
    dut.mmio_request_address_i.value = 0
    dut.mmio_request_write_data_i.value = 0
    dut.mmio_request_write_strobe_i.value = 0
    dut.mmio_response_ready_i.value = 1
    dut.reset_i.value = 1
    for _ in range(4):
        await RisingEdge(dut.clk_i)
    dut.reset_i.value = 0
    for _ in range(4):
        await RisingEdge(dut.clk_i)


async def mmio(
    dut,
    address: int,
    *,
    write: bool = False,
    data: int = 0,
    strobe: int = 0xF,
    response_ready: bool = True,
) -> tuple[int, int]:
    dut.mmio_request_address_i.value = address
    dut.mmio_request_write_i.value = int(write)
    dut.mmio_request_write_data_i.value = data
    dut.mmio_request_write_strobe_i.value = strobe
    dut.mmio_response_ready_i.value = int(response_ready)
    dut.mmio_request_valid_i.value = 1
    while True:
        await RisingEdge(dut.clk_i)
        if int(dut.mmio_request_ready_o.value):
            break
    dut.mmio_request_valid_i.value = 0
    await RisingEdge(dut.clk_i)
    assert int(dut.mmio_response_valid_o.value), (
        f"missing MMIO response address={address:#x}"
    )
    return (
        int(dut.mmio_response_read_data_o.value),
        int(dut.mmio_response_error_o.value),
    )


async def release_response(dut) -> None:
    dut.mmio_response_ready_i.value = 1
    await RisingEdge(dut.clk_i)


def odd_parity_bit(value: int) -> int:
    return 0 if value.bit_count() & 1 else 1


async def drive_ps2_bit(dut, bit: int, half_period_ns: int) -> None:
    dut.ps2_data_i.value = bit
    await Timer(half_period_ns, unit="ns")
    dut.ps2_clk_i.value = 0
    await Timer(half_period_ns, unit="ns")
    dut.ps2_clk_i.value = 1


async def send_frame(
    dut,
    value: int,
    *,
    start: int = 0,
    parity: int | None = None,
    stop: int = 1,
    half_period_ns: int = 1370,
) -> None:
    parity_value = odd_parity_bit(value) if parity is None else parity
    bits = [start, *(value >> index & 1 for index in range(8)), parity_value, stop]
    for bit in bits:
        await drive_ps2_bit(dut, bit, half_period_ns)
    dut.ps2_data_i.value = 1
    await Timer(half_period_ns, unit="ns")


async def send_through_parity(dut, value: int, half_period_ns: int = 1370) -> None:
    bits = [0, *(value >> index & 1 for index in range(8)), odd_parity_bit(value)]
    for bit in bits:
        await drive_ps2_bit(dut, bit, half_period_ns)


async def stop_edge_with_mmio(
    dut,
    *,
    address: int,
    write: bool = False,
    data: int = 0,
    strobe: int = 0xF,
    half_period_ns: int = 1370,
) -> tuple[int, int]:
    """Align one accepted MMIO request with the synchronized stop falling edge."""

    dut.ps2_data_i.value = 1
    await Timer(half_period_ns, unit="ns")
    dut.ps2_clk_i.value = 0
    await RisingEdge(dut.clk_i)
    await RisingEdge(dut.clk_i)
    dut.mmio_request_address_i.value = address
    dut.mmio_request_write_i.value = int(write)
    dut.mmio_request_write_data_i.value = data
    dut.mmio_request_write_strobe_i.value = strobe
    dut.mmio_response_ready_i.value = 0
    dut.mmio_request_valid_i.value = 1
    await RisingEdge(dut.clk_i)
    assert int(dut.mmio_request_ready_o.value)
    dut.mmio_request_valid_i.value = 0
    await Timer(half_period_ns, unit="ns")
    dut.ps2_clk_i.value = 1
    dut.ps2_data_i.value = 1
    assert int(dut.mmio_response_valid_o.value)
    result = (
        int(dut.mmio_response_read_data_o.value),
        int(dut.mmio_response_error_o.value),
    )
    await release_response(dut)
    return result


async def read_status(dut) -> int:
    value, error = await mmio(dut, STATUS)
    assert error == 0
    return value


async def pop_event(dut) -> int:
    value, error = await mmio(dut, DATA)
    assert error == 0
    assert value & BIT_VALID, f"expected queued event, got {value:#010x}"
    return value & 0x3FF


async def clear_all(dut, *, enabled: bool = True) -> None:
    _, error = await mmio(
        dut,
        CONTROL,
        write=True,
        data=int(enabled) | (1 << 1) | (1 << 2),
        strobe=1,
    )
    assert error == 0


async def inject_event(dut, event: int) -> None:
    """Hold the UART-side event until the FIFO accepts it."""

    dut.inject_event_i.value = event
    dut.inject_valid_i.value = 1
    while True:
        await RisingEdge(dut.clk_i)
        if int(dut.inject_ready_o.value):
            break
    dut.inject_valid_i.value = 0


@cocotb.test()
async def ps2_keyboard_serial_mmio_contract(dut) -> None:
    """Check Set-2 events, malformed recovery, FIFO policy, and MMIO flow."""

    seed = int(os.environ.get("N2A_SEED", "34"))
    print(f"seed={seed}")
    cocotb.start_soon(Clock(dut.clk_i, 40, unit="ns").start())
    await reset_dut(dut)

    status = await read_status(dut)
    assert status & (1 << 1)
    assert status & (1 << 12)
    assert ((status >> 3) & 0xF) == 0

    await send_frame(dut, 0x1D)  # W make
    await send_frame(dut, 0xF0)
    await send_frame(dut, 0x1D)  # W break
    await send_frame(dut, 0xE0)
    await send_frame(dut, 0x75)  # up-arrow make
    await send_frame(dut, 0xE0)
    await send_frame(dut, 0xF0)
    await send_frame(dut, 0x75)  # up-arrow break
    assert [await pop_event(dut) for _ in range(4)] == [
        0x01D,
        0x11D,
        0x275,
        0x375,
    ]

    await send_frame(dut, 0x29)
    data, error = await mmio(dut, DATA, response_ready=False)
    assert error == 0 and (data & 0x3FF) == 0x029
    held = (
        int(dut.mmio_response_read_data_o.value),
        int(dut.mmio_response_error_o.value),
    )
    for _ in range(5):
        await RisingEdge(dut.clk_i)
        assert not int(dut.mmio_request_ready_o.value)
        assert (
            int(dut.mmio_response_read_data_o.value),
            int(dut.mmio_response_error_o.value),
        ) == held
    await release_response(dut)

    # A lone idle-high falling edge is an invalid start. Driving the remainder
    # of a nominal frame would contain later low bits that are legal fresh
    # starts on a clock-only receiver.
    await drive_ps2_bit(dut, 1, 1370)
    await send_frame(dut, 0x22, parity=1 - odd_parity_bit(0x22))
    await send_frame(dut, 0x22, stop=0)
    status = await read_status(dut)
    assert status & (1 << 9), f"parity error not sticky: {status:#x}"
    assert status & (1 << 10), f"frame error not sticky: {status:#x}"
    assert ((status >> 3) & 0xF) == 0

    await drive_ps2_bit(dut, 0, 1370)
    await drive_ps2_bit(dut, 1, 1370)
    for _ in range(5010):
        await RisingEdge(dut.clk_i)
    status = await read_status(dut)
    assert status & (1 << 11), f"timeout not sticky: {status:#x}"
    await send_frame(dut, 0x1C)
    assert await pop_event(dut) == 0x01C

    await clear_all(dut)
    for code in range(0x10, 0x18):
        await send_frame(dut, code)
        status = await read_status(dut)
        assert ((status >> 3) & 0xF) == code - 0x0F
    status = await read_status(dut)
    assert status & (1 << 2)

    # A pop accepted on the same edge as a completed frame makes room in a
    # full FIFO: the old head is returned and the new tail is not dropped.
    await send_through_parity(dut, 0x18)
    data, error = await stop_edge_with_mmio(dut, address=DATA)
    assert error == 0 and (data & 0x3FF) == 0x010
    status = await read_status(dut)
    assert not (status & (1 << 8))
    assert ((status >> 3) & 0xF) == 8
    assert [await pop_event(dut) for _ in range(8)] == list(range(0x11, 0x19))

    # With no simultaneous pop, the ninth event is dropped and overflow sticks.
    for code in range(0x20, 0x28):
        await send_frame(dut, code)
    await send_frame(dut, 0x28)
    status = await read_status(dut)
    assert status & (1 << 8)
    assert ((status >> 3) & 0xF) == 8
    assert [await pop_event(dut) for _ in range(8)] == list(range(0x20, 0x28))

    # FIFO clear takes priority over an event completing on that same edge.
    await send_frame(dut, 0x31)
    await send_through_parity(dut, 0x32)
    _, error = await stop_edge_with_mmio(
        dut,
        address=CONTROL,
        write=True,
        data=(1 << 0) | (1 << 1) | (1 << 2),
        strobe=1,
    )
    assert error == 0
    status = await read_status(dut)
    assert ((status >> 3) & 0xF) == 0
    assert not (status & ((1 << 8) | (1 << 9) | (1 << 10) | (1 << 11)))

    # A diagnostic clear aligned with a valid stop edge abandons that frame;
    # it cannot leave the receiver active and cause a delayed ghost timeout.
    await send_through_parity(dut, 0x33)
    _, error = await stop_edge_with_mmio(
        dut,
        address=CONTROL,
        write=True,
        data=(1 << 0) | (1 << 2),
        strobe=1,
    )
    assert error == 0
    for _ in range(5010):
        await RisingEdge(dut.clk_i)
    status = await read_status(dut)
    assert ((status >> 3) & 0xF) == 0
    assert not (status & (1 << 11)), f"clear/stop caused timeout: {status:#x}"
    await send_frame(dut, 0x34)
    assert await pop_event(dut) == 0x034

    # The same receive-boundary rule abandons an in-progress frame when a
    # control write occurs between pin edges, followed by clean recovery.
    await drive_ps2_bit(dut, 0, 1370)
    await drive_ps2_bit(dut, 1, 1370)
    _, error = await mmio(
        dut,
        CONTROL,
        write=True,
        data=(1 << 0) | (1 << 2),
        strobe=1,
    )
    assert error == 0
    for _ in range(5010):
        await RisingEdge(dut.clk_i)
    status = await read_status(dut)
    assert ((status >> 3) & 0xF) == 0
    assert not (status & (1 << 11)), f"mid-frame clear caused timeout: {status:#x}"
    await send_frame(dut, 0x35)
    assert await pop_event(dut) == 0x035

    # A control boundary also prevents a stale prefix from qualifying the next
    # recovered event.
    await send_frame(dut, 0xE0)
    _, error = await mmio(
        dut,
        CONTROL,
        write=True,
        data=(1 << 0) | (1 << 2),
        strobe=1,
    )
    assert error == 0
    await send_frame(dut, 0x75)
    assert await pop_event(dut) == 0x075

    await clear_all(dut, enabled=False)
    await send_frame(dut, 0x2D)
    status = await read_status(dut)
    assert not (status & (1 << 12)) and ((status >> 3) & 0xF) == 0
    await clear_all(dut, enabled=True)
    status = await read_status(dut)
    error_mask = (1 << 8) | (1 << 9) | (1 << 10) | (1 << 11)
    assert not (status & error_mask)
    await send_frame(dut, 0x2D)
    assert await pop_event(dut) == 0x02D

    # UART injection is independent of the physical receiver enable and uses
    # the exact same 10-bit software event representation.
    await clear_all(dut, enabled=False)
    await inject_event(dut, 0x375)
    assert await pop_event(dut) == 0x375

    # A real PS/2 completion wins a simultaneous UART push. The UART valid is
    # retained and accepted on the following cycle, preserving exact order.
    await clear_all(dut, enabled=True)
    await send_through_parity(dut, 0x1C)
    dut.ps2_data_i.value = 1
    await Timer(1370, unit="ns")
    dut.ps2_clk_i.value = 0
    await RisingEdge(dut.clk_i)
    await RisingEdge(dut.clk_i)
    dut.inject_event_i.value = 0x11D
    dut.inject_valid_i.value = 1
    await RisingEdge(dut.clk_i)
    assert not int(dut.inject_ready_o.value)
    await RisingEdge(dut.clk_i)
    assert int(dut.inject_ready_o.value)
    dut.inject_valid_i.value = 0
    dut.ps2_clk_i.value = 1
    dut.ps2_data_i.value = 1
    assert [await pop_event(dut) for _ in range(2)] == [0x01C, 0x11D]

    # UART also wins a simultaneous software INJECT request. The MMIO request
    # is backpressured and succeeds only after its own event can be stored.
    dut.inject_event_i.value = 0x220
    dut.inject_valid_i.value = 1
    dut.mmio_request_address_i.value = INJECT
    dut.mmio_request_write_i.value = 1
    dut.mmio_request_write_data_i.value = 0x121
    dut.mmio_request_write_strobe_i.value = 0xF
    dut.mmio_request_valid_i.value = 1
    await RisingEdge(dut.clk_i)
    assert int(dut.inject_ready_o.value)
    assert not int(dut.mmio_request_ready_o.value)
    dut.inject_valid_i.value = 0
    await RisingEdge(dut.clk_i)
    assert int(dut.mmio_request_ready_o.value)
    dut.mmio_request_valid_i.value = 0
    await RisingEdge(dut.clk_i)
    assert int(dut.mmio_response_valid_o.value)
    assert not int(dut.mmio_response_error_o.value)
    assert [await pop_event(dut) for _ in range(2)] == [0x220, 0x121]

    # Physical completion similarly backpressures a legal MMIO injection, so
    # both sources receive success only for the events actually enqueued.
    await send_through_parity(dut, 0x22)
    dut.ps2_data_i.value = 1
    await Timer(1370, unit="ns")
    dut.ps2_clk_i.value = 0
    await RisingEdge(dut.clk_i)
    await RisingEdge(dut.clk_i)
    dut.mmio_request_address_i.value = INJECT
    dut.mmio_request_write_i.value = 1
    dut.mmio_request_write_data_i.value = 0x123
    dut.mmio_request_write_strobe_i.value = 0xF
    dut.mmio_request_valid_i.value = 1
    await RisingEdge(dut.clk_i)
    assert not int(dut.mmio_request_ready_o.value)
    await RisingEdge(dut.clk_i)
    assert int(dut.mmio_request_ready_o.value)
    dut.mmio_request_valid_i.value = 0
    dut.ps2_clk_i.value = 1
    dut.ps2_data_i.value = 1
    await RisingEdge(dut.clk_i)
    assert [await pop_event(dut) for _ in range(2)] == [0x022, 0x123]

    # A full FIFO backpressures UART without loss. A same-cycle DATA pop makes
    # one slot and accepts the held event, leaving the count full.
    await clear_all(dut)
    for event in range(0x40, 0x48):
        await inject_event(dut, event)
    dut.inject_event_i.value = 0x248
    dut.inject_valid_i.value = 1
    for _ in range(4):
        await RisingEdge(dut.clk_i)
        assert not int(dut.inject_ready_o.value)
    assert await pop_event(dut) == 0x040
    dut.inject_valid_i.value = 0
    assert [await pop_event(dut) for _ in range(8)] == [
        *range(0x41, 0x48),
        0x248,
    ]

    # CONTROL clear has priority over held UART input. The event is accepted
    # only after the clear boundary and therefore remains as the sole entry.
    await inject_event(dut, 0x055)
    dut.inject_event_i.value = 0x155
    dut.inject_valid_i.value = 1
    _, error = await mmio(
        dut,
        CONTROL,
        write=True,
        data=(1 << 0) | (1 << 1),
        strobe=1,
    )
    assert error == 0
    dut.inject_valid_i.value = 0
    assert await pop_event(dut) == 0x155
    assert (await read_status(dut) >> 3) & 0xF == 0

    # The software-visible INJECT register enforces the same event encoding.
    _, error = await mmio(dut, INJECT, write=True, data=0x2AA, strobe=0xF)
    assert error == 0 and await pop_event(dut) == 0x2AA
    _, error = await mmio(dut, INJECT, write=True, data=0x400, strobe=0xF)
    assert error == 1
    _, error = await mmio(dut, INJECT, write=True, data=0x1C, strobe=0x3)
    assert error == 1
    _, error = await mmio(dut, INJECT)
    assert error == 1

    _, error = await mmio(dut, DATA, write=True, data=0xFFFF_FFFF)
    assert error == 1
    _, error = await mmio(dut, 0x00C)
    assert error == 1
    _, error = await mmio(dut, CONTROL, write=True, data=1, strobe=2)
    assert error == 1
    _, error = await mmio(dut, CONTROL, write=True, data=0, strobe=3)
    assert error == 1
    status = await read_status(dut)
    assert ((status >> 3) & 0xF) == 0
    assert status & (1 << 12), "invalid CONTROL write changed enable state"

    print(
        "PS/2 serial/MMIO contract PASS: make/break/extended, "
        "errors, timeout, FIFO"
    )
