"""Public UART Halt against actual CPU/caches/arbiter/SDRAM, no internal force."""
import json
import os
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, Timer, with_timeout
from cocotb.utils import get_sim_time

from src.nand2doom.dv.core.rv32i_smoke_model import encode_i, encode_s, encode_b
from src.nand2doom.dv.memory.sdram_pin_model import SdramPinModel
from src.nand2doom.scripts.n2d_uart_debug_host import (
    CMD_PING, CMD_LOAD, CMD_PEEK, CMD_RESUME, CMD_HALT,
    request, decode_response,
)

BASE = 0x10000
# D-cache geometry of the fixture, set by _geometry() at test start: dirty
# lines that fill every way of every set, their byte span, and the
# memory/HAS.md "Maintained Halt bound" for that many dirty lines: a 24-clock
# slot per line plus CPU drain 256, victim buffer drain 24 (serial: the walk
# starts only after the buffer empties), I-cache walk 514 and coordination 16.
HALT_FIXED = 256 + 24 + 514 + 16
LINES = 1024
SPAN = LINES * 16
BUDGET = LINES * 24 + HALT_FIXED


def _geometry(dut):
    global LINES, SPAN, BUDGET
    LINES = 512 * int(dut.DCACHE_WAYS.value)
    SPAN = LINES * 16
    BUDGET = LINES * 24 + HALT_FIXED
    cocotb.log.info("D-cache %d ways: %d lines, Halt budget %d clocks",
                    LINES // 512, LINES, BUDGET)


def cycle():
    return int(get_sim_time(unit="ns")) // 40


def fill_program(lines, outstanding=False):
    # Contiguous sixteen-byte lines use every way of every set without
    # eviction. A count above the 12-bit immediate is built as 4096 + low.
    count = ((encode_i(lines, 0, 0, 2),) if lines < 2048 else
             (0x00001137, encode_i(lines - 4096, 2, 0, 2)))
    prefix = (0x000100b7, *count,
            encode_s(0, 1, 1), encode_i(16, 1, 0, 1),
            encode_i(-1, 2, 0, 2), encode_b(-12, 0, 2, 1),
            0x100001b7, encode_i(1, 0, 0, 4), encode_s(0, 4, 3))
    if not lines:
        prefix = prefix[len(count) + 5:]
    if outstanding:
        # Repeated cold reads in a distinct tag, after the dirty stores finish.
        return prefix + (0x00040337, encode_i(0, 6, 2, 7, 0x03),
                         encode_i(16, 6, 0, 6), encode_b(-8, 0, 0, 0))
    return prefix + (encode_i(1, 5, 0, 5), 0xffdff06f)


class PinMemory:
    """Strict burst-eight CL2 SDRAM pins plus independent CPU observation."""

    def __init__(self, dut):
        self.dut = dut
        self.sdram = SdramPinModel()
        self.words = self.sdram.words
        self.writes = self.sdram.writes
        self.refresh = self.sdram.refresh
        self.retired_stores = []
        self.counter = []
        self.marker = False
        # Stall the controller port on the first line response after the
        # marker (the outstanding loop's first data refill), so the next
        # request, the loop's instruction refill, is the blocked one
        # independent of SDRAM latency.
        self.stall_after_marker_response = False
        self.errors = 0
        self.retired = 0
        self.tx_starts = 0
        self.tx_previous = 1

    def word(self, address):
        return self.sdram.word(address)

    async def run(self):
        d = self.dut
        while True:
            await FallingEdge(d.clk_i)
            d.sdram_dq_i.value = self.sdram.due.pop(cycle() + 1, 0xdead)
            await RisingEdge(d.clk_i)
            now = cycle()
            if int(d.reset_i.value):
                continue
            tx = int(d.uart_tx_o.value)
            self.tx_starts += self.tx_previous == 1 and tx == 0
            self.tx_previous = tx
            cmd = tuple(int(x.value) for x in
                        (d.sdram_ras_n_o, d.sdram_cas_n_o, d.sdram_we_n_o))
            assert int(d.sdram_cke_o.value) and not int(d.sdram_cs_n_o.value)
            refreshes = len(self.refresh)
            self.sdram.edge(now, cmd, int(d.sdram_bank_o.value), int(d.sdram_address_o.value),
                            int(d.sdram_dq_o.value), int(d.sdram_dqm_o.value),
                            int(d.sdram_dq_oe_o.value))
            if len(self.refresh) > max(refreshes, 2):
                assert self.refresh[-1] - self.refresh[-2] <= 195
            if int(d.observation_valid_o.value):
                self.retired += 1
                assert not int(d.observation_trap_o.value)
                if int(d.observation_memory_write_o.value):
                    address = int(d.observation_memory_address_o.value)
                    if BASE <= address < BASE + SPAN:
                        self.retired_stores.append((address, int(d.observation_memory_write_data_o.value)))
                if int(d.observation_rd_write_o.value) and int(d.observation_rd_address_o.value) == 5:
                    value = int(d.observation_rd_write_data_o.value)
                    assert value == (self.counter[-1] + 1 if self.counter else 1)
                    self.counter.append(value)
            if int(d.framebuffer_write_valid_o.value):
                assert int(d.framebuffer_write_data_o.value) == 1
                self.marker = True
            if int(d.line_response_o.value) and int(d.line_error_o.value):
                self.errors += 1
            if self.stall_after_marker_response and self.marker and int(d.line_response_o.value):
                d.stall_i.value = 1
                self.stall_after_marker_response = False


class SerialHost:
    def __init__(self, dut):
        self.dut, self.sequence = dut, 0
        self.first_response = None
        self.on_response_start = None
        self.request_start = None
        self.request_end = None

    async def receive(self):
        packet, length = bytearray(), 7
        while len(packet) < length:
            await FallingEdge(self.dut.uart_tx_o)
            if not packet:
                self.first_response = cycle()
                if self.on_response_start is not None:
                    self.on_response_start()
            await Timer(1500, unit="ns")
            value = 0
            for bit in range(8):
                value |= int(self.dut.uart_tx_o.value) << bit
                await Timer(1000, unit="ns")
            assert int(self.dut.uart_tx_o.value), "UART stop bit"
            packet.append(value)
            if len(packet) == 6:
                length += packet[5] * 4
        return decode_response(bytes(packet))

    async def exchange(self, command, *, before_crc=None, after_send=None, **kwargs):
        sequence = self.sequence
        self.sequence = (sequence + 1) & 255
        receiver = cocotb.start_soon(self.receive())
        self.request_start = cycle()
        packet = request(command, sequence, **kwargs)
        for index, value in enumerate(packet):
            if index == len(packet) - 1 and before_crc is not None:
                await before_crc()
            for bit in (0, *((value >> i) & 1 for i in range(8)), 1):
                self.dut.uart_rx_i.value = bit
                await Timer(1000, unit="ns")
        self.request_end = cycle()
        if after_send is not None:
            await after_send()
        response = await with_timeout(receiver, 4, "ms")
        assert response.sequence == sequence and response.command == command | 0x80
        return response, self.first_response - self.request_end


@cocotb.test(timeout_time=20, timeout_unit="ms")
async def full_dirty_halt_contract(dut):
    _geometry(dut)
    cocotb.log.info("seed=%s maximum_cycles=%s", os.environ.get("SEED", "82"),
                    os.environ.get("DV_MAX_CYCLES", "500000"))
    dut.reset_i.value = 1
    dut.stall_i.value = 0
    dut.reject_write_i.value = 0
    dut.uart_rx_i.value = 1
    dut.ps2_clk_i.value = 1
    dut.ps2_data_i.value = 1
    dut.sdram_dq_i.value = 0
    cocotb.start_soon(Clock(dut.clk_i, 40, unit="ns").start())
    memory = PinMemory(dut)
    monitor = cocotb.start_soon(memory.run())
    await Timer(200, unit="ns")
    dut.reset_i.value = 0
    await Timer(210, unit="us")
    assert int(dut.init_done_o.value) and int(dut.halt_ack_o.value)
    host = SerialHost(dut)
    response, _ = await host.exchange(CMD_PING)
    assert response.status == 0 and response.words == (0x3144324e, 1)
    program = fill_program(LINES)
    for offset in range(0, len(program), 8):
        response, _ = await host.exchange(CMD_LOAD, address=offset * 4,
                                         words=program[offset:offset + 8], byte_enable=15)
        assert response.status == 0
    response, _ = await host.exchange(CMD_RESUME)
    assert response.status == 0
    for _ in range(2000):
        if memory.marker:
            break
        await Timer(2, unit="us")
    assert memory.marker, "CPU did not retire complete dirty-line program"
    assert memory.retired_stores == [(BASE + 16 * i, BASE + 16 * i) for i in range(LINES)]
    assert not [x for x in memory.writes if BASE <= x[1] < BASE + SPAN], \
        "every line must still be dirty, not already evicted to SDRAM"
    before = cycle()
    at_start = {}
    host.on_response_start = lambda: at_start.update(
        halfwords=sum(BASE <= a < BASE + SPAN for _, a, _ in memory.writes))
    response, wait = await host.exchange(CMD_HALT)
    host.on_response_start = None
    writes_at_reply = sum(BASE <= a < BASE + SPAN for _, a, _ in memory.writes)
    assert int(dut.halt_ack_o.value)
    count = len(memory.counter)
    tx_edges = memory.tx_starts
    # Preserve diagnostics even for old4096: no second Halt, Resume or reset.
    await Timer(2, unit="ms")
    assert len(memory.counter) == count, "timeout must retain CPU Halt"
    assert memory.tx_starts == tx_edges, "no unsolicited late response"
    flushed = [(t, a, v) for t, a, v in memory.writes if BASE <= a < BASE + SPAN]
    assert len(flushed) == LINES * 8, "each dirty line must publish exactly eight halfwords"
    assert sorted(a for _, a, _ in flushed) == list(range(BASE, BASE + SPAN, 2))
    for offset in range(0, SPAN, 4):
        expected = BASE + offset if offset % 16 == 0 else 0
        assert memory.word(BASE + offset) == expected, (hex(BASE + offset), expected)
    result = {"halt_status": response.status, "response_wait_cycles": wait,
              "command_start": host.request_start, "request_end": host.request_end,
              "response_start": host.first_response,
              "writes_at_response_start": at_start["halfwords"],
              "writes_at_reply": writes_at_reply, "final_halfwords": len(flushed),
              "last_write_since_command_start": flushed[-1][0] - before,
              "refresh_during_halt": sum(before <= t <= flushed[-1][0] for t in memory.refresh),
              "retired_stores": len(memory.retired_stores), "held_halt_no_replay": True}
    Path("halt-result.json").write_text(json.dumps(result, indent=2) + "\n")
    cocotb.log.info("HALT_DIAGNOSTIC %s", result)
    monitor.cancel()
    assert response.status == 0, f"maintained Halt deadline failure: {result}"
    assert writes_at_reply == LINES * 8, "success before complete backing-store publication"
    assert at_start["halfwords"] == LINES * 8, "ACK started before complete publication"
    assert wait < BUDGET, "full dirty Halt exceeded the maintained budget"


@cocotb.test(timeout_time=60, timeout_unit="ms")
async def halt_boundaries_contract(dut):
    """Finite negative diagnostics are explicit test actions, not host retries."""
    _geometry(dut)
    cocotb.start_soon(Clock(dut.clk_i, 40, unit="ns").start())
    results = []
    held_prefetch = {}
    for label, lines, outstanding, fault in (
        ("clean", 0, False, None),
        ("partial", 17, False, None),
        ("outstanding_data", 64, True, None),
        ("core_no_progress_fetch", 64, True, "stall"),
        ("no_progress", 8, False, "stall"),
        ("write_errors", 8, False, "error"),
    ):
        dut.reset_i.value = 1
        dut.stall_i.value = 0
        dut.reject_write_i.value = 0
        dut.uart_rx_i.value = 1
        dut.ps2_clk_i.value = 1
        dut.ps2_data_i.value = 1
        dut.sdram_dq_i.value = 0
        memory = PinMemory(dut)
        monitor = cocotb.start_soon(memory.run())
        await Timer(200, unit="ns")
        dut.reset_i.value = 0
        await Timer(210, unit="us")
        host = SerialHost(dut)
        program = fill_program(lines, outstanding)
        for offset in range(0, len(program), 8):
            reply, _ = await host.exchange(CMD_LOAD, address=offset * 4,
                                           words=program[offset:offset + 8], byte_enable=15)
            assert reply.status == 0
        memory.stall_after_marker_response = fault == "stall" and outstanding
        reply, _ = await host.exchange(CMD_RESUME)
        assert reply.status == 0
        for _ in range(2000):
            if memory.marker and not memory.stall_after_marker_response:
                break
            await Timer(2, unit="us")
        assert memory.marker and not memory.stall_after_marker_response, label
        assert memory.retired_stores == [(BASE + 16 * i, BASE + 16 * i) for i in range(lines)]
        assert not [x for x in memory.writes if BASE <= x[1] < BASE + SPAN]
        if fault == "stall":
            dut.stall_i.value = 1
        elif fault == "error":
            dut.reject_write_i.value = 1

        async def block_cpu():
            dut.stall_i.value = 1
            await Timer(4, unit="us")
            # Early permanent stall catches the first instruction refill;
            # the later bounded stall catches an outstanding data request.
            # The D-cache prefetches the line after each install, so a held
            # D-cache prefetch may sit beside the instruction refill, and the
            # owed load may wait behind its own line's prefetch.
            valid = int(dut.memory_request_valid_o.value)
            prefetch = int(dut.memory_request_prefetch_o.value)
            assert valid & ~prefetch == 1 if fault else valid == 2, (label, valid, prefetch)
            held_prefetch[label] = bool(prefetch & 2)
            assert not int(dut.halt_ack_o.value)
            assert int(dut.line_valid_o.value) and not int(dut.line_ready_o.value)

        async def finish_cpu():
            assert not int(dut.halt_ack_o.value)
            await Timer(4, unit="us")
            assert not int(dut.halt_ack_o.value)
            dut.stall_i.value = 0

        first = {}
        host.on_response_start = lambda: first.update(
            halfwords=sum(BASE <= a < BASE + SPAN for _, a, _ in memory.writes))
        reply, wait = await host.exchange(
            CMD_HALT, before_crc=block_cpu if outstanding else None,
            after_send=finish_cpu if outstanding and not fault else None)
        host.on_response_start = None
        halt_times = {"command_start": host.request_start,
                      "request_end": host.request_end, "response_start": host.first_response}
        if fault:
            assert reply.status == 8 and 65520 <= wait <= 65536, (label, reply, wait)
            assert first["halfwords"] == 0
            assert int(dut.halt_ack_o.value) == (0 if outstanding else 1)
            frozen = memory.retired
            # Diagnostics deliberately prove device rejection; production Client
            # must poison on the original failure (covered by host unit tests).
            denied, _ = await host.exchange(CMD_PEEK, address=BASE, count=1)
            assert denied.status == 7 and denied.words == ()
            denied, _ = await host.exchange(CMD_RESUME)
            assert denied.status == 7 and memory.retired == frozen
            if fault == "error":
                assert memory.errors > 1, "failed maintenance must retry without publication"
            else:
                assert not memory.errors
            dut.stall_i.value = 0
            dut.reject_write_i.value = 0
            tx_edges = memory.tx_starts
            # Removing the simulated fault is not another Halt command.
            await Timer(100, unit="us")
            # The permanent stall holds an instruction refill (master 1) with
            # nothing in execute. Under the drain contract (core/HAS.md "Reset
            # and debug behavior") a fetched instruction that has not entered
            # execute is discarded: releasing the refill completes the drain
            # with no retirement, and Resume refetches it from the retained PC.
            assert memory.retired == frozen
            assert int(dut.halt_ack_o.value)
            assert memory.tx_starts == tx_edges, "late completion must not emit an unsolicited reply"
        else:
            assert reply.status == 0 and wait < BUDGET, (label, reply, wait)
            assert first["halfwords"] == lines * 8, "premature success at first ACK byte"
        writes = [x for x in memory.writes if BASE <= x[1] < BASE + SPAN]
        assert len(writes) == lines * 8
        assert sorted(a for _, a, _ in writes) == list(range(BASE, BASE + lines * 16, 2))
        for offset in range(0, SPAN, 4):
            expected = BASE + offset if offset < lines * 16 and offset % 16 == 0 else 0
            assert memory.word(BASE + offset) == expected, (label, offset, expected)
        for address in sorted({BASE, BASE + max(0, lines - 1) * 16, BASE + SPAN - 4}):
            readback, _ = await host.exchange(CMD_PEEK, address=address, count=1)
            assert readback.status == 0 and readback.words == (memory.word(address),)
        # No retirement during maintained Halt; resume from saved PC/registers,
        # without replaying any original store or resetting the live counter.
        retired_before = memory.retired
        counter_before = memory.counter[-1] if memory.counter else 0
        resume, _ = await host.exchange(CMD_RESUME)
        assert resume.status == 0
        await Timer(4, unit="us")
        assert memory.retired > retired_before and not int(dut.halt_ack_o.value)
        assert len(memory.retired_stores) == lines
        if not outstanding:
            assert memory.counter[-1] > counter_before
        results.append({"case": label, "dirty_lines": lines, "status": reply.status,
                        "wait_after_request_end_cycles": wait, **halt_times,
                        "halfwords_at_response_start": first["halfwords"],
                        "errors": memory.errors, "final_halfwords": len(writes),
                        "resume_without_store_replay": True,
                        "held_dcache_prefetch": held_prefetch.get(label)})
        cocotb.log.info("HALT_BOUNDARY %s", results[-1])
        monitor.cancel()
    Path("halt-boundaries.json").write_text(json.dumps(results, indent=2) + "\n")
