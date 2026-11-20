"""Raw bulk readback of real framebuffer + SDRAM shadow, not capture coherence."""
import hashlib
import cocotb
from cocotb.triggers import FallingEdge
from .test_n2d_c_runtime_system import _run_system, _expected_frame
from src.nand2doom.dv.io.test_n2d_uart_bulk import exchange
from src.nand2doom.dv.io.test_n2d_uart_debug import _exchange
from src.nand2doom.scripts.host.n2d_uart_debug_host import CMD_HALT, CMD_PING, request


@cocotb.test()
async def serial_bulk_frame_and_shadow(dut):
    # Reuse the full independent C runtime + four-input + VGA CRC acceptance.
    # It ends with an intentional CPU trap; explicitly Halt before debug reads.
    await _run_system(dut, preload=True, capture_vga=True)
    await FallingEdge(dut.clk_i)
    halt = await _exchange(dut, request(CMD_HALT, 100))
    assert halt.status == 0 and int(dut.halt_ack_o.value)
    caps = await _exchange(dut, request(5, 101))
    assert caps.words == (0x3242324e, 4096, 3)
    expected = bytearray(_expected_frame())
    expected[1], expected[5] = 0xaa, 0xbb
    # Independent C test palette formula. This fixture stages its shadow into
    # SDRAM; only the firmware integration can claim matched active-bank READY.
    palette = b"".join((((i >> 4) << 8) | ((i & 15) << 4) | ((i >> 4) ^ (i & 15))).to_bytes(4, "little")
                       for i in range(256))
    shadow_address = 0x8000
    written = await exchange(dut, 0x31, 102, shadow_address, 1024, palette)
    assert written[4] == 0 and int.from_bytes(written[11:13], "little") == 1024
    observed = bytearray()
    read_count = 0
    for offset in range(0, 64000, 4096):
        length = min(4096, 64000-offset)
        result = await exchange(dut, 0x32, 103+read_count, 0x10000000+offset, length)
        assert result[4] == 0 and int.from_bytes(result[11:13], "little") == length
        observed.extend(result[13:-4])
        read_count += 1
        assert int(dut.halt_ack_o.value)
    result = await exchange(dut, 0x32, 103+read_count, shadow_address, 1024)
    assert result[4] == 0 and result[13:-4] == palette
    assert int.from_bytes(result[11:13], "little") == 1024
    read_count += 1
    assert observed == expected and read_count == 17
    ping = await _exchange(dut, request(CMD_PING, 122))
    assert ping.words == (0x3144324e, 1)
    cocotb.log.info("PASS raw public-port frame64000+shadow1024;17 DATA reads; frame_sha256=%s palette_sha256=%s; no mailbox/coherence claim",
                    hashlib.sha256(observed).hexdigest(), hashlib.sha256(palette).hexdigest())
