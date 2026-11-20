from __future__ import annotations

import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge

from src.nand2doom.scripts.n2d_uart_debug_host import CMD_LOAD, CMD_PEEK, ST_MEMORY, ST_OK, request
from .test_n2d_uart_debug import _exchange


MAX_CYCLES = int(os.environ.get("N2A_MAX_CYCLES", "220000"))


@cocotb.test()
async def serial_commit_rollback_contract(dut):
    seed = int(os.environ.get("N2A_SEED", "31"))
    cocotb.log.info("seed=%d max_cycles=%d", seed, MAX_CYCLES)
    cocotb.start_soon(Clock(dut.clk_i, 40, unit="ns").start())
    dut.reset_i.value = 1
    dut.uart_rx_i.value = 1
    dut.arb_grant_i.value = 1
    dut.line_request_ready_i.value = 1
    dut.line_response_valid_i.value = 0
    dut.line_response_data_i.value = 0
    dut.line_response_error_i.value = 0
    await ClockCycles(dut.clk_i, 4)
    dut.reset_i.value = 0
    await ClockCycles(dut.clk_i, 4)

    original = int.from_bytes(bytes(range(16)), "little")
    memory = {0x100: original}
    control = {"fail_write": 2, "write_count": 0, "commit_seen": False,
               "done_seen": False, "grant_held": True, "premature_tx": False}

    async def line_model():
        pending = None
        delay = 0
        while True:
            await RisingEdge(dut.clk_i)
            dut.line_response_valid_i.value = 0
            dut.line_response_error_i.value = 0
            if int(dut.transaction_commit_o.value):
                control["commit_seen"] = True
            if control["commit_seen"] and not control["done_seen"]:
                control["grant_held"] &= bool(dut.arb_req_o.value)
                control["premature_tx"] |= not bool(dut.uart_tx_o.value)
            if int(dut.transaction_done_o.value):
                control["done_seen"] = True
            if pending is not None:
                if delay:
                    delay -= 1
                else:
                    write, address, data, enables = pending
                    dut.line_response_valid_i.value = 1
                    dut.line_response_data_i.value = memory.get(address, 0)
                    if write:
                        control["write_count"] += 1
                        if control["write_count"] == control["fail_write"]:
                            dut.line_response_error_i.value = 1
                            control["fail_write"] = -1
                        else:
                            value = memory.get(address, 0)
                            for lane in range(16):
                                if enables & (1 << lane):
                                    value = (value & ~(0xFF << (8*lane))) | (data & (0xFF << (8*lane)))
                            memory[address] = value
                    pending = None
            elif int(dut.line_request_valid_o.value) and int(dut.line_request_ready_i.value):
                pending = (bool(dut.line_request_write_o.value),
                           int(dut.line_request_address_o.value),
                           int(dut.line_request_write_data_o.value),
                           int(dut.line_request_byte_enable_o.value))
                delay = 12

    cocotb.start_soon(line_model())
    failed = await _exchange(dut, request(CMD_LOAD, 1, address=0x100,
        words=(0xAABBCCDD, 0x11223344), byte_enable=0xF))
    assert failed.status == ST_MEMORY
    assert control["commit_seen"] and control["done_seen"]
    assert control["grant_held"], "UART released arbitration before rollback completion"
    assert not control["premature_tx"], "UART started a response before commit/rollback completion"
    assert memory[0x100] == original, "later publication failure left a partial write"

    control.update(fail_write=-1, write_count=0, commit_seen=False,
                   done_seen=False, grant_held=True, premature_tx=False)
    recovered = await _exchange(dut, request(CMD_LOAD, 2, address=0x100,
        words=(0xDEADBEEF, 0xCAFEBABE), byte_enable=0xF))
    assert recovered.status == ST_OK
    peek = await _exchange(dut, request(CMD_PEEK, 3, address=0x100, count=2))
    assert peek.status == ST_OK and peek.words == (0xDEADBEEF, 0xCAFEBABE)
    cocotb.log.info("PASS late publication failure rolled back before error; recovery committed")
