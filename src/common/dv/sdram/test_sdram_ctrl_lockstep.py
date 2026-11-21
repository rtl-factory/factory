"""Lockstep of n2a_sdram_ctrl against the project controller it replaces
(wiki/src/common/dv/sdram/dv-plan.md, #140): both take the same random
request stream, timed by the project controller's ready, and the same random
DQ input. Every clock, pins, ready, idle (where the project has one) and
responses must be identical,
except for the differences the MAS lists under Design choices, which are
masked here and nowhere else."""
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge

REQUESTS = 3000
FIELDS = (("cke", 38, 1), ("cs_n", 37, 1), ("ras_n", 36, 1), ("cas_n", 35, 1), ("we_n", 34, 1),
          ("bank", 32, 2), ("address", 19, 13), ("dqm", 17, 2), ("dq_oe", 16, 1), ("dq", 0, 16))


def fields(pins):
    return {name: (pins >> low) & ((1 << width) - 1) for name, low, width in FIELDS}


def masked(line, ref, dut, ref_init, dut_init):
    """Drop the documented differences from one clock's pin fields."""
    command = (ref["ras_n"], ref["cas_n"], ref["we_n"])
    for side in (ref, dut):
        if not side["dq_oe"]:
            side["dq"] = 0
    if line and not ref_init:
        # Ref: MAS Design choices: DQM held high until the mode register is set.
        ref["dqm"] = dut["dqm"] = 0
    if not line and command == (1, 1, 1):
        # Ref: MAS Design choices: the column address is not held on NOP clocks.
        ref["address"] = dut["address"] = 0
    if not line and command == (1, 1, 1) and ref["bank"] != dut["bank"]:
        ref["bank"] = dut["bank"] = 0
    return ref, dut


@cocotb.test()
async def sdram_ctrl_lockstep(dut):
    d = dut
    line = bool(int(d.LINE.value))
    rng = random.Random(int(cocotb.RANDOM_SEED))
    cocotb.start_soon(Clock(d.clk_i, 40, unit="ns").start())
    d.reset_i.value = 1
    for name in ("request_valid_i", "request_write_i", "request_address_i", "request_write_data_i",
                 "request_byte_enable_i", "sdram_dq_i"):
        getattr(d, name).value = 0
    for _ in range(4):
        await FallingEdge(d.clk_i)
    d.reset_i.value = 0
    state = {"clock": 0, "mismatches": [], "done": False, "write": False}

    async def compare():
        while not state["done"]:
            await FallingEdge(d.clk_i)
            state["clock"] += 1
            d.sdram_dq_i.value = rng.getrandbits(16)
            await ReadOnly()
            ref_init, dut_init = int(d.ref_init_done_o.value), int(d.dut_init_done_o.value)
            ref, got = masked(line, fields(int(d.ref_pins_o.value)), fields(int(d.dut_pins_o.value)),
                              ref_init, dut_init)
            pairs = [("pins", ref, got), ("ready", int(d.ref_ready_o.value), int(d.dut_ready_o.value)),
                     ("response_valid", int(d.ref_response_valid_o.value), int(d.dut_response_valid_o.value))]
            if not line:
                # Why: the nand2doom controller has no idle output to compare.
                pairs.append(("idle", int(d.ref_idle_o.value), int(d.dut_idle_o.value)))
            if int(d.ref_response_valid_o.value) and (line or not state["write"]):
                # Ref: MAS Design choices: write response data is undefined in nand2mario.
                pairs.append(("response_data", int(d.ref_response_data_o.value), int(d.dut_response_data_o.value)))
                pairs.append(("response_error", int(d.ref_response_error_o.value), int(d.dut_response_error_o.value)))
            if not line:
                # Ref: MAS Design choices: init_done rises with the mode register set in
                # both; for nand2doom it rises two clocks before the reference's.
                pairs.append(("init_done", ref_init, dut_init))
            elif ref_init:
                pairs.append(("init_done", ref_init, dut_init))
            for name, a, b in pairs:
                if a != b and len(state["mismatches"]) < 10:
                    state["mismatches"].append((state["clock"], name, a, b))

    cocotb.start_soon(compare())
    while not (int(d.ref_init_done_o.value) and int(d.ref_ready_o.value)):
        await FallingEdge(d.clk_i)
    lanes = 16 if line else 2
    for _ in range(REQUESTS):
        await FallingEdge(d.clk_i)
        write = rng.random() < 0.5
        bank, row = rng.randrange(4), rng.choice((3, 4, 0x1ABC))
        column = rng.randrange(1024 // (lanes // 2)) * (lanes // 2)
        if line:
            address = (row << 13) | (((bank ^ row) & 3) << 11) | (column << 1)
        else:
            address = (bank << 24) | (row << 11) | (column << 1)
        if line and rng.random() < 0.03:
            address |= rng.randrange(1, lanes)
        d.request_valid_i.value = 1
        state["write"] = write
        d.request_write_i.value = int(write)
        d.request_address_i.value = address
        d.request_write_data_i.value = rng.getrandbits(128)
        d.request_byte_enable_i.value = rng.getrandbits(lanes) if (write or not line) else 0
        while True:
            await RisingEdge(d.clk_i)
            if int(d.ref_ready_o.value):
                break
        await FallingEdge(d.clk_i)
        d.request_valid_i.value = 0
        while not int(d.ref_response_valid_o.value):
            await FallingEdge(d.clk_i)
        for _ in range(rng.choice((0, 0, 1, 2, 5))):
            await FallingEdge(d.clk_i)
    for _ in range(400):
        await FallingEdge(d.clk_i)
    state["done"] = True
    assert not state["mismatches"], "lockstep mismatches (clock, signal, reference, common): " + \
        "; ".join(f"{c} {n} {a:#x} {b:#x}" if isinstance(a, int) else f"{c} {n} {a} {b}"
                  for c, n, a, b in state["mismatches"])
    dut._log.info("%d requests, %d clocks in lockstep", REQUESTS, state["clock"])
