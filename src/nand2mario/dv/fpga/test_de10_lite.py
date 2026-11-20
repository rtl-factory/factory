"""nand2mario DE10-Lite board smoke: the real board top with the portable
clock backend and the common Virtual JTAG wrapper on its simulation stub,
the owned SDRAM device model on its SDRAM pins (n2m_de10_lite_fixture). The
bench plays the board and the host: the 50 MHz oscillator, KEY0 reset, the
switches and KEY1, an 8-N-1 host on the loader UART pins (Arduino D0/D1) and
the host side of the Virtual JTAG hub through the common stub's bench-owned
scan variables (src/common/dv/jtag). It checks PLL lock and reset
release, the core held until the SDRAM is initialized, loads the owned
background program over the UART into the SDRAM (HOLD, WRITE every chunk,
READ back, RELEASE), runs it from SDRAM until its final HALT, reads the halt
PC back from the 7-segment digits, captures one complete 640x480 VGA frame
on the board pins and requires it to equal the PPU frame observed inside,
then presses and releases joypad buttons over JTAG (an extended arrow and a
plain key) and through the debounced SW/KEY fallback, including a bounce
shorter than the sample period that must not register."""
import os
import random
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, First, ReadOnly, RisingEdge, Timer
from cocotb.utils import get_sim_time

from src.common.dv.jtag.vjtag_stub_host import StubHost
from ...scripts.board.cart_loader import (CMD_HOLD, CMD_PING, CMD_READ, CMD_RELEASE, CMD_WRITE, address_field,
                                  chunks, decode_response, encode_request, response_total_length)
from ..core.test_cpu import unpack
from ..system.test_system import load_image, power_up
from ..video import display_model as dm
from ..video import ppu_model as m

REFERENCE_PERIOD_NS = 20
CLOCK_PERIOD_NS = 40
SAMPLE_NS = 25000 * CLOCK_PERIOD_NS          # one debounce sample period (1 ms)
HEARTBEAT_BIT = 20                           # the target's HEARTBEAT_BIT parameter
HEADER_TYPE = 0x147
UART_CLKS_PER_BIT = 8                        # the target's UART_CLKS_PER_BIT parameter
BIT_NS = UART_CLKS_PER_BIT * CLOCK_PERIOD_NS
# Storage HAS: the SDRAM is initialized 5036 clocks after reset release.
SDRAM_INITIALIZED_CLOCK = 5036
# Joypad bits (joypad HAS order).
RIGHT, LEFT, UP, DOWN, A, B, SELECT, START = (1 << i for i in range(8))
LED_LOCKED, LED_RESET, LED_HALTED, LED_STOPPED, LED_LOCKUP, LED_FAULT = (1 << i for i in range(6))
LED_DISPLAY, LED_HEARTBEAT, LED_BUTTONS, LED_SERIAL = (1 << i for i in range(6, 10))
# Active-high a..g segment patterns of the board's 7-segment digits.
SEGMENTS = [0x3F, 0x06, 0x5B, 0x4F, 0x66, 0x6D, 0x7D, 0x07,
            0x7F, 0x6F, 0x77, 0x7C, 0x39, 0x5E, 0x79, 0x71]
EXTENDED, RELEASE = 0xE0, 0xF0


def digit(nibble):
    """The active-low segment byte (decimal point off) one HEX digit shows."""
    return 0x80 | (~SEGMENTS[nibble] & 0x7F)


def hex_word(*digits):
    """The value whose nibbles the given HEX digits (most significant first) show."""
    inverse = {digit(n) & ((1 << len(digits[0])) - 1): n for n in range(16)}
    value = 0
    for segments in digits:
        assert int(segments.value) in inverse, f'HEX digit shows an unknown pattern {int(segments.value):#04x}'
        value = (value << 4) | inverse[int(segments.value)]
    return value


def read_vga(dut):
    rgb = tuple(int(pin.value) for pin in (dut.VGA_R, dut.VGA_G, dut.VGA_B))
    if len(dut.VGA_R) == 8:
        assert all((c >> 4) == (c & 15) for c in rgb), 'DE2 RGB nibble replication'
        assert int(dut.VGA_BLANK_N.value) == 1 and int(dut.VGA_SYNC_N.value) == 0
        rgb = tuple(c >> 4 for c in rgb)
    return int(dut.VGA_HS.value), int(dut.VGA_VS.value), rgb


class UartHost:
    """The host on the board's loader UART pins: 8-N-1 at the target's bit
    period, bit-banged on UART_RXD and sampled at mid-bit on UART_TXD."""

    def __init__(self, dut):
        self.dut = dut
        self.received = []
        dut.UART_RXD.value = 1

    async def receiver(self):
        d = self.dut
        while True:
            await FallingEdge(d.UART_TXD)
            await Timer(BIT_NS + BIT_NS // 2, unit='ns')
            value = 0
            for bit in range(8):
                value |= int(d.UART_TXD.value) << bit
                await Timer(BIT_NS, unit='ns')
            assert int(d.UART_TXD.value) == 1, 'UART_TXD stop bit'
            self.received.append(value)

    async def send(self, data):
        await writable()
        for byte in data:
            for bit in (0, *((byte >> i) & 1 for i in range(8)), 1):
                self.dut.UART_RXD.value = bit
                await Timer(BIT_NS, unit='ns')

    async def exchange(self, frame, limit_ns=400 * 10 * BIT_NS):
        """Send one frame and return its decoded, CRC-checked response."""
        start = len(self.received)
        await self.send(frame)
        waited, total = 0, None
        while total is None or len(self.received) - start < total:
            assert waited < limit_ns, f'loader response timeout after {len(self.received) - start} bytes'
            await Timer(BIT_NS, unit='ns')
            waited += BIT_NS
            if total is None and len(self.received) - start >= 7:
                total = response_total_length(bytes(self.received[start:start + 7]))
        response = decode_response(bytes(self.received[start:start + total]))
        assert response.crc_ok, 'loader response CRC'
        return response


def check_heartbeat(dut, release_ns):
    heartbeat_bit = HEARTBEAT_BIT
    elapsed = int(get_sim_time(unit='ns') - release_ns) // CLOCK_PERIOD_NS
    assert elapsed >= 2 << heartbeat_bit, f'heartbeat window not reached: {elapsed} edges'
    # A few edges of release-time uncertainty: never sample at a toggle.
    assert 8 < elapsed % (1 << heartbeat_bit) < (1 << heartbeat_bit) - 8, elapsed
    expected = (elapsed >> heartbeat_bit) & 1
    assert bool(int(dut.LEDR.value) & LED_HEARTBEAT) == bool(expected), (elapsed, expected)


async def writable():
    """Leave a ReadOnly phase before driving inputs."""
    await Timer(1, unit='ns')


async def settle(clock, cycles):
    for _ in range(cycles):
        await RisingEdge(clock)


@cocotb.test(timeout_time=400, timeout_unit='ms')
async def board_smoke(dut):
    seed = int(os.environ['DV_SEED'])
    bound = int(os.environ['DV_MAX_CYCLES'])
    assert int(os.environ['DV_CLOCK_HZ']) == 25_000_000
    cocotb.log.info('n2m board smoke seed=%d (directed) bound=%d', seed, bound)
    rng = random.Random(seed)
    board = dut.u_board
    system = board.u_system
    image = load_image()

    # Board inputs at power-up: KEY0 pressed (reset), KEY1 released, no
    # switches, the UART line idle, the JTAG hub idle.
    dut.KEY.value = 0b10
    dut.SW.value = 0
    uart = UartHost(dut)
    # TCK at one quarter of the system clock.
    host = StubHost(dut.u_board.u_jtag_virtual.u_vjtag, half_tck_ns=2 * CLOCK_PERIOD_NS)
    power_up(system, rng, onchip=False)
    for bank in range(3):
        memory = system.u_display.u_frame_ram.g_banks[bank].u_ram.u_enabled.memory
        for index in range(dm.FRAME_PIXELS):
            memory[index].value = rng.randrange(4)

    cocotb.start_soon(Clock(dut.CLOCK_50 if hasattr(dut, 'CLOCK_50') else dut.MAX10_CLK1_50, REFERENCE_PERIOD_NS, unit='ns').start())
    await Timer(10 * REFERENCE_PERIOD_NS, unit='ns')
    await ReadOnly()
    leds = int(dut.LEDR.value)
    assert leds & LED_RESET and not leds & LED_LOCKED, f'reset held with the PLL stopped: LEDR={leds:#05x}'
    await Timer(REFERENCE_PERIOD_NS // 2, unit='ns')
    dut.KEY.value = 0b11
    # Lock three reference edges after release, then the synchronous reset
    # release two system edges later.
    await Timer(4 * REFERENCE_PERIOD_NS + 5, unit='ns')
    await ReadOnly()
    assert int(dut.LEDR.value) & LED_LOCKED, 'PLL lock must be reported'
    release_ns = get_sim_time(unit='ns')
    await settle(system.clk_i, 3)
    await ReadOnly()
    assert not int(board.reset.value), 'the board reset must release after lock'
    assert int(dut.LEDR.value) & LED_RESET, 'the core stays held until the SDRAM is initialized'
    assert int(dut.DRAM_CKE.value) == 1 and int(dut.DRAM_CS_N.value) == 0
    assert int(dut.LEDR.value) & (LED_HALTED | LED_STOPPED | LED_LOCKUP | LED_FAULT | LED_BUTTONS | LED_SERIAL) == 0
    assert hex_word(dut.HEX3, dut.HEX2, dut.HEX1, dut.HEX0) == 0 and hex_word(dut.HEX5, dut.HEX4) == 0

    # Loader over the UART pins: before initialization a PING reports an
    # uninitialized backing; once initialized the core leaves reset.
    cocotb.start_soon(uart.receiver())
    response = await uart.exchange(encode_request(CMD_PING))
    assert response.ok and not response.initialized and not response.held, response.describe()
    await Timer(max(0, int(release_ns + (SDRAM_INITIALIZED_CLOCK + 4) * CLOCK_PERIOD_NS - get_sim_time(unit='ns'))) + 1,
                unit='ns')
    await ReadOnly()
    assert not int(dut.LEDR.value) & LED_RESET, 'the core runs once the SDRAM is initialized'
    response = await uart.exchange(encode_request(CMD_PING))
    assert response.ok and response.initialized and not response.held, response.describe()
    response = await uart.exchange(encode_request(CMD_HOLD))
    assert response.ok and response.held
    await ReadOnly()
    assert int(dut.LEDR.value) & LED_RESET, 'HOLD resets the core'
    pieces = chunks(image)
    for offset, data in pieces:
        response = await uart.exchange(encode_request(CMD_WRITE, address_field(offset), data))
        assert response.ok and response.offset == offset and response.length == len(data), response.describe()
    for offset, data in (pieces[0], pieces[1], pieces[-1]):
        response = await uart.exchange(encode_request(CMD_READ, address_field(offset), length=len(data)))
        assert response.ok and response.payload == data, (offset, response.describe())
    header_word = int(dut.u_sdram.memory[HEADER_TYPE >> 1].value)
    assert (header_word >> 8) & 0xFF == image[HEADER_TYPE], 'the header byte lives in the SDRAM device'
    cocotb.log.info('image loaded over the UART pins: %d frames at %.0f us', len(pieces), get_sim_time(unit='us'))
    response = await uart.exchange(encode_request(CMD_RELEASE))
    assert response.ok and not response.held
    await ReadOnly()
    assert not int(dut.LEDR.value) & LED_RESET, 'RELEASE starts the core'

    # Run the background program to its final HALT (IE cleared), keeping
    # the last retired PC for the digit check.
    cycles, last_pc, retirements = 0, None, 0
    while True:
        await RisingEdge(system.clk_i)
        await ReadOnly()
        cycles += 1
        assert cycles <= bound, 'finite bound exceeded before the final HALT'
        leds = int(dut.LEDR.value)
        assert not leds & (LED_LOCKUP | LED_FAULT | LED_STOPPED), f'CPU status LEDs: {leds:#05x}'
        record = unpack(int(system.retirement_o.value))
        if record['valid']:
            last_pc = record['pc_after']
            retirements += 1
        if int(system.halted_o.value) and int(system.u_memory.interrupt_enable_o.value) == 0:
            break
    cocotb.log.info('final HALT after %d retirements and %d edges', retirements, cycles)
    await settle(system.clk_i, 2)
    await ReadOnly()
    leds = int(dut.LEDR.value)
    assert leds & LED_HALTED and leds & LED_LOCKED and not leds & LED_RESET, f'LEDR={leds:#05x}'
    assert hex_word(dut.HEX3, dut.HEX2, dut.HEX1, dut.HEX0) == last_pc, 'HEX3..0 show the last retired PC'
    assert hex_word(dut.HEX5, dut.HEX4) == 0, 'no serial byte was transmitted'
    assert not leds & LED_SERIAL

    # One PPU frame observed inside, then the output frame on the board pins.
    observed = [[None] * m.VISIBLE_PIXELS for _ in range(m.VISIBLE_LINES)]
    await RisingEdge(system.frame_start_o)
    for index in range(m.VISIBLE_PIXELS * m.VISIBLE_LINES):
        if index:
            await RisingEdge(system.pixel_valid_o)
        await ReadOnly()
        x, y = int(system.pixel_x_o.value), int(system.pixel_y_o.value)
        assert (x, y) == (index % m.VISIBLE_PIXELS, index // m.VISIBLE_PIXELS), ('pixel order', index, x, y)
        observed[y][x] = int(system.pixel_shade_o.value)
    shades = sorted({shade for row in observed for shade in row})
    assert shades == [0, 1, 2, 3], f'the frame must use all four shades, saw {shades}'
    complete_edge = int(get_sim_time(unit='ns')) // CLOCK_PERIOD_NS
    await RisingEdge(system.pixel_start_o)
    if int(get_sim_time(unit='ns')) // CLOCK_PERIOD_NS < complete_edge + dm.OUTPUT_LATENCY + 1:
        await RisingEdge(system.pixel_start_o)
    await ReadOnly()
    assert int(dut.LEDR.value) & LED_DISPLAY, 'a complete frame must be displayed'
    clocks = []
    for _ in range(dm.FRAME_CLOCKS):
        if hasattr(dut, 'VGA_CLK'):
            await RisingEdge(dut.VGA_CLK)
            await ReadOnly()
            assert int(system.clk_i.value) == 0, 'DAC must latch halfway through the pixel'
        else:
            await FallingEdge(system.clk_i)
        clocks.append(read_vga(dut))
    dm.compare_clocks(clocks, dm.expected_frame(observed), label='board VGA frame')
    if hasattr(dut, 'VGA_CLK'):
        # BLANK_N stays high: the display itself must drive black in every
        # horizontal/vertical blanking pixel, not merely during sync pulses.
        for index, (_, _, rgb) in enumerate(clocks):
            x, y = index % dm.H_TOTAL, index // dm.H_TOTAL
            if x >= dm.H_VISIBLE or y >= dm.V_VISIBLE:
                assert rgb == (0, 0, 0), ('DAC blanking', x, y, rgb)
    dm.write_rgb_png(Path.cwd() / 'board_vga.png', dm.visible_rows(clocks))
    corrupted = [row[:] for row in observed]
    corrupted[70][80] ^= 2
    try:
        dm.compare_clocks(clocks, dm.expected_frame(corrupted), label='corrupted')
    except AssertionError:
        pass
    else:
        raise AssertionError('VGA comparison accepted a corrupted expectation')
    if hasattr(dut, 'VGA_CLK'):
        from ...scripts.board import frame_snapshot as fs
        from ...scripts.board.cart_loader import CMD_SNAP_PIN, CMD_SNAP_RELEASE
        pin = await uart.exchange(encode_request(CMD_SNAP_PIN))
        assert pin.ok, pin.describe()
        packed = bytearray()
        for request in fs.plan_frames()[2:-2]:
            response = await uart.exchange(request, limit_ns=300_000 * CLOCK_PERIOD_NS)
            assert response.ok, response.describe()
            packed.extend(response.payload)
        release = await uart.exchange(encode_request(CMD_SNAP_RELEASE))
        assert release.ok, release.describe()
        record = fs.parse_record(bytes(packed))
        assert list(record.shades) == [shade for row in observed for shade in row]
        cocotb.log.info('complete frame snapshot matched UART readback')

    # The heartbeat LED shows one bit of the clock counter since reset
    # release (HEARTBEAT_BIT, 20 in this target): it has toggled at least
    # twice by now and equals that bit of the elapsed edge count.
    check_heartbeat(dut, release_ns)

    # Host joypad over JTAG: Start (plain code) and Up (extended code), each
    # pressed and released, with the status words of the endpoint checked.
    await host.idle(5)
    status = await host.status()
    assert status == (0, 0, 0, 0, 0, 0), status
    await host.send(bytes([0x5A]))
    await settle(system.clk_i, 4)
    await ReadOnly()
    assert int(system.buttons_i.value) == START and int(dut.LEDR.value) & LED_BUTTONS
    await host.send(bytes([EXTENDED, 0x75]))
    await settle(system.clk_i, 4)
    await ReadOnly()
    assert int(system.buttons_i.value) == START | UP
    await host.send(bytes([RELEASE, 0x5A, EXTENDED, RELEASE]))
    await host.send(bytes([0x75]))
    await settle(system.clk_i, 4)
    await ReadOnly()
    assert int(system.buttons_i.value) == 0 and not int(dut.LEDR.value) & LED_BUTTONS
    # An unmapped key is consumed without effect.
    await host.send(bytes([0x1C, RELEASE, 0x1C]))
    await settle(system.clk_i, 4)
    await ReadOnly()
    assert int(system.buttons_i.value) == 0

    # Board fallback: a switch level and the KEY1 Start button reach the
    # joypad after two agreeing 1 ms samples; a bounce shorter than one
    # sample never registers.
    await writable()
    dut.SW.value = RIGHT | B
    await Timer(3 * SAMPLE_NS, unit='ns')
    await ReadOnly()
    assert int(system.buttons_i.value) == RIGHT | B, int(system.buttons_i.value)
    await writable()
    dut.KEY.value = 0b01
    await Timer(3 * SAMPLE_NS, unit='ns')
    await ReadOnly()
    assert int(system.buttons_i.value) == RIGHT | B | START
    await writable()
    dut.KEY.value = 0b11
    dut.SW.value = 0
    await Timer(SAMPLE_NS // 3, unit='ns')
    dut.SW.value = LEFT
    await Timer(SAMPLE_NS // 3, unit='ns')
    dut.SW.value = 0
    await Timer(3 * SAMPLE_NS, unit='ns')
    await ReadOnly()
    assert int(system.buttons_i.value) == 0, 'a sub-sample bounce must not register'
    assert int(dut.LEDR.value) & LED_HALTED, 'the halted program is unaffected'
    final_edges = int(get_sim_time(unit='ns') - release_ns) // CLOCK_PERIOD_NS
    assert final_edges <= bound, f'finite bound exceeded ({final_edges})'
    cocotb.log.info('n2m board smoke passed: retirements=%d edges=%d', retirements, final_edges)
