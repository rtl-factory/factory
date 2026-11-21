"""Independent Python model of the nand2mario UART cartridge loader protocol,
written from the uart HAS, never from the RTL. Given the exact bytes of a
request frame it predicts the response frame and the loader's state change
(hold level, backing writes, the snapshot pin), including every error class
and the sticky status flags; the bench adds framing errors, timeouts and
overruns through `event` and sets whether the display shows a frame
(`available`) and the pinned frame's record. The host tool's CRC is a protocol fact shared with the encoder; its
check value is pinned by the tool's unit test."""
from ...scripts.board.cart_loader import (CMD_HOLD, CMD_READ, CMD_RELEASE, CMD_WRITE, CMD_PING, CMD_SNAP_PIN,
                                  CMD_SNAP_READ, CMD_SNAP_RELEASE, SYNC, ADDRESS_RAM_BIT, RAM_BYTES, ROM_BYTES,
                                  STATUS_BAD_ADDRESS, STATUS_BAD_ARGUMENT, STATUS_BAD_COMMAND, STATUS_BAD_CRC,
                                  STATUS_NO_FRAME, STATUS_NOT_HELD, STATUS_NOT_PINNED, STATUS_OK, crc16)

KNOWN = {CMD_PING, CMD_HOLD, CMD_RELEASE, CMD_WRITE, CMD_READ, CMD_SNAP_PIN, CMD_SNAP_RELEASE, CMD_SNAP_READ}
CONTROL = {CMD_PING, CMD_HOLD, CMD_RELEASE, CMD_SNAP_PIN, CMD_SNAP_RELEASE}
SNAPSHOT_RECORD_BYTES = 5768      # Ref: board spec "Frame snapshot": an eight-byte header plus 160x144 two-bit shades.


class LoaderModel:
    def __init__(self, store, initialized=True):
        """`store` answers `read(ram, offset)` and `write(ram, offset, byte)`
        for the backing the loader is attached to."""
        self.store = store
        self.initialized = initialized
        self.held = False
        self.framing = self.timeout = self.overrun = False
        self.available = False
        self.pinned = False
        self.record = bytes(SNAPSHOT_RECORD_BYTES)
        self.last_ops = []

    def event(self, kind):
        """A line event the bench caused: 'framing', 'timeout' or 'overrun'."""
        setattr(self, kind, True)

    def status_byte(self, code):
        return ((self.held << 7) | (self.initialized << 6) | (self.framing << 5) | (self.timeout << 4)
                | (self.overrun << 3) | code)

    @staticmethod
    def address_valid(address, length):
        offset, ram = address & (ROM_BYTES - 1), address >> ADDRESS_RAM_BIT & 1
        if address >> 22:
            return False
        return offset + length <= (RAM_BYTES if ram else ROM_BYTES)

    def respond(self, frame):
        """Expected response bytes to one complete, well-delimited request
        frame (SYNC first, CRC last, payload present exactly when the command
        byte says WRITE), applying the command's effect."""
        assert frame[0] == SYNC
        body, crc = frame[1:-2], frame[-1] << 8 | frame[-2]
        command, length = body[0], body[4] + 1
        address = body[1] | body[2] << 8 | body[3] << 16
        payload = body[5:]
        if crc16(body) != crc:
            code = STATUS_BAD_CRC
        elif command not in KNOWN:
            code = STATUS_BAD_COMMAND
        elif command in CONTROL and (address or body[4]):
            code = STATUS_BAD_ARGUMENT
        elif command in (CMD_WRITE, CMD_READ) and not self.held:
            code = STATUS_NOT_HELD
        elif command == CMD_SNAP_READ and not self.pinned:
            code = STATUS_NOT_PINNED
        elif command in (CMD_WRITE, CMD_READ) and not self.address_valid(address, length):
            code = STATUS_BAD_ADDRESS
        elif command == CMD_SNAP_READ and address + length > SNAPSHOT_RECORD_BYTES:
            code = STATUS_BAD_ADDRESS
        elif command == CMD_SNAP_PIN and not self.pinned and not self.available:
            code = STATUS_NO_FRAME
        else:
            code = STATUS_OK
        ram, offset = address >> ADDRESS_RAM_BIT & 1, address & (ROM_BYTES - 1)
        data = b''
        self.last_ops = []
        if code == STATUS_OK:
            if command == CMD_HOLD:
                self.held = True
            elif command == CMD_RELEASE:
                self.held = False
            elif command == CMD_WRITE:
                for index, byte in enumerate(payload):
                    self.store.write(ram, offset + index, byte)
                    self.last_ops.append((1, ram, offset + index, byte))
            elif command == CMD_READ:
                data = bytes(self.store.read(ram, offset + index) for index in range(length))
                self.last_ops = [(0, ram, offset + index, byte) for index, byte in enumerate(data)]
            elif command == CMD_SNAP_PIN:
                self.pinned = True
            elif command == CMD_SNAP_RELEASE:
                self.pinned = False
            elif command == CMD_SNAP_READ:
                data = self.record[address:address + length]
        response_body = bytes([self.status_byte(code), command, body[1], body[2], body[3], body[4]]) + data
        self.framing = self.timeout = self.overrun = False
        response_crc = crc16(response_body)
        return bytes([SYNC]) + response_body + bytes([response_crc & 0xFF, response_crc >> 8])
