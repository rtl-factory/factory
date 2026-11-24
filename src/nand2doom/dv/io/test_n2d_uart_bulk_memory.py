"""Actual atomic adapter rollback inside a partially committed bulk write."""
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge

from src.common.dv.driven_inputs import DrivenInputs
from .test_n2d_uart_bulk import exchange
from .test_n2d_uart_debug import _uart_send
from src.nand2doom.scripts.host.n2d_uart_debug_host import request, CMD_RESUME


@cocotb.test()
async def bulk_partial_commit_rollback(dut):
    cocotb.start_soon(Clock(dut.clk_i, 40, unit="ns").start())
    dut.reset_i.value = 1
    dut.uart_rx_i.value = 1
    dut.arb_grant_i.value = 1
    dut.line_request_ready_i.value = 1
    dut.line_response_valid_i.value = 0
    dut.line_response_data_i.value = 0
    dut.line_response_error_i.value = 0
    await ClockCycles(dut.clk_i, 8)
    dut.reset_i.value = 0
    await ClockCycles(dut.clk_i, 8)
    original = bytes((i*19+7)%256 for i in range(96))
    memory = {0xf0+i: int.from_bytes(original[i:i+16], "little") for i in range(0,96,16)}
    writes = []
    stats = dict(commits=0, completions=0, errors=0, refresh_stalls=0)

    async def model():
        pins = DrivenInputs(dut, line_request_ready_i=1, line_response_valid_i=0, line_response_error_i=0)
        pending = None
        delay = 0
        cycle = 0
        committing = False
        failed = False
        while True:
            await RisingEdge(dut.clk_i)
            cycle += 1
            # Why: bounded request stalls model refresh occupation and
            # responses vary independently, without stalls long enough to trip
            # the RTL operation watchdog.
            ready = cycle % 47 >= 9
            pins.drive(line_request_ready_i=ready)
            pins.drive(line_response_valid_i=0, line_response_error_i=0)
            stats["refresh_stalls"] += not ready
            if int(dut.transaction_commit_o.value):
                committing = True
                stats["commits"] += 1
            if committing:
                assert int(dut.arb_req_o.value), "ownership released during commit/rollback"
                assert int(dut.uart_tx_o.value), "response preceded rollback completion"
            if int(dut.transaction_done_o.value):
                committing = False
                stats["completions"] += 1
                stats["errors"] += int(dut.transaction_error_o.value)
            if pending is not None:
                if delay:
                    delay -= 1
                else:
                    write, address, data, mask = pending
                    pins.drive(line_response_valid_i=1)
                    dut.line_response_data_i.value = memory.get(address, 0)
                    if write:
                        writes.append((address, data, mask))
                        # Why: the first eight writes commit; failing the
                        # second chunk's second physical write after its first
                        # reached memory checks that a partial chunk is
                        # reported as failed.
                        if len(writes) == 10 and not failed:
                            pins.drive(line_response_error_i=1)
                            failed = True
                        else:
                            value = memory.get(address, 0)
                            for lane in range(16):
                                if mask & (1 << lane):
                                    value = value & ~(255 << (lane*8)) | (data & (255 << (lane*8)))
                            memory[address] = value
                    pending = None
            elif int(dut.line_request_valid_o.value) and int(dut.line_request_ready_i.value):
                pending = (bool(dut.line_request_write_o.value), int(dut.line_request_address_o.value),
                           int(dut.line_request_write_data_o.value), int(dut.line_request_byte_enable_o.value))
                delay = 7 + cycle%13

    cocotb.start_soon(model())
    data = bytes((255-i*3)%256 for i in range(64))
    result = await exchange(dut, 0x31, 81, 0x100, 64, data)
    assert result[4] == 9 and result[13:-4] == b""
    assert int.from_bytes(result[11:13], "little") == 32
    actual = b"".join(memory[0xf0+i].to_bytes(16, "little") for i in range(0,96,16))
    assert actual == original[:16] + data[:32] + original[48:], "current chunk not rolled back"
    assert stats["commits"] == stats["completions"] == 2 and stats["errors"] == 1
    assert len(writes) > 10, "rollback writes not observed"
    assert stats["refresh_stalls"] > 0
    before = memory.copy(), len(writes)
    await _uart_send(dut, request(CMD_RESUME, 82))
    await ClockCycles(dut.clk_i, 1024)
    assert int(dut.halt_req_o.value) and before == (memory, len(writes))
    cocotb.log.info("PASS committed32/64; second transaction rollback; retained grant; quarantine")
