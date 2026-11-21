"""n2m_system direct preload and the UART loader on one load port, with the
on-chip backing (n2m_system itself, bit-level host on its serial pins) and
with the SDRAM backing (the SDRAM fixture and its host PHYs).

The backing is filled through the direct preload port right after reset
(on-chip) or as soon as the SDRAM initialized (the port is not ready
before), with the console core running whatever the backing holds meanwhile.
Every load response belongs to the direct port, so the loader must not see
any of them. The bench is then the host on the top's serial line: PING, HOLD,
READ back samples of the preloaded image, a direct patch written while the
core is held and read back over the UART, a UART WRITE restoring it, a
second direct write after the loader's last frame, then RELEASE. The program
runs from the preloaded image and is checked exactly as in the SDRAM boot
test. A regression for the response routing of the shared load port."""
import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, ReadOnly, RisingEdge

from ...scripts.board.cart_loader import (CMD_HOLD, CMD_PING, CMD_READ, CMD_WRITE, address_field, decode_response,
                                  encode_request, response_total_length)
from .test_system import CLOCK_PERIOD_NS, power_up
from .test_system_sdram import SDRAM_INITIALIZED_CLOCK, Host, load_image, release_and_verify

UART_CLKS_PER_BIT = 8                 # Why: must match the registry's parameter for this target.
SAMPLES = (0x0000, 0x0100, 0x4000, 0x7F00, 0x13F00, 0x1FF00)
PATCH_OFFSET = 0x13F00                # Why: bank 4 pattern bytes the program never reads ($7F00-$7F0F).


class LineHost:
    """UART host bit-banging the top's serial pins at UART_CLKS_PER_BIT."""

    def __init__(self, dut):
        self.dut = dut
        self.clock = 0
        self.received = []
        self.bound = int(os.environ['DV_MAX_CYCLES'])

    async def ticker(self):
        while True:
            await RisingEdge(self.dut.clk_i)
            self.clock += 1
            assert self.clock <= self.bound, 'finite simulation bound exceeded'

    async def receiver(self):
        """Sample uart_tx_o at mid-bit after each start edge."""
        d = self.dut
        while True:
            await RisingEdge(d.clk_i)
            await ReadOnly()
            if int(d.uart_tx_o.value):
                continue
            await ClockCycles(d.clk_i, UART_CLKS_PER_BIT // 2)
            await ReadOnly()
            assert not int(d.uart_tx_o.value), 'start bit glitch on the loader line'
            byte = 0
            for bit in range(8):
                await ClockCycles(d.clk_i, UART_CLKS_PER_BIT)
                await ReadOnly()
                byte |= int(d.uart_tx_o.value) << bit
            await ClockCycles(d.clk_i, UART_CLKS_PER_BIT)
            await ReadOnly()
            assert int(d.uart_tx_o.value), 'loader stop bit low'
            self.received.append(byte)

    async def send(self, data):
        d = self.dut
        for byte in data:
            for level in [0] + [(byte >> bit) & 1 for bit in range(8)] + [1]:
                await FallingEdge(d.clk_i)
                d.uart_rx_i.value = level
                await ClockCycles(d.clk_i, UART_CLKS_PER_BIT)

    async def exchange(self, frame, limit_clocks=None):
        start = len(self.received)
        await self.send(frame)
        deadline = self.clock + (limit_clocks or 10 * UART_CLKS_PER_BIT * (len(frame) + 300) + 20000)
        total = None
        while total is None or len(self.received) - start < total:
            assert self.clock < deadline, f'response timeout after {len(self.received) - start} bytes'
            await RisingEdge(self.dut.clk_i)
            if total is None and len(self.received) - start >= 7:
                total = response_total_length(bytes(self.received[start:start + 7]))
        response = decode_response(bytes(self.received[start:start + total]))
        assert response.crc_ok, 'response CRC'
        return response


async def direct_write(dut, host, offset, data, every_edge=False):
    """Direct preload writes, each held until an edge samples
    rom_load_ready_o high; `every_edge` (the on-chip backing after reset)
    requires acceptance within one waiting edge: the write waits only for
    the previous write's one-edge response."""
    for index, byte in enumerate(data):
        await FallingEdge(dut.clk_i)
        dut.rom_load_write_i.value = 1
        dut.rom_load_address_i.value = offset + index
        dut.rom_load_data_i.value = byte
        waited = 0
        while True:
            await ReadOnly()
            if int(dut.rom_load_ready_o.value):
                break
            waited += 1
            assert not every_edge or waited <= 1, 'the on-chip backing takes a direct write every second edge'
            await FallingEdge(dut.clk_i)
        await RisingEdge(dut.clk_i)
    await FallingEdge(dut.clk_i)
    dut.rom_load_write_i.value = 0


async def preload_and_interleave(dut, system, host, image, rng, onchip):
    """Direct preload of the whole image with the core running, then the
    UART phase with interleaved direct writes while held, RELEASE and the
    SDRAM boot test's verification. Returns the log fields."""
    await direct_write(dut, host, 0, image, every_edge=onchip)
    preload_clocks = host.clock
    await ClockCycles(dut.clk_i, 4)
    response = await host.exchange(encode_request(CMD_PING))
    assert response.ok and response.initialized and not response.held and not (response.status & 0x38), response.describe()
    response = await host.exchange(encode_request(CMD_HOLD))
    assert response.ok and response.held
    await ReadOnly()
    assert int(dut.core_reset_o.value) == 1
    for offset in SAMPLES:
        response = await host.exchange(encode_request(CMD_READ, address_field(offset), length=256))
        assert response.ok and response.payload == image[offset:offset + 256], (hex(offset), response.describe())
    patch = bytes(rng.randrange(256) for _ in range(16))
    original = image[PATCH_OFFSET:PATCH_OFFSET + 16]
    assert patch != original
    await direct_write(dut, host, PATCH_OFFSET, patch, every_edge=onchip)
    response = await host.exchange(encode_request(CMD_READ, address_field(PATCH_OFFSET), length=16))
    assert response.ok and response.payload == patch, response.describe()
    response = await host.exchange(encode_request(CMD_WRITE, address_field(PATCH_OFFSET), original))
    assert response.ok and response.length == 16
    await direct_write(dut, host, PATCH_OFFSET, original, every_edge=onchip)
    response = await host.exchange(encode_request(CMD_READ, address_field(PATCH_OFFSET), length=16))
    assert response.ok and response.payload == original, response.describe()
    assert (int(system.u_cartridge.header_type_q.value), int(system.u_cartridge.header_rom_size_q.value),
            int(system.u_cartridge.header_ram_size_q.value)) == tuple(image[0x147:0x14A])
    retirements, run_clocks, banks, wram_bytes = await release_and_verify(dut, system, host, image)
    return preload_clocks, retirements, run_clocks, banks, wram_bytes


def garbage_headers(system, rng):
    """The mapper's header registers have no power-up value either."""
    for register in (system.u_cartridge.header_type_q, system.u_cartridge.header_rom_size_q,
                     system.u_cartridge.header_ram_size_q):
        register.value = rng.randrange(256)


@cocotb.test(timeout_time=200, timeout_unit='ms')
async def system_direct_preload(dut):
    """On-chip backing: the direct preload starts on the first edge after
    reset (the backing is ready at once)."""
    seed = int(os.environ['DV_SEED'])
    assert int(os.environ['DV_CLOCK_HZ']) == 25_000_000
    cocotb.log.info('n2m_system direct preload (on-chip) seed=%d (directed)', seed)
    image = load_image()
    rng = random.Random(seed)
    power_up(dut, rng, rom_bytes=len(image))
    garbage_headers(dut, rng)
    host = LineHost(dut)
    dut.reset_i.value = 1
    dut.buttons_i.value = 0
    dut.uart_rx_i.value = 1
    dut.dram_dq_i.value = 0
    dut.rom_load_write_i.value = 0
    dut.rom_load_address_i.value = 0
    dut.rom_load_data_i.value = 0
    cocotb.start_soon(Clock(dut.clk_i, CLOCK_PERIOD_NS, unit='ns').start())
    for _ in range(3):
        await RisingEdge(dut.clk_i)
    await FallingEdge(dut.clk_i)
    dut.reset_i.value = 0
    cocotb.start_soon(host.ticker())
    cocotb.start_soon(host.receiver())
    await ReadOnly()
    assert int(dut.core_reset_o.value) == 0, 'the on-chip backing is initialized at once'
    fields = await preload_and_interleave(dut, dut, host, image, rng, onchip=True)
    cocotb.log.info('n2m_system direct preload (on-chip) passed: preload_clocks=%d retirements=%d run_clocks=%d '
                    'banks_executed=%s wram_bytes=%d total_clocks=%d', fields[0], fields[1], fields[2],
                    sorted(fields[3]), fields[4], host.clock)


@cocotb.test(timeout_time=400, timeout_unit='ms')
async def system_sdram_direct_preload(dut):
    """SDRAM backing: a direct write presented during system reset and
    during the SDRAM power-up is not accepted until the backing reports
    initialized (then between the running core's fetches, which win a
    shared edge); it then lands, and the whole preload runs through the
    SDRAM controller at its own pace."""
    seed = int(os.environ['DV_SEED'])
    assert int(os.environ['DV_CLOCK_HZ']) == 25_000_000
    cocotb.log.info('n2m_system direct preload (SDRAM) seed=%d (directed)', seed)
    image = load_image()
    rng = random.Random(seed)
    system = dut.u_system
    power_up(system, rng, onchip=False)
    garbage_headers(system, rng)
    host = Host(dut)
    dut.reset_i.value = 1
    dut.buttons_i.value = 0
    dut.host_tx_valid_i.value = 0
    dut.host_tx_data_i.value = 0
    cocotb.start_soon(Clock(dut.clk_i, CLOCK_PERIOD_NS, unit='ns').start())
    await FallingEdge(dut.clk_i)
    dut.rom_load_write_i.value = 1
    dut.rom_load_address_i.value = 0
    dut.rom_load_data_i.value = image[0]
    for _ in range(3):
        await RisingEdge(dut.clk_i)
        await ReadOnly()
        assert int(dut.rom_load_ready_o.value) == 0, 'not ready during system reset'
    await FallingEdge(dut.clk_i)
    dut.reset_i.value = 0
    cocotb.start_soon(host.ticker())
    cocotb.start_soon(host.receiver())
    while True:
        await RisingEdge(dut.clk_i)
        await ReadOnly()
        if int(dut.rom_load_ready_o.value):
            break
        if host.clock < SDRAM_INITIALIZED_CLOCK:
            assert int(dut.core_reset_o.value) == 1, 'the core waits for the SDRAM'
        # Why: the core runs from SDRAM once it is initialized and the mapper
        # wins a shared edge, so the direct port gets in between fetches.
        assert host.clock < SDRAM_INITIALIZED_CLOCK + 200, 'the direct port never became ready'
    assert host.clock >= SDRAM_INITIALIZED_CLOCK, ('ready before the SDRAM initialized', host.clock)
    await RisingEdge(dut.clk_i)
    await FallingEdge(dut.clk_i)
    dut.rom_load_write_i.value = 0
    for _ in range(16):                         # Why: lets the write complete inside the controller.
        await RisingEdge(dut.clk_i)
    word = int(dut.u_sdram.memory[0].value)
    assert word & 0xFF == image[0], ('the write held through reset and power-up landed', hex(word))
    fields = await preload_and_interleave(dut, system, host, image, rng, onchip=False)
    for address in (0x146, 0x1FFFE):
        word = int(dut.u_sdram.memory[address >> 1].value)
        assert word == image[address] | image[address + 1] << 8, (hex(address), hex(word))
    cocotb.log.info('n2m_system direct preload (SDRAM) passed: preload_clocks=%d retirements=%d run_clocks=%d '
                    'banks_executed=%s wram_bytes=%d total_clocks=%d', fields[0], fields[1], fields[2],
                    sorted(fields[3]), fields[4], host.clock)
