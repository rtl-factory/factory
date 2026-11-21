"""UART memory endpoint through a real nand2doom board top (wiki/src/nand2doom/dv/io/dv-plan.md).

Load transaction (identify, hold, write and verify, entry, release) of a
small program over the board UART, then live reads of the front framebuffer
view and the scanned palette while the program keeps running and writing
the framebuffer port. The host side is the bench library codec."""
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, Timer, with_timeout
from cocotb.utils import get_sim_time

from src.nand2doom.dv.memory.sdram_pin_model import WRITE, SdramPinModel
from src.common.scripts.uart_mem import uart_mem as um

PATTERN = (0x0403_0201, 0x0807_0605)
COLOR = 0x56C
COUNTER = 0x100
SCREEN, PALETTE = 0x1002_0000, 0x1003_0000


def _u(opcode, rd, imm20):
    return ((imm20 & 0xFFFFF) << 12) | (rd << 7) | opcode


def _i(opcode, rd, funct3, rs1, imm):
    return ((imm & 0xFFF) << 20) | (rs1 << 15) | (funct3 << 12) | (rd << 7) | opcode


def _s(rs2, rs1, imm):
    return (((imm >> 5) & 0x7F) << 25) | (rs2 << 20) | (rs1 << 15) | (2 << 12) | \
        ((imm & 0x1F) << 7) | 0x23


def _jal(offset):
    imm = offset & 0x1FFFFF
    return (((imm >> 20) & 1) << 31) | (((imm >> 1) & 0x3FF) << 21) | \
        (((imm >> 11) & 1) << 20) | (((imm >> 12) & 0xFF) << 12) | 0x6F


# Why: palette entry 0 and two back-bank words, then one palette commit plus
# swap (video control +8 = 3); afterwards an endless loop counts at COUNTER
# in SDRAM and keeps writing the back bank, so the live reads below contend
# with a running CPU.
PROGRAM = (
    _u(0x37, 7, 0x10010),            # lui  x7, palette
    _i(0x13, 6, 0, 0, COLOR),        # addi x6, x0, COLOR
    _s(6, 7, 0),                     # sw   x6, 0(x7)
    _u(0x37, 5, 0x10000),            # lui  x5, framebuffer back bank
    _u(0x37, 6, 0x04030),            # lui  x6, 0x04030
    _i(0x13, 6, 0, 6, 0x201),        # addi x6, x6, 0x201
    _s(6, 5, 0),                     # sw   x6, 0(x5)
    _u(0x37, 6, 0x08070),            # lui  x6, 0x08070
    _i(0x13, 6, 0, 6, 0x605),        # addi x6, x6, 0x605
    _s(6, 5, 4),                     # sw   x6, 4(x5)
    _u(0x37, 8, 0x20001),            # lui  x8, video control
    _i(0x13, 9, 0, 0, 3),            # addi x9, x0, 3: swap and commit
    _s(9, 8, 8),                     # sw   x9, 8(x8)
    _i(0x13, 10, 0, 10, 1),          # loop: addi x10, x10, 1
    _s(10, 0, COUNTER),              # sw   x10, COUNTER(x0)
    _s(10, 5, 64),                   # sw   x10, 64(x5): back bank
    _jal(-12),                       # j    loop
    0x0000_0013,                     # nop (whole-word padding)
)
IMAGE = b"".join(word.to_bytes(4, "little") for word in PROGRAM)


LIVE_GAP = 64


class LiveMonitor:
    """Samples n2d_system every clock while the CPU runs: each CPU wait on the
    framebuffer or palette port must coincide with an endpoint screen read,
    screen reads are at least LIVE_GAP clocks apart, the stall count stays
    within one per LIVE_GAP clocks, and the CPU is never halted."""

    def __init__(self, system):
        self.s = system
        self.cycles = self.stalls = self.cpu_port_accesses = 0
        self.screen_cycles = []
        self.halted_cycles = 0
        self.task = None

    async def _run(self):
        s = self.s
        while True:
            await FallingEdge(s.clk_i)
            self.cycles += 1
            port = int(s.framebuffer_address_selected.value) or \
                int(s.palette_address_selected.value)
            cpu = int(s.data_request_valid.value) and port
            screen = int(s.screen_framebuffer_valid.value) or int(s.screen_palette_valid.value)
            if screen:
                self.screen_cycles.append(self.cycles)
            if cpu and int(s.data_request_ready.value):
                self.cpu_port_accesses += 1
            if cpu and not int(s.data_request_ready.value):
                assert screen, f"CPU port wait without a screen read at clock {self.cycles}"
                self.stalls += 1
            if int(s.halt_request.value):
                self.halted_cycles += 1

    def start(self):
        self.task = cocotb.start_soon(self._run())

    def check(self):
        self.task.cancel()
        gaps = [b - a for a, b in zip(self.screen_cycles, self.screen_cycles[1:])]
        assert self.screen_cycles and min(gaps, default=LIVE_GAP) >= LIVE_GAP, \
            f"screen reads {min(gaps, default=None)} clocks apart"
        assert self.stalls <= self.cycles // LIVE_GAP + 1, (self.stalls, self.cycles)
        assert self.halted_cycles == 0, f"CPU halted for {self.halted_cycles} clocks"
        assert self.cpu_port_accesses > 1000, "the program did not contend for the port"
        return self.stalls


class BoardLink:
    """Bit-exact host UART on the board pins plus the external SDRAM BFM."""

    def __init__(self, dut, *, clock, reset, rx, tx, baud, dqm):
        self.dut, self.rx, self.tx = dut, getattr(dut, rx), getattr(dut, tx)
        self.clock, self.reset, self.dqm = getattr(dut, clock), getattr(dut, reset), dqm
        self.bit_ps = 10**12 / baud
        self.model = SdramPinModel()
        self.received = bytearray()

    async def start(self):
        dut = self.dut
        self.reset.value = 0
        self.rx.value = 1
        dut.PS2_CLK.value = 1
        dut.PS2_DAT.value = 1
        dut.dram_drive_i.value = 0
        dut.dram_data_i.value = 0
        cocotb.start_soon(Clock(self.clock, 20, unit="ns").start())
        await Timer(100, unit="ns")
        self.reset.value = 1
        cocotb.start_soon(self._sdram())
        cocotb.start_soon(self._receiver())
        await Timer(250, unit="us")
        assert int(dut.LEDR.value) & 7 == 7, "PLL/init/halt status after reset"

    async def _read_return(self, beats):
        await Timer(46, unit="ns")
        for index, value in enumerate(beats):
            self.dut.dram_data_i.value = value
            self.dut.dram_drive_i.value = 1
            await Timer(34 if index == len(beats) - 1 else 40, unit="ns")
        self.dut.dram_drive_i.value = 0

    async def _sdram(self):
        dut, edge = self.dut, 0
        while True:
            await RisingEdge(dut.DRAM_CLK)
            edge += 1
            cmd = (int(dut.DRAM_RAS_N.value), int(dut.DRAM_CAS_N.value), int(dut.DRAM_WE_N.value))
            if cmd == WRITE or self.model.write_burst is not None:
                assert not int(dut.dram_drive_i.value), "DQ bus contention"
            beats = self.model.edge(edge, cmd, int(dut.DRAM_BA.value), int(dut.DRAM_ADDR.value),
                                    int(dut.dram_data_o.value), self.dqm(dut))
            if beats is not None:
                cocotb.start_soon(self._read_return(beats))

    async def _receiver(self):
        while True:
            await FallingEdge(self.tx)
            start = get_sim_time(unit="ps")
            value = 0
            for bit in range(9):
                await Timer(start + round((bit + 1.5) * self.bit_ps) - get_sim_time(unit="ps"),
                            unit="ps")
                if bit < 8:
                    value |= int(self.tx.value) << bit
            assert int(self.tx.value), "UART stop bit"
            self.received.append(value)

    async def send(self, data):
        start, index = get_sim_time(unit="ps"), 0
        for value in data:
            for bit in (0, *((value >> i) & 1 for i in range(8)), 1):
                self.rx.value = bit
                index += 1
                await Timer(start + round(index * self.bit_ps) - get_sim_time(unit="ps"), unit="ps")

    async def response(self):
        while True:
            buffer = self.received
            start = buffer.find(bytes([um.RESPONSE_SOF]))
            if start >= 0 and len(buffer) >= start + 6:
                total = 6 + int.from_bytes(buffer[start + 4:start + 6], "little") + 5
                if len(buffer) >= start + total:
                    frame = bytes(buffer[start:start + total])
                    del buffer[:start + total]
                    return frame
            await Timer(round(self.bit_ps * 10), unit="ps")


async def exchange(link, command, address=0, length=0, payload=b"", expect=um.OK,
                   timeout_us=20000):
    link.seq = (getattr(link, "seq", 0) + 1) & 0xFF
    await link.send(um.encode_request(command, link.seq, address, length, payload))
    frame = await with_timeout(cocotb.start_soon(link.response()), timeout_us, "us")
    response = um.decode_response(frame)
    assert (response.command, response.seq) == (command, link.seq), response
    assert response.status == expect, f"command 0x{command:02x} status {response.status}"
    return response.payload


async def endpoint_load_and_readback(link, half_width=False):
    identity = um.parse_identity(await exchange(link, um.IDENTIFY))
    assert identity.project == "n2d " and identity.max_length == 4096
    assert [(w.base, w.size, w.attributes) for w in identity.windows] == [
        (0, 0x0400_0000, 3), (SCREEN, 64000, 6), (PALETTE, 0x400, 6)]
    assert identity.held, "the engine holds the CPU from reset"

    await exchange(link, um.HOLD)
    await exchange(link, um.WRITE, 0, len(IMAGE), IMAGE)
    assert await exchange(link, um.READ, 0, len(IMAGE)) == IMAGE
    await exchange(link, um.ENTRY, 0)
    await exchange(link, um.RELEASE)
    running = um.parse_identity(await exchange(link, um.IDENTIFY))
    assert not running.held and not running.hold_requested
    monitor = LiveMonitor(link.dut.u_board.u_system)
    monitor.start()

    # Ref: the bank swap and palette commit land at the next vertical blank;
    # poll the live front view until the program's words are displayed. A
    # half-width bank stores only even-x bytes and reads each one for both
    # source pixels it represents (wiki/src/nand2doom/rtl/video/mas.md).
    expected = b"".join(w.to_bytes(4, "little") for w in PATTERN)
    if half_width:
        expected = bytes(expected[i & ~1] for i in range(len(expected)))
    cocotb.log.info("endpoint: program loaded and released; polling the front view")
    for _ in range(200):
        front = await exchange(link, um.READ, SCREEN, 8)
        if front == expected:
            break
    else:
        raise AssertionError(f"front view never showed the program's words: {front.hex()}")
    palette = await exchange(link, um.READ, PALETTE, 4)
    assert int.from_bytes(palette, "little") == COLOR, palette.hex()
    # Why: a 1 KiB read of the displayed bank contends with the running program,
    # so a starved CPU or a stalled read shows.
    frame = await exchange(link, um.READ, SCREEN, 1024, timeout_us=40000)
    assert frame[:8] == expected
    await exchange(link, um.READ, COUNTER, 4, expect=0x07)  # Ref: io MAS "UART memory endpoint": SDRAM reads need a hold (0x07, not halted)
    stalls = monitor.check()
    cocotb.log.info("live phase: %d clocks, %d screen reads, %d CPU port accesses, "
                    "%d stall clocks (bound %d)", monitor.cycles, len(monitor.screen_cycles),
                    monitor.cpu_port_accesses, stalls, monitor.cycles // LIVE_GAP + 1)

    # Why: a stopped program would leave the counter unchanged, so the hold
    # below proves it kept running through the live reads.
    await exchange(link, um.HOLD)
    counter = int.from_bytes(await exchange(link, um.READ, COUNTER, 4), "little")
    assert counter > 1000, f"program loop count {counter}"
    cocotb.log.info("endpoint: loaded %d bytes, live front view and palette, loop count %d",
                    len(IMAGE), counter)
