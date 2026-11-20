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

from src.nand2doom.dv.io.test_n2d_uart_debug import _exchange
from src.nand2doom.scripts.n2d_uart_debug_host import (
    CMD_RESET_PC, CMD_RESUME, ST_OK, request,
)

CLOCK_PERIOD_NS = 40
FRAME_CYCLES = 800 * 525
CAUSE_STORE_ACCESS = 7
MTVEC = 0x305


def _lui(rd: int, upper: int) -> int:
    return (upper << 12) | (rd << 7) | 0x37


def _i(opcode: int, rd: int, funct3: int, rs1: int, imm: int) -> int:
    return ((imm & 0xfff) << 20) | (rs1 << 15) | (funct3 << 12) | (rd << 7) | opcode


def _s(funct3: int, rs2: int, rs1: int, imm: int) -> int:
    return (((imm >> 5) & 0x7f) << 25) | (rs2 << 20) | (rs1 << 15) | (funct3 << 12) | \
        ((imm & 0x1f) << 7) | 0x23


def _csrw(csr: int, rs1: int) -> int:
    return (csr << 20) | (rs1 << 15) | (1 << 12) | 0x73


PROGRAM = (
    _lui(5, 0x10020),             # 0:  x5 = front view
    _i(0x13, 6, 0, 0, 20),        # 4:  x6 = 20
    _csrw(MTVEC, 6),              # 8:  mtvec = 20
    _i(0x03, 7, 2, 5, 0),         # 12: lw x7, 0(x5): front read, no trap
    _s(2, 0, 5, 0),               # 16: sw x0, 0(x5): store access fault
    _lui(5, 0x20001),             # 20: x5 = video control
    _i(0x13, 6, 0, 0, 40),        # 24: x6 = 40
    _csrw(MTVEC, 6),              # 28: mtvec = 40
    _i(0x13, 7, 0, 0, 3),         # 32: x7 = 3
    _s(0, 7, 5, 9),               # 36: sb x7, 9(x5): no lane 0, access fault
    _i(0x13, 6, 0, 0, 52),        # 40: x6 = 52
    _csrw(MTVEC, 6),              # 44: mtvec = 52
    _s(2, 7, 5, 8),               # 48: sw x7, 8(x5): swap + palette commit
    0x0000006f,                   # 52: jal x0, 0
)
EXPECTED_TRAPS = [(16, CAUSE_STORE_ACCESS, 0x1002_0000), (36, CAUSE_STORE_ACCESS, 0x2000_1009)]


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
                # The lane-less store's handler: that store requested no swap.
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
