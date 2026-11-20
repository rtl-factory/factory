"""Serial-pin DV for the nand2doom streaming load session (io/DV.md)."""

from __future__ import annotations

import os
import random
import zlib

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, RisingEdge, Timer
from cocotb.utils import get_sim_time

import src.nand2doom.scripts.host.n2d_uart_debug_host as wire


BAUD = 3_000_000  # exact FT232R maximum; the fixture runs 8 1/3 clocks per bit
BIT_PS = 10**12 / BAUD
CLOCK_NS = 40
GAP_CYCLES = 4000  # n2d_uart_stream_memory STREAM_GAP_TIMEOUT_CYCLES
IDLE_CYCLES = 40000  # n2d_uart_stream_memory STREAM_IDLE_CYCLES
SAMPLE_WINDOW_PS = 100_000  # host samples each bit centre and +/-100 ns
SEED = int(os.environ.get("DV_SEED", os.environ.get("N2A_SEED", "125")))
MAX_CYCLES = int(os.environ.get("DV_MAX_CYCLES", os.environ.get("N2A_MAX_CYCLES", "3000000")))


def raw_frame(kind: int, sequence: int, address: int, length: int, payload: bytes) -> bytes:
    """Independent encoder used for frames the host library refuses to build."""
    header = bytes((kind,)) + sequence.to_bytes(2, "little")
    header += address.to_bytes(4, "little") + length.to_bytes(4, "little")
    body = header + bytes((wire.crc8(header),)) + payload
    return bytes((wire.STREAM_SOF,)) + body + zlib.crc32(body).to_bytes(4, "little")


class Host:
    """Exact-3 Mbaud host UART: absolute-time bit edges and centre sampling."""

    def __init__(self, dut):
        self.dut = dut
        self.bit_ps = BIT_PS  # skewed by the FT232R oscillator-tolerance case
        self.rx = bytearray()
        self.cursor = 0

    async def send(self, data: bytes) -> None:
        start = get_sim_time(unit="ps")
        index = 0
        for byte in data:
            for level in [0] + [(byte >> bit) & 1 for bit in range(8)] + [1]:
                self.dut.uart_rx_i.value = level
                index += 1
                await Timer(start + round(index * self.bit_ps) - get_sim_time(unit="ps"), unit="ps")

    async def receive_loop(self) -> None:
        while True:
            await FallingEdge(self.dut.uart_tx_o)
            start = get_sim_time(unit="ps")
            value = 0
            for bit in range(10):
                centre = start + round((bit + 0.5) * self.bit_ps)
                samples = []
                for offset in (-SAMPLE_WINDOW_PS, 0, SAMPLE_WINDOW_PS):
                    await Timer(centre + offset - get_sim_time(unit="ps"), unit="ps")
                    samples.append(int(self.dut.uart_tx_o.value))
                assert len(set(samples)) == 1, f"device TX bit {bit} unstable in +/-100 ns window"
                if bit == 0:
                    assert samples[0] == 0, "device start bit not low at centre"
                elif bit == 9:
                    assert samples[0] == 1, "device stop bit not high at centre"
                else:
                    value |= samples[0] << (bit - 1)
            self.rx.append(value)

    async def take(self, count: int, limit: int) -> bytes:
        for _ in range(limit):
            if len(self.rx) - self.cursor >= count:
                data = bytes(self.rx[self.cursor:self.cursor + count])
                self.cursor += count
                return data
            await ClockCycles(self.dut.clk_i, 8)
        raise AssertionError(f"timed out waiting for {count} device bytes")

    def stream_replies(self) -> list[wire.StreamReply]:
        """Decode every complete reply received so far (strict framing)."""
        replies = []
        while len(self.rx) - self.cursor >= wire.STREAM_REPLY_BYTES:
            packet = bytes(self.rx[self.cursor:self.cursor + wire.STREAM_REPLY_BYTES])
            self.cursor += wire.STREAM_REPLY_BYTES
            replies.append(wire.decode_stream_reply(packet))
        assert self.cursor == len(self.rx) or len(self.rx) - self.cursor < wire.STREAM_REPLY_BYTES
        return replies

    async def exchange(self, frame: bytes, limit: int = 20000) -> wire.StreamReply:
        await self.send(frame)
        return wire.decode_stream_reply(await self.take(wire.STREAM_REPLY_BYTES, limit))

    async def settle(self, cycles: int) -> list[wire.StreamReply]:
        await ClockCycles(self.dut.clk_i, cycles)
        return self.stream_replies()


@cocotb.test()
async def stream_load_contract(dut):
    rng = random.Random(SEED)
    cocotb.log.info("seed=%d max_cycles=%d baud=%d", SEED, MAX_CYCLES, BAUD)
    cocotb.start_soon(Clock(dut.clk_i, CLOCK_NS, unit="ns").start())
    dut.reset_i.value = 1
    dut.uart_rx_i.value = 1
    dut.halt_ack_i.value = 0
    dut.arb_grant_i.value = 1
    dut.line_request_ready_i.value = 1
    dut.line_response_valid_i.value = 0
    dut.line_response_data_i.value = 0
    dut.line_response_error_i.value = 0
    await ClockCycles(dut.clk_i, 4)
    dut.reset_i.value = 0
    await ClockCycles(dut.clk_i, 4)

    # Backing memory: 16-byte lines, every line pre-filled with a sentinel.
    sentinel = bytes((0xA5, 0x5A, 0xC3, 0x3C)) * 4
    memory: dict[int, bytearray] = {}
    control = {"fail_after": -1, "publications": 0, "writes": 0, "cycles": 0}
    written_lines: set[int] = set()

    def line(address: int) -> bytearray:
        return memory.setdefault(address & ~0xF, bytearray(sentinel))

    def read_bytes(address: int, length: int) -> bytes:
        return bytes(line(a)[a & 0xF] for a in range(address, address + length))

    async def line_model():
        pending = None
        delay = 0
        while True:
            await RisingEdge(dut.clk_i)
            control["cycles"] += 1
            assert control["cycles"] <= MAX_CYCLES, "cycle bound exceeded"
            dut.line_response_valid_i.value = 0
            dut.line_response_error_i.value = 0
            if pending is not None:
                if delay:
                    delay -= 1
                    continue
                write, address, data, enables = pending
                dut.line_response_valid_i.value = 1
                dut.line_response_data_i.value = int.from_bytes(line(address), "little")
                if write:
                    control["publications"] += 1
                    if control["publications"] == control["fail_after"]:
                        dut.line_response_error_i.value = 1
                        control["fail_after"] = -1
                    else:
                        target = line(address)
                        for lane in range(16):
                            if enables & (1 << lane):
                                target[lane] = (data >> (8 * lane)) & 0xFF
                        control["writes"] += 1
                        written_lines.add(address)
                pending = None
            elif int(dut.line_request_valid_o.value) and int(dut.line_request_ready_i.value):
                pending = (bool(dut.line_request_write_o.value),
                           int(dut.line_request_address_o.value),
                           int(dut.line_request_write_data_o.value),
                           int(dut.line_request_byte_enable_o.value))
                delay = rng.randrange(4, 14)

    host = Host(dut)
    cocotb.start_soon(line_model())
    cocotb.start_soon(host.receive_loop())
    sequence = 0

    def next_sequence() -> int:
        nonlocal sequence
        sequence += 1
        return sequence & 0xFFFF

    # --- Session open, identity and not-halted rejection -------------------
    hello = await host.exchange(wire.stream_frame(wire.STREAM_HELLO, next_sequence()))
    assert (hello.kind, hello.status, hello.value) == (wire.STREAM_HELLO, wire.ST_OK,
                                                        wire.STREAM_IDENTITY), hello
    early = await host.exchange(wire.stream_write_frame(next_sequence(), 0x40, b"\x11" * 8))
    assert early.status == wire.ST_NOT_HALTED and control["writes"] == 0, early
    dut.halt_ack_i.value = 1

    # --- Trusted-header rejections answer in order without memory traffic --
    bad_range = await host.exchange(raw_frame(wire.STREAM_WRITE, next_sequence(),
                                              0x03FFFFFC, 8, bytes(8)))
    assert bad_range.status == wire.ST_RANGE, bad_range
    bad_align = await host.exchange(raw_frame(wire.STREAM_WRITE, next_sequence(), 0x102, 4, bytes(4)))
    assert bad_align.status == wire.ST_ALIGNMENT, bad_align
    bad_kind = await host.exchange(raw_frame(0x5A, next_sequence(), 0, 0, b""))
    assert bad_kind.status == wire.ST_COMMAND and bad_kind.kind == 0x5A, bad_kind
    bad_length = await host.exchange(raw_frame(wire.STREAM_HELLO, next_sequence(), 0, 5, b""))
    assert bad_length.status == wire.ST_LENGTH, bad_length
    assert control["writes"] == 0

    # --- Multi-segment pipelined stream with injected link faults ----------
    segments = [(0x0000_1000, rng.randbytes(2500)),
                (0x0002_0000, rng.randbytes(3075)),
                (0x03FF_FC00, rng.randbytes(1024))]
    chunks = [(address + offset, data[offset:offset + wire.STREAM_MAX_PAYLOAD])
              for address, data in segments
              for offset in range(0, len(data), wire.STREAM_MAX_PAYLOAD)]
    assert len(chunks) >= 8
    first = sequence + 1
    stream = bytearray()
    sent: dict[int, int] = {}
    for index, (address, data) in enumerate(chunks):
        seq = next_sequence()
        sent[seq] = index
        frame = bytearray(wire.stream_write_frame(seq, address, data))
        if index == 1:
            frame[20] ^= 0x40  # payload corruption: frame CRC fails
        elif index == 3:
            del frame[40]  # dropped payload byte: this frame fails, next resyncs
        elif index == 5:
            frame[1] ^= 0x01  # header corruption: silently resynchronized
        elif index == len(chunks) - 1:
            frame = frame[:-3]  # truncated tail: abandoned by the gap timeout
        stream += frame
    await host.send(bytes(stream))
    replies = await host.settle(GAP_CYCLES + 4000)
    order = [reply.sequence for reply in replies]
    assert order == sorted(order), f"replies out of order: {order}"
    status = {reply.sequence: reply.status for reply in replies}
    assert all(reply.kind == wire.STREAM_WRITE for reply in replies)
    assert status.get(first + 0) == wire.ST_OK and status.get(first + 2) == wire.ST_OK
    assert status.get(first + 1) == wire.ST_CHECKSUM, status
    assert status.get(first + 3) == wire.ST_CHECKSUM, status
    assert first + 4 not in status, "frame after a dropped byte must be lost"
    assert first + 5 not in status, "corrupt header must not be answered"
    assert first + len(chunks) - 1 not in status, "truncated frame must not be answered"
    assert all(value in (wire.ST_OK,) for seq, value in status.items()
               if seq not in (first + 1, first + 3)), status
    committed = sum(value == wire.ST_OK for value in status.values())
    cocotb.log.info("first pass replies=%s", sorted(status.items()))

    # --- Recovery: resend every unacknowledged frame, pipelined ------------
    pending = [index for seq, index in sent.items() if status.get(seq) != wire.ST_OK]
    memory_error_seen = False
    for round_index in range(4):
        if not pending:
            break
        if round_index == 0:
            control["fail_after"] = control["publications"] + 3
        resend = bytearray()
        sent = {}
        for index in pending:
            seq = next_sequence()
            sent[seq] = index
            resend += wire.stream_write_frame(seq, *chunks[index])
        await host.send(bytes(resend))
        # A full 1 KiB frame commits 256 words after its last byte arrives.
        replies = await host.settle(20000)
        status = {reply.sequence: reply.status for reply in replies}
        assert [r.sequence for r in replies] == sorted(sent), (round_index, replies)
        memory_error_seen |= wire.ST_MEMORY in status.values()
        committed += sum(value == wire.ST_OK for value in status.values())
        pending = [index for seq, index in sent.items() if status[seq] != wire.ST_OK]
    assert not pending, f"frames never committed: {pending}"
    assert memory_error_seen, "injected publication failure was not reported"

    # --- Stored-memory contents, tails and sentinels -----------------------
    for address, data in segments:
        assert read_bytes(address, len(data)) == data, f"segment 0x{address:08x} mismatch"
    assert read_bytes(0x0002_0000 + 3075, 1) == sentinel[3:4], "tail lane beyond segment changed"
    assert read_bytes(0x0000_1000 - 16, 16) == sentinel and read_bytes(0x1000 + 2500, 12) == sentinel[4:]
    allowed = {a & ~0xF for address, data in segments for a in range(address, address + len(data))}
    assert written_lines <= allowed, "write outside the streamed segments"

    # --- Whole-image digest of stored SDRAM, chained across segments -------
    seed = 0xFFFFFFFF
    for address, data in segments:
        reply = await host.exchange(wire.stream_digest_frame(next_sequence(), address, len(data), seed),
                                    limit=40000)
        assert reply.kind == wire.STREAM_DIGEST and reply.status == wire.ST_OK, reply
        assert reply.value == wire.stream_digest(read_bytes(address, len(data)), seed)
        seed = reply.value
    image = b"".join(data for _, data in segments)
    assert seed ^ 0xFFFFFFFF == zlib.crc32(image), "whole-image digest mismatch"
    flip = 0x0002_0000 + 1234
    line(flip)[flip & 0xF] ^= 0x10  # a stored-memory fault the RX path never saw
    corrupted = await host.exchange(wire.stream_digest_frame(next_sequence(), 0x0002_0000, 3075,
                                                              0xFFFFFFFF), limit=40000)
    assert corrupted.value != wire.stream_digest(segments[1][1]), "digest did not read SDRAM"
    line(flip)[flip & 0xF] ^= 0x10

    # --- Close, then the legacy engine owns the link again ----------------
    done = await host.exchange(wire.stream_frame(wire.STREAM_END, next_sequence()))
    assert done.kind == wire.STREAM_END and done.status == wire.ST_OK
    assert done.value == committed, (done.value, committed)
    await ClockCycles(dut.clk_i, 200)
    await host.send(wire.request(wire.CMD_PING, 7))
    ping = wire.decode_response(await host.take(15, 20000))
    assert ping.status == wire.ST_OK and ping.sequence == 7 and ping.words[0] == 0x3144324E

    # --- A host killed mid-frame: a restarted host's End recovers at once --
    junk = b"\xee" * 512
    reopened = await host.exchange(wire.stream_frame(wire.STREAM_HELLO, next_sequence()))
    assert reopened.value == wire.STREAM_IDENTITY
    await host.send(wire.stream_write_frame(next_sequence(), 0x1000, junk)[:200])
    # A restarted host is slower than the frame gap bound; the abandoned
    # frame is dropped before its first byte arrives.
    await ClockCycles(dut.clk_i, GAP_CYCLES + 100)
    restart = await host.exchange(wire.stream_frame(wire.STREAM_END, next_sequence()))
    assert restart.kind == wire.STREAM_END and restart.status == wire.ST_OK
    await ClockCycles(dut.clk_i, 200)
    await host.send(wire.request(wire.CMD_PING, 8))
    ping = wire.decode_response(await host.take(15, 20000))
    assert ping.status == wire.ST_OK and ping.sequence == 8

    # --- ... and with no host at all, the gap and idle bounds recover -------
    reopened = await host.exchange(wire.stream_frame(wire.STREAM_HELLO, next_sequence()))
    assert reopened.value == wire.STREAM_IDENTITY
    await host.send(wire.stream_write_frame(next_sequence(), 0x1000, junk)[:200])
    await ClockCycles(dut.clk_i, GAP_CYCLES + IDLE_CYCLES + 100)
    await host.send(wire.request(wire.CMD_PING, 9))
    ping = wire.decode_response(await host.take(15, 20000))
    assert ping.status == wire.ST_OK and ping.sequence == 9
    assert read_bytes(0x1000, len(segments[0][1])) == segments[0][1], "truncated frame wrote memory"
    # --- FT232R oscillator tolerance: host clock +/-1667 ppm -----------------
    # The FT232R's internal 12 MHz oscillator is specified at 11.98-12.02 MHz.
    # The host sends and samples with a skewed bit period in both directions.
    for ppm in (+1667, -1667):
        host.bit_ps = BIT_PS / (1 + ppm * 1e-6)
        skewed = bytes((i * 11 + ppm) & 0xFF for i in range(130))
        hello = await host.exchange(wire.stream_frame(wire.STREAM_HELLO, next_sequence()))
        assert hello.value == wire.STREAM_IDENTITY, (ppm, hello)
        frames = (wire.stream_write_frame(next_sequence(), 0x6000, skewed[:64]) +
                  wire.stream_write_frame(next_sequence(), 0x6040, skewed[64:]))
        await host.send(frames)
        replies = await host.settle(6000)
        assert [(r.kind, r.status) for r in replies] == [(wire.STREAM_WRITE, wire.ST_OK)] * 2, (ppm, replies)
        digest = await host.exchange(wire.stream_digest_frame(next_sequence(), 0x6000, len(skewed),
                                                              0xFFFFFFFF), limit=40000)
        assert digest.value ^ 0xFFFFFFFF == zlib.crc32(skewed), ppm
        assert read_bytes(0x6000, len(skewed)) == skewed, ppm
        done = await host.exchange(wire.stream_frame(wire.STREAM_END, next_sequence()))
        assert done.status == wire.ST_OK and done.value == 2, (ppm, done)
        await ClockCycles(dut.clk_i, 200)
    host.bit_ps = BIT_PS

    cocotb.log.info("PASS stream: %d frames committed, digest 0x%08x, %d cycles",
                    committed, zlib.crc32(image), control["cycles"])
