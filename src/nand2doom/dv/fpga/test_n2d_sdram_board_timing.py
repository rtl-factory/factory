"""Public-pin CL2 timing, independently seeded reads, and storage write oracle."""
import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, FallingEdge, Timer

from src.common.dv.driven_inputs import DrivenInputs

from src.nand2doom.dv.memory.sdram_pin_model import SdramPinModel, WRITE

POISON = 0xDEAD


@cocotb.test(timeout_time=500, timeout_unit="us")
async def board_capture_window(dut):
    seed = int(os.environ["DV_SEED"])
    rng = random.Random(seed)
    model = SdramPinModel()
    dut.reset_i.value = 1
    dut.request_valid_i.value = 0
    dut.request_write_i.value = 0
    dut.request_address_i.value = 0
    dut.request_write_data_i.value = 0
    dut.request_byte_enable_i.value = 0
    dut.device_data_i.value = POISON
    dut.device_drive_i.value = 1
    cocotb.start_soon(Clock(dut.clk_i, 40, unit="ns").start())

    async def device():
        pins = DrivenInputs(dut, device_drive_i=1)
        edge = 0
        while True:
            await RisingEdge(dut.device_clk_o)
            edge += 1
            cmd = tuple(int(getattr(dut, name).value) for name in
                        ("sdram_ras_n_o", "sdram_cas_n_o", "sdram_we_n_o"))
            writing = cmd == WRITE or model.write_burst is not None
            pins.drive(device_drive_i=int(not writing))
            # Why: wait 1 ns after releasing device drive so the public DQ bus
            # resolves before it is read.
            await Timer(1, unit="ns")
            beats = model.edge(edge, cmd, int(dut.sdram_bank_o.value),
                               int(dut.sdram_address_o.value), int(dut.device_data_o.value),
                               int(dut.sdram_dqm_o.value))
            if beats is not None:
                # Why: 45 ns here, because 1 ns already elapsed since the
                # device clock edge.
                async def launch(values):
                    await Timer(45, unit="ns")
                    for index, value in enumerate(values):
                        dut.device_data_i.value = value
                        await Timer(34 if index == 7 else 40, unit="ns")
                    dut.device_data_i.value = POISON
                cocotb.start_soon(launch(beats))

    cocotb.start_soon(device())
    for _ in range(3):
        await FallingEdge(dut.clk_i)
    dut.reset_i.value = 0
    while not int(dut.init_done_o.value):
        await FallingEdge(dut.clk_i)

    async def transact(address, write=False, data=0, enables=0):
        while not int(dut.request_ready_o.value):
            await FallingEdge(dut.clk_i)
        dut.request_address_i.value = address
        dut.request_write_i.value = int(write)
        dut.request_write_data_i.value = data
        dut.request_byte_enable_i.value = enables
        dut.request_valid_i.value = 1
        await RisingEdge(dut.clk_i)
        await Timer(1, unit="ns")
        dut.request_valid_i.value = 0
        for _ in range(64):
            if int(dut.response_valid_o.value):
                assert not int(dut.response_error_o.value)
                result = int(dut.response_data_o.value)
                await FallingEdge(dut.clk_i)
                return result
            await RisingEdge(dut.clk_i)
            await Timer(1, unit="ns")
        raise AssertionError("bounded SDRAM response timeout")

    # Why: memory is seeded independently of controller writes, and unique
    # halfwords make any missing, repeated or rotated beat visible, including
    # first and last.
    for address in (0x120, 0x130, 0x800, 0x2000, 0x4010):
        halfwords = rng.sample(range(1, POISON), 8)
        model.words.update({address + 2*k: value for k, value in enumerate(halfwords)})
        expected = sum(value << (16*k) for k, value in enumerate(halfwords))
        observed = await transact(address)
        assert observed == expected, f"CL2 window addr={address:#x} expected={expected:032x} observed={observed:032x}"
        # Why: compare physical storage directly, because a faulty read cannot
        # validate writes.
        for mask in (0xFFFF, 0x8001, 0x5555, 0xAAAA, 0):
            data = rng.getrandbits(128)
            old = bytes(sum((model.words[address+2*k] << (16*k)) for k in range(8)).to_bytes(16, "little"))
            await transact(address, True, data, mask)
            expected_bytes = bytes((data >> (8*k)) & 255 if mask >> k & 1 else old[k] for k in range(16))
            stored = sum(model.words[address+2*k] << (16*k) for k in range(8))
            assert stored == int.from_bytes(expected_bytes, "little"), f"write storage/mask {mask:#x}"
            assert await transact(address) == stored
    for _ in range(400):
        await FallingEdge(dut.clk_i)
    assert model.row_hits and model.precharges and len(model.refresh) >= 3
    assert max(b-a for a,b in zip(model.refresh,model.refresh[1:])) <= 195
    dut._log.info("PASS seed=%d CL2 n+1+tAC independent read storage, write masks, refresh", seed)
