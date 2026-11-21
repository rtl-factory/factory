"""Model-checked public-port runner for the nand2doom RV32IM cores.

The runner drives only the public ports of ``n2d_rv32im_core``. Each port has an in-order responder that permits the
pipelined handshake: one outstanding request, a response no earlier than the
cycle after acceptance, and acceptance of the next request on the edge that
samples the previous response. Request holds, response latencies and access
errors are configurable or seeded. Every observation is compared field by
field with :class:`Rv32imModel` in lockstep, so a halt, stall or flush can
only pass if the architectural stream is unchanged.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge, Timer

from .observation import read_observation
from .rv32im_model import DEFAULT_INSTRUCTION, Rv32imModel

MEMORY_SIZE = 0x1000  # Why: 4 KiB holds the hand-encoded programs and their data; a larger initial image extends it


@dataclass
class PortTiming:
    """Request holds and response latency of one responder.

    ``ready_pattern`` cycles deterministically; when ``ready_probability``
    is set the seeded generator decides each cycle instead. ``latency`` is a
    fixed response delay in cycles (>= 1) or an inclusive random range.
    """

    ready_pattern: tuple[int, ...] = (1,)
    ready_probability: float | None = None
    latency: int | tuple[int, int] = 1

    def ready(self, cycle: int, rng: random.Random) -> int:
        if self.ready_probability is not None:
            return int(rng.random() < self.ready_probability)
        return self.ready_pattern[cycle % len(self.ready_pattern)]

    def delay(self, rng: random.Random) -> int:
        if isinstance(self.latency, tuple):
            return rng.randint(*self.latency)
        return self.latency


@dataclass
class HaltPlan:
    """Debug halt injected at ``cycle``; held until acknowledged.

    While halted the runner checks for silence for ``hold`` cycles, then
    optionally pulses Reset-PC and pulses resume.
    """

    cycle: int
    hold: int = 3
    reset_pc: bool = False


@dataclass
class RunResult:
    observations: list
    memory: bytearray
    cycles: int
    model: Rv32imModel
    halts_requested: int = 0
    halts_acknowledged: int = 0
    halt_drain_seen: bool = False
    first_observation_cycle: int | None = None
    last_observation_cycle: int | None = None

    @property
    def ipc(self) -> float:
        if self.first_observation_cycle is None or self.last_observation_cycle is None:
            return 0.0
        span = self.last_observation_cycle - self.first_observation_cycle + 1
        return len(self.observations) / span


def word(memory: bytearray, address: int) -> int:
    base = address & ~3
    return int.from_bytes(memory[base : base + 4], "little")


def check_observation_shape(item, expected_order: int) -> None:
    assert item.order == expected_order, (item, expected_order)
    if item.trap:
        assert not item.rd_write and item.rd_address == 0 and item.rd_write_data == 0
        assert not item.memory_valid and not item.memory_write
        assert item.memory_address == 0
        assert item.memory_read_data == 0 and item.memory_write_data == 0
        assert item.memory_read_mask == 0 and item.memory_write_mask == 0
        return
    assert item.cause == 0 and item.trap_value == 0
    if not item.rd_write:
        assert item.rd_address == 0 and item.rd_write_data == 0
    if not item.memory_valid:
        assert not item.memory_write and item.memory_address == 0
        assert item.memory_read_data == 0 and item.memory_write_data == 0
        assert item.memory_read_mask == 0 and item.memory_write_mask == 0
    elif item.memory_write:
        assert item.memory_read_data == 0 and item.memory_read_mask == 0
        assert item.memory_write_mask != 0
    else:
        assert item.memory_write_data == 0 and item.memory_write_mask == 0
        assert item.memory_read_mask != 0


def _initial_inputs(dut) -> None:
    dut.enable_i.value = 1
    dut.halt_request_i.value = 0
    dut.reset_pc_request_i.value = 0
    dut.resume_i.value = 0
    dut.instruction_request_ready_i.value = 0
    dut.instruction_response_valid_i.value = 0
    dut.instruction_response_data_i.value = 0
    dut.instruction_response_error_i.value = 0
    dut.data_request_ready_i.value = 0
    dut.data_response_valid_i.value = 0
    dut.data_response_read_data_i.value = 0
    dut.data_response_error_i.value = 0
    dut.pmon_platform_events_i.value = 0


def btb_entries(dut) -> int:
    """The core's ``BTB_ENTRIES`` parameter: 0 (no fetch BTB) or the table size."""
    return int(dut.BTB_ENTRIES.value)


async def reset_core(dut, settle_cycles: int | None = None) -> None:
    """Reset, then hold enable_i low for ``settle_cycles`` clocks so the
    program starts with the fetch BTB swept, as in the product. The default
    is the sweep length, one clock per entry (wiki/src/nand2doom/rtl/core/mas.md "Reset sweep");
    products hold the core stopped far longer (SDRAM initialization and
    host resume)."""
    if settle_cycles is None:
        settle_cycles = btb_entries(dut)
    dut.reset_i.value = 1
    _initial_inputs(dut)
    dut.enable_i.value = int(settle_cycles == 0)
    await RisingEdge(dut.clk_i)
    await RisingEdge(dut.clk_i)
    await FallingEdge(dut.clk_i)
    dut.reset_i.value = 0
    for _ in range(settle_cycles):
        await FallingEdge(dut.clk_i)
    dut.enable_i.value = 1
    await Timer(1, unit="ns")


async def run_program(
    dut,
    words,
    *,
    stop_pc: int | None = None,
    stop_trap: bool = False,
    stop_cause: int | None = None,
    initial_memory: bytes | None = None,
    max_cycles: int = 256,
    instruction_error_addresses=(),
    data_error_addresses=(),
    instruction: PortTiming | None = None,
    data: PortTiming | None = None,
    seed: int = 0,
    halts: tuple[HaltPlan, ...] = (),
    halt_on_first_data: bool = False,
    reset: bool = True,
    settle_cycles: int | None = None,
    platform_events: dict[int, int] | None = None,
    platform_observe_mask: int = 0,
    platform_observe_legal: int = 0,
    platform_fetch_window: tuple[int, int, int] | None = None,
) -> RunResult:
    """Run ``words`` from PC zero until a stop condition, checking every step.

    With ``reset`` the core is reset and held for ``settle_cycles`` clocks
    (default: the fetch BTB reset sweep) before it runs.

    ``platform_events`` drives ``pmon_platform_events_i`` per cycle, and
    ``platform_fetch_window`` (low, high, events) adds events in cycles whose
    presented fetch address lies in [low, high). With
    ``platform_observe_mask`` the registered ``pmon_events_o`` is checked
    each cycle against the driven events restricted to the legal IDs.
    """
    instruction = instruction or PortTiming()
    data = data or PortTiming()
    rng = random.Random(seed)
    words = tuple(words)
    instruction_errors = set(instruction_error_addresses)
    data_errors = set(data_error_addresses)
    memory_size = max(MEMORY_SIZE, len(initial_memory or b""))
    memory = bytearray(memory_size)
    if initial_memory is not None:
        memory[: len(initial_memory)] = initial_memory
    model = Rv32imModel(
        words,
        memory_size=memory_size,
        initial_memory=initial_memory,
        instruction_error_addresses=instruction_errors,
        data_error_addresses=data_errors,
    )

    def data_error(address: int) -> bool:
        return address in data_errors or (address & ~3) + 4 > memory_size

    if reset:
        await reset_core(dut, settle_cycles)

    pending_instruction: list | None = None  # Why: a list, not a tuple, so remaining counts down in place: [address, remaining]
    pending_data: list | None = None  # Why: a list so remaining counts down in place: [write, address, data, mask, remaining]
    held_data: tuple | None = None
    observations: list = []
    result = RunResult(observations, memory, 0, model)
    halt_queue = sorted(halts, key=lambda plan: plan.cycle)
    active_halt: HaltPlan | None = None
    halted_cycles = 0
    resume_pending = False
    expect_fetch_at: int | None = None
    halt_on_data_armed = halt_on_first_data

    for cycle in range(max_cycles):
        instruction_now = pending_instruction is not None and pending_instruction[1] == 0
        data_now = pending_data is not None and pending_data[4] == 0
        dut.instruction_response_valid_i.value = int(instruction_now)
        if instruction_now:
            address = pending_instruction[0]
            index = address >> 2
            dut.instruction_response_data_i.value = (
                words[index] if index < len(words) else DEFAULT_INSTRUCTION
            )
            dut.instruction_response_error_i.value = int(address in instruction_errors)
        else:
            dut.instruction_response_data_i.value = 0
            dut.instruction_response_error_i.value = 0
        dut.data_response_valid_i.value = int(data_now)
        if data_now:
            dut.data_response_read_data_i.value = (
                0 if data_error(pending_data[1]) else word(memory, pending_data[1])
            )
            dut.data_response_error_i.value = int(data_error(pending_data[1]))
        else:
            dut.data_response_read_data_i.value = 0
            dut.data_response_error_i.value = 0

        # Ref: core MAS "Pipelined memory handshake": one request outstanding
        # per port, the next accepted on the edge that samples the response, so
        # ready may rise only when the port is free or its response is sampled
        # on this edge.
        instruction_free = pending_instruction is None or instruction_now
        data_free = pending_data is None or data_now
        dut.instruction_request_ready_i.value = int(
            instruction_free and instruction.ready(cycle, rng)
        )
        dut.data_request_ready_i.value = int(data_free and data.ready(cycle, rng))

        dut.resume_i.value = 0
        dut.reset_pc_request_i.value = 0
        if active_halt is None and halt_queue and cycle >= halt_queue[0].cycle:
            active_halt = halt_queue.pop(0)
            halted_cycles = 0
            result.halts_requested += 1
            dut.halt_request_i.value = 1
        if resume_pending:
            dut.halt_request_i.value = 0
            dut.resume_i.value = 1
            if active_halt is not None and active_halt.reset_pc:
                dut.reset_pc_request_i.value = 1
                model.pc = 0
            expect_fetch_at = model.pc
            resume_pending = False
            active_halt = None

        await Timer(1, unit="ns")

        platform_now = (platform_events or {}).get(cycle, 0)
        if platform_fetch_window is not None and int(dut.instruction_request_valid_o.value):
            window_low, window_high, window_events = platform_fetch_window
            if window_low <= int(dut.instruction_request_address_o.value) < window_high:
                platform_now |= window_events
        dut.pmon_platform_events_i.value = platform_now

        next_instruction = None
        if int(dut.instruction_request_valid_o.value):
            address = int(dut.instruction_request_address_o.value)
            assert instruction_free, f"cycle {cycle}: fetch request while a response is owed"
            assert address % 4 == 0, f"cycle {cycle}: misaligned fetch 0x{address:08x}"
            if expect_fetch_at is not None:
                assert address == expect_fetch_at, (
                    f"cycle {cycle}: resume fetched 0x{address:08x}, "
                    f"architectural next PC 0x{expect_fetch_at:08x}"
                )
                expect_fetch_at = None
            if int(dut.instruction_request_ready_i.value):
                next_instruction = address
        next_data = None
        if int(dut.data_request_valid_o.value):
            payload = (
                bool(dut.data_request_write_o.value),
                int(dut.data_request_address_o.value),
                int(dut.data_request_write_data_o.value),
                int(dut.data_request_byte_enable_o.value),
            )
            assert data_free, f"cycle {cycle}: data request while a response is owed"
            if held_data is not None:
                assert payload == held_data, f"cycle {cycle}: data payload changed while held"
            if int(dut.data_request_ready_i.value):
                next_data = payload
                held_data = None
                if halt_on_data_armed:
                    dut.halt_request_i.value = 1
                    active_halt = HaltPlan(cycle)
                    result.halts_requested += 1
                    halt_on_data_armed = False
            else:
                held_data = payload
        else:
            assert held_data is None, f"cycle {cycle}: data request withdrawn before acceptance"

        await RisingEdge(dut.clk_i)
        await ReadOnly()
        if platform_observe_mask:
            # Why: sampled after the edge, the registered pmon_events_o must
            # hold exactly the legal events driven before it, so a dropped,
            # extra or unregistered event fails here.
            observed_events = int(dut.pmon_events_o.value) & platform_observe_mask
            assert observed_events == platform_now & platform_observe_legal, (
                f"cycle {cycle}: pmon_events_o {observed_events:#x}"
            )

        if data_now:
            write, address, value, mask, _ = pending_data
            if write and not data_error(address):
                base = address & ~3
                for lane in range(4):
                    if mask & (1 << lane):
                        memory[base + lane] = (value >> (8 * lane)) & 0xFF

        stop = False
        if int(dut.observation_valid_o.value):
            observed = read_observation(dut)
            check_observation_shape(observed, len(observations))
            cycle_value = None
            if model.reads_cycle_counter(model.fetch(model.pc)) and model.pc not in model.instruction_errors:
                cycle_value = observed.rd_write_data
            expected = model.step(cycle_value)
            assert observed == expected, (
                f"cycle {cycle}: observation mismatch\n expected {expected}\n observed {observed}"
            )
            observations.append(observed)
            if result.first_observation_cycle is None:
                result.first_observation_cycle = cycle
            result.last_observation_cycle = cycle
            stop = (
                (stop_pc is not None and observed.pc == stop_pc)
                or (stop_trap and observed.trap)
                or (stop_cause is not None and observed.trap and observed.cause == stop_cause)
            )

        halt_ack = int(dut.halt_ack_o.value)
        if halt_ack:
            assert active_halt is not None or resume_pending, f"cycle {cycle}: unrequested halt ack"
            owed_instruction = pending_instruction is not None and not instruction_now
            owed_data = pending_data is not None and not data_now
            assert not owed_instruction and not owed_data and next_instruction is None \
                and next_data is None, f"cycle {cycle}: halt acknowledged with a response owed"
            if halted_cycles == 0:
                result.halts_acknowledged += 1
                result.halt_drain_seen = True
            else:
                assert not int(dut.observation_valid_o.value), "observation while halted"
            halted_cycles += 1
            if active_halt is not None and halted_cycles > active_halt.hold:
                resume_pending = True

        if instruction_now:
            pending_instruction = None
        elif pending_instruction is not None:
            pending_instruction[1] -= 1
        if data_now:
            pending_data = None
        elif pending_data is not None:
            pending_data[4] -= 1
        if next_instruction is not None:
            assert pending_instruction is None
            pending_instruction = [next_instruction, instruction.delay(rng) - 1]
        if next_data is not None:
            assert pending_data is None
            pending_data = [*next_data, data.delay(rng) - 1]

        result.cycles = cycle + 1
        if stop:
            # Why: a halt still draining when the stop observation retires is
            # the only request allowed to end unacknowledged.
            pending = int(active_halt is not None and halted_cycles == 0)
            assert result.halts_acknowledged + pending == result.halts_requested
            await FallingEdge(dut.clk_i)
            dut.halt_request_i.value = 0
            return result
        await FallingEdge(dut.clk_i)
    raise AssertionError(
        f"bounded run exceeded {max_cycles} cycles; observations={len(observations)} "
        f"last={observations[-1] if observations else None}"
    )
