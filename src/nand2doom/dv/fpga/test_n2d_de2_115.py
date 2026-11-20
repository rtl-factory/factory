"""DE2-115 board wrapper: low-chip SDRAM BFM, JP5 UART, PS/2, JTAG keyboard and ADV7123 VGA."""
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, Timer, with_timeout
from cocotb.utils import get_sim_time

import zlib

from src.nand2doom.dv.fpga.board_jtag import check_jtag_keys
from src.nand2doom.dv.memory.sdram_pin_model import NOP, WRITE, SdramPinModel, join
import src.nand2doom.scripts.n2d_uart_debug_host as wire
from src.nand2doom.scripts.n2d_uart_debug_host import (
    CMD_HALT, CMD_PING, CMD_POKE, CMD_PEEK, CMD_RESUME, request, decode_response,
)

PIXEL_NS = 40
LINE_NS = 800 * PIXEL_NS
PALETTE_ENTRY = 0x1001_0000
FRAMEBUFFER = 0x1000_0000
RESULT = 0x100
COLOR = 0x96c  # RGB444 palette entry 0: red 9, green 6, blue 12
SCAN_CODE = 0x1c
# The board top runs the whole debug link at the 3 Mbaud load rate. The host
# side uses exact absolute-time bit edges, not 25 MHz clock multiples.
BAUD = 3_000_000
BIT_PS = 10**12 / BAUD
STREAM_ADDRESS = 0x0004_0000


def _i(opcode, rd, funct3, rs1, imm):
    return ((imm & 0xfff) << 20) | (rs1 << 15) | (funct3 << 12) | (rd << 7) | opcode


def _s(rs2, rs1, imm):
    return (((imm >> 5) & 0x7f) << 25) | (rs2 << 20) | (rs1 << 15) | (2 << 12) | \
        ((imm & 0x1f) << 7) | 0x23


def _beq(rs1, rs2, offset):
    imm = offset & 0x1fff
    return (((imm >> 12) & 1) << 31) | (((imm >> 5) & 0x3f) << 25) | (rs2 << 20) | \
        (rs1 << 15) | (((imm >> 1) & 0xf) << 8) | (((imm >> 11) & 1) << 7) | 0x63


# Commit the palette and swap the framebuffer banks (the host poked the back
# bank), poll the input FIFO DATA register until a PS/2 event arrives, store
# it at RESULT and park. x5/x6 are scratch registers.
PROGRAM = (
    (0x20001 << 12) | (5 << 7) | 0x37,  # lui  x5, 0x20001 (video control)
    _i(0x13, 6, 0, 0, 1),               # addi x6, x0, 1
    _s(6, 5, 0),                        # sw   x6, 0(x5): palette commit
    _s(6, 5, 8),                        # sw   x6, 8(x5): framebuffer swap
    (0x20002 << 12) | (5 << 7) | 0x37,  # lui  x5, 0x20002 (input DATA)
    _i(0x03, 6, 2, 5, 0),               # lw   x6, 0(x5): pops one event
    _beq(6, 0, -4),                     # beq  x6, x0, -4: empty reads zero
    _s(6, 0, RESULT),                   # sw   x6, RESULT(x0)
    0x0000006f,                         # jal  x0, 0
)


def odd_parity(value):
    return 0 if value.bit_count() & 1 else 1


@cocotb.test(timeout_time=25, timeout_unit="ms")
async def board_serial_sdram_video_contract(dut):
    dut.KEY.value = 0
    dut.UART_RX.value = 1
    dut.PS2_CLK.value = 1
    dut.PS2_DAT.value = 1
    dut.dram_drive_i.value = 0
    dut.dram_data_i.value = 0
    cocotb.start_soon(Clock(dut.CLOCK_50, 20, unit="ns").start())
    await Timer(100, unit="ns")
    assert int(dut.LEDR.value) & 8
    dut.KEY.value = 1
    model, initial = SdramPinModel(), []

    def check_constant_pins():
        assert int(dut.LEDR.value) >> 4 == 0 and int(dut.LEDG.value) == 0
        for digit in range(8):
            assert int(getattr(dut, f"HEX{digit}").value) == 0x7f, "7-segment digit lit"
        assert int(dut.VGA_BLANK_N.value) == 1 and int(dut.VGA_SYNC_N.value) == 0

    async def read_return(beats):
        # CL2 data is valid by device n+2, launched after n+1+tAC;
        # each later beat follows one device clock after the previous one.
        await Timer(46, unit="ns")
        for index, value in enumerate(beats):
            dut.dram_data_i.value = value
            dut.dram_drive_i.value = 1
            await Timer(34 if index == len(beats) - 1 else 40, unit="ns")
        dut.dram_drive_i.value = 0

    async def sdram():
        edge = 0
        while True:
            await RisingEdge(dut.DRAM_CLK)
            edge += 1
            cmd = (int(dut.DRAM_RAS_N.value), int(dut.DRAM_CAS_N.value),
                   int(dut.DRAM_WE_N.value))
            bank, address = int(dut.DRAM_BA.value), int(dut.DRAM_ADDR.value)
            dqm = int(dut.DRAM_DQM.value)
            assert int(dut.DRAM_CKE.value) and not int(dut.DRAM_CS_N.value)
            # The high chip shares every command but must never drive DQ[31:16].
            assert dqm >> 2 == 3, "high SDRAM chip unmasked"
            if cmd != NOP and len(initial) < 4:
                initial.append(cmd)
            if cmd == WRITE or model.write_burst is not None:
                assert not int(dut.dram_drive_i.value), "DQ bus contention"
            beats = model.edge(edge, cmd, bank, address, int(dut.dram_data_o.value), dqm & 3)
            if beats is not None:
                cocotb.start_soon(read_return(beats))

    task = cocotb.start_soon(sdram())
    await Timer(190, unit="us")
    assert int(dut.LEDR.value) & 8, "system released before SDRAM initialization"
    check_constant_pins()
    await Timer(60, unit="us")
    assert int(dut.LEDR.value) == 7, "PLL/init/halt status must be set, reset clear"
    check_constant_pins()
    assert initial == [(0, 1, 0), (0, 0, 1), (0, 0, 1), (0, 0, 0)]

    async def uart_byte():
        await FallingEdge(dut.UART_TX)
        start = get_sim_time(unit="ps")
        value = 0
        for bit in range(9):
            await Timer(start + round((bit + 1.5) * BIT_PS) - get_sim_time(unit="ps"), unit="ps")
            if bit < 8:
                value |= int(dut.UART_TX.value) << bit
        assert int(dut.UART_TX.value), "UART stop bit"
        return value

    async def receive():
        packet = bytearray()
        length = 7
        while len(packet) < length:
            packet.append(await uart_byte())
            if len(packet) == 6:
                length += packet[5] * 4
        return decode_response(bytes(packet))

    async def send(data):
        start, index = get_sim_time(unit="ps"), 0
        for value in data:
            for bit in (0, *((value >> i) & 1 for i in range(8)), 1):
                dut.UART_RX.value = bit
                index += 1
                await Timer(start + round(index * BIT_PS) - get_sim_time(unit="ps"), unit="ps")

    async def exchange(command, sequence, timeout_us=500, **kwargs):
        receiver = cocotb.start_soon(receive())
        await send(request(command, sequence, **kwargs))
        response = await with_timeout(receiver, timeout_us, "us")
        assert response.sequence == sequence and response.status == 0, response
        return response.words

    async def stream(frame, timeout_us=500):
        async def reply():
            return wire.decode_stream_reply(bytes([await uart_byte() for _ in range(10)]))
        receiver = cocotb.start_soon(reply())
        await send(frame)
        result = await with_timeout(receiver, timeout_us, "us")
        assert result.status == 0, result
        return result

    # Low-chip SDRAM through the JP5 UART: burst-eight lines in all four
    # banks, a row crossing and byte masks on DQM[1:0].
    assert await exchange(CMD_PING, 1) == (0x3144324e, 1)
    for bank in range(4):
        # The last word of each bank's row 0; the two-word poke crosses into
        # the next bank (or into row 1 after bank 3).
        address = join(bank, 0, 1022)
        value = 0x12345678 ^ (bank * 0x11111111)
        await exchange(CMD_POKE, 2 + bank * 3, address=address,
                       words=(value, 0xabcdef01), byte_enable=15)
        assert await exchange(CMD_PEEK, 3 + bank * 3, address=address, count=2) == (value, 0xabcdef01)
        await exchange(CMD_POKE, 4 + bank * 3, address=address,
                       words=(0xdeadbeef,), byte_enable=5)
        assert model.words[address] == ((value & 0xff00) | 0xef)
        assert model.words[address + 2] == (((value >> 16) & 0xff00) | 0xad)

    # Streaming load through the board top at 3 Mbaud: Hello, two back-to-back
    # Write frames (one with a three-byte tail), a stored-SDRAM digest, End,
    # then legacy Peek of the stored words.
    image = bytes((i * 29 + 7) & 0xff for i in range(67))
    hello = await stream(wire.stream_frame(wire.STREAM_HELLO, 1))
    assert hello.value == wire.STREAM_IDENTITY
    replies = []

    async def two_replies():
        for _ in range(2):
            replies.append(wire.decode_stream_reply(bytes([await uart_byte() for _ in range(10)])))
    collector = cocotb.start_soon(two_replies())
    await send(wire.stream_write_frame(2, STREAM_ADDRESS, image[:32]) +
               wire.stream_write_frame(3, STREAM_ADDRESS + 32, image[32:]))
    await with_timeout(collector, 500, "us")
    assert [(r.sequence, r.status) for r in replies] == [(2, 0), (3, 0)], replies
    digest = await stream(wire.stream_digest_frame(4, STREAM_ADDRESS, len(image), 0xffffffff))
    assert digest.value ^ 0xffffffff == zlib.crc32(image), "stored digest mismatch"
    done = await stream(wire.stream_frame(wire.STREAM_END, 5))
    assert done.value == 2
    words = await exchange(CMD_PEEK, 19, address=STREAM_ADDRESS + 64, count=1)
    assert words[0] & 0xffffff == int.from_bytes(image[64:], "little")

    # Video and input setup while halted: palette entry 0 of the inactive
    # bank, the first 16 source pixels of row 0 at index 0, then the program.
    await exchange(CMD_POKE, 20, address=PALETTE_ENTRY, words=(COLOR,), byte_enable=15)
    await exchange(CMD_POKE, 21, address=FRAMEBUFFER, words=(0, 0, 0, 0), byte_enable=15)
    await exchange(CMD_POKE, 22, address=0, words=PROGRAM[:8], byte_enable=15)
    await exchange(CMD_POKE, 23, address=32, words=PROGRAM[8:], byte_enable=15)
    await exchange(CMD_RESUME, 24)

    # One Set-2 make code on the on-board PS/2 pins (device-clocked frame).
    await Timer(20, unit="us")
    for bit in (0, *((SCAN_CODE >> i) & 1 for i in range(8)), odd_parity(SCAN_CODE), 1):
        dut.PS2_DAT.value = bit
        await Timer(1370, unit="ns")
        dut.PS2_CLK.value = 0
        await Timer(1370, unit="ns")
        dut.PS2_CLK.value = 1
    dut.PS2_DAT.value = 1
    await Timer(50, unit="us")
    await exchange(CMD_HALT, 25, timeout_us=3000)
    assert await exchange(CMD_PEEK, 26, address=RESULT, count=1) == ((1 << 31) | SCAN_CODE,), \
        "PS/2 event did not reach the program"
    # Host keys over the board's Virtual JTAG wrapper into the same FIFO.
    await check_jtag_keys(dut.u_board)
    assert model.writes and model.bursts and len(model.refresh) > 5
    assert max(b - a for a, b in zip(model.refresh, model.refresh[1:])) <= 195
    task.cancel()

    # The committed palette and the swapped framebuffer bank become active at
    # the same vertical-blank boundary
    # (line 480) that precedes the next vertical sync pulse. Scaled source
    # row 0 then appears on output lines 40 and 41 of the following frame.
    await FallingEdge(dut.VGA_VS)
    # Sync starts on line 490; begin sampling half-way through line 38.
    await Timer(73 * LINE_NS + 400 * PIXEL_NS, unit="ns")
    expected = ((COLOR >> 8) * 0x11, ((COLOR >> 4) & 0xf) * 0x11, (COLOR & 0xf) * 0x11)
    samples, phase_violations = [], []

    async def watch_rgb_changes():
        # RGB launches on the rising 25 MHz edge, which is the falling DAC
        # clock edge; it must never change at the rising VGA_CLK sample edge.
        while True:
            await dut.VGA_R.value_change
            if int(dut.VGA_CLK.value):
                phase_violations.append(get_sim_time("ns"))

    watcher = cocotb.start_soon(watch_rgb_changes())
    for _ in range(3 * 800):
        await RisingEdge(dut.VGA_CLK)
        rgb = (int(dut.VGA_R.value), int(dut.VGA_G.value), int(dut.VGA_B.value))
        for channel in rgb:
            assert channel >> 4 == channel & 0xf, f"4-bit channel not replicated: {rgb}"
        samples.append(rgb)
    watcher.cancel()
    assert not phase_violations, phase_violations
    lit = [index for index, rgb in enumerate(samples) if rgb == expected]
    assert lit, "committed palette color never reached the DAC"
    first = lit[0]
    # Border line 39 and the horizontal blank before line 40 stay black.
    assert first >= 400 + 160, "color appeared on the black border"
    assert all(rgb == (0, 0, 0) for rgb in samples[:first]), "border must be black"
    assert samples[first:first + 16] == [expected] * 16, "first 16 scaled pixels"
    check_constant_pins()
    cocotb.log.info("PASS low-chip burst-eight SDRAM, DQM[3:2] high, 3 Mbaud JP5 UART and stream load, PS/2, JTAG keys, VGA DAC replication/phase")



@cocotb.test(timeout_time=80, timeout_unit="ms")
async def board_uart_mem_endpoint(dut):
    """Common UART memory endpoint at 3 Mbaud through the DE2-115 top."""
    from src.nand2doom.dv.fpga.uart_mem_board import BoardLink, endpoint_load_and_readback
    link = BoardLink(dut, clock="CLOCK_50", reset="KEY", rx="UART_RX", tx="UART_TX",
                     baud=BAUD, dqm=lambda d: int(d.DRAM_DQM.value) & 3)
    await link.start()
    await endpoint_load_and_readback(link)
