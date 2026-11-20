import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge


CLOCK_PERIOD_NS = 40
DUAL_CLOCK_B_PERIOD_NS = 56


def _environment() -> tuple[int, int]:
    seed = int(os.environ["DV_SEED"])
    max_cycles = int(os.environ["DV_MAX_CYCLES"])
    clock_hz = int(os.environ["DV_CLOCK_HZ"])
    assert seed >= 0, f"seed must be non-negative: {seed}"
    assert max_cycles >= 20, f"cycle budget too small: {max_cycles}"
    assert clock_hz == 25_000_000, (
        f"clock contract mismatch: expected=25000000 observed={clock_hz}"
    )
    cocotb.log.info(
        "nand2mario dual-port RAM seed=%d max_cycles=%d", seed, max_cycles
    )
    return seed, max_cycles


def _idle(dut) -> None:
    dut.reset_a_i.value = 0
    dut.reset_b_i.value = 0
    dut.read_a_i.value = 0
    dut.write_a_i.value = 0
    dut.address_a_i.value = 0
    dut.write_data_a_i.value = 0
    dut.byte_enable_a_i.value = 0
    dut.read_b_i.value = 0
    dut.address_b_i.value = 0


async def _a_edge(
    dut,
    *,
    address: int = 0,
    read: bool = False,
    write: bool = False,
    data: int = 0,
    lanes: int = 0,
) -> tuple[int, int]:
    await FallingEdge(dut.clk_a_i)
    dut.address_a_i.value = address
    dut.read_a_i.value = int(read)
    dut.write_a_i.value = int(write)
    dut.write_data_a_i.value = data
    dut.byte_enable_a_i.value = lanes
    await RisingEdge(dut.clk_a_i)
    await ReadOnly()
    return int(dut.read_valid_a_o.value), int(dut.read_data_a_o.value)


async def _b_edge(dut, *, address: int, read: bool = True) -> tuple[int, int]:
    await FallingEdge(dut.clk_b_i)
    dut.address_b_i.value = address
    dut.read_b_i.value = int(read)
    await RisingEdge(dut.clk_b_i)
    await ReadOnly()
    return int(dut.read_valid_b_o.value), int(dut.read_data_b_o.value)


@cocotb.test(timeout_time=10, timeout_unit="us")
async def single_clock_contract(dut) -> None:
    _, max_cycles = _environment()
    _idle(dut)
    cocotb.start_soon(Clock(dut.clk_a_i, CLOCK_PERIOD_NS, unit="ns").start())
    cocotb.start_soon(Clock(dut.clk_b_i, CLOCK_PERIOD_NS, unit="ns").start())
    cycle = 0

    # Reset masks requests and valids but intentionally does not clear storage.
    dut.reset_a_i.value = 1
    dut.reset_b_i.value = 1
    dut.read_a_i.value = 1
    dut.read_b_i.value = 1
    await RisingEdge(dut.clk_a_i)
    await ReadOnly()
    cycle += 1
    assert int(dut.read_valid_a_o.value) == 0
    assert int(dut.read_valid_b_o.value) == 0
    await FallingEdge(dut.clk_a_i)
    _idle(dut)

    await _a_edge(dut, address=3, write=True, data=0x1122_3344, lanes=0xF)
    cycle += 1
    valid, observed = await _a_edge(dut, address=3, read=True)
    cycle += 1
    assert (valid, observed) == (1, 0x1122_3344), (
        f"cycle={cycle} full write/read expected=0x11223344 observed=0x{observed:08x}"
    )

    # Partial writes merge enabled lanes and leave disabled lanes unchanged.
    await _a_edge(
        dut,
        address=3,
        write=True,
        data=0xAABB_CCDD,
        lanes=0b0101,
    )
    cycle += 1
    valid, observed = await _a_edge(dut, address=3, read=True)
    cycle += 1
    assert (valid, observed) == (1, 0x11BB_33DD), (
        f"cycle={cycle} byte merge expected=0x11bb33dd observed=0x{observed:08x}"
    )

    # A supported same-port read/write enables every lane and is write-first.
    valid, observed = await _a_edge(
        dut,
        address=3,
        read=True,
        write=True,
        data=0x1020_3040,
        lanes=0xF,
    )
    cycle += 1
    assert (valid, observed) == (1, 0x1020_3040)

    # In single-clock mode, port B is read-first on a cross-port collision.
    await FallingEdge(dut.clk_a_i)
    dut.address_a_i.value = 3
    dut.write_a_i.value = 1
    dut.write_data_a_i.value = 0x5566_7788
    dut.byte_enable_a_i.value = 0xF
    dut.address_b_i.value = 3
    dut.read_b_i.value = 1
    await RisingEdge(dut.clk_a_i)
    await ReadOnly()
    cycle += 1
    assert int(dut.read_valid_b_o.value) == 1
    assert int(dut.read_data_b_o.value) == 0x1020_3040, (
        f"cycle={cycle} cross-port read-first expected=0x10203040 "
        f"observed=0x{int(dut.read_data_b_o.value):08x}"
    )

    await FallingEdge(dut.clk_a_i)
    dut.write_a_i.value = 0
    dut.read_b_i.value = 0
    dut.read_a_i.value = 1
    await RisingEdge(dut.clk_a_i)
    await ReadOnly()
    cycle += 1
    assert int(dut.read_data_a_o.value) == 0x5566_7788
    assert int(dut.read_valid_b_o.value) == 0

    # Reset clears the valid pipeline only; the initialized word survives.
    await FallingEdge(dut.clk_a_i)
    dut.reset_a_i.value = 1
    dut.reset_b_i.value = 1
    await RisingEdge(dut.clk_a_i)
    await ReadOnly()
    cycle += 1
    assert int(dut.read_valid_a_o.value) == 0
    assert int(dut.read_valid_b_o.value) == 0
    await FallingEdge(dut.clk_a_i)
    dut.reset_a_i.value = 0
    dut.reset_b_i.value = 0
    dut.read_a_i.value = 1
    await RisingEdge(dut.clk_a_i)
    await ReadOnly()
    cycle += 1
    assert int(dut.read_data_a_o.value) == 0x5566_7788

    # Data holds whenever no read is sampled: a write-only request to the
    # word just read and idle edges leave port A's output unchanged.
    await FallingEdge(dut.clk_a_i)
    _idle(dut)
    held = await _a_edge(dut, address=3, write=True, data=0x0BAD_F00D, lanes=0xF)
    cycle += 1
    assert held == (0, 0x5566_7788), f"cycle={cycle} A hold over a write observed={held}"
    held = await _a_edge(dut, address=5)
    cycle += 1
    assert held == (0, 0x5566_7788), f"cycle={cycle} A hold over idle observed={held}"

    # Port B holds its last read through idle edges and A writes to that word.
    valid, observed = await _b_edge(dut, address=3)
    cycle += 1
    assert (valid, observed) == (1, 0x0BAD_F00D), f"cycle={cycle} B read observed=0x{observed:08x}"
    await FallingEdge(dut.clk_a_i)
    dut.read_b_i.value = 0
    for value in (0x600D_D00D, 0x7777_0000):
        held = await _a_edge(dut, address=3, write=True, data=value, lanes=0xF)
        cycle += 1
        assert int(dut.read_valid_b_o.value) == 0
        assert int(dut.read_data_b_o.value) == 0x0BAD_F00D, f"cycle={cycle} B hold"

    # A bypassed same-port read/write holds the written word, not the
    # array's pre-write word, after the read.
    valid, observed = await _a_edge(dut, address=3, read=True, write=True, data=0x1357_9BDF, lanes=0xF)
    cycle += 1
    assert (valid, observed) == (1, 0x1357_9BDF)
    held = await _a_edge(dut, address=3)
    cycle += 1
    assert held == (0, 0x1357_9BDF), f"cycle={cycle} bypass hold observed={held}"

    # Back-to-back reads of different words update on every edge.
    await _a_edge(dut, address=4, write=True, data=0x4444_4444, lanes=0xF)
    cycle += 1
    for address, expected in ((4, 0x4444_4444), (3, 0x1357_9BDF), (4, 0x4444_4444)):
        valid, observed = await _a_edge(dut, address=address, read=True)
        cycle += 1
        assert (valid, observed) == (1, expected), f"cycle={cycle} back-to-back read {address}"
    assert cycle <= max_cycles


@cocotb.test(timeout_time=10, timeout_unit="us")
async def dual_clock_contract(dut) -> None:
    _, max_cycles = _environment()
    _idle(dut)
    cocotb.start_soon(Clock(dut.clk_a_i, CLOCK_PERIOD_NS, unit="ns").start())
    cocotb.start_soon(
        Clock(dut.clk_b_i, DUAL_CLOCK_B_PERIOD_NS, unit="ns").start()
    )
    cycle = 0

    dut.reset_a_i.value = 1
    dut.reset_b_i.value = 1
    await RisingEdge(dut.clk_a_i)
    await RisingEdge(dut.clk_b_i)
    await ReadOnly()
    cycle += 2
    assert int(dut.read_valid_a_o.value) == 0
    assert int(dut.read_valid_b_o.value) == 0
    await FallingEdge(dut.clk_a_i)
    dut.reset_a_i.value = 0
    dut.reset_b_i.value = 0

    await _a_edge(dut, address=2, write=True, data=0x0123_4567, lanes=0xF)
    await _a_edge(dut, address=9, write=True, data=0x89AB_CDEF, lanes=0xF)
    cycle += 2

    valid, observed = await _b_edge(dut, address=2)
    cycle += 1
    assert (valid, observed) == (1, 0x0123_4567), (
        f"cycle={cycle} independent B read expected=0x01234567 observed=0x{observed:08x}"
    )

    # Both ports may be active concurrently when their addresses differ.
    await FallingEdge(dut.clk_a_i)
    dut.address_a_i.value = 2
    dut.write_a_i.value = 1
    dut.write_data_a_i.value = 0xCAFE_BABE
    dut.byte_enable_a_i.value = 0b1010
    dut.address_b_i.value = 9
    dut.read_b_i.value = 1
    await RisingEdge(dut.clk_a_i)
    await RisingEdge(dut.clk_b_i)
    await ReadOnly()
    cycle += 2
    assert int(dut.read_valid_b_o.value) == 1
    assert int(dut.read_data_b_o.value) == 0x89AB_CDEF
    await FallingEdge(dut.clk_a_i)
    dut.write_a_i.value = 0
    dut.read_b_i.value = 0

    valid, observed = await _a_edge(dut, address=2, read=True)
    cycle += 1
    assert (valid, observed) == (1, 0xCA23_BA67), (
        f"cycle={cycle} dual-clock byte merge expected=0xca23ba67 observed=0x{observed:08x}"
    )

    # The B-domain reset suppresses only B validity and does not touch memory.
    await FallingEdge(dut.clk_b_i)
    dut.reset_b_i.value = 1
    dut.read_b_i.value = 1
    dut.address_b_i.value = 9
    await RisingEdge(dut.clk_b_i)
    await ReadOnly()
    cycle += 1
    assert int(dut.read_valid_b_o.value) == 0
    await FallingEdge(dut.clk_b_i)
    dut.reset_b_i.value = 0
    dut.read_b_i.value = 1
    await RisingEdge(dut.clk_b_i)
    await ReadOnly()
    cycle += 1
    assert int(dut.read_valid_b_o.value) == 1
    assert int(dut.read_data_b_o.value) == 0x89AB_CDEF
    assert cycle <= max_cycles


@cocotb.test(timeout_time=10, timeout_unit="us")
async def dual_clock_collision_is_rejected(dut) -> None:
    _environment()
    _idle(dut)
    cocotb.start_soon(Clock(dut.clk_a_i, CLOCK_PERIOD_NS, unit="ns").start())
    cocotb.start_soon(
        Clock(dut.clk_b_i, DUAL_CLOCK_B_PERIOD_NS, unit="ns").start()
    )
    await FallingEdge(dut.clk_a_i)
    dut.address_a_i.value = 4
    dut.write_a_i.value = 1
    dut.write_data_a_i.value = 0xDEAD_BEEF
    dut.byte_enable_a_i.value = 0xF
    dut.address_b_i.value = 4
    dut.read_b_i.value = 1
    # The RTL assertion must terminate this deliberately invalid transaction.
    await RisingEdge(dut.clk_a_i)
    await RisingEdge(dut.clk_a_i)
    assert False, "forbidden dual-clock collision did not fire"


@cocotb.test(timeout_time=10, timeout_unit="us")
async def partial_same_port_collision_is_rejected(dut) -> None:
    _environment()
    _idle(dut)
    cocotb.start_soon(Clock(dut.clk_a_i, CLOCK_PERIOD_NS, unit="ns").start())
    await FallingEdge(dut.clk_a_i)
    dut.address_a_i.value = 5
    dut.read_a_i.value = 1
    dut.write_a_i.value = 1
    dut.write_data_a_i.value = 0xA5A5_5A5A
    dut.byte_enable_a_i.value = 0b0101
    await RisingEdge(dut.clk_a_i)
    await RisingEdge(dut.clk_a_i)
    assert False, "partial same-port read/write did not fire"


@cocotb.test(timeout_time=10, timeout_unit="us")
async def reset_suppresses_requests_and_holds_data(dut) -> None:
    """Reset clears valid, holds read data, and suppresses port-A writes.

    This is a directed same-edge check of the register/memory-port split: a
    write presented during reset must not reach the array, a read presented
    during reset must not update the data register, and a disabled read must
    hold the data register while its valid falls.
    """
    _, max_cycles = _environment()
    _idle(dut)
    cocotb.start_soon(Clock(dut.clk_a_i, CLOCK_PERIOD_NS, unit="ns").start())
    cocotb.start_soon(
        Clock(dut.clk_b_i, DUAL_CLOCK_B_PERIOD_NS, unit="ns").start()
    )
    cycle = 0

    await _a_edge(dut, address=6, write=True, data=0x0102_0304, lanes=0xF)
    valid, observed = await _a_edge(dut, address=6, read=True)
    cycle += 2
    assert (valid, observed) == (1, 0x0102_0304), (valid, hex(observed))
    # Single-clock fixture: port B is clocked by clk_a_i (clk_b_i is ignored),
    # so drive and sample the B request on clk_a_i edges.
    await FallingEdge(dut.clk_a_i)
    dut.address_b_i.value = 6
    dut.read_b_i.value = 1
    await RisingEdge(dut.clk_a_i)
    await ReadOnly()
    valid, observed = int(dut.read_valid_b_o.value), int(dut.read_data_b_o.value)
    cycle += 1
    assert (valid, observed) == (1, 0x0102_0304), (valid, hex(observed))

    # Reset asserted with a full-lane write and reads on both ports.
    await FallingEdge(dut.clk_a_i)
    dut.reset_a_i.value = 1
    dut.reset_b_i.value = 1
    dut.address_b_i.value = 6
    dut.read_b_i.value = 1
    valid, observed = await _a_edge(
        dut, address=6, read=True, write=True, data=0xFFFF_FFFF, lanes=0xF
    )
    cycle += 1
    assert (valid, observed) == (0, 0x0102_0304), (
        f"reset must clear valid and hold data: {(valid, hex(observed))}"
    )
    assert int(dut.read_valid_b_o.value) == 0
    assert int(dut.read_data_b_o.value) == 0x0102_0304

    # Release reset with every request withdrawn at the same falling edge, so
    # the intervening rising edge is idle (a write still asserted after reset
    # release would legitimately execute). Then read: the word is untouched.
    await FallingEdge(dut.clk_a_i)
    dut.reset_a_i.value = 0
    dut.reset_b_i.value = 0
    dut.read_b_i.value = 0
    dut.read_a_i.value = 0
    dut.write_a_i.value = 0
    cycle += 1
    valid, observed = await _a_edge(dut, address=6, read=True)
    cycle += 1
    assert (valid, observed) == (1, 0x0102_0304), (
        f"write during reset must be suppressed: {(valid, hex(observed))}"
    )

    # Disabled read: valid falls and data holds although the address moved.
    valid, observed = await _a_edge(dut, address=7, read=False)
    cycle += 1
    assert (valid, observed) == (0, 0x0102_0304), (valid, hex(observed))
    assert cycle <= max_cycles
