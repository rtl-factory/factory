"""n2m_chip lockstep: the ASIC chip top (pads, chip core, off-chip cartridge
bus, no display tier) runs the background program from an external
cartridge device in lockstep with the reference n2m_system (on-chip backing,
display tier) of the existing n2m_system_background target.

Both consoles start from the same seeded garbage in every store, the bench
preloads both cartridge stores through one preload port while reset is held,
and the fixture fails on the first edge on which any chip output pin differs
from the reference output. Checked here: the reference program halts with no
fault or lock; the cartridge pins carried the program's reads; one complete
frame read from the chip's pixel pins (coordinates rebuilt from the line and
frame markers only) equals the independent background renderer applied to
the chip's own VRAM and the LCD registers the program read back into WRAM,
and the chip's VRAM and WRAM equal the reference's; a loader exchange on the
shared serial line (PING, HOLD, a cartridge RAM write and read-back through
the chip's bus, a ROM read, RELEASE) answers correctly from the chip's pin
while the reference answers the same bytes. A corrupted expectation is
rejected (negative control)."""
import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, ReadOnly, RisingEdge, Timer

from ...tools.cart_loader import (CMD_HOLD, CMD_PING, CMD_READ, CMD_RELEASE, CMD_WRITE, address_field,
                                  encode_request)
from ..video import ppu_model as m
from .test_system import load_image, store_bytes
from .test_system_background import compare_frame
from .test_system_preload import UART_CLKS_PER_BIT, LineHost

CLOCK_PERIOD_NS = 40
RESET_TAIL_EDGES = 16          # reset held after the preload (VRAM clear needs 8192 in total)
RAM_PATCH = bytes(range(0x5A, 0x5A + 8))


def stores(system):
    """The n2m_system stores that start with garbage, by name."""
    memory = system.u_memory
    return {'vram': memory.u_vram.memory, 'wram': memory.u_wram.memory,
            'oam': memory.u_oam.memory, 'hram': memory.u_hram.memory}


def fill_both(rng, pairs):
    """The same seeded nonzero garbage into each (reference, chip) array pair."""
    for ref, chip in pairs:
        for index in range(len(ref)):
            value = rng.randrange(1, 256)
            ref[index].value = value
            chip[index].value = value


@cocotb.test(timeout_time=120, timeout_unit='ms')
async def chip_lockstep(dut):
    seed = int(os.environ['DV_SEED'])
    bound = int(os.environ['DV_MAX_CYCLES'])
    assert int(os.environ['DV_CLOCK_HZ']) == 25_000_000
    cocotb.log.info('n2m_chip lockstep seed=%d bound=%d', seed, bound)
    rng = random.Random(seed)
    image = load_image()
    ref, chip = dut.u_ref, dut.u_chip.u_core.u_system
    pairs = [(stores(ref)[name], stores(chip)[name]) for name in ('vram', 'wram', 'oam', 'hram')]
    pairs += [(ref.g_onchip.u_backing.u_rom.memory, dut.u_device.u_rom.memory),
              (ref.g_onchip.u_backing.u_ram.memory, dut.u_device.u_ram.memory)]
    fill_both(rng, pairs)

    # The pin reset is asynchronous: raise it before the first clock edge so
    # the synchronizers assert the system reset before any edge samples it.
    dut.reset_i.value = 0
    await Timer(1, unit='ns')
    dut.reset_i.value = 1
    await Timer(1, unit='ns')
    dut.uart_rx_i.value = 1
    dut.buttons_i.value = 0
    dut.rom_load_write_i.value = 0
    dut.rom_load_address_i.value = 0
    dut.rom_load_data_i.value = 0
    cocotb.start_soon(Clock(dut.clk_i, CLOCK_PERIOD_NS, unit='ns').start())
    host = LineHost(dut)
    cocotb.start_soon(host.ticker())
    cocotb.start_soon(host.receiver())
    # Both stores take one preload write per edge while the system reset holds.
    for address, byte in enumerate(image):
        await FallingEdge(dut.clk_i)
        dut.rom_load_write_i.value = 1
        dut.rom_load_address_i.value = address
        dut.rom_load_data_i.value = byte
    await FallingEdge(dut.clk_i)
    dut.rom_load_write_i.value = 0
    await ClockCycles(dut.clk_i, RESET_TAIL_EDGES)
    await FallingEdge(dut.clk_i)
    dut.reset_i.value = 0
    release = host.clock

    # Run to the reference's final HALT (the program sleeps after VBlank).
    while True:
        await RisingEdge(dut.clk_i)
        await ReadOnly()
        assert not int(dut.ref_fault_o.value), 'unexpected bus fault'
        assert not int(dut.ref_locked_o.value), 'unexpected illegal-opcode lock'
        if not int(dut.ref_reset_o.value) and int(dut.ref_halted_o.value) and host.clock - release > 1000:
            # The program halts twice: once waiting for VBlank, then for good
            # after its handler stored the evidence (WRAM byte 4 = 1 VBlank).
            if int(chip.u_memory.u_wram.memory[4].value) == 1:
                break
    halted_at = host.clock - release
    reads, writes = int(dut.cart_reads_o.value), int(dut.cart_writes_o.value)
    assert reads > 1000 and writes == 0, ('cartridge pin traffic', reads, writes)
    cocotb.log.info('program halted %d edges after release; %d cartridge reads on the pins', halted_at, reads)

    # One frame from the chip's pins: coordinates from the markers only.
    observed = [[None] * m.VISIBLE_PIXELS for _ in range(m.VISIBLE_LINES)]
    await RisingEdge(dut.frame_start_o)
    x = y = -1
    for index in range(m.VISIBLE_PIXELS * m.VISIBLE_LINES):
        if index:
            await RisingEdge(dut.pixel_valid_o)
        await ReadOnly()
        assert int(dut.pixel_valid_o.value)
        if int(dut.frame_start_o.value):
            assert index == 0, ('frame marker inside the frame', index)
            y = -1
        if int(dut.line_start_o.value):
            x, y = 0, y + 1
        else:
            x += 1
        assert (x, y) == (index % m.VISIBLE_PIXELS, index // m.VISIBLE_PIXELS), ('marker order', index, x, y)
        assert (x, y) == (int(dut.pixel_x_o.value), int(dut.pixel_y_o.value)), 'reference raster position'
        observed[y][x] = int(dut.pixel_shade_o.value)
    chip_vram = store_bytes(chip.u_memory.u_vram.memory)
    chip_wram = store_bytes(chip.u_memory.u_wram.memory)
    assert chip_vram == store_bytes(ref.u_memory.u_vram.memory), 'chip VRAM differs from the reference'
    assert chip_wram == store_bytes(ref.u_memory.u_wram.memory), 'chip WRAM differs from the reference'
    lcdc, scy, scx, bgp = chip_wram[:4]
    assert lcdc & 0x80, 'the program enables the LCD'
    expected = m.render_background(chip_vram, lcdc, scy, scx, bgp)
    compare_frame(observed, expected)
    corrupted = [row[:] for row in expected]
    corrupted[17][33] ^= 1
    try:
        compare_frame(observed, corrupted)
    except AssertionError:
        pass
    else:
        raise AssertionError('frame comparison accepted a corrupted expectation')
    assert sorted({shade for row in observed for shade in row}) == [0, 1, 2, 3]

    # Loader on the shared serial line: the chip's pin answers; the fixture
    # holds the reference's line equal on every edge.
    response = await host.exchange(encode_request(CMD_PING))
    assert response.status & 0x07 == 0, response
    response = await host.exchange(encode_request(CMD_HOLD))
    assert response.status & 0x07 == 0, response
    reads_before = int(dut.cart_reads_o.value)
    response = await host.exchange(encode_request(CMD_WRITE, address_field(0x10, ram=True), RAM_PATCH))
    assert response.status & 0x07 == 0, response
    assert int(dut.cart_writes_o.value) == len(RAM_PATCH), 'loader writes reach the cartridge pins'
    response = await host.exchange(encode_request(CMD_READ, address_field(0x10, ram=True), length=len(RAM_PATCH)))
    assert response.status & 0x07 == 0 and response.payload == RAM_PATCH, response
    response = await host.exchange(encode_request(CMD_READ, address_field(0x0100), length=16))
    assert response.status & 0x07 == 0 and response.payload == image[0x100:0x110], response
    assert int(dut.cart_reads_o.value) - reads_before == len(RAM_PATCH) + 16
    response = await host.exchange(encode_request(CMD_RELEASE))
    assert response.status & 0x07 == 0, response
    await ClockCycles(dut.clk_i, 64)
    assert host.clock <= bound
    cocotb.log.info('n2m_chip lockstep passed: halt_edges=%d cart_reads=%d loader_writes=%d total_edges=%d',
                    halted_at, reads, len(RAM_PATCH), host.clock)
