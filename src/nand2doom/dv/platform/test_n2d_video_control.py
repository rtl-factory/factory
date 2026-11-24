"""Video-control and framebuffer-window decode on the integrated system.

A preloaded RV32IM program checks, through the architectural trap
observation:
- a read of the front view 0x1002_0000 completes without a trap;
- a store to the read-only front view is a store access fault;
- a video-control write without byte lane 0 (a byte store to 0x2000_1009)
  is a store access fault and requests no swap;
- one word write of 3 to 0x2000_1008 requests a swap together with a
  palette commit, which completes at the next vertical blank.
"""

from __future__ import annotations

import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, ReadOnly, RisingEdge

from src.nand2doom.dv.core.rv32_asm import (
    MTVEC, X0, X5, X6, X7, addi, assemble, csrw, j, lui, lw, sb, sw,
)
from src.nand2doom.dv.io.test_n2d_uart_debug import _exchange
from src.nand2doom.scripts.host.n2d_uart_debug_host import (
    CMD_RESET_PC, CMD_RESUME, ST_OK, request,
)

CLOCK_PERIOD_NS = 40
FRAME_CYCLES = 800 * 525
CAUSE_STORE_ACCESS = 7
FRONT_VIEW, VIDEO_CONTROL = 0x1002_0000, 0x2000_1000

# Why: each faulting store's mtvec points at the next step, so its handler is
# the following code: the front-view store at 16 traps to 20, the lane-less
# byte store to video control +9 (no byte lane 0) at 36 traps to 40, and the
# word store of 3 to video control +8 (swap plus palette commit) at 48 falls
# into the loop at 52. The front-view load at 12 must not trap.
PROGRAM = tuple(assemble([
    lui(X5, FRONT_VIEW >> 12),
    addi(X6, X0, 20),
    csrw(MTVEC, X6),
    lw(X7, 0, X5),
    sw(X0, 0, X5),
    lui(X5, VIDEO_CONTROL >> 12),
    addi(X6, X0, 40),
    csrw(MTVEC, X6),
    addi(X7, X0, 3),
    sb(X7, 9, X5),
    addi(X6, X0, 52),
    csrw(MTVEC, X6),
    sw(X7, 8, X5),
    "park:",
    j("park"),
]))
EXPECTED_TRAPS = [(16, CAUSE_STORE_ACCESS, FRONT_VIEW), (36, CAUSE_STORE_ACCESS, VIDEO_CONTROL + 9)]


async def _observe(dut, state: dict) -> None:
    while True:
        await RisingEdge(dut.clk_i)
        await ReadOnly()
        if int(dut.observation_valid_o.value):
            pc = int(dut.observation_pc_o.value)
            if int(dut.observation_trap_o.value):
                state["traps"].append((pc, int(dut.observation_cause_o.value),
                                       int(dut.observation_trap_value_o.value)))
            state["retired"].append(pc)
            if pc == 40:
                # Why: pc 40 is the lane-less store's handler, so the state
                # sampled here shows that store requested no swap.
                state["after_byte_store"] = (int(dut.framebuffer_swap_pending_o.value),
                                             int(dut.framebuffer_front_bank_o.value))
        if int(dut.framebuffer_swap_pending_o.value):
            state["pending_seen"] = True
        state["front"].append(int(dut.framebuffer_front_bank_o.value))
        del state["front"][:-2]


@cocotb.test(timeout_time=200, timeout_unit="ms")
async def video_control_decode_contract(dut) -> None:
    seed = int(os.environ["DV_SEED"])
    maximum = int(os.environ["DV_MAX_CYCLES"])
    assert maximum >= 2 * FRAME_CYCLES
    cocotb.log.info("n2d video-control decode seed=%d max_cycles=%d", seed, maximum)
    for name in ("uart_rx_i", "ps2_clk_i", "ps2_data_i"):
        getattr(dut, name).value = 1
    dut.reset_i.value = 1
    dut.sdram_force_error_i.value = 0
    dut.preload_mode_i.value = 1
    dut.preload_valid_i.value = 0
    dut.preload_address_i.value = 0
    dut.preload_data_i.value = 0
    dut.preload_byte_enable_i.value = 0
    cocotb.start_soon(Clock(dut.clk_i, CLOCK_PERIOD_NS, unit="ns").start())
    await ClockCycles(dut.clk_i, 4)
    image = b"".join(word.to_bytes(4, "little") for word in PROGRAM)
    await FallingEdge(dut.clk_i)
    dut.preload_valid_i.value = 1
    dut.preload_data_i.value = int.from_bytes(image, "little")
    dut.preload_byte_enable_i.value = (1 << len(image)) - 1
    await RisingEdge(dut.clk_i)
    assert int(dut.preload_ready_o.value)
    await FallingEdge(dut.clk_i)
    dut.preload_valid_i.value = 0
    dut.preload_byte_enable_i.value = 0
    dut.preload_mode_i.value = 0
    await FallingEdge(dut.clk_i)
    dut.reset_i.value = 0
    await ClockCycles(dut.clk_i, 4)
    assert int(dut.framebuffer_front_bank_o.value) == 0

    state = {"traps": [], "retired": [], "front": [], "pending_seen": False}
    cocotb.start_soon(_observe(dut, state))
    assert (await _exchange(dut, request(CMD_RESET_PC, 1, address=0))).status == ST_OK
    assert (await _exchange(dut, request(CMD_RESUME, 2))).status == ST_OK

    for _ in range(2 * FRAME_CYCLES):
        await RisingEdge(dut.clk_i)
        if 52 in state["retired"] and int(dut.framebuffer_front_bank_o.value) == 1:
            break
    assert state["traps"] == EXPECTED_TRAPS, state["traps"]
    assert state.get("after_byte_store") == (0, 0), state.get("after_byte_store")
    assert 12 in state["retired"], "front-view read did not retire"
    assert 48 in state["retired"], "swap write did not retire"
    assert state["pending_seen"], "the swap was never pending"
    assert int(dut.framebuffer_front_bank_o.value) == 1, "swap not taken"
    assert not int(dut.framebuffer_swap_pending_o.value)
    cocotb.log.info("PASS traps=%s", [(pc, cause, hex(value)) for pc, cause, value in state["traps"]])
