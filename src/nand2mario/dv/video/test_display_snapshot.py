"""nand2mario display frame snapshot: a synthetic PPU pixel stream drives
n2m_display while the bench pins the displayed frame and reads its record
through the snapshot byte port, against the independent display model's
record encoding and pin policy. Covered: no pin before the first complete
frame, the frame ID and bank identity, the whole record of a pinned frame
read while later frames complete, drop and are held back (origins repeat,
never swap), the VGA output unchanged and frozen on the pinned frame while
the reader shares the store's read port, pin idempotence, release and the
swap of the held fresh frame, a pin and a release in the raster-origin cycle,
the LCD-off blank while pinned (the on-screen flag clears, the pinned bank
stays readable), no pin while the display is blank, and reset clearing the
pin. The pinned bank is also compared word for word with the frame store
itself. Frame contents and the pixel cadence are seeded random."""
import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge, Timer
from cocotb.utils import get_sim_time

from . import display_model as m
from .test_display import (CLOCK_PERIOD_NS, HALF_PERIOD_NS, PULSE_LATENCY, PULSES, Bench, check_lines,
                           check_pulses, random_frame)

OFFSET_BITS = 13
RESPONSE_LIMIT = 2000     # edges a byte may take (at most one image segment of 480 clocks plus four reads)


class SnapshotBench(Bench):
    def __init__(self, dut, seed):
        super().__init__(dut, seed)
        self.model = m.SnapshotBridgeModel()
        self.byte_edges = []      # edges between acceptance and response, per pixel byte

    def idle_snapshot(self):
        d = self.dut
        d.snapshot_pin_i.value = 0
        d.snapshot_release_i.value = 0
        d.snapshot_request_i.value = 0

    async def pulse(self, name, at_edge=None):
        """One-cycle request on ``name``; with ``at_edge`` it is presented in
        cycle ``at_edge - 1`` so that edge ``at_edge`` samples it."""
        d = self.dut
        if at_edge is not None:
            target_ns = at_edge * CLOCK_PERIOD_NS - HALF_PERIOD_NS
            assert target_ns > get_sim_time(unit='ns') + CLOCK_PERIOD_NS, 'request scheduled in the past'
            await Timer(target_ns - 10 - get_sim_time(unit='ns'), unit='ns')
        await FallingEdge(d.clk_i)
        getattr(d, name).value = 1
        await FallingEdge(d.clk_i)
        getattr(d, name).value = 0

    async def pin(self, at_edge=None):
        await self.pulse('snapshot_pin_i', at_edge)

    async def release(self, at_edge=None):
        await self.pulse('snapshot_release_i', at_edge)

    async def read_byte(self, offset):
        """One request/response on the byte port; returns the data byte. The
        request is accepted at the first edge that samples it with ready high."""
        d = self.dut
        await FallingEdge(d.clk_i)
        d.snapshot_request_i.value = (1 << OFFSET_BITS) | offset
        while True:
            await ReadOnly()
            ready = int(d.snapshot_ready_o.value)
            await RisingEdge(d.clk_i)
            if ready:
                break
        accepted = self.now_edge()
        await FallingEdge(d.clk_i)
        d.snapshot_request_i.value = 0
        while True:
            await ReadOnly()
            response = int(d.snapshot_response_o.value)
            if response >> 8 & 1:
                break
            assert self.now_edge() - accepted <= RESPONSE_LIMIT, f'no snapshot response for offset {offset}'
            await RisingEdge(d.clk_i)
        if offset >= m.SNAPSHOT_HEADER_BYTES:
            self.byte_edges.append(self.now_edge() + 1 - accepted)
        await RisingEdge(d.clk_i)
        await ReadOnly()
        assert not (int(d.snapshot_response_o.value) >> 8 & 1), 'a response lasts one cycle'
        return response & 0xFF

    async def read(self, offset, length):
        return bytes([await self.read_byte(offset + index) for index in range(length)])

    def pinned(self):
        return int(self.dut.snapshot_pinned_o.value)

    def bank_contents(self, bank):
        memory = self.dut.u_frame_ram.g_banks[bank].u_ram.u_enabled.memory
        return [[int(memory[y * m.FRAME_WIDTH + x].value) for x in range(m.FRAME_WIDTH)]
                for y in range(m.FRAME_HEIGHT)]


def check_record(record, frame, frame_id, bank, on_screen, label):
    expected = m.snapshot_record(frame, frame_id, bank, on_screen)
    assert len(record) == m.SNAPSHOT_RECORD_BYTES, (label, len(record))
    if record != expected:
        first = next(i for i, (a, b) in enumerate(zip(record, expected)) if a != b)
        raise AssertionError(f'{label}: record byte {first} is {record[first]:#04x}, expected {expected[first]:#04x}')


def header_fields(header):
    assert header[:3] == b'NM\x01', header.hex()
    assert header[3] & 0x7C == 0 and header[3] & 3 < 3, header.hex()
    return header[3] & 3, bool(header[3] >> 7), int.from_bytes(header[4:8], 'little')


@cocotb.test(timeout_time=900, timeout_unit='ms')
async def display_snapshot(dut):
    seed = int(os.environ['DV_SEED'])
    assert int(os.environ['DV_CLOCK_HZ']) == 25_000_000
    cocotb.log.info('nand2mario display snapshot seed=%d', seed)
    b = SnapshotBench(dut, seed)
    model = b.model
    b.power_up()
    b.idle_snapshot()
    cocotb.start_soon(Clock(dut.clk_i, CLOCK_PERIOD_NS, unit='ns').start())
    await b.reset()
    for name in (*PULSES, 'pixel_start_o'):
        cocotb.start_soon(b.watch(name))
    await b.next_start()

    # First frame: nothing is displayed, so a pin has no effect and a read
    # carries no frame.
    await b.pin()
    model.pin()
    assert not b.pinned() and not model.pinned, 'no pin before the first complete frame'
    assert await b.read(0, 8) == bytes(8), 'a read while not pinned answers zero'
    assert await b.read(1000, 2) == bytes(2)

    # Frame A completes and is swapped in; it is frame 1.
    await b.set_lcd(True)
    frame_a = random_frame(b.rng)
    phase_edge = b.now_edge()
    await b.drive_frame(frame_a)
    model.complete('A')
    await b.wait_pulse('frame_swapped_o', phase_edge)
    model.origin()
    assert model.displayed == 'A' and model.displayed_id == 1

    # Pin A: the header names frame 1 and its bank, which holds A word for word.
    await b.pin()
    model.pin()
    assert b.pinned() and model.pinned
    bank, on_screen, frame_id = header_fields(await b.read(0, 8))
    assert (on_screen, frame_id) == (True, 1), (on_screen, frame_id)
    assert b.bank_contents(bank) == frame_a, 'the pinned bank holds frame A'
    cocotb.log.info('frame A pinned: bank %d, frame ID %d', bank, frame_id)

    # While pinned frame B completes and is held back: the origin repeats A
    # (no swap) though B is fresh. Then a whole output frame is captured
    # while the record is read concurrently, so the reader shares the read
    # port during image lines: the output is exactly A. Frame C completes
    # meanwhile and drops B.
    phase_edge = await b.begin_phase()
    frame_b, frame_c = random_frame(b.rng), random_frame(b.rng)
    await b.drive_frame(frame_b, gap_max=1)
    model.complete('B')
    repeat_edge = await b.wait_pulse('frame_repeated_o', phase_edge)
    assert repeat_edge == b.origin_cycle(phase_edge) + PULSE_LATENCY
    assert model.origin() == 'repeat'
    capture = cocotb.start_soon(b.capture_frame())
    await b.next_start()
    record = await b.read(0, m.SNAPSHOT_RECORD_BYTES)
    await b.drive_frame(frame_c, gap_max=1)
    model.complete('C')
    start, clocks = await capture
    assert model.origin() == 'repeat'
    m.compare_clocks(clocks, m.expected_frame(frame_a), label='VGA output while pinned and read')
    check_record(record, frame_a, 1, bank, True, 'pinned frame A')
    check_pulses(b.events_since(phase_edge),
                 ('frame_complete_o', 'frame_repeated_o', 'frame_repeated_o', 'frame_complete_o', 'frame_dropped_o'),
                 'pinned: B held, A repeated, C drops B')
    assert b.bank_contents(bank) == frame_a, 'the pinned bank was never written'
    cocotb.log.info('record of frame A read during a full VGA frame; pixel byte took %d..%d edges',
                    min(b.byte_edges), max(b.byte_edges))
    assert max(b.byte_edges) <= 500, 'a pixel byte took longer than the HAS bound'
    # Negative control: one changed source pixel fails the record check.
    corrupted = [row[:] for row in frame_a]
    corrupted[143][159] ^= 3
    try:
        check_record(record, corrupted, 1, bank, True, 'corrupted')
    except AssertionError:
        pass
    else:
        raise AssertionError('the record check accepted a corrupted frame')

    # Pin again: idempotent, the original frame and ID are kept.
    await b.pin()
    model.pin()
    assert header_fields(await b.read(0, 8)) == (bank, True, 1)

    # Release: the next origin swaps C in (frame 3, B was 2 and dropped).
    phase_edge = b.now_edge()
    await b.release()
    model.release()
    swap_edge = await b.wait_pulse('frame_swapped_o', phase_edge)
    assert model.origin() == 'swap' and model.displayed == 'C' and model.displayed_id == 3
    await b.pin()
    model.pin()
    bank_c, on_screen, frame_id = header_fields(await b.read(0, 8))
    assert (on_screen, frame_id) == (True, 3) and bank_c != bank, (bank_c, frame_id)
    check_record(await b.read(0, m.SNAPSHOT_RECORD_BYTES), frame_c, 3, bank_c, True, 'frame C')
    await b.release()
    model.release()
    cocotb.log.info('release swapped frame C (ID 3) in at edge %d', swap_edge)

    # Pin in the origin cycle: frame D completes, a pin presented in the very
    # origin cycle holds C (the origin repeats, D stays fresh). A release in
    # the next origin cycle lets that origin swap D in (frame 4).
    phase_edge = await b.begin_phase()
    frame_d = random_frame(b.rng)
    await b.drive_frame(frame_d, gap_max=1)
    model.complete('D')
    origin = b.origin_cycle(b.now_edge() + 10)
    await b.pin(at_edge=origin + 1)
    model.pin()
    assert model.origin() == 'repeat'
    assert header_fields(await b.read(0, 8)) == (bank_c, True, 3), 'the pin held the frame displayed before the origin'
    await b.release(at_edge=origin + m.FRAME_CLOCKS + 1)
    model.release()
    swap_edge = await b.wait_pulse('frame_swapped_o', phase_edge)
    assert swap_edge == origin + m.FRAME_CLOCKS + PULSE_LATENCY, (swap_edge, origin)
    assert model.origin() == 'swap' and model.displayed_id == 4
    check_pulses(b.events_since(phase_edge), ('frame_complete_o', 'frame_repeated_o', 'frame_swapped_o'),
                 'pin and release at the origin')
    _, captured = await b.capture_lines(b.sample_lines())
    check_lines(captured, frame_d, 'frame D after the origin release')
    cocotb.log.info('pin and release in the origin cycle verified')

    # LCD off while pinned: D pinned; the next origin with the LCD off blanks
    # the window and clears the on-screen flag, but the pin and the bank stay
    # readable. After the release nothing is displayed, so a pin fails.
    await b.pin()
    model.pin()
    bank_d, on_screen, frame_id = header_fields(await b.read(0, 8))
    assert (on_screen, frame_id) == (True, 4)
    await b.set_lcd(False)
    phase_edge = await b.begin_phase()
    assert model.origin(lcd_on=False) == 'blank'
    assert int(dut.display_valid_o.value) == 0 and b.pinned()
    check_record(await b.read(0, m.SNAPSHOT_RECORD_BYTES), frame_d, 4, bank_d, False, 'frame D after an LCD-off blank')
    _, captured = await b.capture_lines(b.sample_lines())
    check_lines(captured, None, 'blank window while pinned with the LCD off')
    await b.release()
    model.release()
    await b.pin()
    model.pin()
    assert not b.pinned() and not model.pinned, 'no pin while the display is blank'
    # LCD on again and frame E completes: it swaps in and can be pinned.
    await b.set_lcd(True)
    frame_e = random_frame(b.rng)
    phase_edge = await b.begin_phase()
    await b.drive_frame(frame_e, gap_max=1)
    model.complete('E')
    await b.wait_pulse('frame_swapped_o', phase_edge)
    assert model.origin() == 'swap' and model.displayed_id == 5
    await b.pin()
    model.pin()
    bank_e, on_screen, frame_id = header_fields(await b.read(0, 8))
    assert (on_screen, frame_id) == (True, 5)
    check_record(await b.read(0, m.SNAPSHOT_RECORD_BYTES), frame_e, 5, bank_e, True, 'frame E')
    cocotb.log.info('LCD-off blank under a pin and recovery verified')

    # Reset clears the pin and restarts the frame IDs.
    await FallingEdge(dut.clk_i)
    dut.reset_i.value = 1
    await FallingEdge(dut.clk_i)
    dut.reset_i.value = 0
    model.reset()
    assert not b.pinned(), 'reset clears the pin'
    assert await b.read(0, 4) == bytes(4)
    b.check_bound()
    cocotb.log.info('display snapshot checks passed seed=%d edges=%d pixel_byte_edges=%d..%d',
                    seed, b.now_edge(), min(b.byte_edges), max(b.byte_edges))
