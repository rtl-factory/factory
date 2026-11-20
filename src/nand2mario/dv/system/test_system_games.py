"""Real licensed game compatibility scenarios on the unmodified system."""
import hashlib
import json
import os
import random
from collections import Counter, deque
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge, Timer, ValueChange
from cocotb.utils import get_sim_time

from ..core.test_cpu import unpack
from ..video.ppu_model import write_png
from .test_system import idle_host, power_up
from ..joypad.joypad_model import JoypadModel
from ...scripts.regression.game_qualification import check_image, manifest


@cocotb.test()
async def game_program(dut):
    image = Path(os.environ['N2M_SYSTEM_IMAGE']).read_bytes()
    game = os.environ['N2M_GAME']
    pin = manifest()['games'][game]
    check_image(image, pin)
    assert int(os.environ['DV_CLOCK_HZ']) == 25_000_000
    seed = int(os.environ['DV_SEED'])
    cocotb.log.info('licensed game=%s image_sha256=%s seed=%d', game, pin['sha256'], seed)
    power_up(dut, random.Random(seed))
    dut.reset_i.value = 1
    dut.buttons_i.value = 0
    dut.rom_load_write_i.value = 0
    dut.rom_load_address_i.value = 0
    dut.rom_load_data_i.value = 0
    idle_host(dut)
    # The GPI clock keeps the 25 MHz edge stream in cocotb's native layer;
    # Python wakes only for observations, never to accelerate the hardware.
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
    frames, pcs, tail = [], Counter(), deque(maxlen=24)
    aborted_frames = 0
    retirements = 0
    joypad = JoypadModel()
    input_reads = Counter()
    input_epoch_ns = start_ns
    pressed_reads = Counter()
    faults = []

    async def progress():
        nonlocal retirements
        while True:
            await ValueChange(dut.retirement_o)
            record = unpack(int(dut.retirement_o.value))
            if record['valid']:
                retirements += 1
                pcs[record['pc_before']] += 1
                tail.append(record)
                opcode = record['opcode']
                op = opcode & 255
                # The standard GBDK high-I/O and absolute accumulator loads
                # expose their returned A on the public retirement record.
                direct = op in (0xE0, 0xF0) and (opcode >> 8) & 255 == 0
                indirect = op in (0xE2, 0xF2) and record['c'] == 0
                absolute = op in (0xEA, 0xFA) and (opcode >> 8) & 65535 == 0xFF00
                if direct or indirect or absolute:
                    if op in (0xE0, 0xE2, 0xEA):
                        joypad.edge(write=record['a'])
                    elif get_sim_time(unit='ns') - input_epoch_ns > 5120:
                        # A held-input interval: exclude only an instruction
                        # spanning a button transition (128 clocks guard).
                        expected = joypad.read()
                        assert record['a'] == expected, (
                            f"JOYP pc={record['pc_before']:04x}: "
                            f"got {record['a']:02x}, want {expected:02x}, "
                            f"buttons={joypad.buttons:02x}, select={joypad.select}")
                        input_reads[(joypad.buttons, expected)] += 1
                        if joypad.lines():
                            pressed_reads[joypad.buttons] += 1

    async def status(signal, label):
        await RisingEdge(signal)
        faults.append(label)
        raise AssertionError(label)

    async def pixels():
        nonlocal aborted_frames
        buffer = bytearray()
        while True:
            await RisingEdge(dut.pixel_valid_o)
            await ReadOnly()
            x, y = int(dut.pixel_x_o.value), int(dut.pixel_y_o.value)
            if int(dut.frame_start_o.value):
                # LCD-off may abort a partial frame. Only complete frames
                # enter the evidence; a new frame must start at (0, 0).
                aborted_frames += bool(buffer)
                buffer.clear()
            assert int(dut.line_start_o.value) == int(x == 0), ('line marker', x, y)
            assert int(dut.frame_start_o.value) == int(x == 0 and y == 0), ('frame marker', x, y)
            assert (x, y) == (len(buffer) % 160, len(buffer) // 160), ('pixel order', x, y, len(buffer))
            buffer.append(int(dut.pixel_shade_o.value))
            if len(buffer) == 160 * 144:
                frame = bytes(buffer)
                frames.append(frame)
                if len(frames) % 30 == 0:
                    cocotb.log.info('game=%s completed_frames=%d retirements=%d', game, len(frames), retirements)
                buffer.clear()

    tasks = [cocotb.start_soon(progress()), cocotb.start_soon(pixels())]
    tasks += [cocotb.start_soon(status(signal, label)) for signal, label in (
        (dut.fault_o, 'bus fault'), (dut.locked_o, 'illegal opcode lock'),
        (dut.stopped_o, 'unexpected STOP'))]
    samples = []
    try:
        scenario = pin['scenario']
        for label, mask, periods in scenario:
            await FallingEdge(dut.clk_i)
            dut.buttons_i.value = mask
            joypad.edge(buttons=mask)
            input_epoch_ns = get_sim_time(unit='ns')
            previous_retirements, previous_frames = retirements, len(frames)
            await Timer(round(periods * 70224 * 1e9 / 4194304), unit='ns')
            await ReadOnly()
            elapsed = round((get_sim_time(unit='ns') - start_ns) / 40)
            assert elapsed <= int(os.environ['DV_MAX_CYCLES'])
            assert not int(dut.fault_o.value), 'bus fault'
            assert not int(dut.locked_o.value), 'illegal opcode lock'
            assert not int(dut.stopped_o.value), 'unexpected STOP'
            record = dict(label=label, buttons=mask, periods=periods, cycles=elapsed,
                          frames=len(frames), aborted_frames=aborted_frames, retirements=retirements,
                          top_pcs=pcs.most_common(8), last_retirements=list(tail),
                          joypad_reads=[dict(buttons=b, value=v, count=n) for (b,v),n in sorted(input_reads.items())])
            if frames:
                frame = frames[-1]
                record.update(sha256=hashlib.sha256(frame).hexdigest(), shades=dict(Counter(frame)))
                Path(f'{label}.shades').write_bytes(frame)
                write_png(Path(f'{label}.png'), [list(frame[y*160:(y+1)*160]) for y in range(144)])
            samples.append(record)
            Path('game-result.json').write_text(json.dumps(dict(game=game, image_sha256=hashlib.sha256(image).hexdigest(), seed=seed, samples=samples), indent=2)+'\n')
            cocotb.log.info('game sample %s frames=%d retirements=%d pcs=%s', label, len(frames), retirements, pcs.most_common(4))
            assert retirements > previous_retirements, f'no CPU progress during {label}'
            if label in ('title', 'play', 'right'):
                assert len(frames) > previous_frames, f'no completed frame during {label}'
            if label == 'title':
                assert record['sha256'] in pin['title_sha256'], ('independent DMG title mismatch', record['sha256'], pin['title_sha256'])
        assert len(frames) >= 3, 'no completed visible frames'
        assert all(len(set(f)) >= 2 for f in frames[-3:]), 'blank final frames'
        selected = [s for s in samples if s['label'] in ('title', 'play', 'right')]
        assert len(selected) == 3 and len({s['sha256'] for s in selected}) == 3, 'title/play/right must differ'
        assert all(len(s['shades']) >= 2 for s in selected), 'blank selected sample'
        assert pressed_reads[128] and pressed_reads[1], ('CPU did not read Start/Right pressed', dict(pressed_reads))
        assert not faults
    finally:
        for task in tasks:
            task.cancel()
