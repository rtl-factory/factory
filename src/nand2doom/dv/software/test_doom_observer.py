"""Public-port pulse/burst proof for the long Doom gate's passive observer."""
import os
import json
from pathlib import Path
from types import SimpleNamespace

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import Event, FallingEdge, First, ReadOnly, RisingEdge, Timer

from src.nand2doom.dv.platform.event_monitors import (
    observe_retired_stores, observe_gameplay_retirements, next_writable_input_cycle,
    stop_monitors,
)
from .doom_gameplay import GameplayTrace
from .input_witness import InputWitness
from .input_witness_vectors import abi_fixture, initialized_trace, rows


@cocotb.test(timeout_time=10, timeout_unit="us")
async def retirement_observer_contract(dut):
    trace = GameplayTrace({"gametic": 0x100, "maketic": 0x104, "ticdup": 0x108})
    dut.clock_reset_i.value = 1
    dut.valid_i.value = 0
    dut.memory_valid_i.value = 0
    dut.write_i.value = 0
    dut.trap_i.value = 0
    dut.order_i.value = 0
    dut.tick_i.value = 0
    dut.address_i.value = 0
    dut.data_i.value = 0
    dut.mask_i.value = 0
    dut.uart_rx_i.value = 1
    for name in ('pc_i', 'instruction_i', 'next_pc_i', 'read_data_i',
                 'rd_data_i', 'rd_i', 'read_mask_i', 'rd_write_i'):
        getattr(dut, name).value = 0
    cocotb.start_soon(Clock(dut.clk_i, 40, unit="ns").start())
    observer = cocotb.start_soon(observe_retired_stores(dut, trace))
    await FallingEdge(dut.clk_i)
    assert int(dut.clock_tick_o.value) == 0
    dut.clock_reset_i.value = 0
    cycle = 0

    async def drive(address=0, data=0, mask=15, valid=1, memory_valid=1,
                    write=1, trap=0):
        nonlocal cycle
        cycle += 1
        assert cycle <= int(os.environ["DV_MAX_CYCLES"])
        dut.order_i.value = cycle
        dut.tick_i.value = cycle
        dut.address_i.value = address
        dut.data_i.value = data
        dut.mask_i.value = mask
        dut.valid_i.value = valid
        dut.memory_valid_i.value = memory_valid
        dut.write_i.value = write
        dut.trap_i.value = trap
        await FallingEdge(dut.clk_i)

    # No low gap: all twelve byte stores must be seen, not just the first edge.
    for address in (0x100, 0x104, 0x108):
        for lane in range(4):
            await drive(address + lane, 0xFFFFFFFF ^ (0xFF << (8 * lane)), 1 << lane)
    assert trace.counters == {"gametic": 0, "maketic": 0, "ticdup": 0}
    await drive(0x108, 1)
    for invalid in ({"valid": 0}, {"memory_valid": 0}, {"trap": 1}, {"write": 0}):
        await drive(0x104, 9, **invalid)
    assert trace.counters["maketic"] == 0
    # Another edge followed by consecutive and repeated equal counter stores.
    await drive(0x104, 1)
    await drive(0x104, 1)
    await drive(0x104, 2)
    await drive(0x100, 1)
    await drive(write=0)
    assert trace.counters == {"gametic": 1, "maketic": 2, "ticdup": 1}
    assert [item["value"] for item in trace.counter_events
            if item["name"] == "maketic"] == [0, 1, 1, 2]
    observer.cancel()
    # Await the failing observer directly so the assertion is an expected
    # negative check, and verify evidence survived before it propagated.
    await drive(write=0)
    evidence = Path.cwd() / "expected-observer-failure.json"
    stimulus = cocotb.start_soon(drive(0x100, 9))
    try:
        await observe_retired_stores(dut, trace, evidence)
    except AssertionError as error:
        assert "reset/decreased/jumped" in str(error)
    else:
        raise AssertionError("malformed counter increment was accepted")
    await stimulus
    recorded = json.loads(evidence.read_text())
    assert "reset/decreased/jumped" in recorded["failure"]
    assert recorded["last_store"]["data"] == 9
    assert recorded["known_bytes"] == {"gametic": 15, "maketic": 15, "ticdup": 15}
    cocotb.log.info("PASS store pulse/burst/byte-mask/valid/failure evidence seed=%s cycles=%d",
                    os.environ["DV_SEED"], cycle)
    cycle = await _writable_cycle_contract(dut, cycle)
    cycle = await _input_dispatch_contract(dut, cycle)
    await _monitor_shutdown_contract(dut, cycle)


async def _writable_cycle_contract(dut, cycles):
    """Real posedge counter, ReadOnly events, strict old-path rejection/pin write."""
    clock_dut = SimpleNamespace(clk_i=dut.clk_i, system_tick_o=dut.clock_tick_o)
    for role in ('make', 'break'):
        for schedule in ('latched', 'pending', 'ack-later'):
            if role == 'make' and schedule == 'ack-later':
                continue  # ACK serialization applies to release, after MAKE.
            await FallingEdge(dut.clk_i)
            dut.valid_i.value = 0
            dut.write_i.value = 0
            dut.uart_rx_i.value = 1
            await RisingEdge(dut.clk_i)
            await ReadOnly()
            tick = int(dut.clock_tick_o.value)
            event, ack = Event(), Event()
            if schedule == 'latched':
                event.set()
            if schedule != 'ack-later':
                ack.set()
            abi = abi_fixture()
            witness = InputWitness(abi, initialized_trace(abi), lambda: None, lambda: None)
            witness.boundary_tick = witness.ready_tick = witness.make_ack_tick = tick

            async def driver(old=False):
                await event.wait()
                await ack.wait()
                bound = witness.boundary_tick if role == 'make' else max(
                    witness.ready_tick, witness.make_ack_tick)
                if old:
                    await FallingEdge(dut.clk_i)
                    assert int(dut.clock_tick_o.value) == bound
                    try:
                        getattr(witness, 'start_' + role)(bound)
                    except AssertionError:
                        return
                    raise AssertionError('old same-cycle falling edge was accepted')
                getattr(witness, 'start_' + role)(
                    await next_writable_input_cycle(clock_dut, bound))
                dut.uart_rx_i.value = 0  # A real writable-phase pin operation.
                await ReadOnly()
                assert int(dut.uart_rx_o.value) == 0

            old, corrected = cocotb.start_soon(driver(True)), cocotb.start_soon(driver())
            if schedule != 'latched':
                # Give both drivers time to register before the ReadOnly event.
                await Timer(1, unit='ns')
                await ReadOnly()
                event.set()
            await FallingEdge(dut.clk_i)
            assert int(dut.clock_tick_o.value) == tick and not corrected.done()
            if schedule == 'ack-later':
                await RisingEdge(dut.clk_i)
                await ReadOnly()
                witness.make_ack_tick = int(dut.clock_tick_o.value)
                assert witness.make_ack_tick > witness.ready_tick
                ack.set()
            await old
            await corrected
            bound = witness.boundary_tick if role == 'make' else max(
                witness.ready_tick, witness.make_ack_tick)
            assert getattr(witness, role + '_start_tick') == bound + 1
            cycles += 4 if schedule == 'ack-later' else 3
            assert cycles <= int(os.environ['DV_MAX_CYCLES'])
    # Return to a writable edge before the architectural vector driver starts.
    await FallingEdge(dut.clk_i)
    dut.uart_rx_i.value = 1
    cocotb.log.info('PASS posedge-counter MAKE/BREAK latched/pending/ACK-later old-edge rejection/pin drive')
    return cycles


async def _input_dispatch_contract(dut, cycles):
    """Actual public ports/ReadOnly dispatch and latched falling-edge driver.

    Synthetic ABI/input vectors test the observer, not firmware/UART wire time.
    The separate offline ELF preflight owns actual compiled byte binding.
    """
    abi = abi_fixture()
    trace = initialized_trace(abi, slot=7)
    armed, ready, boundary = Event(), Event(), Event()
    witness = InputWitness(abi, trace, ready.set, boundary.set)
    trace.input_witness = witness
    evidence = Path.cwd() / 'input-dispatch-witness.json'
    dut.write_i.value = 0
    dut.valid_i.value = 0
    observer = cocotb.start_soon(observe_gameplay_retirements(dut, trace, witness, armed, evidence))
    await FallingEdge(dut.clk_i)
    witness.arm(trace.phase_start + 1)
    armed.set()
    mapping = {'order': 'order_i', 'tick': 'tick_i', 'pc': 'pc_i',
               'instruction': 'instruction_i', 'next_pc': 'next_pc_i',
               'valid': 'valid_i', 'trap': 'trap_i', 'memory_valid': 'memory_valid_i',
               'memory_write': 'write_i', 'address': 'address_i', 'write_data': 'data_i',
               'write_mask': 'mask_i', 'read_data': 'read_data_i',
               'read_mask': 'read_mask_i', 'rd': 'rd_i', 'rd_write': 'rd_write_i',
               'rd_data': 'rd_data_i'}
    for index, vector in enumerate(rows(abi, slot=7)):
        cycles += 1
        assert cycles <= int(os.environ['DV_MAX_CYCLES'])
        record = dict(vector, order=100 + index, tick=1000 + 10 * index)
        for name, port in mapping.items():
            getattr(dut, port).value = record[name]
        await FallingEdge(dut.clk_i)
        if boundary.is_set() and witness.make_start_tick is None:
            # Event already latched before waiter registration; driver writes
            # only at FallingEdge, never in the observer's ReadOnly callback.
            await boundary.wait()
            witness.start_make(record['tick'] + 1)
            witness.ack_make(record['tick'] + 2)
        if ready.is_set() and witness.break_start_tick is None:
            await ready.wait()
            witness.start_break(record['tick'] + 3)
            witness.break_ack_tick = record['tick'] + 4
    witness.check_final()
    assert [i['slot'] for i in witness.history] == [7, 8]
    assert [i['turns'] for i in witness.history] == [1, 0]
    trace.save(evidence)
    await stop_monitors(observer)
    assert observer.cancelled()
    cocotb.log.info('PASS public input dispatcher/latched chain/direct BREAK/nonzero slots cycles=%d', cycles)
    return cycles


async def _monitor_shutdown_contract(dut, cycles):
    """Monitors suspended in First, plain edges and finished tasks all stop
    before the test returns, as the long Doom gate's teardown requires.

    The gate once cancelled its monitors and returned at once; cocotb's test
    shutdown cancelled the still-scheduled frame observer a second time while
    it was suspended in First, and it continued running (RuntimeError). This
    contract ends the test the same way the gate does, through stop_monitors.
    """
    first_wakes = {"count": 0}

    async def first_monitor():
        # Same wait shape as the gameplay frame observer: two triggers.
        while True:
            await First(RisingEdge(dut.write_i), dut.clock_tick_o.value_change)
            await ReadOnly()
            first_wakes["count"] += 1

    async def edge_monitor():
        while True:
            await RisingEdge(dut.observation_trap_o)
            await ReadOnly()

    async def single_edge():
        await RisingEdge(dut.clk_i)

    abi = abi_fixture()
    trace = initialized_trace(abi, slot=7)
    armed = Event()
    witness = InputWitness(abi, trace, lambda: None, lambda: None)
    unarmed_observer = cocotb.start_soon(observe_gameplay_retirements(
        dut, trace, witness, armed, Path.cwd() / 'shutdown-witness.json'))
    monitors = [cocotb.start_soon(first_monitor()), cocotb.start_soon(edge_monitor()),
                cocotb.start_soon(single_edge()), unarmed_observer]
    for _ in range(3):
        cycles += 1
        assert cycles <= int(os.environ['DV_MAX_CYCLES'])
        await FallingEdge(dut.clk_i)
    assert first_wakes["count"] == 3, first_wakes
    assert monitors[2].done() and not any(task.done() for task in monitors[:2])
    assert not unarmed_observer.done()
    # Cancelled at a falling edge: the First monitors are suspended in First.
    await stop_monitors(*monitors)
    assert [task.cancelled() for task in monitors] == [True, True, False, True]
    assert monitors[2].exception() is None and first_wakes["count"] == 3
    # The gate stops its framebuffer monitor from a ReadOnly phase at scene
    # completion; a First monitor suspended there must stop the same way.
    late = cocotb.start_soon(first_monitor())
    cycles += 1
    assert cycles <= int(os.environ['DV_MAX_CYCLES'])
    await RisingEdge(dut.clk_i)
    await ReadOnly()
    await stop_monitors(late)
    assert late.cancelled()
    await FallingEdge(dut.clk_i)
    cocotb.log.info('PASS monitor shutdown First/edge/finished/unarmed-observer cycles=%d', cycles)
