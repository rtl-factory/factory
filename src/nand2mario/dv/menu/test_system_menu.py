"""Run the unchanged menu against its independent donor frame reference.

Every complete source frame is checked in order, including the initial LCD
startup frame, the complete splash, cursor movement, refusal and return.
Inputs are changed after the last visible pixel, before the following
VBlank. Only public system outputs determine progress or the result.
"""
import hashlib
import json
import os
from pathlib import Path
import random
import time

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge, Timer
from cocotb.utils import get_sim_time

from . import fixture, reference, timing
from ..system.test_system import idle_host, power_up
from ..video.ppu_model import write_png

async def sdram_load(dut, image):
    """Only public UART HOLD/WRITE/READ/RELEASE; no device-model deposits."""
    from ..system.test_system_sdram import Host
    from ...scripts.board.cart_loader import CMD_HOLD, CMD_WRITE, CMD_READ, CMD_RELEASE, encode_request
    host = Host(dut)
    tasks = [cocotb.start_soon(host.ticker()), cocotb.start_soon(host.receiver())]
    started = time.monotonic()
    try:
        await Timer(400, unit='ns')
        dut.reset_i.value = 0
        response = await host.exchange(encode_request(CMD_HOLD))
        assert response.ok and response.held
        await Timer(250_000, unit='ns')  # specified 5038-clock SDRAM initialization
        # Every byte read by these selections is written through the real UART.
        # Other game slots remain seeded garbage and are never selected.
        ranges = ((32768, 65536), (5 * 32768, 7 * 32768), (0x80000, 0x8c000))
        loaded = 0
        for start, end in ranges:
            for address in range(start, end, 256):
                data = image[address:address + 256]
                response = await host.exchange(encode_request(CMD_WRITE, address, data))
                assert response.ok and response.held and int(dut.core_reset_o.value)
                loaded += len(data)
                if address == start or address + 256 == end:
                    response = await host.exchange(encode_request(CMD_READ, address, length=256))
                    assert response.ok and response.payload == data
        response = await host.exchange(encode_request(CMD_RELEASE))
        assert response.ok and not response.held
        assert int(dut.core_reset_o.value), 'menu copy/CRC must still hold core after RELEASE'
        cocotb.log.info('MENU_SDRAM_LOAD bytes=%d clocks=%d wall_seconds=%.2f',
                        loaded, host.clock, time.monotonic() - started)
    finally:
        for task in tasks:
            task.cancel()


@cocotb.test()
async def system_menu(dut):
    image = Path(os.environ['N2M_LIBRARY_IMAGE']).read_bytes()
    menu = Path(os.environ['N2M_MENU_IMAGE']).read_bytes()
    exit_image = Path(os.environ['N2M_EXIT_IMAGE']).read_bytes()
    assert hashlib.sha256(menu).hexdigest() == 'f74293b907517ff7c702be754031c66787eae34ec535f31e7ac9fcdd62a995d1'
    entries = fixture.entries(menu, exit_image)
    assert int(os.environ['DV_CLOCK_HZ']) == 25_000_000
    assert len(image) == 1024 * 1024
    backing = os.environ.get('N2M_MENU_BACKING', 'onchip')
    sdram = backing == 'sdram'
    system = dut.u_system if sdram else dut
    power_up(system, random.Random(int(os.environ['DV_SEED'])), rom_bytes=0, onchip=not sdram)
    dut.reset_i.value = 1
    dut.buttons_i.value = 0
    dut.rom_load_write_i.value = 0
    dut.rom_load_address_i.value = 0
    dut.rom_load_data_i.value = 0
    if sdram:
        dut.host_tx_valid_i.value = 0
        dut.host_tx_data_i.value = 0
    else:
        idle_host(dut)
    started = get_sim_time(unit='ns')
    Clock(dut.clk_i, 40, unit='ns', impl='gpi').start()
    if sdram:
        await sdram_load(dut, image)
    else:
        await RisingEdge(dut.clk_i)
        for address, byte in enumerate(image):
            await FallingEdge(dut.clk_i)
            dut.rom_load_write_i.value = 1
            dut.rom_load_address_i.value = address
            dut.rom_load_data_i.value = byte
            assert int(dut.rom_load_ready_o.value)
            await RisingEdge(dut.clk_i)
        await FallingEdge(dut.clk_i)
        dut.rom_load_write_i.value = 0
        await RisingEdge(dut.clk_i)
        await FallingEdge(dut.clk_i)
        dut.reset_i.value = 0
    frames, faults, results, resets = [], [], [], []

    async def pixels():
        frame = bytearray()
        while True:
            await RisingEdge(dut.pixel_valid_o)
            await ReadOnly()
            if int(dut.frame_start_o.value):
                frame.clear()
            x, y = int(dut.pixel_x_o.value), int(dut.pixel_y_o.value)
            assert (x, y) == (len(frame) % 160, len(frame) // 160), ('pixel order', x, y, len(frame))
            frame.append(int(dut.pixel_shade_o.value))
            if len(frame) == 23040:
                frames.append(bytes(frame))
                frame.clear()

    async def fault(signal, label):
        await RisingEdge(signal)
        faults.append(label)

    async def reset_watch():
        while True:
            await RisingEdge(dut.core_reset_o)
            resets.append(get_sim_time(unit='ns'))

    async def hold_watch():
        while True:
            if not int(dut.core_reset_o.value):
                await RisingEdge(dut.core_reset_o)
            held = get_sim_time(unit='ns')
            while int(dut.core_reset_o.value):
                await Timer(timing.POLL_NS, unit='ns')
                assert get_sim_time(unit='ns') - held <= timing.reset_wait_ns(backing), \
                    'backing/image CRC copy stalled while core held in reset'

    tasks = [cocotb.start_soon(pixels()), cocotb.start_soon(reset_watch()),
             cocotb.start_soon(hold_watch())]
    tasks += [cocotb.start_soon(fault(s, label)) for s, label in (
        (dut.fault_o, 'fault'), (dut.locked_o, 'illegal opcode'), (dut.stopped_o, 'STOP'))]
    consumed = 0

    async def next_frame(label, expected):
        nonlocal consumed
        deadline = get_sim_time(unit='ns') + timing.frame_wait_ns(backing)
        while len(frames) <= consumed:
            await Timer(4000, unit='ns')
            assert get_sim_time(unit='ns') < deadline, f'no frame: {label}'
            assert (get_sim_time(unit='ns') - started) / 40 <= int(os.environ['DV_MAX_CYCLES'])
            assert not faults, faults
        actual = frames[consumed]
        consumed += 1
        record = dict(label=label, frame=consumed, sha256=hashlib.sha256(actual).hexdigest())
        results.append(record)
        Path('menu-result.json').write_text(json.dumps(dict(samples=results, resets=resets), indent=2) + '\n')
        if actual != expected:
            for name, data in [('actual', actual), ('expected', expected)]:
                write_png(Path(f'{label}-{name}.png'), [list(data[y * 160:(y + 1) * 160]) for y in range(144)])
            first = next(i for i, pair in enumerate(zip(actual, expected)) if pair[0] != pair[1])
            raise AssertionError(f'MENU_PIXEL {label} frame={consumed} x={first % 160} y={first // 160} '
                                 f'expected={expected[first]} actual={actual[first]}')
        cocotb.log.info('MENU_FRAME %s index=%d sha256=%s', label, consumed, record['sha256'])

    try:
        # LCD startup displays the initial fade state before the first
        # WaitVBlank loop. Thereafter the donor's numbered schedule applies.
        await next_frame('lcd-start', reference.expected('splash-0', entries))
        for number in range(reference.SETTLED_FRAME + 1):
            await next_frame(f'splash-{number}', reference.expected(f'splash-{number}', entries))
        menu_number = 0
        cursor, result, index = 0, 0, 255

        async def menu_frame(label):
            nonlocal menu_number
            menu_number += 1
            await next_frame(label, reference.frame(entries, cursor=cursor, result=result, index=index,
                                                    phase=reference.phase_of_frame(menu_number)))

        async def tap(mask, label, new_cursor=None, new_result=None, new_index=None):
            nonlocal cursor, result, index
            await FallingEdge(dut.clk_i)
            dut.buttons_i.value = mask
            if new_cursor is not None:
                cursor = new_cursor
            if new_result is not None:
                result = new_result
            if new_index is not None:
                index = new_index
            await menu_frame(label)
            await FallingEdge(dut.clk_i)
            dut.buttons_i.value = 0
            await menu_frame(label + '-release')

        await tap(0x04, 'up-bound', 0)
        await tap(0x08, 'down-1', 1)
        await tap(0x08, 'down-2', 2)
        await tap(0x08, 'down-empty', 3)
        await tap(0x10, 'refused', new_result=2, new_index=3)
        await tap(0x04, 'up-with-message', 2)
        await tap(0x04, 'up-exit-demo', 1)
        # Cross the automatic cursor/star phase boundary with no input.
        while menu_number < 17:
            await menu_frame(f'phase-{menu_number + 1}')
        # The built direct game shows the independently specified solid bar.
        await FallingEdge(dut.clk_i)
        dut.buttons_i.value = 0x10
        await next_frame('exit-demo', fixture.exit_frame())
        await FallingEdge(dut.clk_i)
        dut.buttons_i.value = 0
        await next_frame('exit-demo-idle', fixture.exit_frame())
        # Start is consumed by the actual game, which writes the exit ABI.
        await FallingEdge(dut.clk_i)
        dut.buttons_i.value = 0x80
        await next_frame('returned-menu', reference.frame(entries))
        await FallingEdge(dut.clk_i)
        dut.buttons_i.value = 0
        menu_number, cursor, result, index = 0, 0, 0, 1
        await menu_frame('returned-idle')
        assert len(resets) == 2, ('direct selection and return resets', resets)
        # The owner's 64 KiB fixture selects bank 2 and executes its exit
        # code there. Returning therefore proves both image slots copied.
        for number in range(1, 6):
            await tap(0x08, f'mbc1-down-{number}', number)
        await FallingEdge(dut.clk_i)
        dut.buttons_i.value = 0x10
        await next_frame('mbc1-returned-menu', reference.frame(entries))
        await FallingEdge(dut.clk_i)
        dut.buttons_i.value = 0
        assert len(resets) == 4, ('MBC1 selection and bank-2 return resets', resets)
        assert not faults
        cocotb.log.info('MENU_SCENARIO PASS frames=%d pixels=%d resets=%d', consumed, consumed * 23040, len(resets))
    finally:
        for task in tasks:
            task.cancel()
