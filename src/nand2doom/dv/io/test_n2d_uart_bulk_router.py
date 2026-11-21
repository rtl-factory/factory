"""Byte-layer bound and final-drain isolation; serial-pin coverage is separate."""
import struct
import zlib
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, Timer


@cocotb.test()
async def router_maximum_discard_and_drain(dut):
    cocotb.start_soon(Clock(dut.clk_i, 40, unit="ns").start())
    dut.reset_i.value = 1
    dut.rx_valid_i.value = 0
    dut.rx_error_i.value = 0
    dut.rx_byte_i.value = 0
    dut.tx_ready_i.value = 0
    dut.tx_busy_i.value = 0
    dut.engine_idle_i.value = 1
    dut.engine_frame_idle_i.value = 1
    dut.engine_tx_byte_i.value = 0
    dut.engine_tx_valid_i.value = 0
    await FallingEdge(dut.clk_i)
    dut.reset_i.value = 0
    payload = (b"\xa5\x01\x03\x00\xa6\x5a\x6a"*9363)[:65535]
    assert len(payload) == 65535
    body = struct.pack("<BBBIH", 2, 0x31, 81, 0x100, 65535)+payload
    raw = b"\xa6"+body+struct.pack("<I", zlib.crc32(body))
    for byte in raw:
        await FallingEdge(dut.clk_i)
        dut.rx_valid_i.value = 1
        dut.rx_byte_i.value = byte
        await Timer(1, unit="ns")
        assert not int(dut.engine_rx_valid_o.value), "discard escaped to legacy engine"
    await FallingEdge(dut.clk_i)
    dut.rx_valid_i.value = 0
    response = bytearray()
    last = None
    for cycle in range(200):
        await FallingEdge(dut.clk_i)
        ready = cycle%5 == 0
        dut.tx_ready_i.value = ready
        await Timer(1, unit="ns")
        valid, byte = int(dut.tx_valid_o.value), int(dut.tx_byte_o.value)
        if last is not None:
            assert valid and byte == last, "TX changed under backpressure"
        last = byte if valid and not ready else None
        if valid and ready:
            response.append(byte)
        if len(response) == 17:
            await RisingEdge(dut.clk_i)
            break
    assert response[:5] == b"\x6a\x02\xb1\x51\x06"
    assert response[9:13] == b"\0"*4
    assert zlib.crc32(response[1:-4]) == int.from_bytes(response[-4:], "little")
    # Why: the parser must not recover on its own after the full maximum-size
    # error response, so these valid-looking bytes must not dispatch.
    for byte in b"\xa5\x01\x03\0\0\0\0\0\0\0\0":
        await FallingEdge(dut.clk_i)
        dut.rx_byte_i.value = byte
        dut.rx_valid_i.value = 1
        await Timer(1, unit="ns")
        assert not int(dut.engine_rx_valid_o.value)
    cocotb.log.info("PASS65535-byte discard; zero legacy dispatch; bounded response stalls/quarantine")
    # Why: with a scaled frame bound of 70000 cycles and a 1024-cycle gap,
    # continued slow input must not extend the absolute frame budget. The first
    # case proved the maximum length itself fits.
    await FallingEdge(dut.clk_i)
    dut.reset_i.value = 1
    dut.rx_valid_i.value = 0
    dut.tx_ready_i.value = 0
    await FallingEdge(dut.clk_i)
    dut.reset_i.value = 0
    for byte in raw[:10]:
        await FallingEdge(dut.clk_i)
        dut.rx_valid_i.value = 1
        dut.rx_byte_i.value = byte
    for cycle in range(70020):
        await FallingEdge(dut.clk_i)
        dut.rx_valid_i.value = cycle%2 == 0
        dut.rx_byte_i.value = 0xa5
        await Timer(1, unit="ns")
        assert not int(dut.engine_rx_valid_o.value)
        if int(dut.tx_valid_o.value):
            break
    assert 69980 <= cycle <= 70000, cycle
    response = bytearray()
    for _ in range(17):
        await FallingEdge(dut.clk_i)
        dut.rx_valid_i.value = 0
        dut.tx_ready_i.value = 1
        await Timer(1, unit="ns")
        assert int(dut.tx_valid_o.value)
        response.append(int(dut.tx_byte_o.value))
    assert response[:5] == b"\x6a\x02\xb1\x51\x08"
    assert response[9:13] == b"\0"*4
    assert zlib.crc32(response[1:-4]) == int.from_bytes(response[-4:], "little")
    cocotb.log.info("PASS absolute receive bound despite continued below-gap drip input")
