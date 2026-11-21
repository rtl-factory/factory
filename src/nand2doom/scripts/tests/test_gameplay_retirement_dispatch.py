"""Run the actual async dispatcher with independent host edge scheduling.

This checks dispatch ordering/arming/latching, not Verilator or UART timing.
"""
import asyncio
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from src.nand2doom.dv.platform import event_monitors as monitors
from src.nand2doom.dv.software.doom_gameplay import GameplayTrace
from src.nand2doom.dv.software.input_witness import InputWitness
from src.nand2doom.dv.software.input_witness_vectors import abi_fixture, initialized_trace


class Edge:
    def __init__(self, value=0):
        self.value = value
        self.waiters = []

    async def wait(self):
        future = asyncio.get_running_loop().create_future()
        self.waiters.append(future)
        await future

    def fire(self):
        waiters, self.waiters = self.waiters, []
        for future in waiters:
            if not future.done():
                future.set_result(None)


class Latch(Edge):
    def __init__(self):
        super().__init__()
        self.latched = False

    def set(self):
        self.latched = True
        self.fire()

    async def wait(self):
        if not self.latched:
            await super().wait()


async def first(*awaitables):
    tasks = [asyncio.ensure_future(a) for a in awaitables]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def settle():
    for _ in range(16):
        await asyncio.sleep(0)


class DispatcherTests(unittest.IsolatedAsyncioTestCase):
    async def test_make_and_break_same_tick_event_require_next_clock(self):
        for role in ('make', 'break'):
            with self.subTest(role=role):
                rising, falling, event = Edge(), Edge(), Latch()
                dut = SimpleNamespace(clk_i=object(), system_tick_o=Edge(42))
                a = abi_fixture()
                w = InputWitness(a, initialized_trace(a), lambda: None, lambda: None)
                w.boundary_tick = w.ready_tick = w.make_ack_tick = 42
                start = getattr(w, 'start_' + role)
                event.set()  # Why: set first: the retirement ReadOnly callback precedes the driver wait.

                async def old_driver():
                    await event.wait()
                    await falling.wait()
                    start(int(dut.system_tick_o.value))

                old = asyncio.create_task(old_driver())
                await settle()
                falling.fire()  # Why: the falling edge is the same retirement cycle's writable phase.
                with self.assertRaises(AssertionError):
                    await old
                with patch.object(monitors, 'RisingEdge', lambda _: rising.wait()), patch.object(monitors, 'FallingEdge', lambda _: falling.wait()):
                    async def corrected_driver():
                        await event.wait()
                        start(await monitors.next_writable_input_cycle(dut, 42))
                    driver = asyncio.create_task(corrected_driver())
                    await settle()
                    falling.fire()
                    await settle()
                    self.assertFalse(driver.done(), 'same-tick falling edge drove input')
                    dut.system_tick_o.value = 43
                    rising.fire()
                    await settle()
                    self.assertFalse(driver.done(), 'rising phase is not writable input edge')
                    falling.fire()
                    await driver
                self.assertEqual(43, getattr(w, role + '_start_tick'))

    async def test_next_clock_with_unchanged_counter_fails(self):
        rising, falling = Edge(), Edge()
        dut = SimpleNamespace(clk_i=object(), system_tick_o=Edge(42))
        with patch.object(monitors, 'RisingEdge', lambda _: rising.wait()), patch.object(monitors, 'FallingEdge', lambda _: falling.wait()):
            driver = asyncio.create_task(monitors.next_writable_input_cycle(dut, 42))
            await settle()
            rising.fire()
            await settle()
            falling.fire()
            with self.assertRaisesRegex(AssertionError, 'did not leave witnessed cycle'):
                await driver

    async def test_pending_event_and_later_ack_keep_driver_serialized(self):
        for role, ack_later in (('make', False), ('break', False), ('break', True)):
            with self.subTest(role=role, ack_later=ack_later):
                rising, falling, event, ack = Edge(), Edge(), Latch(), Latch()
                dut = SimpleNamespace(clk_i=object(), system_tick_o=Edge(42))
                a = abi_fixture()
                w = InputWitness(a, initialized_trace(a), lambda: None, lambda: None)
                w.boundary_tick = w.ready_tick = w.make_ack_tick = 42
                if not ack_later:
                    ack.set()
                with patch.object(monitors, 'RisingEdge', lambda _: rising.wait()), patch.object(monitors, 'FallingEdge', lambda _: falling.wait()):
                    async def drive():
                        await event.wait()
                        await ack.wait()
                        bound = w.boundary_tick if role == 'make' else max(w.ready_tick, w.make_ack_tick)
                        getattr(w, 'start_' + role)(await monitors.next_writable_input_cycle(dut, bound))
                    driver = asyncio.create_task(drive())
                    await settle()
                    self.assertEqual(1, len(event.waiters))
                    event.set()
                    await settle()
                    if ack_later:
                        self.assertEqual([], rising.waiters, 'release preceded MAKE ACK')
                        dut.system_tick_o.value = w.make_ack_tick = 43
                        ack.set()
                        await settle()
                    bound = dut.system_tick_o.value
                    falling.fire()
                    await settle()
                    self.assertFalse(driver.done())
                    dut.system_tick_o.value = bound + 1
                    rising.fire()
                    await settle()
                    falling.fire()
                    await driver
                self.assertEqual(bound + 1, getattr(w, role + '_start_tick'))

    async def test_actual_dispatcher_contiguous_reads_arm_and_counter_before_witness(self):
        names = ['valid', 'memory_valid', 'memory_write', 'order', 'pc', 'instruction',
                 'next_pc', 'rd_write', 'rd_address', 'rd_write_data', 'memory_address',
                 'memory_read_data', 'memory_write_data', 'memory_read_mask',
                 'memory_write_mask', 'trap']
        dut = SimpleNamespace(clk_i=Edge(), system_tick_o=Edge())
        for name in names:
            setattr(dut, 'observation_' + name + '_o', Edge())
        trace = GameplayTrace({'gametic': 0x100, 'maketic': 0x104, 'ticdup': 0x108})
        armed = Latch()
        received = []
        witness = SimpleNamespace(armed=False, completed=False)
        witness.accept = lambda r: received.append((dict(r), dict(trace.counters)))
        async def readonly():
            await asyncio.sleep(0)
        with tempfile.TemporaryDirectory() as temp, patch.object(monitors, 'RisingEdge', lambda p: p.wait()), patch.object(monitors, 'ReadOnly', readonly), patch.object(monitors, 'First', first):
            evidence = Path(temp) / 'trace.json'
            observer = asyncio.create_task(monitors.observe_gameplay_retirements(
                dut, trace, witness, armed, evidence))
            await settle()
            order = 0
            async def drive(write=0, valid=1, address=0, data=0, rd=15):
                nonlocal order
                order += 1
                old_write = dut.observation_memory_write_o.value
                values = dict(valid=valid, memory_valid=1, memory_write=write,
                              order=order, pc=0x4000, instruction=0x2083,
                              next_pc=0x4004, rd_write=not write, rd_address=rd,
                              rd_write_data=data, memory_address=address,
                              memory_read_data=data, memory_write_data=data,
                              memory_read_mask=0 if write else 15,
                              memory_write_mask=15 if write else 0, trap=0)
                for name, value in values.items():
                    getattr(dut, 'observation_' + name + '_o').value = value
                dut.system_tick_o.value = order
                if write and not old_write:
                    dut.observation_memory_write_o.fire()
                dut.clk_i.fire()
                await settle()
            # Why: contiguous startup stores go through the real trace checker, not a stub.
            for address in (0x100, 0x104, 0x108):
                await drive(write=1, address=address)
            self.assertEqual({'gametic': 0, 'maketic': 0, 'ticdup': 0}, trace.counters)
            await drive(valid=0)
            # Why: arming between samples must not replay a stale valid, load or store.
            witness.armed = True
            armed.set()
            await settle()
            self.assertEqual([], received)
            await drive(data=5)
            await drive(data=5)  # Why: adjacent equal values are separate retirements, so both must be received.
            await drive(valid=0)
            await drive(write=1, address=0x104, data=1)
            self.assertEqual([5, 6, 8], [r['order'] for r, _ in received])
            self.assertEqual(1, received[-1][1]['maketic'])
            self.assertEqual(8, trace.last_order)
            witness.completed = True
            await drive(valid=0)
            await drive(write=1, address=0x104, data=1)
            self.assertEqual(3, len(received))
            self.assertEqual(10, trace.last_order)
            # Why: a malformed later store must still fail the original checker and save
            # evidence after the supplemental witness has completed.
            await drive(write=1, address=0x104, data=9)
            with self.assertRaisesRegex(AssertionError, 'reset/decreased/jumped'):
                await observer
            self.assertIn('reset/decreased/jumped', json.loads(evidence.read_text())['failure'])

    async def test_latched_chain_event_before_ack_waiter(self):
        event = Latch()
        event.set()
        await asyncio.wait_for(event.wait(), 0.1)
        self.assertTrue(event.latched)


if __name__ == '__main__':
    unittest.main()
