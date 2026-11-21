"""Public serial-pin bulk oracle; expected payload/CRC use Python's stdlib."""
import struct
import zlib
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, with_timeout
from .test_n2d_uart_debug import _uart_send, _uart_send_byte, _uart_receive_byte, _uart_receive, _exchange, _memory_model
from src.nand2doom.scripts.host.n2d_uart_debug_host import request, CMD_PING, CMD_HALT, CMD_RESUME


def packet(command, sequence, address, length, payload=b""):
    body = struct.pack("<BBBIH", 2, command, sequence, address, length) + payload
    return b"\xa6" + body + struct.pack("<I", zlib.crc32(body))


async def receive(dut):
    raw = bytearray()
    while len(raw) < 13:
        raw.append(await _uart_receive_byte(dut))
    length = int.from_bytes(raw[9:11], "little")
    assert length <= 4096
    while len(raw) < 17 + length:
        raw.append(await _uart_receive_byte(dut))
    assert raw[:2] == b"\x6a\x02"
    assert int.from_bytes(raw[-4:], "little") == zlib.crc32(raw[1:-4])
    return bytes(raw)


async def exchange(dut, command, sequence, address, length, data=b""):
    receiver = cocotb.start_soon(receive(dut))
    await _uart_send(dut, packet(command, sequence, address, length, data))
    result = await with_timeout(receiver, 100, "ms")
    assert result[2:4] == bytes((command | 128, sequence))
    assert int.from_bytes(result[5:9], "little") == address
    return result


async def setup(dut):
    cocotb.start_soon(Clock(dut.clk_i, 40, unit="ns").start())
    dut.reset_i.value = 1
    dut.uart_rx_i.value = 1
    dut.halt_ack_i.value = 1
    dut.cache_maint_done_i.value = 1
    dut.arb_grant_i.value = 1
    dut.input_event_ready_i.value = 1
    controls = dict(ready=True, respond=True, error=False, error_address=None, stall_address=None)
    stats = dict(begin=0, commit=0, abort=0, staged=0)
    memory = {}
    cocotb.start_soon(_memory_model(dut, memory, controls, stats))
    await ClockCycles(dut.clk_i, 4)
    dut.reset_i.value = 0
    await ClockCycles(dut.clk_i, 4)
    return memory, controls, stats


@cocotb.test()
async def bulk_smoke(dut):
    memory, _, stats = await setup(dut)
    caps = await _exchange(dut, request(5, 1))
    assert caps.status == 0 and caps.words == (0x3242324e, 4096, 3)
    expected_commits = 0
    for ordinal, length in enumerate((4, 32, 36, 4096, 4096)):
        base = 0x1000 + ordinal*0x2000
        data = bytes((i*37 + length) % 256 for i in range(length))
        data = bytes(value ^ ordinal for value in data)
        memory[base-4], memory[base+length] = 0xdeadbeef, 0xcafebabe
        before = stats["commit"]
        staged = stats["staged"]
        result = await exchange(dut, 0x31, 2, base, length, data)
        assert result[4] == 0, result.hex()
        assert int.from_bytes(result[11:13], "little") == length
        observed = b"".join(memory[base+i].to_bytes(4, "little") for i in range(0, length, 4))
        assert observed == data
        assert stats["staged"]-staged == length//4, "duplicate or missing memory writes"
        assert stats["commit"]-before == (length+31)//32
        snapshot = memory.copy()
        staged = stats["staged"]
        result = await exchange(dut, 0x32, 3, base, length)
        assert result[4] == 0 and result[13:-4] == data
        assert int.from_bytes(result[11:13], "little") == length
        assert memory == snapshot and stats["staged"] == staged, "read mutated memory"
        assert memory[base-4] == 0xdeadbeef and memory[base+length] == 0xcafebabe
        expected_commits += 2*((length+31)//32)
        ping = await _exchange(dut, request(CMD_PING, 4))
        assert ping.status == 0 and ping.words[0] == 0x3144324e
    # Existing atomic engine completes a transaction for reads as well as writes.
    assert stats["commit"] == expected_commits == 520
    # Bulk whole-word portion plus a masked legacy three-byte tail. The high
    # byte of the final word and next word must survive unchanged.
    memory[0xe01c], memory[0xe020] = 0xcccccccc, 0xdeadbeef
    result = await exchange(dut, 0x31, 5, 0xe000, 28, bytes(range(28)))
    assert result[4] == 0
    tail = await _exchange(dut, request(0x11, 6, address=0xe01c,
                                       words=(0x001e1d1c,), byte_enable=7))
    assert tail.status == 0 and memory[0xe01c] == 0xcc1e1d1c
    assert memory[0xe020] == 0xdeadbeef
    result = await exchange(dut, 0x32, 7, 0xe000, 32)
    assert result[13:-4] == bytes(range(31))+b"\xcc"
    # A long bulk submission can pause beyond the legacy20.48us gap without
    # fragmenting into commands. This does not change legacy framing deadlines.
    raw = packet(0x31, 8, 0xf000, 32, bytes(range(32)))
    receiver = cocotb.start_soon(receive(dut))
    await _uart_send(dut, raw[:20])
    await ClockCycles(dut.clk_i, 25000)  # 1ms bounded submission pause
    await _uart_send(dut, raw[20:])
    result = await with_timeout(receiver, 100, "ms")
    assert result[4] == 0 and int.from_bytes(result[11:13], "little") == 32
    assert b"".join(memory[0xf000+i].to_bytes(4, "little") for i in range(0,32,4)) == bytes(range(32))


async def reset(dut):
    dut.reset_i.value = 1
    dut.uart_rx_i.value = 1
    await ClockCycles(dut.clk_i, 8)
    dut.reset_i.value = 0
    await ClockCycles(dut.clk_i, 8)


@cocotb.test()
async def bulk_fail_closed(dut):
    memory, controls, stats = await setup(dut)
    # Embedded valid Resume/Halt frames must remain data, including bad CRC,
    # timeout residue and the oversized-discard path.
    embedded = (request(CMD_RESUME, 91) + request(CMD_HALT, 92))*200
    cases = []
    bad_crc = packet(0x31, 7, 0x100, 32, embedded[:32])
    cases.append((bad_crc[:-1]+bytes((bad_crc[-1]^1,)), 3))
    cases += [(packet(0x31, 7, 1, 32, embedded[:32]), 4),
              (packet(0x31, 7, 0x3fffffc, 32, embedded[:32]), 5),
              (packet(0x31, 7, 0x10000000, 32, embedded[:32]), 5),
              (packet(0x31, 7, 0x100, 4097, embedded[:4097]), 6),
              (packet(0x32, 7, 0x10000ffc, 4096), 0),
              (packet(0x32, 7, 0x1000fffc, 8), 5)]
    for raw, status in cases:
        await reset(dut)
        before = stats.copy()
        snapshot = memory.copy()
        receiver = cocotb.start_soon(receive(dut))
        await _uart_send(dut, raw)
        result = await with_timeout(receiver, 100, "ms")
        assert result[4] == status, result.hex()
        assert memory == snapshot and stats["staged"] == before["staged"]
        if status:
            assert int.from_bytes(result[9:13], "little") == 0
            await _uart_send(dut, request(CMD_RESUME, 98))
            await ClockCycles(dut.clk_i, 600)
            assert int(dut.halt_req_o.value) == 1, "residue/next command escaped quarantine"
            assert stats == before

    await reset(dut)
    before = stats.copy()
    receiver = cocotb.start_soon(receive(dut))
    await _uart_send(dut, packet(0x31, 7, 0x100, 32, embedded[:32])[:14])
    result = await with_timeout(receiver, 100, "ms")
    assert result[4] == 8 and int.from_bytes(result[9:13], "little") == 0
    await _uart_send(dut, embedded[:44])
    assert stats == before and int(dut.halt_req_o.value) == 1

    await reset(dut)
    before = stats.copy()
    receiver = cocotb.start_soon(receive(dut))
    await _uart_send(dut, packet(0x31, 7, 0x100, 32, embedded[:32])[:10])
    await _uart_send_byte(dut, 0x55, stop=0)
    result = await with_timeout(receiver, 1, "ms")
    assert result[4] == 10 and int.from_bytes(result[9:13], "little") == 0
    await _uart_send(dut, embedded[:44])
    assert stats == before and int(dut.halt_req_o.value) == 1

    # A read can have a completed diagnostic prefix but no accepted partial data.
    for control, value, expected in (("error_address", 0x120, 9),
                                      ("ready", False, 8)):
        await reset(dut)
        controls[control] = value
        result = await exchange(dut, 0x32, 8, 0x100, 64)
        assert result[4] == expected and result[13:-4] == b""
        assert int.from_bytes(result[11:13], "little") == (32 if control == "error_address" else 0)
        controls[control] = None if control == "error_address" else True
    await reset(dut)
    dut.arb_grant_i.value = 0
    result = await exchange(dut, 0x31, 8, 0x100, 32, embedded[:32])
    assert result[4] == 11 and int.from_bytes(result[9:13], "little") == 0
    dut.arb_grant_i.value = 1

    # Bulk arriving during an active legacy request never routes its body into
    # the legacy BUSY parser. Drain old response, then report bulk BUSY.
    await reset(dut)
    controls["respond"] = False
    async def both():
        old = await _uart_receive(dut)
        new = await receive(dut)
        return old, new
    receiver = cocotb.start_soon(both())
    await _uart_send(dut, request(0x10, 10, address=0x100, count=1))
    await _uart_send(dut, packet(0x31, 11, 0x100, 32, embedded[:32]))
    old, new = await with_timeout(receiver, 2, "ms")
    assert old.status == 8 and old.sequence == 10
    assert new[4] == 12 and new[3] == 11 and int.from_bytes(new[9:13], "little") == 0
    assert int(dut.halt_req_o.value) == 1
    controls["respond"] = True

    # Reverse overlap: a complete legacy Resume during a bulk response cannot
    # alter Halt or enter the old BUSY parser; it quarantines after completion.
    await reset(dut)
    before = stats["staged"]
    receiver = cocotb.start_soon(receive(dut))
    await _uart_send(dut, packet(0x32, 12, 0x100, 32))
    await ClockCycles(dut.clk_i, 300)
    await _uart_send(dut, request(CMD_RESUME, 13))
    result = await with_timeout(receiver, 2, "ms")
    assert result[4] == 0 and int.from_bytes(result[11:13], "little") == 32
    await _uart_send(dut, request(CMD_RESUME, 14))
    assert int(dut.halt_req_o.value) == 1 and stats["staged"] == before
