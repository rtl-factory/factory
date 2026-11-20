"""Physical UART pins, maintained caches and a complete public VGA raster."""
import json
from pathlib import Path
import struct
import sys
import time

import cocotb
from cocotb.triggers import ClockCycles, FallingEdge, ReadOnly, RisingEdge, with_timeout
from cocotb.utils import get_sim_time
from cocotb.task import bridge
from src.nand2doom.dv.platform.test_n2d_c_runtime_system import _initialize
from src.nand2doom.dv.io.test_n2d_uart_debug import _exchange
from src.nand2doom.scripts.n2d_uart_debug_host import (
    CMD_HALT,CMD_LOAD,CMD_PEEK,CMD_POKE,CMD_RESET_PC,CMD_RESUME,ST_OK,request)
from .capture_system_build import build
from .capture_uart_transport import CaptureUartTransport

ROOT=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(ROOT/'src/nand2doom/scripts'))
import n2d_capture as capture
import n2d_uart as uart
import n2d_uart_debug_host as wire

@cocotb.test(timeout_time=2500,timeout_unit='ms')
async def capture_system_contract(dut):
    started=time.monotonic()
    progress=[]
    def milestone(phase, **details):
        item=dict(phase=phase,cycle=int(get_sim_time(unit='ns')//40),
                  wall_seconds=time.monotonic()-started,**details)
        progress.append(item)
        (Path.cwd()/'capture-progress.json').write_text(json.dumps(progress,indent=2)+'\n')
        cocotb.log.info('CAPTURE PROGRESS %s',item)

    async def trap_monitor():
        await RisingEdge(dut.observation_trap_o)
        await ReadOnly()
        details=dict(pc=int(dut.observation_pc_o.value),cause=int(dut.observation_cause_o.value))
        milestone('trap',**details)
        assert False,details

    image,address=build(Path.cwd()/'capture_system')
    milestone('compiled',image_bytes=len(image),mailbox=address)
    await _initialize(dut)
    cocotb.start_soon(trap_monitor())
    dut.reset_i.value=0
    await ClockCycles(dut.clk_i,8)
    seq=0
    async def cmd(code,**kwargs):
        nonlocal seq
        try:
            result=await with_timeout(_exchange(dut,request(code,seq,**kwargs)),1,'ms')
        except Exception:
            milestone('transaction-failure',command=code,sequence=seq,arguments=kwargs,
                      pc=int(dut.observation_pc_o.value),halted=int(dut.halt_ack_o.value),
                      completion=int(dut.system_completion_o.value))
            raise
        seq=(seq+1)&255
        if result.status!=ST_OK:
            milestone('device-error',command=code,status=result.status,arguments=kwargs)
        assert result.status==ST_OK,(code,result.status)
        return result.words
    for offset in range(0,len(image),32):
        chunk=image[offset:offset+32]
        chunk+=bytes(-len(chunk)%4)
        await cmd(CMD_LOAD,address=offset,words=struct.unpack('<'+'I'*(len(chunk)//4),chunk),byte_enable=15)
        if offset%512==0:milestone('loading',bytes_acknowledged=min(offset+32,len(image)))
    milestone('loaded')
    async def header():
        values=[]
        for i in range(0,24,8):values.extend(await cmd(CMD_PEEK,address=address+4*i,count=8))
        assert values[:3]==[0x4332444e,1,280]
        assert values[12:20]==list(range(1,9))
        return values
    async def poke(value):
        await cmd(CMD_POKE,address=address+12,words=(value,),byte_enable=15)
        assert await cmd(CMD_PEEK,address=address+12,count=1)==(value,)
    await cmd(CMD_RESET_PC,address=0)
    await cmd(CMD_RESUME)
    milestone('first-resume')
    # Observe a real framebuffer store; request during a partially copied frame.
    await with_timeout(RisingEdge(dut.framebuffer_write_valid_o),25,'ms')
    milestone('first-pixel')
    await cmd(CMD_HALT)
    h=await header()
    assert h[5]==0 and h[7]==0
    await poke(1)
    await cmd(CMD_RESUME)
    # Cancel before the gameplay seam, then prove a later fresh request works.
    await ClockCycles(dut.clk_i,5000)
    await cmd(CMD_HALT)
    h=await header();assert h[5]==0
    await poke(0)
    await cmd(CMD_RESUME)
    milestone('partial-frame-cancelled')
    evidence=[]
    operation=None
    blocks=[]
    packets=[]
    rasters=0

    async def observe_host_packet(packet):
        nonlocal rasters
        code=packet[2]
        packets.append(code)
        if code==wire.CMD_BULK_READ:
            assert int(dut.halt_ack_o.value), 'bulk read without maintained Halt'
            blocks.append((int.from_bytes(packet[4:8],'little'),int.from_bytes(packet[8:10],'little')))
            milestone('host-bulk',sequence=operation.sequence,block=len(blocks),length=blocks[-1][1])
        # Observe at the actual production host's release-write boundary. Do
        # not drive requests/READY/release or decode responses on its behalf.
        if not (code==CMD_POKE and int.from_bytes(packet[4:8],'little')==address+12
                and int.from_bytes(packet[10:14],'little')==0):
            return
        assert int(dut.halt_ack_o.value)
        h=operation.snapshot
        assert h is not None and h[3]==h[5]==h[6]==operation.sequence
        frame=h[7]
        expected=bytes((3*x+5*y+frame)&255 for y in range(200) for x in range(320))
        colors=tuple((i*13+frame)&4095 for i in range(256))
        assert bytes(operation.partial_indices)==expected
        assert struct.unpack('<256I',operation.partial_palette)==colors
        assert blocks==[(0x10000000+4096*i,min(4096,64000-4096*i)) for i in range(16)]+[(address+96,1024)]
        first=packets.index(wire.CMD_BULK_READ)
        assert all(c not in (CMD_HALT,CMD_RESUME) for c in packets[first:])
        milestone('host-dump-checked',sequence=operation.sequence,frame=frame)
        # One entire public 800x525 raster, not an internal palette/framebuffer peek.
        await with_timeout(RisingEdge(dut.frame_start_o),20,'ms')
        await ReadOnly()
        for cycle in range(800*525):
            if cycle:await RisingEdge(dut.clk_i);await ReadOnly()
            x,y=int(dut.pixel_x_o.value),int(dut.pixel_y_o.value)
            rgb=int(dut.vga_red_o.value)*256+int(dut.vga_green_o.value)*16+int(dut.vga_blue_o.value)
            want=colors[expected[(y-40)//2*320+x//2]] if x<640 and 40<=y<440 else 0
            assert rgb==want,(cycle,x,y,rgb,want)
            assert int(dut.vga_hsync_o.value)==int(not 656<=x<752)
            assert int(dut.vga_vsync_o.value)==int(not 490<=y<492)
        await FallingEdge(dut.clk_i)
        assert int(dut.halt_ack_o.value)
        rasters+=1
        milestone('raster-checked',sequence=operation.sequence)

    transport=CaptureUartTransport(dut,observe_host_packet)
    client=uart.Client(transport,.5,clock=transport.clock,sleep=transport.sleep)
    identity=struct.pack('<8I',*range(1,9)).hex()

    @bridge
    def run_host():
        nonlocal operation
        operation=capture.Capture(client,dict(address=address,identity=identity),
                                  clock=transport.clock,sleep=transport.sleep)
        return operation.run(continuity_attested=True)

    try:
        for index in range(2):
            blocks.clear();packets.clear()
            report=await run_host()
            assert rasters==index+1
            assert report['generation_after']!=report['generation_ready']
            assert report['sequence']==index+1
            prefix=[wire.CMD_PING,wire.CMD_CAPS,CMD_HALT] if index==0 else [wire.CMD_PING,CMD_HALT]
            assert packets[:len(prefix)]==prefix
            assert packets[-1]==CMD_RESUME and not int(dut.halt_ack_o.value)
            evidence.append(dict(report,events=operation.events))
            milestone('host-released',sequence=report['sequence'],generation=report['generation_after'])
    finally:
        transport.stop()
    (Path.cwd()/'capture-system-evidence.json').write_text(json.dumps(evidence,indent=2)+'\n')
    milestone('pass')
    cocotb.log.info('CAPTURE SYSTEM PASS: production Capture/Client; two 17-block snapshots and public VGA rasters')
