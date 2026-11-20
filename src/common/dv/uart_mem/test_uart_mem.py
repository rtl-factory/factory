"""Contract of the common UART memory endpoint (wiki/src/common/uart_mem/HAS.md).

The bench drives the fixture's host PHY by bytes, keeps its own codec built
from the HAS framing tables, and runs a cycle scoreboard: a shadow of the
fixture memory updated from every observed transfer (endpoint and CPU),
compared with each endpoint read at its access cycle and with the bytes the
host receives. CPU stalls are checked against the LIVE_GAP_CYCLES bound."""
import os
import random
import zlib

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, Timer

CLOCK_PERIOD_NS = 40
CLKS_PER_BIT = 4
BYTE_CYCLES = 10 * CLKS_PER_BIT
LIVE_GAP = 32
GAP_CYCLES = 1500
HOLD_TIMEOUT = 400
MAX_LENGTH = 1024
PROJECT_ID = 0x5453_4554
ENTRY = 0x100

REQ_SOF, RSP_SOF, VERSION = 0xA8, 0x8A, 0x01
IDENTIFY, HOLD, RELEASE, ENTRY_CMD, WRITE, READ = 0x01, 0x02, 0x03, 0x04, 0x10, 0x11
OK, ST_VERSION, ST_COMMAND, ST_CHECKSUM, ST_ALIGNMENT = 0, 1, 2, 3, 4
ST_RANGE, ST_LENGTH, ST_NOT_HELD, ST_TIMEOUT, ST_MEMORY, ST_OVERRUN = 5, 6, 7, 8, 9, 10
ATTR_W, ATTR_RH, ATTR_RL = 1, 2, 4
WINDOWS = [(0x0000, 0x800, ATTR_W | ATTR_RH), (0x1000, 0x400, ATTR_W | ATTR_RH | ATTR_RL)]


def crc8(data):
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def request(command, seq, address=0, length=None, payload=b"", bad_header=False,
            bad_payload=False, version=VERSION):
    length = len(payload) if length is None else length
    header = bytes([version, command, seq]) + address.to_bytes(4, "little") + \
        length.to_bytes(2, "little")
    check = crc8(header) ^ (0x01 if bad_header else 0)
    frame = bytes([REQ_SOF]) + header + bytes([check]) + payload
    if payload:
        crc = zlib.crc32(payload) ^ (1 if bad_payload else 0)
        frame += crc.to_bytes(4, "little")
    return frame


class Bench:
    def __init__(self, dut):
        self.d = dut
        self.cycle = 0
        self.max_cycles = int(os.environ.get("DV_MAX_CYCLES", "4000000"))
        self.tx_queue = bytearray()
        self.tx_fire = False
        self.rx = bytearray()
        self.forwarded = []
        self.shadow = {}
        self.expected_reads = []
        self.read_log = []
        self.ep_log = []
        self.cpu_accepts = []
        self.stalls = []
        self.releases = []
        self.entries = []
        self.rsp_cycles = []
        self.print_queue = bytearray()
        self.print_fire = False
        self.printed = 0
        self.seq = 0

    async def start(self):
        d = self.d
        cocotb.start_soon(Clock(d.clk_i, CLOCK_PERIOD_NS, unit="ns").start())
        for name in ("host_tx_valid_i", "host_tx_byte_i", "host_line_low_i", "print_valid_i",
                     "print_byte_i", "cpu_enable_i", "hold_block_i", "mem_stall_i",
                     "mem_error_i", "mem_rsp_delay_i"):
            getattr(d, name).value = 0
        d.down_idle_i.value = 1
        d.reset_i.value = 1
        await self.ticks(4)
        await FallingEdge(d.clk_i)
        d.reset_i.value = 0
        await self.ticks(4)

    async def tick(self):
        d = self.d
        await FallingEdge(d.clk_i)
        if self.tx_fire:
            del self.tx_queue[0]
        if self.print_fire:
            del self.print_queue[0]
            self.printed += 1
        d.host_tx_valid_i.value = int(bool(self.tx_queue))
        d.host_tx_byte_i.value = self.tx_queue[0] if self.tx_queue else 0
        d.print_valid_i.value = int(bool(self.print_queue))
        d.print_byte_i.value = self.print_queue[0] if self.print_queue else 0
        await ReadOnly()
        self.cycle += 1
        assert self.cycle < self.max_cycles, f"cycle bound {self.max_cycles} exceeded"
        self.tx_fire = bool(self.tx_queue) and int(d.host_tx_ready_o.value) == 1
        self.print_fire = bool(self.print_queue) and int(d.print_ready_o.value) == 1
        if int(d.host_rx_valid_o.value):
            self.rx.append(int(d.host_rx_byte_o.value))
        if int(d.down_rx_valid_o.value):
            self.forwarded.append(int(d.down_rx_byte_o.value))
        if int(d.ep_rsp_valid_o.value):
            self.rsp_cycles.append(self.cycle)
            write, address = self.ep_log[-1][1], self.ep_log[-1][2]
            if not write:
                data = int(d.ep_rsp_data_o.value)
                expected = self.expected_reads.pop(0)
                assert expected is None or data == expected, (
                    f"endpoint read 0x{address:x} returned 0x{data:08x}, memory holds "
                    f"0x{expected:08x} (cycle {self.cycle})")
                self.read_log.append((address, data))
        held = int(d.hold_ack_o.value) == 1
        if int(d.ep_accept_o.value):
            write = int(d.ep_write_o.value)
            address = int(d.ep_addr_o.value)
            self.ep_log.append((self.cycle, write, address, held))
            if write:
                self.shadow[address] = int(d.ep_wdata_o.value)
            else:
                self.expected_reads.append(self.shadow.get(address))
        if int(d.cpu_accept_o.value):
            assert not held, f"CPU access while held (cycle {self.cycle})"
            self.cpu_accepts.append(self.cycle)
            if int(d.cpu_write_o.value):
                self.shadow[int(d.cpu_addr_o.value)] = int(d.cpu_wdata_o.value)
        if int(d.cpu_stall_o.value):
            assert int(d.ep_accept_o.value), "CPU stall without an endpoint transfer"
            self.stalls.append(self.cycle)
        if int(d.release_o.value):
            self.releases.append(self.cycle)
        if int(d.entry_valid_o.value):
            self.entries.append((self.cycle, int(d.entry_addr_o.value)))
        # Leave the read-only phase so the test may drive inputs before the
        # next rising edge.
        await Timer(1, unit="ps")

    async def ticks(self, count):
        for _ in range(count):
            await self.tick()

    async def drain(self):
        while self.tx_queue or self.tx_fire:
            await self.tick()

    def next_seq(self):
        self.seq = (self.seq + 1) & 0xFF
        return self.seq

    def take_response(self):
        """Parse one response from the host stream; bytes before it are project
        traffic. Returns None until a complete, CRC-valid response arrived."""
        start = self.rx.find(bytes([RSP_SOF]))
        while start >= 0:
            frame = self.rx[start:]
            if len(frame) >= 6:
                length = int.from_bytes(frame[4:6], "little")
                total = 6 + length + 1 + 4
                if len(frame) >= total and frame[1] == VERSION and frame[2] & 0x80:
                    body = bytes(frame[1:total - 4])
                    if zlib.crc32(body) == int.from_bytes(frame[total - 4:total], "little"):
                        before = bytes(self.rx[:start])
                        del self.rx[:start + total]
                        return before, frame[2] & 0x7F, frame[3], bytes(frame[6:6 + length]), \
                            frame[6 + length]
                elif len(frame) < total:
                    return None
            start = self.rx.find(bytes([RSP_SOF]), start + 1)
        return None

    async def response(self, budget):
        for _ in range(budget):
            got = self.take_response()
            if got is not None:
                return got
            await self.tick()
        raise AssertionError(f"no response within {budget} cycles; host received {self.rx.hex()}")

    async def call(self, command, address=0, length=None, payload=b"", expect=OK, **kwargs):
        seq = self.next_seq()
        frame = request(command, seq, address, length, payload, **kwargs)
        self.tx_queue += frame
        rsp_bytes = (length or 0) if command == READ else 128
        _, cmd, rseq, data, status = await self.response(
            (len(frame) + rsp_bytes + 16) * BYTE_CYCLES * 2 + HOLD_TIMEOUT + 2000)
        assert (cmd, rseq) == (command, seq), f"response for {cmd:#x}/{rseq} not {command:#x}/{seq}"
        assert status == expect, f"command {command:#x} status {status} expected {expect}"
        return data

    async def no_response(self, cycles):
        await self.ticks(cycles)
        assert self.take_response() is None, "unexpected response"

    async def write(self, address, data, expect=OK):
        for offset in range(0, len(data), MAX_LENGTH):
            await self.call(WRITE, address + offset, payload=data[offset:offset + MAX_LENGTH],
                            expect=expect)

    async def read(self, address, length, expect=OK):
        out = bytearray()
        for offset in range(0, length, MAX_LENGTH):
            out += await self.call(READ, address + offset, min(MAX_LENGTH, length - offset),
                                   expect=expect)
        return bytes(out)

    def shadow_bytes(self, address, length):
        return b"".join(self.shadow[address + o].to_bytes(4, "little")
                        for o in range(0, length, 4))


def check_reads_match_scoreboard(bench, payload, address, first_log):
    """The host bytes of a read are exactly the words the endpoint fetched, at
    ascending addresses, and every fetch matched memory at its cycle."""
    words = bench.read_log[first_log:]
    assert [a for a, _ in words] == list(range(address, address + len(payload), 4)), \
        "read addresses are not the ascending requested range"
    fetched = b"".join(w.to_bytes(4, "little") for _, w in words)
    assert fetched == payload, "host payload differs from the words read from memory"


def check_stall_bound(bench, begin):
    live = [c for c, write, _, held in bench.ep_log if c >= begin and not held]
    gaps = [b - a for a, b in zip(live, live[1:])]
    assert all(g >= LIVE_GAP for g in gaps), f"live endpoint accesses closer than {LIVE_GAP}: {min(gaps)}"
    stalls = [c for c in bench.stalls if c >= begin]
    assert set(stalls) <= set(live), "a CPU stall outside an endpoint access"
    span = bench.cycle - begin
    assert len(stalls) <= span // LIVE_GAP + 1, (
        f"{len(stalls)} stall cycles in {span} cycles exceed one per {LIVE_GAP}")
    return len(stalls), span


@cocotb.test()
async def uart_mem_load_readback(dut):
    """Acceptance: load and verify an image while held, then read the screen
    window while the CPU runs and draws."""
    seed = int(os.environ.get("DV_SEED", "61"))
    rng = random.Random(seed)
    dut._log.info("seed %d", seed)
    bench = Bench(dut)
    await bench.start()
    dut.cpu_enable_i.value = 1
    await bench.ticks(300)
    assert bench.cpu_accepts, "CPU model is not running"

    ident = await bench.call(IDENTIFY)
    words = [int.from_bytes(ident[i:i + 4], "little") for i in range(0, len(ident), 4)]
    assert words[:2] == [0x4D41324E, PROJECT_ID]
    assert words[2] == (MAX_LENGTH << 16) | (2 << 8), hex(words[2])
    assert words[3:5] == [ENTRY, LIVE_GAP]
    assert [tuple(words[5 + 3 * i:8 + 3 * i]) for i in range(2)] == WINDOWS

    program = bytes(rng.getrandbits(8) for _ in range(0x800))
    screen = bytes(rng.getrandbits(8) for _ in range(0x400))
    segments = [(0x0000, program), (0x1000, screen)]

    # One load transaction: hold once, write and verify each segment, set the
    # entry state, release once.
    await bench.call(HOLD)
    held_from = bench.cycle
    assert int(dut.hold_ack_o.value) == 1
    for address, data in segments:
        await bench.write(address, data)
        assert await bench.read(address, len(data)) == data, f"segment 0x{address:x} verify"
    await bench.call(ENTRY_CMD, ENTRY)
    assert [a for _, a in bench.entries] == [ENTRY]
    assert not bench.releases, "released before every segment verified"
    assert not [c for c in bench.cpu_accepts if c > held_from], "CPU ran while held"
    assert all(bench.shadow_bytes(a, len(d)) == d for a, d in segments)
    await bench.call(RELEASE)
    assert len(bench.releases) == 1 and int(dut.hold_o.value) == 0

    # Live readback while the CPU keeps drawing into the screen window.
    begin = bench.cycle
    cpu_before = len(bench.cpu_accepts)
    for _ in range(2):
        first = len(bench.read_log)
        frame = await bench.read(0x1000, 0x400)
        check_reads_match_scoreboard(bench, frame, 0x1000, first)
    stalls, span = check_stall_bound(bench, begin)
    ran = len(bench.cpu_accepts) - cpu_before
    assert ran > span // 2, f"CPU made only {ran} accesses in {span} cycles"
    assert bench.shadow_bytes(0x1000, 0x400) != screen, "CPU did not draw during readback"
    dut._log.info("live readback: %d cycles, %d CPU accesses, %d stall cycles (bound %d)",
                  span, ran, stalls, span // LIVE_GAP + 1)

    # The program the CPU never writes is still the loaded image.
    await bench.call(HOLD)
    assert await bench.read(0x0000, 0x800) == program
    await bench.call(RELEASE)


@cocotb.test()
async def uart_mem_contract(dut):
    """Framing, rejection, integrity, timeouts and UART sharing."""
    bench = Bench(dut)
    await bench.start()
    dut.cpu_enable_i.value = 1

    # Pass-through: other bytes, and a SOF while the project is mid-frame.
    bench.tx_queue += bytes([0x41, 0x5A])
    dut.down_idle_i.value = 0
    bench.tx_queue += bytes([REQ_SOF, 0x42])
    await bench.drain()
    await bench.ticks(3 * BYTE_CYCLES)
    assert bench.forwarded == [0x41, 0x5A, REQ_SOF, 0x42], bench.forwarded
    dut.down_idle_i.value = 1

    # Project prints share the transmitter only between endpoint responses.
    prints = bytes((0x30 + i % 10) for i in range(64))
    bench.print_queue += prints
    await bench.ticks(5 * BYTE_CYCLES)
    seq = bench.next_seq()
    bench.tx_queue += request(IDENTIFY, seq)
    before, cmd, rseq, data, status = await bench.response(200 * BYTE_CYCLES)
    assert (cmd, rseq, status) == (IDENTIFY, seq, OK) and len(data) == 4 * 11
    assert before and set(before) <= set(prints), "project bytes before the response"
    while bench.print_queue:
        await bench.tick()
    await bench.ticks(3 * BYTE_CYCLES)
    assert bytes(before) + bytes(bench.rx) == prints, "print stream lost or reordered"
    bench.rx.clear()

    # Header rejections, in HAS table order; none touches memory.
    accesses = len(bench.ep_log)
    await bench.call(IDENTIFY, version=2, expect=ST_VERSION)
    await bench.call(0x7F, expect=ST_COMMAND)
    await bench.call(HOLD, address=4, expect=ST_LENGTH)
    await bench.call(READ, 0x1000, 0, expect=ST_LENGTH)
    await bench.call(READ, 0x1000, 6, expect=ST_LENGTH)
    await bench.call(READ, 0x1000, MAX_LENGTH + 4, expect=ST_LENGTH)
    await bench.call(READ, 0x1002, 4, expect=ST_ALIGNMENT)
    await bench.call(READ, 0x0800, 4, expect=ST_RANGE)
    await bench.call(READ, 0x13FC, 8, expect=ST_RANGE)
    await bench.call(READ, 0x0000, 4, expect=ST_NOT_HELD)
    await bench.call(WRITE, 0x1000, payload=bytes(8), expect=ST_NOT_HELD)
    await bench.call(ENTRY_CMD, ENTRY, expect=ST_NOT_HELD)
    assert len(bench.ep_log) == accesses, "a rejected request reached memory"

    # Overrun: a byte before the status leaves is reported.
    seq = bench.next_seq()
    bench.tx_queue += request(READ, seq, 0x1000, 64) + bytes([0x00])
    _, cmd, rseq, data, status = await bench.response(400 * BYTE_CYCLES)
    assert (rseq, status) == (seq, ST_OVERRUN)

    # Overrun boundary: Identify bytes leave back to back, so the status
    # (byte 50) waits one byte time after the last payload byte is accepted.
    # A host byte started D clocks after byte 47 arrives completes inside
    # that wait for small D; for large D it completes after the status left.
    for delay, expect in ((5, ST_OVERRUN), (15, ST_OVERRUN), (25, ST_OVERRUN), (90, OK)):
        bench.rx.clear()
        seq = bench.next_seq()
        bench.tx_queue += request(IDENTIFY, seq)
        while len(bench.rx) < 48:
            await bench.tick()
        await bench.ticks(delay)
        forwarded = len(bench.forwarded)
        bench.tx_queue += bytes([0x00])
        _, cmd, rseq, data, status = await bench.response(200 * BYTE_CYCLES)
        assert (rseq, status) == (seq, expect), f"byte {delay} clocks after byte 47: {status}"
        await bench.ticks(3 * BYTE_CYCLES)
        assert len(bench.forwarded) == forwarded, "a byte during a response reached the project"

    await bench.call(HOLD)
    await bench.call(ENTRY_CMD, ENTRY + 4, expect=ST_RANGE)
    await bench.call(ENTRY_CMD, ENTRY + 2, expect=ST_ALIGNMENT)
    await bench.write(0x0000, bytes(range(64)))

    # Payload CRC failure: reported; the trusted header bounds the damage.
    await bench.call(WRITE, 0x0040, payload=bytes(16), bad_payload=True, expect=ST_CHECKSUM)
    assert all(a in range(0x40, 0x50) for _, w, a, _ in bench.ep_log[-4:] if w)

    # Header CRC failure: no response, no forwarding, until the line is quiet.
    forwarded = len(bench.forwarded)
    bench.tx_queue += request(READ, bench.next_seq(), 0x0000, 4, bad_header=True) + b"xyz"
    await bench.drain()
    await bench.no_response(GAP_CYCLES // 2)
    assert len(bench.forwarded) == forwarded, "discarded bytes reached the project"
    assert int(dut.owner_o.value) == 1
    await bench.ticks(GAP_CYCLES)
    assert int(dut.owner_o.value) == 0
    assert await bench.read(0x0000, 64) == bytes(range(64))

    # Framing error inside a header: dropped like a bad header.
    bench.tx_queue += request(READ, bench.next_seq(), 0x0000, 4)[:5]
    await bench.drain()
    dut.host_line_low_i.value = 1
    await bench.ticks(12 * CLKS_PER_BIT)
    dut.host_line_low_i.value = 0
    await bench.no_response(GAP_CYCLES + 4 * BYTE_CYCLES)
    assert int(dut.owner_o.value) == 0

    # Inter-byte gap: a partial frame is abandoned and the link returns.
    bench.tx_queue += request(IDENTIFY, bench.next_seq())[:4]
    await bench.drain()
    await bench.no_response(GAP_CYCLES + 2 * BYTE_CYCLES)
    bench.tx_queue += bytes([0x55])
    await bench.drain()
    await bench.ticks(2 * BYTE_CYCLES)
    assert bench.forwarded[-1] == 0x55

    # Memory error and timeout: reported, read payload zero-filled.
    dut.mem_error_i.value = 1
    assert await bench.read(0x0000, 16, expect=ST_MEMORY) == bytes(16)
    await bench.call(WRITE, 0x0100, payload=bytes(8), expect=ST_MEMORY)
    dut.mem_error_i.value = 0
    dut.mem_stall_i.value = 1
    assert await bench.read(0x0000, 16, expect=ST_TIMEOUT) == bytes(16)
    await bench.call(WRITE, 0x0100, payload=bytes(8), expect=ST_TIMEOUT)
    dut.mem_stall_i.value = 0
    assert await bench.read(0x0000, 8) == bytes(range(8))

    # Accepted request that times out: the late response is an orphan. It is
    # dropped, no request is issued until it arrives, and the next frame
    # recovers with the right data.
    late = 3000
    dut.mem_rsp_delay_i.value = late
    accepted = len(bench.ep_log)
    assert await bench.read(0x0000, 8, expect=ST_TIMEOUT) == bytes(8)
    orphan_accept = bench.ep_log[accepted][0]
    assert len(bench.ep_log) == accepted + 1, "a second request during the failed read"
    dut.mem_rsp_delay_i.value = 0
    rsp_before = len(bench.rsp_cycles)
    assert await bench.read(0x0020, 8) == bytes(range(0x20, 0x28)), "orphan data was used"
    orphan_rsp = bench.rsp_cycles[rsp_before]
    assert orphan_rsp >= orphan_accept + late, "orphan answered early"
    assert bench.ep_log[accepted + 1][0] > orphan_rsp, "request issued before the orphan drained"
    dut.mem_rsp_delay_i.value = late
    await bench.call(WRITE, 0x0100, payload=bytes(8), expect=ST_TIMEOUT)
    dut.mem_rsp_delay_i.value = 0
    # A write word that completes while the orphan is outstanding cannot be
    # issued: overrun, and nothing is written.
    accepted = len(bench.ep_log)
    await bench.call(WRITE, 0x0100, payload=bytes(range(8)), expect=ST_OVERRUN)
    assert len(bench.ep_log) == accepted
    await bench.ticks(late)
    await bench.write(0x0100, bytes(range(8)))
    assert await bench.read(0x0100, 8) == bytes(range(8))

    # Hold timeout keeps the hold requested; release always succeeds.
    await bench.call(RELEASE)
    await bench.ticks(50)
    dut.hold_block_i.value = 1
    await bench.call(HOLD, expect=ST_TIMEOUT)
    assert int(dut.hold_o.value) == 1 and int(dut.hold_ack_o.value) == 0
    ident = await bench.call(IDENTIFY)
    assert int.from_bytes(ident[8:12], "little") & 3 == 2, "identify flags: requested, not held"
    await bench.call(RELEASE)
    dut.hold_block_i.value = 0
    assert int(dut.hold_o.value) == 0
    assert len(bench.releases) == 2
