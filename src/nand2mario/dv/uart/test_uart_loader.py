"""nand2mario UART cartridge loader against the Python protocol model.

The bench is the host: it sends request frames byte by byte through the
fixture's UART transmitter and collects the response through the fixture's
receiver, while a bench coroutine plays the cartridge backing's load port
with randomized ready and response delays over a byte store the model shares.
A second coroutine plays the display's snapshot side: the display-valid and
pin levels, the pin/release pulses and the snapshot byte port with
randomized ready and response delays over a seeded record. Directed
scenarios cover every command and status code, the hold level, the buffered
commit (a corrupted WRITE changes nothing), readback, line noise, framing
errors, the receive timeout and overrun with their sticky flags, a reload
without reset, and the snapshot commands (pin with and without a frame,
idempotence, the whole record, range ends, a pin lost under a payload);
a randomized phase mixes valid and invalid frames and asserts its own
coverage.
"""
import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, First, ReadOnly, RisingEdge, Timer

from ...scripts.board.cart_loader import (CMD_HOLD, CMD_PING, CMD_READ, CMD_RELEASE, CMD_SNAP_PIN, CMD_SNAP_READ,
                                  CMD_SNAP_RELEASE, CMD_WRITE, RAM_BYTES, ROM_BYTES, STATUS_BAD_ADDRESS,
                                  STATUS_BAD_ARGUMENT, STATUS_BAD_COMMAND, STATUS_BAD_CRC, STATUS_NO_FRAME,
                                  STATUS_NOT_HELD, STATUS_NOT_PINNED, STATUS_OK, address_field, crc16,
                                  decode_response, encode_request, response_total_length)
from .loader_model import SNAPSHOT_RECORD_BYTES, LoaderModel

CLOCK_PERIOD_NS = 40
CLKS_PER_BIT, RX_TIMEOUT_CLOCKS = 8, 4000     # the registry's parameters for this target
BYTE_CLOCKS = 10 * CLKS_PER_BIT
SYNC = 0xA5


def request_fields(word):
    return {'valid': word >> 31 & 1, 'write': word >> 30 & 1, 'ram': word >> 29 & 1,
            'address': word >> 8 & 0x1FFFFF, 'data': word & 0xFF}


class Store:
    """The backing's bytes as the bench's player serves them: seeded garbage
    until written (nothing is zero by assumption)."""

    def __init__(self, rng):
        self.rng = rng
        self.bytes = {}

    def read(self, ram, offset):
        key = (ram, offset % (RAM_BYTES if ram else ROM_BYTES))
        if key not in self.bytes:
            self.bytes[key] = self.rng.randrange(1, 256)
        return self.bytes[key]

    def write(self, ram, offset, byte):
        self.bytes[(ram, offset % (RAM_BYTES if ram else ROM_BYTES))] = byte


class Bench:
    def __init__(self, dut, seed):
        self.dut = dut
        self.rng = random.Random(seed)
        self.bound = int(os.environ['DV_MAX_CYCLES'])
        self.clock = 0
        self.store = Store(random.Random(seed ^ 0x5A5A))
        self.model = LoaderModel(self.store)
        self.received = []          # (clock, byte) from the loader
        self.ops = []               # accepted load-port operations
        self.stalled = 0
        self.slow = False
        # Display side of the snapshot, played by `display_player`.
        self.record = bytes(random.Random(seed ^ 0x3C3C).randrange(256) for _ in range(SNAPSHOT_RECORD_BYTES))
        self.model.record = self.record
        self.available = False
        self.pinned = False
        self.pins = self.releases = 0
        self.snap_reads = 0
        self.snap_stalled = 0
        self.drop_after = None      # lose the pin at this accepted snapshot request, never answering it

    def idle(self):
        d = self.dut
        d.host_tx_valid_i.value = 0
        d.host_tx_data_i.value = 0
        d.line_override_i.value = 0
        d.line_i.value = 1
        d.backing_initialized_i.value = 1
        d.load_ready_i.value = 0
        d.load_response_i.value = 0
        d.snapshot_pinned_i.value = 0
        d.snapshot_available_i.value = 0
        d.snapshot_ready_i.value = 0
        d.snapshot_response_i.value = 0

    def set_available(self, available):
        """Whether the display shows a complete frame (applies to the model too)."""
        self.available = available
        self.model.available = available

    async def ticker(self):
        while True:
            await RisingEdge(self.dut.clk_i)
            self.clock += 1
            assert self.clock <= self.bound, 'finite simulation bound exceeded'

    async def receiver(self):
        d = self.dut
        while True:
            await RisingEdge(d.host_rx_valid_o)
            await ReadOnly()
            self.received.append((self.clock, int(d.host_rx_data_o.value)))

    async def backing_player(self):
        """Serve the load port with randomized ready (0-3 edges) and response
        (1-4 edges) delays; `slow` forces the maxima."""
        d = self.dut
        ready_delay = 0
        pending = None                      # [edges left, response word]
        while True:
            await FallingEdge(d.clk_i)
            ready_now = ready_delay == 0
            d.load_ready_i.value = int(ready_now)
            d.load_response_i.value = pending[1] if pending and pending[0] == 0 else 0
            # The request the loader presents during this clock is what the
            # coming edge accepts; sample it before the edge.
            await ReadOnly()
            request = request_fields(int(d.load_request_o.value))
            await RisingEdge(d.clk_i)
            if pending is not None:
                if pending[0] == 0:
                    pending = None
                else:
                    pending[0] -= 1
            if request['valid'] and ready_now:
                assert pending is None, 'a second request while one is outstanding'
                if request['write']:
                    self.store.write(request['ram'], request['address'], request['data'])
                    data = 0
                else:
                    data = self.store.read(request['ram'], request['address'])
                self.ops.append((request['write'], request['ram'], request['address'], request['data'] if request['write'] else data))
                pending = [4 if self.slow else self.rng.randint(1, 4), 1 << 8 | data]
                pending[0] -= 1
                ready_delay = 3 if self.slow else self.rng.randint(0, 3)
            elif request['valid']:
                self.stalled += 1
                ready_delay -= 1

    async def display_player(self):
        """Play the display: the pin follows the loader's pulses (a pin needs
        a displayed frame), the byte port answers from the record with
        randomized ready (0-3 edges) and response (0-5 edges) delays, a
        request while not pinned is answered with zero, and `drop_after`
        simulates a display reset under a request: the pin vanishes and that
        request is never answered."""
        d = self.dut
        ready_delay = 0
        pending = None                      # [edges left, byte]
        while True:
            await FallingEdge(d.clk_i)
            ready_now = ready_delay == 0
            d.snapshot_ready_i.value = int(ready_now)
            d.snapshot_response_i.value = (1 << 8 | pending[1]) if pending and pending[0] == 0 else 0
            d.snapshot_pinned_i.value = int(self.pinned)
            d.snapshot_available_i.value = int(self.available)
            await ReadOnly()
            request = int(d.snapshot_request_o.value)
            pin, release = int(d.snapshot_pin_o.value), int(d.snapshot_release_o.value)
            await RisingEdge(d.clk_i)
            if pending is not None:
                pending = None if pending[0] == 0 else [pending[0] - 1, pending[1]]
            assert not (pin and release), 'pin and release together'
            if release:
                self.releases += 1
                self.pinned = False
            if pin:
                assert self.available or self.pinned, 'a pin request without a displayed frame'
                self.pins += 1
                self.pinned = True
            if request >> 13 & 1 and ready_now:
                assert pending is None, 'a second snapshot request while one is outstanding'
                assert self.pinned, 'a snapshot request while not pinned'
                offset = request & 0x1FFF
                self.snap_reads += 1
                if self.drop_after is not None and self.drop_after == 0:
                    self.drop_after = None
                    self.pinned = False
                    self.model.pinned = False
                    continue
                if self.drop_after is not None:
                    self.drop_after -= 1
                pending = [self.rng.randint(0, 5), self.record[offset]]
                ready_delay = self.rng.randint(0, 3)
            elif request >> 13 & 1:
                self.snap_stalled += 1
                ready_delay -= 1

    async def send_byte(self, byte):
        d = self.dut
        if not int(d.host_tx_ready_o.value):
            await RisingEdge(d.host_tx_ready_o)
        await FallingEdge(d.clk_i)
        d.host_tx_valid_i.value = 1
        d.host_tx_data_i.value = byte
        await RisingEdge(d.clk_i)
        await FallingEdge(d.clk_i)
        d.host_tx_valid_i.value = 0

    async def send(self, data):
        for byte in data:
            await self.send_byte(byte)

    async def send_bits(self, bits):
        """Drive the loader's receive line directly, one entry per bit time."""
        d = self.dut
        await FallingEdge(d.clk_i)
        d.line_override_i.value = 1
        for bit in bits:
            d.line_i.value = bit
            await ClockCycles(d.clk_i, CLKS_PER_BIT)
        d.line_i.value = 1
        await ClockCycles(d.clk_i, 2 * CLKS_PER_BIT)
        d.line_override_i.value = 0

    async def expect_response(self, expected, limit_clocks):
        """Collect exactly the expected response frame within the limit."""
        start = len(self.received)
        deadline = self.clock + limit_clocks
        while len(self.received) - start < len(expected):
            assert self.clock < deadline, (f'response timeout: {len(self.received) - start}/{len(expected)} bytes',
                                           bytes(b for _, b in self.received[start:]).hex())
            await RisingEdge(self.dut.clk_i)
        frame = bytes(b for _, b in self.received[start:])
        assert frame == expected, f'response {frame.hex()} expected {expected.hex()}'
        assert response_total_length(frame[:7]) == len(frame)
        return decode_response(frame)

    async def exchange(self, frame, expected_ops=None, settle=True):
        """Send one frame, expect the model's response and the backing
        operations the frame implies (the model's, or the caller's explicit
        list; none for a rejected frame)."""
        before = len(self.ops)
        expected = self.model.respond(frame)
        if expected_ops is None:
            expected_ops = self.model.last_ops
        else:
            assert expected_ops == self.model.last_ops, 'the bench and the model disagree on the implied operations'
        await self.send(frame)
        payload = len(frame) + len(expected)
        response = await self.expect_response(expected, BYTE_CLOCKS * (payload + 8) + 12 * len(frame) + 4000)
        if settle:
            await ClockCycles(self.dut.clk_i, 4)
        assert self.pinned == self.model.pinned, ('pin level', self.pinned, self.model.pinned, frame[:6].hex())
        ops = self.ops[before:]
        assert ops == expected_ops, (frame[:6].hex(), ops[:4], expected_ops[:4], len(ops), len(expected_ops))
        return response

    def write_ops(self, offset, data, ram=False):
        return [(1, int(ram), offset + i, byte) for i, byte in enumerate(data)]

    def read_ops(self, offset, length, ram=False):
        return [(0, int(ram), offset + i, self.store.read(int(ram), offset + i)) for i in range(length)]


def with_payload(response, payload):
    """A response frame with its payload replaced and the CRC recomputed."""
    body = response[1:7] + payload
    crc = crc16(body)
    return bytes([SYNC]) + body + bytes([crc & 0xFF, crc >> 8])


def corrupt(frame, index):
    return frame[:index] + bytes([frame[index] ^ 0x40]) + frame[index + 1:]


@cocotb.test(timeout_time=400, timeout_unit='ms')
async def loader_contract(dut):
    seed = int(os.environ['DV_SEED'])
    assert int(os.environ['DV_CLOCK_HZ']) == 25_000_000
    cocotb.log.info('nand2mario UART loader seed=%d', seed)
    b = Bench(dut, seed)
    b.idle()
    dut.reset_i.value = 1
    cocotb.start_soon(Clock(dut.clk_i, CLOCK_PERIOD_NS, unit='ns').start())
    for _ in range(3):
        await RisingEdge(dut.clk_i)
    await FallingEdge(dut.clk_i)
    dut.reset_i.value = 0
    cocotb.start_soon(b.ticker())
    cocotb.start_soon(b.receiver())
    cocotb.start_soon(b.backing_player())
    cocotb.start_soon(b.display_player())
    await ClockCycles(dut.clk_i, 4)
    rng = b.rng

    # PING: not held, initialized, no flags. Line noise before the frame is
    # ignored. An uninitialized backing shows in the status.
    response = await b.exchange(encode_request(CMD_PING), [])
    assert (response.code, response.held, response.initialized, response.status & 0x38) == (STATUS_OK, False, True, 0)
    assert int(dut.hold_o.value) == 0
    count = len(b.received)
    await b.send(bytes([0x00, 0xFF, 0x5A, 0x24]))
    await ClockCycles(dut.clk_i, 3 * BYTE_CLOCKS)
    assert len(b.received) == count, 'noise outside a frame produces no response'
    dut.backing_initialized_i.value = 0
    b.model.initialized = False
    assert not (await b.exchange(encode_request(CMD_PING), [])).initialized
    dut.backing_initialized_i.value = 1
    b.model.initialized = True

    # Writes and reads need the hold; HOLD raises the core reset request.
    response = await b.exchange(encode_request(CMD_WRITE, address_field(0x100), b'\x12'), [])
    assert response.code == STATUS_NOT_HELD and int(dut.hold_o.value) == 0
    response = await b.exchange(encode_request(CMD_READ, address_field(0x100)), [])
    assert response.code == STATUS_NOT_HELD
    response = await b.exchange(encode_request(CMD_HOLD), [])
    assert response.ok and response.held and int(dut.hold_o.value) == 1
    assert (await b.exchange(encode_request(CMD_HOLD), [])).held, 'HOLD is idempotent'

    # WRITE 1, 7 and 256 bytes including both ends of the ROM space; every
    # byte reaches the load port in order; READ returns them.
    for offset, data in ((0x0147, b'\x03'), (0x0000, bytes(range(7))), (ROM_BYTES - 256, bytes(rng.randrange(256) for _ in range(256))),
                         (0x3FFF, bytes(rng.randrange(256) for _ in range(2)))):
        response = await b.exchange(encode_request(CMD_WRITE, address_field(offset), data), b.write_ops(offset, data))
        assert response.ok and response.length == len(data) and response.offset == offset and not response.ram
        response = await b.exchange(encode_request(CMD_READ, address_field(offset), length=len(data)), b.read_ops(offset, len(data)))
        assert response.ok and response.payload == data, (response.payload.hex(), data.hex())
    # Cartridge RAM through the RAM bit, both ends.
    for offset, data in ((0x0000, b'\x50\x60'), (RAM_BYTES - 256, bytes(rng.randrange(256) for _ in range(256)))):
        response = await b.exchange(encode_request(CMD_WRITE, address_field(offset, ram=True), data), b.write_ops(offset, data, True))
        assert response.ok and response.ram and response.offset == offset
        response = await b.exchange(encode_request(CMD_READ, address_field(offset, ram=True), length=len(data)), b.read_ops(offset, len(data), True))
        assert response.ok and response.payload == data
    # ROM and RAM bytes at one offset are distinct stores.
    response = await b.exchange(encode_request(CMD_READ, address_field(0), length=2), b.read_ops(0, 2))
    assert response.payload == bytes(range(2))
    response = await b.exchange(encode_request(CMD_READ, address_field(0, ram=True), length=2), b.read_ops(0, 2, True))
    assert response.payload == b'\x50\x60'

    # Error classes: bad CRC (payload and CRC byte corrupted) commits
    # nothing and the frame data are echoed; unknown command; arguments on
    # argument-free commands; ranges past either space and reserved bits.
    good = encode_request(CMD_WRITE, address_field(0x200), bytes(range(16)))
    for index in (7, len(good) - 1, len(good) - 2):
        response = await b.exchange(corrupt(good, index), [])
        assert response.code == STATUS_BAD_CRC, response.describe()
    response = await b.exchange(encode_request(CMD_READ, address_field(0x200), length=16), b.read_ops(0x200, 16))
    assert response.ok, 'the corrupted writes left the bytes untouched (garbage still there)'
    response = await b.exchange(corrupt(encode_request(CMD_RELEASE), 7), [])
    assert response.code == STATUS_BAD_CRC and response.held and int(dut.hold_o.value) == 1, 'a corrupted RELEASE does not release'
    for command in (0x00, 0x09, 0x7F, 0xFF):
        response = await b.exchange(encode_request(command), [])
        assert (response.code, response.command) == (STATUS_BAD_COMMAND, command)
    assert (await b.exchange(encode_request(CMD_PING, length=4), [])).code == STATUS_BAD_ARGUMENT
    assert (await b.exchange(encode_request(CMD_HOLD, address=0x10), [])).code == STATUS_BAD_ARGUMENT
    assert (await b.exchange(encode_request(CMD_RELEASE, address=1 << 21), [])).code == STATUS_BAD_ARGUMENT
    assert int(dut.hold_o.value) == 1
    for address, length in ((ROM_BYTES - 255, 256), (ROM_BYTES - 1, 2), (address_field(RAM_BYTES - 255, ram=True), 256),
                            (address_field(RAM_BYTES - 1, ram=True), 2), (1 << 22, 1), (1 << 23 | 5, 1), (address_field(0, ram=True) | 1 << 20, 1)):
        response = await b.exchange(encode_request(CMD_READ, address, length=length), [])
        assert response.code == STATUS_BAD_ADDRESS, (hex(address), length, response.describe())
        response = await b.exchange(encode_request(CMD_WRITE, address, bytes(length)), [])
        assert response.code == STATUS_BAD_ADDRESS, (hex(address), length, response.describe())
    # The last valid byte of each space is reachable.
    for address in (ROM_BYTES - 256, address_field(RAM_BYTES - 1, ram=True)):
        length = 256 if address == ROM_BYTES - 256 else 1
        assert (await b.exchange(encode_request(CMD_READ, address, length=length))).ok

    # Framing error inside a frame: no response, the frame is dropped, the
    # next response reports it once; a byte with a bad stop bit outside a
    # frame is reported too.
    await b.send(bytes([SYNC, CMD_WRITE, 0x00]))
    await b.send_bits([0, 1, 0, 1, 0, 1, 0, 1, 0, 0])      # start, 8 data bits, low stop bit
    b.model.event('framing')
    await ClockCycles(dut.clk_i, 4 * BYTE_CLOCKS)
    count = len(b.received)
    response = await b.exchange(encode_request(CMD_PING), [])
    assert response.framing and len(b.received) == count + 9, 'the aborted frame produced no response'
    assert not (await b.exchange(encode_request(CMD_PING), [])).framing
    await b.send_bits([0, 1, 1, 1, 1, 1, 1, 1, 1, 0])
    b.model.event('framing')
    await ClockCycles(dut.clk_i, 2 * BYTE_CLOCKS)
    assert (await b.exchange(encode_request(CMD_PING), [])).framing
    # Receive timeout: a partial frame left idle is dropped and flagged.
    await b.send(bytes([SYNC, CMD_READ, 0x10, 0x00]))
    await ClockCycles(dut.clk_i, RX_TIMEOUT_CLOCKS + 2 * BYTE_CLOCKS)
    b.model.event('timeout')
    count = len(b.received)
    response = await b.exchange(encode_request(CMD_PING), [])
    assert response.timeout and not response.framing and len(b.received) == count + 9
    assert not (await b.exchange(encode_request(CMD_PING), [])).timeout
    # A frame sent slowly, but inside the timeout, still completes.
    frame = encode_request(CMD_READ, address_field(0x0147))
    expected = b.model.respond(frame)
    for index, byte in enumerate(frame):
        if index:
            await ClockCycles(dut.clk_i, RX_TIMEOUT_CLOCKS - 4 * BYTE_CLOCKS)
        await b.send_byte(byte)
    await b.expect_response(expected, 4000)
    # Overrun: bytes arriving while a WRITE is committed (slow backing) are
    # dropped and flagged in the next response; the WRITE itself completes.
    b.slow = True
    frame = encode_request(CMD_WRITE, address_field(0x1000), bytes(rng.randrange(256) for _ in range(256)))
    expected = b.model.respond(frame)
    await b.send(frame)
    await b.send(encode_request(CMD_PING))
    b.model.event('overrun')
    await b.expect_response(expected, 40000)
    await ClockCycles(dut.clk_i, 3 * BYTE_CLOCKS)
    b.slow = False
    count = len(b.received)
    response = await b.exchange(encode_request(CMD_PING), [])
    assert response.overrun and len(b.received) == count + 9, 'the dropped PING produced no response'
    assert not (await b.exchange(encode_request(CMD_PING), [])).overrun
    response = await b.exchange(encode_request(CMD_READ, address_field(0x1000), length=256), b.read_ops(0x1000, 256))
    assert response.payload == frame[6:-2]

    # RELEASE drops the hold; reload without reset: hold again and rewrite
    # the header area, the new bytes read back.
    response = await b.exchange(encode_request(CMD_RELEASE), [])
    assert response.ok and not response.held and int(dut.hold_o.value) == 0
    assert (await b.exchange(encode_request(CMD_RELEASE), [])).ok, 'RELEASE is idempotent'
    assert (await b.exchange(encode_request(CMD_WRITE, address_field(0x147), b'\x01'), [])).code == STATUS_NOT_HELD
    assert (await b.exchange(encode_request(CMD_HOLD), [])).held and int(dut.hold_o.value) == 1
    await b.exchange(encode_request(CMD_WRITE, address_field(0x147), b'\x01\x02\x03'), b.write_ops(0x147, b'\x01\x02\x03'))
    response = await b.exchange(encode_request(CMD_READ, address_field(0x147), length=3), b.read_ops(0x147, 3))
    assert response.payload == b'\x01\x02\x03'

    # Frame snapshot. No frame displayed: SNAP_PIN answers NO_FRAME and pins
    # nothing, SNAP_READ NOT_PINNED, SNAP_RELEASE is harmless. Arguments on
    # the control commands are rejected; a corrupted SNAP_PIN pins nothing.
    assert int(dut.hold_o.value) == 1, 'the snapshot commands work while the core is held'
    assert (await b.exchange(encode_request(CMD_SNAP_PIN), [])).code == STATUS_NO_FRAME
    assert (await b.exchange(encode_request(CMD_SNAP_READ, 0, length=8), [])).code == STATUS_NOT_PINNED
    assert (await b.exchange(encode_request(CMD_SNAP_RELEASE), [])).ok
    b.set_available(True)
    assert (await b.exchange(encode_request(CMD_SNAP_PIN, address=1), [])).code == STATUS_BAD_ARGUMENT
    assert (await b.exchange(encode_request(CMD_SNAP_RELEASE, length=2), [])).code == STATUS_BAD_ARGUMENT
    assert (await b.exchange(corrupt(encode_request(CMD_SNAP_PIN), 3), [])).code == STATUS_BAD_CRC
    assert b.pins == 0 and not b.pinned
    # Pin, idempotent pin (also once the display went blank), the whole
    # record in 256-byte chunks, both range ends.
    assert (await b.exchange(encode_request(CMD_SNAP_PIN), [])).ok and b.pinned and b.pins == 1
    b.set_available(False)
    assert (await b.exchange(encode_request(CMD_SNAP_PIN), [])).ok and b.pinned, 'a held pin is kept'
    record = b''
    for offset in range(0, SNAPSHOT_RECORD_BYTES, 256):
        length = min(256, SNAPSHOT_RECORD_BYTES - offset)
        record += (await b.exchange(encode_request(CMD_SNAP_READ, offset, length=length), [])).payload
    assert record == b.record, 'the whole record over SNAP_READ'
    assert (await b.exchange(encode_request(CMD_SNAP_READ, SNAPSHOT_RECORD_BYTES - 1), [])).payload == b.record[-1:]
    for address, length in ((SNAPSHOT_RECORD_BYTES - 1, 2), (SNAPSHOT_RECORD_BYTES, 1), (SNAPSHOT_RECORD_BYTES - 255, 256),
                            (1 << 23, 1), (1 << 21, 1)):
        response = await b.exchange(encode_request(CMD_SNAP_READ, address, length=length), [])
        assert response.code == STATUS_BAD_ADDRESS, (hex(address), length, response.describe())
    # A pin lost under a payload (the display reset with the core): the bytes
    # already answered are the record's, the rest are zero, the status said
    # OK; afterwards the loader answers NOT_PINNED. Once with the request
    # outstanding (the wait escape), once more on the next request.
    for drop_after in (37, 0):
        b.set_available(True)
        assert (await b.exchange(encode_request(CMD_SNAP_PIN), [])).ok and b.pinned
        frame = encode_request(CMD_SNAP_READ, 1000, length=200)
        expected = b.model.respond(frame)
        b.model.pinned = True
        expected = with_payload(expected, b.record[1000:1000 + drop_after] + bytes(200 - drop_after))
        b.drop_after = drop_after
        await b.send(frame)
        response = await b.expect_response(expected, BYTE_CLOCKS * 230 + 4000)
        assert response.ok and not b.pinned and b.drop_after is None
        b.model.pinned = False
        assert (await b.exchange(encode_request(CMD_SNAP_READ, 0, length=8), [])).code == STATUS_NOT_PINNED
    # SNAP_READ and the pin do not need the hold; RELEASE of the pin.
    assert (await b.exchange(encode_request(CMD_RELEASE), [])).ok and int(dut.hold_o.value) == 0
    b.set_available(True)
    assert (await b.exchange(encode_request(CMD_SNAP_PIN), [])).ok
    response = await b.exchange(encode_request(CMD_SNAP_READ, 0, length=8), [])
    assert response.payload == b.record[:8]
    releases = b.releases
    assert (await b.exchange(encode_request(CMD_SNAP_RELEASE), [])).ok and not b.pinned and b.releases == releases + 1
    assert (await b.exchange(encode_request(CMD_HOLD), [])).held
    cocotb.log.info('snapshot commands verified: %d snapshot reads, %d stalled edges', b.snap_reads, b.snap_stalled)

    # Negative control for the bench itself: the comparison rejects a
    # response that differs in one byte.
    try:
        await b.expect_response(corrupt(b.model.respond(encode_request(CMD_PING)), 1), 1)
    except AssertionError:
        pass
    else:
        raise AssertionError('the response comparison accepted a wrong frame')

    # Randomized phase: valid and invalid frames with their predicted
    # responses and backing operations; coverage asserted below.
    codes = {code: 0 for code in range(8)}
    ok_writes = ok_reads = ok_snap_reads = 0
    lengths, spaces = set(), set()
    stalled_before = b.stalled
    for _ in range(300):
        kind = rng.choices(('write', 'read', 'badcrc', 'badcmd', 'badarg', 'badaddr', 'hold', 'release',
                            'snappin', 'snaprelease', 'snapread', 'snapbad', 'snapunpinned'),
                           weights=(28, 24, 8, 5, 5, 8, 8, 8, 16, 8, 14, 4, 8))[0]
        if rng.random() < 0.15:
            b.set_available(not b.available)
        ram = rng.random() < 0.3
        space = RAM_BYTES if ram else ROM_BYTES
        length = rng.choice((1, 2, rng.randint(1, 256), 255, 256))
        offset = rng.choice((0, space - length, rng.randrange(0, space - length + 1)))
        if kind == 'write':
            data = bytes(rng.randrange(256) for _ in range(length))
            frame = encode_request(CMD_WRITE, address_field(offset, ram), data)
        elif kind == 'read':
            frame = encode_request(CMD_READ, address_field(offset, ram), length=length)
        elif kind == 'badcrc':
            data = bytes(rng.randrange(256) for _ in range(length))
            frame = encode_request(CMD_WRITE, address_field(offset, ram), data)
            frame = corrupt(frame, rng.randrange(6, len(frame)))
        elif kind == 'badcmd':
            frame = encode_request(rng.choice((0, 9, 0x7F, 0x80, 0xFF)), rng.randrange(1 << 24), length=rng.randint(1, 256))
        elif kind == 'badarg':
            frame = encode_request(rng.choice((CMD_PING, CMD_HOLD, CMD_RELEASE, CMD_SNAP_PIN, CMD_SNAP_RELEASE)),
                                   rng.choice((0, rng.randrange(1, 1 << 24))),
                                   length=rng.choice((1, rng.randint(2, 256))))
            if frame[2:6] == bytes(4):
                frame = encode_request(CMD_PING, length=2)
        elif kind == 'badaddr':
            # Past the end of the selected space (the range end is what the
            # loader checks) or with a reserved address bit set.
            address = rng.choice((space - length + 1 + rng.randrange(0, 4) | (ram << 21), rng.randrange(1 << 22, 1 << 24)))
            command = rng.choice((CMD_READ, CMD_WRITE))
            frame = encode_request(command, address, bytes(length) if command == CMD_WRITE else b'', length=length)
        elif kind == 'snappin':
            # Whether a frame is displayed is the pin's real input. Half the
            # pins find nothing to pin (released first, display blank).
            if rng.random() < 0.5:
                if b.model.pinned:
                    response = await b.exchange(encode_request(CMD_SNAP_RELEASE), settle=False)
                    codes[response.code] += 1
                b.set_available(False)
            else:
                b.set_available(True)
            frame = encode_request(CMD_SNAP_PIN)
        elif kind == 'snaprelease':
            frame = encode_request(CMD_SNAP_RELEASE)
        elif kind == 'snapread':
            if not b.model.pinned and rng.random() < 0.8:
                # Mostly read a pinned frame: pin one first (its own frame).
                b.set_available(True)
                response = await b.exchange(encode_request(CMD_SNAP_PIN), settle=False)
                codes[response.code] += 1
            snap_length = rng.choice((1, 8, rng.randint(1, 256), 256))
            frame = encode_request(CMD_SNAP_READ, rng.choice((0, SNAPSHOT_RECORD_BYTES - snap_length,
                                                              rng.randrange(SNAPSHOT_RECORD_BYTES - snap_length + 1))),
                                   length=snap_length)
        elif kind == 'snapunpinned':
            # A read after the release: NOT_PINNED.
            if b.model.pinned:
                response = await b.exchange(encode_request(CMD_SNAP_RELEASE), settle=False)
                codes[response.code] += 1
            frame = encode_request(CMD_SNAP_READ, rng.randrange(SNAPSHOT_RECORD_BYTES - 7), length=8)
        elif kind == 'snapbad':
            snap_length = rng.randint(1, 256)
            frame = encode_request(CMD_SNAP_READ, rng.choice((SNAPSHOT_RECORD_BYTES - snap_length + 1 + rng.randrange(4),
                                                              rng.randrange(1 << 14, 1 << 24))), length=snap_length)
        elif kind == 'hold':
            frame = encode_request(CMD_HOLD)
        else:
            frame = encode_request(CMD_RELEASE)
        response = await b.exchange(frame, settle=False)
        codes[response.code] += 1
        assert int(dut.hold_o.value) == int(b.model.held)
        if response.ok and response.command == CMD_WRITE:
            ok_writes += 1
            lengths.add(response.length)
            spaces.add(response.ram)
        if response.ok and response.command == CMD_READ:
            ok_reads += 1
            lengths.add(response.length)
        if response.ok and response.command == CMD_SNAP_READ:
            ok_snap_reads += 1
    assert ok_writes >= 15 and ok_reads >= 12 and ok_snap_reads >= 8, (ok_writes, ok_reads, ok_snap_reads)
    assert min(codes.values()) >= 3, codes
    assert {1, 256} <= lengths and spaces == {True, False}, (lengths, spaces)
    assert b.stalled - stalled_before >= 50, 'the backing player stalled the loader'
    assert b.snap_stalled >= 20, 'the display player stalled the snapshot reads'
    cocotb.log.info('UART loader checks passed seed=%d clocks=%d codes=%s ok_writes=%d ok_reads=%d ok_snap_reads=%d',
                    seed, b.clock, codes, ok_writes, ok_reads, ok_snap_reads)
