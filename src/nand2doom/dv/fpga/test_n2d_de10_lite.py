"""Real board wrapper, external-clock SDRAM BFM, serial host transactions and JTAG keys."""
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, Timer, with_timeout

from src.common.dv.uart.uart_wire import UartWire
from src.nand2doom.dv.fpga.board_jtag import check_jtag_keys
from src.nand2doom.dv.memory.sdram_pin_model import NOP, WRITE, SdramPinModel, join
from src.nand2doom.scripts.host.n2d_uart_debug_host import (
    CMD_PING, CMD_POKE, CMD_PEEK, request, decode_response,
)


@cocotb.test(timeout_time=8, timeout_unit="ms")
async def board_serial_sdram_contract(dut):
    dut.RESET_N.value = 0
    dut.UART_RXD.value = 1
    dut.PS2_CLK.value = 1
    dut.PS2_DAT.value = 1
    dut.dram_drive_i.value = 0
    dut.dram_data_i.value = 0
    cocotb.start_soon(Clock(dut.MAX10_CLK1_50, 20, unit="ns").start())
    await Timer(100, unit="ns")
    assert int(dut.LEDR.value) & 8
    dut.RESET_N.value = 1
    model, initial = SdramPinModel(), []

    async def read_return(beats):
        # Ref: IS42S16320D datasheet: CL2 data is valid by device clock n+2, launched
        # after n+1+tAC; each later beat follows one device clock after the previous.
        await Timer(46, unit="ns")
        for index, value in enumerate(beats):
            dut.dram_data_i.value = value
            dut.dram_drive_i.value = 1
            await Timer(34 if index == len(beats) - 1 else 40, unit="ns")
        dut.dram_drive_i.value = 0

    async def sdram():
        edge = 0
        while True:
            await RisingEdge(dut.DRAM_CLK)
            edge += 1
            cmd = (int(dut.DRAM_RAS_N.value), int(dut.DRAM_CAS_N.value),
                   int(dut.DRAM_WE_N.value))
            bank, address = int(dut.DRAM_BA.value), int(dut.DRAM_ADDR.value)
            assert int(dut.DRAM_CKE.value) and not int(dut.DRAM_CS_N.value)
            if cmd != NOP and len(initial) < 4:
                initial.append(cmd)
            if cmd == WRITE or model.write_burst is not None:
                assert not int(dut.dram_drive_i.value), "DQ bus contention"
            beats = model.edge(edge, cmd, bank, address, int(dut.dram_data_o.value),
                               int(dut.DRAM_UDQM.value) << 1 | int(dut.DRAM_LDQM.value))
            if beats is not None:
                cocotb.start_soon(read_return(beats))

    task = cocotb.start_soon(sdram())
    await Timer(190, unit="us")
    assert int(dut.LEDR.value) & 8, "system released before SDRAM initialization"
    await Timer(60, unit="us")
    assert int(dut.LEDR.value) == 7, "PLL/init/halt status must be set, reset clear"
    assert initial == [(0, 1, 0), (0, 0, 1), (0, 0, 1), (0, 0, 0)]

    # Ref: 2 Mbaud board rate, 500 ns per bit.
    uart = UartWire(rx=dut.UART_RXD, tx=dut.UART_TXD, bit_ps=500_000)

    async def receive():
        packet = bytearray()
        length = 7
        while len(packet) < length:
            packet.append(await uart.receive_byte())
            if len(packet) == 6:
                length += packet[5] * 4
        return decode_response(bytes(packet))

    async def exchange(command, sequence, **kwargs):
        receiver = cocotb.start_soon(receive())
        await uart.send(request(command, sequence, **kwargs))
        response = await with_timeout(receiver, 500, "us")
        assert response.sequence == sequence and response.status == 0, response
        return response.words

    assert await exchange(CMD_PING, 1) == (0x3144324e, 1)
    for bank in range(4):
        # Why: the last word of each bank's row 0, so the two-word poke crosses
        # into the next bank (or into row 1 after bank 3) and a wrong bank/row
        # split shows.
        address = join(bank, 0, 1022)
        value = 0x12345678 ^ (bank * 0x11111111)
        await exchange(CMD_POKE, 2 + bank * 3, address=address,
                       words=(value, 0xabcdef01), byte_enable=15)
        assert await exchange(CMD_PEEK, 3 + bank * 3, address=address, count=2) == (value, 0xabcdef01)
        await exchange(CMD_POKE, 4 + bank * 3, address=address,
                       words=(0xdeadbeef,), byte_enable=5)
        assert model.words[address] == ((value & 0xff00) | 0xef)
        assert model.words[address + 2] == (((value >> 16) & 0xff00) | 0xad)
    assert model.writes and model.bursts and len(model.refresh) > 5
    assert max(b - a for a, b in zip(model.refresh, model.refresh[1:])) <= 195
    await check_jtag_keys(dut.u_board)
    task.cancel()
    cocotb.log.info("PASS external falling-phase SDRAM, burst-eight, four banks, row crossing, DQM, UART, JTAG keys")



@cocotb.test(timeout_time=80, timeout_unit="ms")
async def board_uart_mem_endpoint(dut):
    """Common UART memory endpoint at 2 Mbaud through the DE10-Lite top."""
    from src.nand2doom.dv.fpga.uart_mem_board import BoardLink, endpoint_load_and_readback
    link = BoardLink(dut, clock="MAX10_CLK1_50", reset="RESET_N", rx="UART_RXD", tx="UART_TXD",
                     baud=2_000_000,
                     dqm=lambda d: int(d.DRAM_UDQM.value) << 1 | int(d.DRAM_LDQM.value))
    await link.start()
    await endpoint_load_and_readback(link, half_width=True)
