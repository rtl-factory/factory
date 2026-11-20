"""Original nand2mario games (sw-games.json) on the unmodified system.

The image is built from committed source and checked against its recorded
SHA-256. A finite button scenario runs; each step must complete frames with
no bus fault, illegal-opcode lock or STOP. The last frame of each step is
written as shades/PNG evidence; title, play and right frames must be
non-blank and pairwise different (the program reacts to Start and Right).
"""
import hashlib
import json
import os
import random
from collections import Counter
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge, Timer
from cocotb.utils import get_sim_time

from ..video.ppu_model import write_png
from .test_system import idle_host, power_up

FRAME_NS = 70224 * 1e9 / 4194304


@cocotb.test()
async def sw_game_program(dut):
    game = os.environ['N2M_GAME']
    spec = json.loads(Path(os.environ['N2M_SW_GAMES']).read_text())['games'][game]
    image = Path(os.environ['N2M_SYSTEM_IMAGE']).read_bytes()
    assert hashlib.sha256(image).hexdigest() == spec['image_sha256'], 'image differs from recorded SHA-256'
    assert int(os.environ['DV_CLOCK_HZ']) == 25_000_000
    bound = int(os.environ['DV_MAX_CYCLES'])
    power_up(dut, random.Random(int(os.environ['DV_SEED'])))
    dut.reset_i.value = 1
    dut.buttons_i.value = 0
    dut.rom_load_write_i.value = 0
    dut.rom_load_address_i.value = 0
    dut.rom_load_data_i.value = 0
    idle_host(dut)
    Clock(dut.clk_i, 40, unit='ns', impl='gpi').start()
    await RisingEdge(dut.clk_i)
    for address, byte in enumerate(image):
        await FallingEdge(dut.clk_i)
        dut.rom_load_write_i.value = 1
        dut.rom_load_address_i.value = address
        dut.rom_load_data_i.value = byte
        await RisingEdge(dut.clk_i)
    await FallingEdge(dut.clk_i)
    dut.rom_load_write_i.value = 0
    await RisingEdge(dut.clk_i)
    await FallingEdge(dut.clk_i)
    dut.reset_i.value = 0
    start_ns = get_sim_time(unit='ns')
    frames, faults = [], []

    async def pixels():
        buffer = bytearray()
        while True:
            await RisingEdge(dut.pixel_valid_o)
            await ReadOnly()
            x, y = int(dut.pixel_x_o.value), int(dut.pixel_y_o.value)
            if int(dut.frame_start_o.value):
                buffer.clear()
            assert (x, y) == (len(buffer) % 160, len(buffer) // 160), ('pixel order', x, y, len(buffer))
            buffer.append(int(dut.pixel_shade_o.value))
            if len(buffer) == 160 * 144:
                frames.append(bytes(buffer))
                buffer.clear()

    async def status(signal, label):
        await RisingEdge(signal)
        faults.append(label)

    tasks = [cocotb.start_soon(pixels())] + [cocotb.start_soon(status(s, l)) for s, l in (
        (dut.fault_o, 'bus fault'), (dut.locked_o, 'illegal opcode lock'), (dut.stopped_o, 'STOP'))]
    samples = []
    try:
        for label, mask, periods in spec['scenario']:
            await FallingEdge(dut.clk_i)
            dut.buttons_i.value = mask
            before = len(frames)
            await Timer(round(periods * FRAME_NS), unit='ns')
            await ReadOnly()
            cycles = round((get_sim_time(unit='ns') - start_ns) / 40)
            assert cycles <= bound, 'finite bound exceeded'
            assert not faults, faults
            assert len(frames) > before, f'no completed frame during {label}'
            frame = frames[-1]
            samples.append(dict(label=label, buttons=mask, periods=periods, cycles=cycles,
                                frames=len(frames), sha256=hashlib.sha256(frame).hexdigest(),
                                shades=dict(sorted(Counter(frame).items()))))
            Path(f'{label}.shades').write_bytes(frame)
            write_png(Path(f'{label}.png'), [list(frame[y * 160:(y + 1) * 160]) for y in range(144)])
            Path('game-result.json').write_text(json.dumps(dict(game=game, samples=samples), indent=2) + '\n')
            cocotb.log.info('sw game %s %s frames=%d sha=%s', game, label, len(frames), samples[-1]['sha256'][:12])
        chosen = {s['label']: s for s in samples}
        key = [chosen[l] for l in ('title', 'play', 'right')]
        assert all(len(s['shades']) >= 2 for s in key), 'blank title/play/right frame'
        assert len({s['sha256'] for s in key}) == 3, 'title/play/right frames must differ'
    finally:
        for task in tasks:
            task.cancel()
