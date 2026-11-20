"""Small lossless signal-edge monitors for nand2doom system DV."""

from __future__ import annotations

from cocotb.triggers import FallingEdge, First, ReadOnly, RisingEdge


async def stop_monitors(*tasks) -> None:
    """Cancel monitor tasks and wait until each one has finished cancelling.

    cocotb's test shutdown force-cancels every task that is still alive. A
    task that was only cancelled, and not yet resumed, is then cancelled a
    second time; a coroutine suspended in ``First`` (a monitor waiting on
    several triggers) uncancels once while it cancels its child waiters and
    continues, which cocotb reports as "Task was cancelled, but continued
    running". Awaiting completion here keeps shutdown to one cancellation
    per task. A monitor that already failed re-raises its own error.
    """
    for task in tasks:
        task.cancel()
    for task in tasks:
        await task.complete
        if not task.cancelled() and task.exception() is not None:
            raise task.exception()


async def next_writable_input_cycle(dut, after_tick: int) -> int:
    """Leave a retirement's cycle before driving at a writable falling edge.

    A latched ReadOnly event can wake within its retirement cycle. The first
    following falling edge still has that cycle's counter, so cross the next
    rising edge explicitly rather than infer cycle ordering from edge phase.
    """
    await RisingEdge(dut.clk_i)
    await FallingEdge(dut.clk_i)
    tick = int(dut.system_tick_o.value)
    assert tick > after_tick, "input driver did not leave witnessed cycle"
    return tick


async def observe_retired_stores(dut, trace, evidence_path=None) -> None:
    """Lossless architectural-store observer, including contiguous valid cycles."""
    try:
        await _retired_store_loop(dut, trace)
    except Exception as error:
        trace.failure = repr(error)
        if evidence_path is not None:
            trace.save(evidence_path)
        raise


async def _retired_store_loop(dut, trace) -> None:
    while True:
        await RisingEdge(dut.observation_memory_write_o)
        while True:
            await ReadOnly()
            if not int(dut.observation_memory_write_o.value):
                break
            trace.retirement(
                int(dut.observation_order_o.value), int(dut.system_tick_o.value),
                int(dut.observation_memory_address_o.value),
                int(dut.observation_memory_write_data_o.value),
                int(dut.observation_memory_write_mask_o.value),
                valid=bool(dut.observation_valid_o.value),
                memory_valid=bool(dut.observation_memory_valid_o.value),
                write=bool(dut.observation_memory_write_o.value),
                trap=bool(dut.observation_trap_o.value),
            )
            await RisingEdge(dut.clk_i)


async def observe_gameplay_retirements(dut, trace, witness, armed, evidence_path) -> None:
    """One ordered dispatch; stores throughout boot, all retirements while armed.

    Arming occurs at a falling edge. Its wakeup waits for the NEXT rising edge,
    so an already sampled store cannot be dispatched twice. While armed each
    clock is sampled once, including adjacent equal reads/control retirements.
    """
    writing = False
    try:
        while True:
            if writing or (witness.armed and not witness.completed):
                await RisingEdge(dut.clk_i)
            elif not witness.armed:
                await First(RisingEdge(dut.observation_memory_write_o), armed.wait())
                if witness.armed:
                    await RisingEdge(dut.clk_i)
            else:
                await RisingEdge(dut.observation_memory_write_o)
            await ReadOnly()
            writing = bool(dut.observation_memory_write_o.value)
            if writing:
                trace.retirement(
                    int(dut.observation_order_o.value), int(dut.system_tick_o.value),
                    int(dut.observation_memory_address_o.value),
                    int(dut.observation_memory_write_data_o.value),
                    int(dut.observation_memory_write_mask_o.value),
                    valid=bool(dut.observation_valid_o.value),
                    memory_valid=bool(dut.observation_memory_valid_o.value),
                    write=True, trap=bool(dut.observation_trap_o.value),
                )
            if witness.armed and not witness.completed and int(dut.observation_valid_o.value):
                # Counter stores update GameplayTrace before the same-cycle
                # publication cross-check; there is no concurrent counter writer.
                witness.accept({
                    'valid': True, 'order': int(dut.observation_order_o.value),
                    'tick': int(dut.system_tick_o.value),
                    'pc': int(dut.observation_pc_o.value),
                    'instruction': int(dut.observation_instruction_o.value),
                    'next_pc': int(dut.observation_next_pc_o.value),
                    'trap': bool(dut.observation_trap_o.value),
                    'rd_write': bool(dut.observation_rd_write_o.value),
                    'rd': int(dut.observation_rd_address_o.value),
                    'rd_data': int(dut.observation_rd_write_data_o.value),
                    'memory_valid': bool(dut.observation_memory_valid_o.value),
                    'memory_write': writing,
                    'address': int(dut.observation_memory_address_o.value),
                    'read_data': int(dut.observation_memory_read_data_o.value),
                    'write_data': int(dut.observation_memory_write_data_o.value),
                    'read_mask': int(dut.observation_memory_read_mask_o.value),
                    'write_mask': int(dut.observation_memory_write_mask_o.value),
                })
    except Exception as error:
        trace.failure = repr(error)
        trace.save(evidence_path)
        raise


async def observe_trap_events(dut, state: dict[str, object]) -> None:
    """Record every low-to-high architectural trap pulse."""

    while True:
        await RisingEdge(dut.observation_trap_o)
        await ReadOnly()
        state["traps"].append(
            (int(dut.observation_pc_o.value), int(dut.observation_cause_o.value))
        )


async def observe_completion_events(dut, state: dict[str, object]) -> None:
    """Record every software-visible completion mailbox value change."""

    while True:
        await dut.system_completion_o.value_change
        await ReadOnly()
        state["markers"].add(int(dut.system_completion_o.value))


class FramebufferBanks:
    """Shadow of both framebuffer banks as displayed 320x200 index images.

    CPU and host writes land in the back bank (the bank opposite
    ``framebuffer_front_bank_o``). A half-width write stores only even-x
    bytes, each shown for two source pixels. ``framebuffer``/``written``
    always mirror the front bank, the image being scanned out.
    """

    def __init__(self, dut, framebuffer: bytearray, written: bytearray,
                 full_width: bool = True) -> None:
        size = len(framebuffer)
        self.framebuffer, self.written, self.full_width = framebuffer, written, full_width
        self.banks = [bytearray(size), bytearray(size)]
        self.known = [bytearray(size), bytearray(size)]
        self.front = int(dut.framebuffer_front_bank_o.value)
        self.swaps = 0

    def sample(self, dut) -> bool:
        """Apply one ReadOnly sample after a rising edge; True on a swap.

        A swap seen on this sample happened on the edge just taken, so a
        write presented on this sample commits with the new front bank.
        """
        swapped = False
        front = int(dut.framebuffer_front_bank_o.value)
        if front != self.front:
            self.front = front
            self.swaps += 1
            self.framebuffer[:] = self.banks[front]
            self.written[:] = self.known[front]
            swapped = True
        if int(dut.framebuffer_write_valid_o.value):
            back = 1 - self.front
            address = int(dut.framebuffer_write_address_o.value)
            data = int(dut.framebuffer_write_data_o.value)
            strobes = int(dut.framebuffer_write_strobe_o.value)
            for lane in range(4):
                target = address + lane
                if not strobes & (1 << lane) or (not self.full_width and lane & 1):
                    continue
                assert target < len(self.framebuffer)
                for pixel in range(1 if self.full_width else 2):
                    self.banks[back][target + pixel] = (data >> (lane * 8)) & 0xff
                    self.known[back][target + pixel] = 1
        return swapped


async def observe_framebuffer_events(
    dut,
    framebuffer: bytearray,
    written: bytearray,
    state: dict[str, object],
    full_width: bool = True,
) -> None:
    """Track both banks; ``framebuffer``/``written`` mirror the front bank.

    One event wake per write burst or bank swap, then every adjacent cycle
    while writes continue.
    """

    banks = FramebufferBanks(dut, framebuffer, written, full_width)
    while True:
        await First(RisingEdge(dut.framebuffer_write_valid_o),
                    dut.framebuffer_front_bank_o.value_change)
        while True:
            await ReadOnly()
            writing = int(dut.framebuffer_write_valid_o.value)
            banks.sample(dut)
            state["framebuffer_swaps"] = banks.swaps
            if not writing:
                break
            state["framebuffer_transactions"] += 1
            await RisingEdge(dut.clk_i)
