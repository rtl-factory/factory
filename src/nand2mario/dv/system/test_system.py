"""n2m_system smoke: a locally built program runs out of RTL memory with the
real timebase, every retirement (including the timer, joypad and serial
interrupt entries) matches the independent CPU oracle over the Python memory
map with the timer, interrupt-flag, joypad and serial models, the bench
presses buttons at labelled program points, the serial port transmits the
exact byte string, and the final WRAM/HRAM signature is exact."""
import json
import os
import random
import time
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge

from ..core.cpu_model import CPU
from ..core.test_cpu import unpack
from ..joypad.joypad_model import A, DOWN, RIGHT, START, UP
from ..memory.memory_model import GameBoyMemory
from .peripheral_model import Peripherals

CLOCK_PERIOD_NS = 40
VRAM_BYTES = 8192
MACHINE_CYCLE_EDGES = 390625 / 16384   # Why: the exact 25 MHz / 1,048,576 Hz ratio, not a rounded one.
INSTRUCTION_BOUND = 1500
HALT_OPCODE = 0x76
# Why: 6 = one cycle for the flag set at the end of a tick (a TIMA
# reload or serial completion tick, or the edge after a joypad line fell) to
# become visible at the next boundary, plus the five-cycle entry it starts
# directly, up to the entry's retirement by a CPU sleeping in HALT.
# IME-enabled HALT must not insert a discarded opcode-fetch cycle.
HALT_ENTRY_CYCLES = 6
TIMER_BIT, SERIAL_BIT, JOYPAD_BIT = 2, 3, 4
BUTTON_SCRIPT = {'joypad_press_1': RIGHT | UP | A | START, 'joypad_release': 0, 'joypad_woken': DOWN}
SERIAL_STRING = b'N2M' + bytes([0xE7])


def ticks_at(edges):
    """Machine-cycle enables sampled in the first ``edges`` active edges
    after reset release (core HAS timebase contract)."""
    return edges * 16384 // 390625


def compare_signature(observed, expected, label):
    """Exact byte comparison with the first mismatch reported."""
    assert len(observed) == len(expected), (label, len(observed), len(expected))
    for offset, (got, want) in enumerate(zip(observed, expected)):
        assert got == want, f'{label}[{offset:04x}] observed {got:02x} expected {want:02x}'


def load_image(button_script=None):
    """The 32 KiB image; with ``button_script`` (label -> buttons) also the
    program addresses of those labels, from the symbols the assembler wrote
    next to the image."""
    root = Path(__file__).resolve().parents[4]
    image_arg = os.environ.get('N2M_SYSTEM_IMAGE')
    assert image_arg, 'set N2M_SYSTEM_IMAGE to the locally built system program'
    image = (root / image_arg).resolve()
    assert image.is_relative_to(root / 'workdir') and image.is_file(), 'image must exist below repository workdir'
    data = image.read_bytes()
    assert len(data) == 32768, 'the direct-entry image is exactly 32 KiB'
    if button_script is None:
        return data
    symbols = {entry['symbol']: entry['value']
               for entry in json.loads((image.parent / 'symbols.json').read_text())['symbols']}
    triggers = {symbols[label]: buttons for label, buttons in button_script.items()}
    assert len(triggers) == len(button_script), 'button-script labels must be distinct program addresses'
    return data, triggers


def store_array(ram):
    """The storage array of one n2m_dual_port_ram instance in either backend:
    the portable array, or the ASIC backend's two-port array or shared
    single-port store (n2m_dual_port_ram_asic.sv)."""
    for path in (('memory',), ('g_two_port', 'memory'), ('g_shared_sram', 'u_store', 'memory')):
        handle = ram
        try:
            for name in path:
                handle = getattr(handle, name)
        except AttributeError:
            continue
        return handle
    raise AttributeError(f'no storage array under {ram._path}')


def store_bytes(array):
    return bytes(int(array[index].value) for index in range(len(array)))


def defined_signature(observed, memory, name):
    """The program-defined bytes of one store as observed/expected byte strings."""
    pairs = memory.defined_bytes(name)
    return bytes(observed[offset] for offset, _ in pairs), bytes(value for _, value in pairs)


def fill_garbage(array, rng, count=None):
    """Seeded nonzero garbage into the first `count` words of one store array
    (all of it by default)."""
    for index in range(len(array) if count is None else count):
        array[index].value = rng.randrange(1, 256)


def power_up(dut, rng, rom_bytes=32768, onchip=True):
    """Stores have no power-up value: fill them with seeded nonzero garbage
    through the simulator before the first edge, so only bytes the program
    defines can match the oracle. With the on-chip backing (`onchip`) its
    ROM store is filled over `rom_bytes` (the load must overwrite every
    garbage byte the program can reach; a byte beyond the image is undefined
    to the strict oracle anyway; 0 skips the ROM store, whose simulator
    handle is costly for large backings) and its cartridge RAM store
    entirely; the SDRAM backing's device model fills itself."""
    started = time.monotonic()
    if onchip:
        backing = dut.g_onchip.u_backing
        if rom_bytes:
            fill_garbage(store_array(backing.u_rom), rng, rom_bytes)
        fill_garbage(store_array(backing.u_ram), rng)
    for store in (dut.u_memory.u_vram, dut.u_memory.u_wram, dut.u_memory.u_oam, dut.u_memory.u_hram):
        fill_garbage(store_array(store), rng)
    cocotb.log.info('power-up garbage fill took %.2f s', time.monotonic() - started)


def idle_host(dut):
    """The loader's serial line idles high and the SDRAM data input is parked;
    the bench of an on-chip system never uses either."""
    dut.uart_rx_i.value = 1
    dut.dram_dq_i.value = 0


def interrupt_entry(model, memory, peripherals):
    """Expected retirement record of an interrupt entry and the oracle's
    state change: the lowest enabled pending flag is acknowledged, its
    vector entered, the return PC pushed and IME cleared."""
    pending = memory.interrupt_enable() & peripherals.flags.flags
    assert model.ime and pending, 'RTL entered an interrupt the oracle does not have pending'
    bit = (pending & -pending).bit_length() - 1
    vector = 0x40 + 8 * bit
    expected = {'pc_before': model.pc, 'pc_after': vector, 'opcode': 0, 'opcode_length': 0,
                'a': model.r[7], 'f': model.f, **dict(zip('bcdehl', model.r[:6])),
                'sp': (model.sp - 2) & 0xFFFF, 'ime': 0, 'halted': 0, 'stopped': 0, 'is_interrupt': 1}
    model.push(model.pc)
    model.pc, model.ime, model.delayed, model.halted = vector, False, False, False
    peripherals.acknowledge(bit)
    return expected, bit


@cocotb.test(timeout_time=20, timeout_unit='ms')
async def system_smoke(dut):
    seed = int(os.environ['DV_SEED'])
    bound = int(os.environ['DV_MAX_CYCLES'])
    assert int(os.environ['DV_CLOCK_HZ']) == 25_000_000
    cocotb.log.info('n2m_system smoke seed=%d (directed) bound=%d', seed, bound)
    image, triggers = load_image(BUTTON_SCRIPT)
    cycles = 0
    power_up(dut, random.Random(seed))

    dut.reset_i.value = 1
    dut.buttons_i.value = 0
    dut.rom_load_write_i.value = 0
    dut.rom_load_address_i.value = 0
    dut.rom_load_data_i.value = 0
    idle_host(dut)
    cocotb.start_soon(Clock(dut.clk_i, CLOCK_PERIOD_NS, unit='ns').start())
    await RisingEdge(dut.clk_i)

    for address, byte in enumerate(image):
        await FallingEdge(dut.clk_i)
        dut.rom_load_write_i.value = 1
        dut.rom_load_address_i.value = address
        dut.rom_load_data_i.value = byte
        await RisingEdge(dut.clk_i)
        cycles += 1
    await FallingEdge(dut.clk_i)
    dut.rom_load_write_i.value = 0
    await RisingEdge(dut.clk_i)
    await FallingEdge(dut.clk_i)
    # Ref: Pan Docs "Power Up Sequence": the DMG boot ROM leaves VRAM zeroed, so the reset
    # held over the 32 KiB load must sweep the garbage-filled VRAM to zero.
    assert store_bytes(store_array(dut.u_memory.u_vram)) == bytes(VRAM_BYTES), 'VRAM not cleared during reset'
    dut.reset_i.value = 0
    release_edge = cycles

    peripherals = Peripherals()
    memory = GameBoyMemory(image, io_read=peripherals.read, io_write=peripherals.write)
    model = CPU(mem=memory)
    retirements, interrupts = 0, []
    anchor_edge = anchor_cycles = None
    halt_edge = None
    halted = False
    serial_observed = []
    joypad_wake_entry = None
    while not halted:
        await RisingEdge(dut.clk_i)
        await ReadOnly()
        cycles += 1
        assert cycles <= bound, f'finite bound exceeded after {retirements} retirements'
        assert not int(dut.fault_o.value), 'unexpected bus fault'
        assert not int(dut.locked_o.value), 'unexpected illegal-opcode lock'
        assert not int(dut.stopped_o.value), 'unexpected STOP'
        if int(dut.serial_tx_valid_o.value):
            serial_observed.append((ticks_at(cycles - release_edge), int(dut.serial_tx_data_o.value)))
        record = unpack(int(dut.retirement_o.value))
        if not record['valid']:
            continue
        # Why: the oracle's peripherals are advanced to the machine cycle in which
        # the retiring instruction touched the bus: the last cycle of a load
        # or store fetches the next opcode, so its data access and the vector
        # selection of an interrupt entry see the state two ticks before the
        # completing edge. HALT samples the pending flags at its completing
        # edge, where a reload on the previous tick is already visible.
        ticks = ticks_at(cycles - release_edge)
        if record['is_interrupt']:
            peripherals.advance(ticks - 2)
            expected, bit = interrupt_entry(model, memory, peripherals)
            interrupts.append((ticks, bit))
            instruction_cycles = None
        else:
            peripherals.advance(ticks - (1 if memory[model.pc] == HALT_OPCODE else 2))
            pending = memory.interrupt_enable() & peripherals.flags.flags
            expected, instruction_cycles, _ = model.step(pending=pending)
        mismatch = {k: (record[k], v) for k, v in expected.items() if record[k] != v}
        assert not mismatch, (f'retirement {retirements} pc={expected["pc_before"]:04x}', mismatch)
        retirements += 1
        assert retirements <= INSTRUCTION_BOUND, 'program did not halt within the instruction bound'
        # Why: composed timing: between consecutive retirements of one run of
        # instructions the edges follow the 23/24-edge machine cadence with
        # no stall; a HALT sleep or interrupt entry starts a new run.
        if anchor_edge is not None and instruction_cycles is not None:
            anchor_cycles += instruction_cycles
            drift = (cycles - anchor_edge) - anchor_cycles * MACHINE_CYCLE_EDGES
            assert abs(drift) < MACHINE_CYCLE_EDGES, (f'retirement {retirements}: edges since anchor '
                                                      f'{cycles - anchor_edge} expected about {anchor_cycles * MACHINE_CYCLE_EDGES:.1f}')
        if anchor_edge is None or instruction_cycles is None or record['halted']:
            anchor_edge, anchor_cycles = cycles, 0
        if record['halted'] and not halt_edge:
            halt_edge = cycles
        # Why: a HALT with IME set and an enabled source sleeps until the interrupt
        # wakes it; only a HALT that nothing can wake ends the run.
        halted = bool(record['halted']) and not (memory.interrupt_enable() & 0x1F and model.ime)
        # Why: button script: the bench changes the buttons on the falling edge
        # after the triggering retirement. The joypad samples them on the
        # next edge and a selected line falling sets the flag on the edge
        # after that, so the CPU can see it from the following tick.
        if record['pc_after'] in triggers and not record['is_interrupt']:
            await FallingEdge(dut.clk_i)
            buttons = triggers.pop(record['pc_after'])
            dut.buttons_i.value = buttons
            flag_edge = cycles + 2
            peripherals.press(buttons, ticks_at(flag_edge - release_edge) + 1)
            if record['halted']:
                joypad_wake_entry = ticks_at(flag_edge - release_edge) + HALT_ENTRY_CYCLES
    for _ in range(4):
        await RisingEdge(dut.clk_i)
        await ReadOnly()
    assert int(dut.halted_o.value) == 1, 'CPU must report HALT after the halting retirement'

    assert not triggers, ('button script points never reached', triggers)
    by_bit = {bit: [tick for tick, b in interrupts if b == bit] for bit in (TIMER_BIT, SERIAL_BIT, JOYPAD_BIT)}
    assert len(interrupts) == sum(len(v) for v in by_bit.values()), ('unexpected interrupt entries', interrupts)
    assert len(peripherals.reload_ticks) == 1, ('TIMA reloads predicted by the oracle', peripherals.reload_ticks)
    entry_tick, reload_tick = by_bit[TIMER_BIT][0], peripherals.reload_ticks[0]
    assert by_bit[TIMER_BIT] == [reload_tick + HALT_ENTRY_CYCLES], (
        f'timer entries {by_bit[TIMER_BIT]}, reload at tick {reload_tick}')
    assert joypad_wake_entry is not None and by_bit[JOYPAD_BIT] == [joypad_wake_entry], (
        f'joypad entries {by_bit[JOYPAD_BIT]}, expected {joypad_wake_entry}')
    # Why: two scripted presses fell on a selected line: the first press under the
    # direction row (IE zero, flag only) and the wake press.
    assert len(peripherals.joypad_event_ticks) == 2, ('bench presses that raised the flag', peripherals.joypad_event_ticks)
    completion_ticks = [tick for tick, _ in peripherals.completions]
    assert len(completion_ticks) == len(SERIAL_STRING), ('serial completions predicted by the oracle', peripherals.completions)
    assert by_bit[SERIAL_BIT] == [tick + HALT_ENTRY_CYCLES for tick in completion_ticks], (
        f'serial entries {by_bit[SERIAL_BIT]}, completions {completion_ticks}')
    assert halt_edge is not None and halt_edge < cycles, 'the program must sleep in HALT before the interrupt'

    observed_string = bytes(byte for _, byte in serial_observed)
    compare_signature(observed_string, SERIAL_STRING, 'SERIAL')
    assert serial_observed == peripherals.completions, ('serial observation ticks', serial_observed, peripherals.completions)

    # Why: bytes the program never wrote have no expected value, so the signature
    # covers only the bytes the program defined, read back from the RTL stores.
    stores = {'wram': store_array(dut.u_memory.u_wram), 'hram': store_array(dut.u_memory.u_hram),
              'vram': store_array(dut.u_memory.u_vram), 'oam': store_array(dut.u_memory.u_oam)}
    signatures = {}
    for name, array in stores.items():
        observed, expected = defined_signature(store_bytes(array), memory, name)
        compare_signature(observed, expected, name.upper())
        signatures[name] = (observed, expected)
    assert int(dut.u_memory.interrupt_enable_o.value) == memory.interrupt_enable()
    assert int(dut.u_interrupts.interrupt_flags_o.value) == peripherals.flags.flags
    assert len(signatures['wram'][0]) >= 16 and len(signatures['hram'][0]) == 127, (
        'the program must define a WRAM signature and every HRAM byte')
    # Why: timer evidence pinned independently of the oracle: the reloaded TMA
    # value seen after the interrupt, the acknowledged flag, one handler run.
    wram = store_bytes(store_array(dut.u_memory.u_wram))
    assert wram[0x13] == 0xA5 and wram[0x14] == 0xE0, ('post-interrupt TIMA/IF', wram[0x13], wram[0x14])
    assert wram[0xF0] == 1 and wram[0xF1] == 0xA5 and wram[0xF2] == 0xE0, ('handler evidence', wram[0xF0:0xF3].hex())
    assert wram[0x0E] == 0xFB and wram[0x11] == 0x00 and wram[0x12] == 0x01, ('TAC/DIV evidence', wram[0x0E], wram[0x11], wram[0x12])
    # Why: joypad evidence pinned independently of the oracle: the row reads under
    # the scripted buttons, the flag a select write raised, the P1 byte after
    # the wake, the handler count and its reads.
    assert wram[0x17:0x21].hex() == 'ffead6c2f0ffe0e7e0e0', ('joypad evidence', wram[0x17:0x21].hex())
    assert wram[0xF3:0xF6].hex() == '01e7e0', ('joypad handler evidence', wram[0xF3:0xF6].hex())
    # Why: serial evidence pinned independently of the oracle: SB/SC after the last byte, the external-clock wait,
    # the cancelled transfer, four handler runs and their reads.
    assert wram[0x21:0x27].hex() == 'ff7ffe5a7ee0', ('serial evidence', wram[0x21:0x27].hex())
    assert wram[0xF6:0xF9].hex() == '04ff7f', ('serial handler evidence', wram[0xF6:0xF9].hex())

    # Why: proves the check can fail: the signature comparison must reject a single flipped byte.
    corrupted = bytearray(signatures['wram'][1])
    corrupted[0] ^= 0x01
    try:
        compare_signature(signatures['wram'][0], bytes(corrupted), 'WRAM-negative')
    except AssertionError:
        pass
    else:
        raise AssertionError('signature comparison accepted a corrupted expectation')
    # Why: proves the check can fail: the serial string comparison must reject one wrong byte.
    try:
        compare_signature(observed_string, b'N2m' + SERIAL_STRING[3:], 'SERIAL-negative')
    except AssertionError:
        pass
    else:
        raise AssertionError('serial comparison accepted a corrupted expectation')
    cocotb.log.info('n2m_system smoke passed: retirements=%d timer_entry_tick=%d reload_tick=%d '
                    'joypad_entry_tick=%d serial_entries=%s total_cycles=%d',
                    retirements, entry_tick, reload_tick, joypad_wake_entry, by_bit[SERIAL_BIT], cycles)
    cocotb.log.info('defined signature bytes: %s', {k: len(v[0]) for k, v in signatures.items()})
