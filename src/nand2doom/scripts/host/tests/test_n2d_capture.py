"""Byte transport exercises the production Client, not a permissive client stub."""
from pathlib import Path
import json
import subprocess
import struct
import sys
import tempfile
import unittest
import zlib
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[5]
from src.nand2doom.scripts.host import n2d_capture as capture
from src.nand2doom.scripts.host import n2d_uart as uart
from src.nand2doom.scripts.host import n2d_uart_debug_host as wire
from src.nand2doom.scripts.host.tests.test_n2d_uart import response

class Device:
    def __init__(self):
        self.now=0.; self.halted=False; self.pending=bytearray(); self.requests=[]
        self.header=[capture.MAGIC,1,280,0,0,0,0,7,17,1,0,1]+[0]*12
        self.indices=bytes((i*19+i//320)&255 for i in range(64000))
        self.palette=struct.pack('<256I',*(i*13 & 4095 for i in range(256)))
        self.freeze_generation=False; self.wrong_ready=False; self.corrupt_at=None
        self.delay=.001; self.reads=0; self.mutate=False; self.release_seen=False
        self.partial_ready=0; self.reset_during_dump=False
        self.closed=False
        self.caps=(wire.BULK_IDENTITY,4096,3)
        self.bulk_fault=None; self.bulk_fault_at=1; self.budget_at=None
        self.wire_timing=False
    def __enter__(self):
        return self
    def __exit__(self,*args):
        self.closed=True
    def sleep(self,seconds):
        self.now+=seconds
    def write(self,packet):
        self.now+=.016+len(packet)*10/1_000_000 if self.wire_timing else self.delay
        command,seq=packet[2:4]; address=int.from_bytes(packet[4:8],'little')
        count=int.from_bytes(packet[8:10],'little') if command==wire.CMD_BULK_READ else packet[8]
        self.requests.append((command,address,count))
        words=()
        if command==wire.CMD_PING: words=(0x3144324e,int(self.halted))
        elif command==wire.CMD_CAPS: words=self.caps or ()
        elif command==wire.CMD_HALT: self.halted=True
        elif command==wire.CMD_RESUME:
            self.halted=False
            if not self.freeze_generation: self.header[9]=(self.header[9]+1)&0xffffffff
            if self.header[3]:
                self.header[4]=capture.READY; self.header[5]=self.header[3]+int(self.wrong_ready)
                self.header[6]=self.header[3]
                if self.partial_ready:
                    self.header[5]=0; self.partial_ready-=1
            else:
                self.release_seen=True; self.header[4]=self.header[5]=0
        elif command==wire.CMD_POKE:
            assert self.halted and address==0x100c and count==1 and packet[9]==15
            self.header[3]=int.from_bytes(packet[10:14],'little')
        elif command==wire.CMD_PEEK:
            assert self.halted
            if 0x1000<=address<0x1060:
                if self.mutate and self.reads==17: self.header[7]+=1
                if self.reset_during_dump and self.reads==17:self.header[0]=0
                offset=(address-0x1000)//4; words=tuple(self.header[offset:offset+count])
            elif address==0x100c: words=(self.header[3],)
            else: raise AssertionError('payload must use bulk reads')
        elif command==wire.CMD_BULK_READ:
            assert self.halted and packet[:2]==b'\xa6\x02'
            assert zlib.crc32(packet[1:-4])==int.from_bytes(packet[-4:],'little')
            self.reads+=1
            offset=address-(capture.FRAMEBUFFER if address>=capture.FRAMEBUFFER else 0x1060)
            data=(self.indices if address>=capture.FRAMEBUFFER else self.palette)[offset:offset+count]
            fault=self.bulk_fault if self.reads==self.bulk_fault_at else None
            status=wire.ST_MEMORY if fault=='status' else 0
            completed=4 if status else count
            if status:data=b''
            body=bytes((2,command|128,seq ^ int(fault=='sequence'),status))
            body+=(address+4*int(fault=='address')).to_bytes(4,'little')
            body+=(len(data)+4*int(fault=='length')).to_bytes(2,'little')
            body+=completed.to_bytes(2,'little')+data
            result=b'\x6a'+body+zlib.crc32(body).to_bytes(4,'little')
            if fault=='crc':result=result[:-1]+bytes([result[-1]^1])
            if fault=='truncated':result=result[:-7]
            if fault=='missing':result=b''
            if fault=='short-write':return len(packet)-1
        else: raise AssertionError(command)
        if command!=wire.CMD_BULK_READ:
            result=response(command,seq,words,status=wire.ST_COMMAND if command==wire.CMD_CAPS and self.caps is None else 0)
        if len(self.requests)==self.corrupt_at: result=result[:-1]+bytes([result[-1]^1])
        self.pending.extend(result)
        return len(packet)
    def read(self,count):
        count=min(count,3); data=bytes(self.pending[:count]); del self.pending[:count]
        if self.wire_timing:self.now+=len(data)*10/1_000_000
        return data

class CaptureHostTests(unittest.TestCase):
    def operation(self,device):
        client=uart.Client(device,.1,clock=lambda:device.now,sleep=device.sleep)
        def clock():
            # Why: a healthy scheduling delay after a fully checked block, outside the
            # Client transaction, must still leave cleanup its 120 s reserve.
            if device.budget_at==device.reads and not device.pending:
                device.now=104.95;device.budget_at=None
            return device.now
        return capture.Capture(client,dict(address=0x1000,identity='00'*32),
                               clock=clock,sleep=device.sleep)
    def test_exact_bytes_bursts_single_halt_and_release(self):
        d=Device(); op=self.operation(d); result=op.run(continuity_attested=True)
        self.assertEqual(bytes(op.partial_indices),d.indices)
        self.assertEqual(bytes(op.partial_palette),d.palette)
        self.assertEqual(d.reads,17)
        self.assertEqual(result['sequence'],1)
        self.assertFalse(d.halted)
        self.assertEqual(d.header[3:6],[0,0,0])
        self.assertGreater(result['generation_after'],result['generation_ready'])
        payload=[r for r in d.requests if r[0]==wire.CMD_BULK_READ]
        self.assertEqual(payload,[(wire.CMD_BULK_READ,capture.FRAMEBUFFER+4096*i,
            min(4096,64000-4096*i)) for i in range(16)]+[(wire.CMD_BULK_READ,0x1060,1024)])
        first=d.requests.index(payload[0]); last=d.requests.index(payload[-1])
        self.assertTrue(all(r[0]==wire.CMD_BULK_READ for r in d.requests[first:last+1]))
        self.assertEqual([r[0] for r in d.requests[:3]],[wire.CMD_PING,wire.CMD_CAPS,wire.CMD_HALT])
        self.assertEqual(result['payload_exchanges'],17)
        self.assertEqual(result['capabilities'],[wire.BULK_IDENTITY,4096,3])
    def test_partial_publication_resumes_and_repeated_capture(self):
        d=Device();d.partial_ready=2
        first=self.operation(d).run(continuity_attested=True)
        second=self.operation(d).run(continuity_attested=True)
        self.assertEqual([first['sequence'],second['sequence']],[1,2])
        self.assertEqual(d.reads,34)
        self.assertFalse(d.halted)
    def test_reset_during_dump_never_writes_unknown_image(self):
        d=Device();d.reset_during_dump=True;op=self.operation(d)
        with self.assertRaisesRegex(capture.CaptureError,'wrong-image'):
            op.run(continuity_attested=True)
        writes=[r for r in d.requests if r[0]==wire.CMD_POKE]
        self.assertEqual(len(writes),1)
        self.assertTrue(d.halted)
    def test_unknown_history_or_initial_halt_never_resumed(self):
        d=Device(); op=self.operation(d)
        with self.assertRaisesRegex(capture.CaptureError,'prior clock'): op.run()
        self.assertFalse(d.requests)
        d.halted=True
        with self.assertRaisesRegex(capture.CaptureError,'initial halt'): op.run(continuity_attested=True)
        self.assertEqual(len(d.requests),1)
    def test_uninitialized_foreign_and_exhausted(self):
        for index,value in ((0,0),(1,2),(3,9),(6,0xffffffff),(12,1)):
            with self.subTest(index=index):
                d=Device();d.header[index]=value;op=self.operation(d)
                with self.assertRaises(capture.CaptureError):op.run(continuity_attested=True)
                self.assertFalse(any(r[0]==wire.CMD_POKE for r in d.requests))
    def test_resume_is_not_clock_service(self):
        d=Device();d.freeze_generation=True;op=self.operation(d)
        with self.assertRaisesRegex(capture.CaptureError,'progress deadline'):op.run(continuity_attested=True)
        self.assertFalse(any(r[0]==wire.CMD_POKE for r in d.requests))
        self.assertFalse(d.halted)
    def test_wrong_ready_palette_and_header_mutation_fail(self):
        for case in ('ready','palette','header'):
            with self.subTest(case=case):
                d=Device()
                if case=='ready':d.wrong_ready=True
                if case=='palette':d.palette=struct.pack('<I',4096)+d.palette[4:]
                if case=='header':d.mutate=True
                op=self.operation(d)
                with self.assertRaises(capture.CaptureError):op.run(continuity_attested=True)
                self.assertFalse(d.halted)
    def test_poison_during_dump_sends_no_cleanup(self):
        d=Device();d.corrupt_at=23;op=self.operation(d)
        with self.assertRaises(uart.HostError):op.run(continuity_attested=True)
        self.assertEqual(len(d.requests),23)
        self.assertTrue(op.client.failed)
        self.assertTrue(d.halted)
        self.assertLess(len(op.partial_indices),64000)
    def test_healthy_budget_abort_releases_without_success(self):
        d=Device();d.budget_at=8;op=self.operation(d)
        with self.assertRaisesRegex(capture.CaptureError,'budget'):op.run(continuity_attested=True)
        self.assertFalse(op.client.failed)
        self.assertFalse(d.halted)
        self.assertEqual(d.header[3:6],[0,0,0])
        self.assertLess(d.now,120)
        self.assertLess(len(op.partial_indices),64000)

    def test_release_reset_boundaries_stop_without_further_mutations(self):
        for boundary in ('before-release','after-release','later-ack'):
            with self.subTest(boundary=boundary):
                d=ReleaseReset(boundary);op=self.operation(d)
                with self.assertRaisesRegex(capture.CaptureError,'ownership.*reset'):
                    op.run(continuity_attested=True)
                self.assertTrue(d.injected)
                self.assertFalse(op.safe_resume)
                self.assertTrue(d.halted)
                self.assertEqual(d.requests[-3:],[(wire.CMD_PEEK,0x1000+i*32,8) for i in range(3)])
                self.assertEqual(len(d.requests),d.last_header_end)
                self.assertEqual(sum(r[0]==wire.CMD_POKE for r in d.requests),
                                 1 if boundary=='before-release' else 2)

    def test_generation_wrap_and_preacceptance_deadline_release(self):
        for generation in (0xfffffffd,0xfffffffe,0xffffffff):
            with self.subTest(generation=generation):
                d=Device();d.header[9]=generation
                result=self.operation(d).run(continuity_attested=True)
                self.assertEqual(d.header[6],result['sequence'])
                self.assertNotEqual(result['generation_after'],result['generation_ready'])
                self.assertFalse(d.halted)
        class Unaccepted(Device):
            def write(self,packet):
                result=super().write(packet)
                if packet[2]==wire.CMD_RESUME and self.header[3]:
                    self.header[4:7]=[capture.IDLE,0,0]
                return result
        d=Unaccepted();op=self.operation(d)
        with self.assertRaisesRegex(capture.CaptureError,'progress deadline'):
            op.run(continuity_attested=True)
        self.assertFalse(op.accepted)
        self.assertEqual(d.header[3:7],[0,0,0,0])
        self.assertFalse(d.halted)
        self.assertLess(d.now,120)

    def test_poison_at_release_boundaries_never_sends_another_command(self):
        healthy=Device();self.operation(healthy).run(continuity_attested=True)
        for index in range(len(healthy.requests)-12,len(healthy.requests)+1):
            with self.subTest(index=index):
                d=Device();d.corrupt_at=index;op=self.operation(d)
                with self.assertRaises(uart.HostError):op.run(continuity_attested=True)
                self.assertTrue(op.client.failed)
                self.assertEqual(len(d.requests),index)

    def test_old_or_incompatible_endpoint_refused_before_halt(self):
        for caps in (None,(0,4096,3),(wire.BULK_IDENTITY,2048,3),(wire.BULK_IDENTITY,4096,1)):
            with self.subTest(caps=caps):
                d=Device();d.caps=caps;op=self.operation(d)
                with self.assertRaises(uart.HostError):op.run(continuity_attested=True)
                self.assertEqual([r[0] for r in d.requests],[wire.CMD_PING,wire.CMD_CAPS])
                self.assertTrue(op.client.failed)
                self.assertFalse(d.halted)
                self.assertFalse(op.partial_indices)

    def test_same_client_capability_cached_across_captures(self):
        d=Device();first=self.operation(d);first.run(continuity_attested=True)
        second=capture.Capture(first.client,first.layout,clock=lambda:d.now,sleep=d.sleep)
        second.run(continuity_attested=True)
        self.assertEqual(sum(r[0]==wire.CMD_CAPS for r in d.requests),1)
        self.assertEqual(d.reads,34)
        self.assertFalse(d.halted)

    def test_each_bulk_boundary_fault_poison_no_partial_block_or_cleanup(self):
        for fault in ('crc','sequence','address','length','status','truncated','missing','short-write'):
            for block in range(1,18):
                with self.subTest(fault=fault,block=block):
                    d=Device();d.bulk_fault=fault;d.bulk_fault_at=block;op=self.operation(d)
                    with self.assertRaises(uart.BulkError) as error:
                        op.run(continuity_attested=True)
                    self.assertTrue(op.client.failed)
                    self.assertEqual(d.reads,block)
                    self.assertEqual(d.requests[-1][0],wire.CMD_BULK_READ)
                    self.assertEqual(len(op.partial_indices),min((block-1)*4096,64000))
                    self.assertEqual(len(op.partial_palette),0)
                    self.assertEqual(error.exception.completed,4 if fault=='status' else None)
                    self.assertEqual(sum(r[0]==wire.CMD_POKE for r in d.requests),1)
                    self.assertTrue(d.halted)

    def test_full_timeout_reserved_for_capability_bulk_and_cleanup(self):
        for code,args in ((wire.CMD_CAPS,{}),(wire.CMD_BULK_READ,dict(address=capture.FRAMEBUFFER,length=4096))):
            d=Device();op=self.operation(d);d.now=104.901
            with self.assertRaisesRegex(capture.CaptureError,'budget'):
                op.command(code,**args)
            self.assertFalse(d.requests)
            self.assertFalse(op.client.failed)
        d=Device();op=self.operation(d);d.now=105.1
        op.command(wire.CMD_RESUME,cleanup=True)
        self.assertEqual(len(d.requests),1)
        d.now=119.901
        with self.assertRaisesRegex(capture.CaptureError,'budget'):
            op.command(wire.CMD_RESUME,cleanup=True)
        self.assertEqual(len(d.requests),1)

    def test_modelled_wire_latency_and_wire_sequence_wrap(self):
        d=Device();d.wire_timing=True;op=self.operation(d);op.client.sequence=250
        result=op.run(continuity_attested=True)
        self.assertEqual(d.reads,17)
        self.assertGreater(d.now,65024*10/1_000_000)
        self.assertLess(d.now,3)
        self.assertEqual(op.client.sequence,(250+len(d.requests))%256)
        self.assertEqual(result['payload_exchanges'],17)
        self.assertEqual(bytes(op.partial_indices),d.indices)

    def test_command_overrun_discards_validated_late_block(self):
        d=Device();op=self.operation(d)
        # Why: descheduling after a complete transaction but before the capture-level
        # post-check must discard the block; no transport corruption is invented.
        checks=0
        def clock():
            nonlocal checks
            if d.reads==1 and not d.pending:
                checks+=1
                if checks==1:d.now=105.1
            return d.now
        op.clock=clock
        with self.assertRaisesRegex(capture.CaptureError,'exceeded capture budget'):
            op.run(continuity_attested=True)
        self.assertFalse(op.partial_indices)
        self.assertFalse(d.halted)
        self.assertFalse(op.client.failed)


class ReleaseReset(Device):
    """Both independent-review reset boundaries plus a later ack poll."""
    def __init__(self,boundary):
        super().__init__();self.boundary=boundary;self.end_headers=0
        self.injected=False;self.last_header_end=None;self.release_resumes=0
    def reset_header(self):
        self.header[3:12]=[0,capture.IDLE,0,0,0,0,1,0,0]
        self.injected=True
    def write(self,packet):
        command=packet[2];address=int.from_bytes(packet[4:8],'little')
        if command==wire.CMD_PEEK and address==0x1000 and self.reads==17:
            self.end_headers+=1
            if self.boundary=='before-release' and self.end_headers==2:
                self.reset_header()
        result=super().write(packet)
        if command==wire.CMD_RESUME and self.reads==17 and self.header[3]==0:
            self.release_resumes+=1
            if self.boundary=='after-release' or (self.boundary=='later-ack' and self.release_resumes==2):
                self.reset_header()
            elif self.boundary=='later-ack':
                self.header[4]=capture.PREPARING
        if self.injected and command==wire.CMD_PEEK and address==0x1040:
            self.last_header_end=len(self.requests)
        return result

class CapturePlanTests(unittest.TestCase):
    def fixture(self, root):
        inputs={'sources': {'fixture.c': 'test-only'}}
        identity=capture.digest(json.dumps(inputs,sort_keys=True,separators=(',', ':')).encode())
        binary=bytes(16)+bytes.fromhex(identity)+bytes(16)
        elf=b'unit-test ELF digest fixture'
        (root/'firmware.bin').write_bytes(binary)
        (root/'firmware.elf').write_bytes(elf)
        (root/'hardware.sof').write_bytes(b'unit-test hardware provenance fixture')
        manifest=dict(schema_version=1,architecture='rv32im',abi='ilp32',
            endianness='little',entry=0,memory_map_revision='n2d-platform-v1',
            segments=[dict(address=0,path='firmware.bin',byte_length=len(binary),
                           sha256=capture.digest(binary))],
            capture=dict(abi=1,words=280,address=0x1000,identity_address=16,
                         identity=identity,identity_inputs=inputs,
                         elf=dict(path='firmware.elf',sha256=capture.digest(elf))))
        path=root/'manifest.json'
        path.write_text(json.dumps(manifest))
        return path,manifest

    def test_manifest_digests_types_identity_and_output_plan(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'workdir') as directory:
            root=Path(directory)
            path,manifest=self.fixture(root)
            self.assertEqual(capture.validate_layout(path)['address'],0x1000)
            for field,value in (('address',0x1001),('abi',True),('identity','ff'*32),
                                ('identity_address',20),('words',279)):
                old=manifest['capture'][field];manifest['capture'][field]=value
                path.write_text(json.dumps(manifest))
                with self.assertRaises(ValueError):capture.validate_layout(path)
                manifest['capture'][field]=old
            path.write_text(json.dumps(manifest))
            before={str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}
            result=subprocess.run([sys.executable,'-S',str(ROOT/'src/nand2doom/scripts/host/n2d_capture.py'),
                '--dry-run','--manifest',str(path),'--hardware-image',str(root/'hardware.sof'),
                '--output',str(root/'new-output')],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            plan=json.loads(result.stdout)
            self.assertEqual(plan['bursts'],17)
            self.assertEqual(plan['framebuffer_block_lengths'],[4096]*15+[2560])
            self.assertEqual(plan['palette_block_lengths'],[1024])
            self.assertEqual(plan['capability_required'],[wire.BULK_IDENTITY,4096,3])
            self.assertFalse((root/'new-output').exists())
            self.assertEqual(before,{str(p.relative_to(root)):p.read_bytes()
                                     for p in root.rglob('*') if p.is_file()})
            (root/'firmware.elf').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'ELF digest'):capture.validate_layout(path)

    def test_help_without_site_packages(self):
        result=subprocess.run([sys.executable,'-S',str(ROOT/'src/nand2doom/scripts/host/n2d_capture.py'),
                               '--help'],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('--clock-history-attested',result.stdout)

    def test_output_planning_rejects_file_ancestors_and_symlinks_without_writes(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'workdir') as directory:
            root=Path(directory);manifest,_=self.fixture(root)
            for case in ('root-file','nested-file','root-link','nested-link','broken-link','existing'):
                repo=root/case;repo.mkdir();work=repo/'workdir'
                if case=='root-file':work.write_bytes(b'preserve')
                elif case=='root-link':work.symlink_to(root,target_is_directory=True)
                else:work.mkdir()
                if case=='nested-file':(work/'parent').write_bytes(b'preserve')
                elif case in ('nested-link','broken-link'):
                    (work/'parent').symlink_to(root if case=='nested-link' else root/'absent',target_is_directory=True)
                elif case=='existing':(work/'parent').mkdir()
                output=work/'parent' if case=='existing' else work/'parent'/'capture'
                argv=['--manifest',str(manifest),'--hardware-image',str(root/'hardware.sof'),'--output',str(output)]
                for mode in ('plan','live'):
                    with self.subTest(case=case,mode=mode), \
                         patch.object(capture,'__file__',str(repo/'src/nand2doom/scripts/host/n2d_capture.py')), \
                         patch.object(Path,'mkdir',side_effect=AssertionError('mkdir called')), \
                         patch.object(Path,'write_bytes',side_effect=AssertionError('write called')), \
                         patch.object(Path,'write_text',side_effect=AssertionError('write called')):
                        args=argv+(['--dry-run'] if mode=='plan' else ['--port','MOCK','--clock-history-attested'])
                        opener=unittest.mock.Mock(side_effect=AssertionError('opener called'))
                        self.assertEqual(capture.main(args,opener=opener),2)
                        opener.assert_not_called()
                if case=='root-file':self.assertEqual(work.read_bytes(),b'preserve')
                if case=='nested-file':self.assertEqual((work/'parent').read_bytes(),b'preserve')
            repo=root/'valid';repo.mkdir()
            with patch.object(capture,'__file__',str(repo/'src/nand2doom/scripts/host/n2d_capture.py')), \
                 patch.object(Path,'mkdir',side_effect=AssertionError('mkdir called')):
                self.assertEqual(capture.main(['--manifest',str(manifest),'--hardware-image',str(root/'hardware.sof'),
                    '--output',str(repo/'workdir/new/nested/capture'),'--dry-run']),0)
            self.assertFalse((repo/'workdir').exists())

    def test_release_reset_never_publishes_success_or_continuity(self):
        for boundary in ('before-release','after-release','later-ack'):
            with self.subTest(boundary=boundary),tempfile.TemporaryDirectory(dir=ROOT/'workdir') as directory:
                root=Path(directory);path,manifest=self.fixture(root)
                d=ReleaseReset(boundary)
                d.header[12:20]=struct.unpack('<8I',bytes.fromhex(manifest['capture']['identity']))
                real_capture=capture.Capture
                def operation(client,layout):
                    client.clock=lambda:d.now;client.sleep=d.sleep
                    return real_capture(client,layout,clock=lambda:d.now,sleep=d.sleep)
                with patch.object(capture,'Capture',operation):
                    status=capture.main(['--manifest',str(path),'--output',str(root/'failed'),
                        '--hardware-image',str(root/'hardware.sof'),'--port','MOCK',
                        '--clock-history-attested'],opener=lambda *_:d)
                self.assertEqual(status,3)
                self.assertTrue(d.closed)
                report=json.loads((root/'failed/failure.json').read_text())
                self.assertEqual(report['status'],'failed')
                self.assertIn('unknown',report['clock_continuity'])
                for name in ('capture.json','indices.bin','palette.bin'):
                    self.assertFalse((root/'failed'/name).exists())

    def test_clock_overrun_preserves_failure_and_never_sends_cleanup(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'workdir') as directory:
            root=Path(directory)
            path,_=self.fixture(root)
            d=Device();d.delay=200
            real_capture=capture.Capture
            def operation(client,layout):
                client.clock=lambda:d.now
                client.sleep=d.sleep
                return real_capture(client,layout,clock=lambda:d.now,sleep=d.sleep)
            with patch.object(capture,'Capture',operation):
                status=capture.main(['--manifest',str(path),'--output',str(root/'failed'),
                    '--hardware-image',str(root/'hardware.sof'),'--port','FAKE',
                    '--clock-history-attested'],opener=lambda *_:d)
            self.assertEqual(status,3)
            self.assertEqual(len(d.requests),1)
            self.assertTrue(d.closed)
            report=json.loads((root/'failed/failure.json').read_text())
            self.assertEqual(report['clock_continuity'],'lost-clock-continuity')
            self.assertTrue(report['transport_poisoned'])
            self.assertFalse((root/'failed/capture.json').exists())
            self.assertFalse((root/'failed/indices.bin').exists())

    def test_bulk_error_prefix_never_publishes_image_or_success(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'workdir') as directory:
            root=Path(directory);path,manifest=self.fixture(root)
            d=Device();d.bulk_fault='status';d.bulk_fault_at=2
            d.header[12:20]=struct.unpack('<8I',bytes.fromhex(manifest['capture']['identity']))
            real_capture=capture.Capture
            def operation(client,layout):
                client.clock=lambda:d.now;client.sleep=d.sleep
                return real_capture(client,layout,clock=lambda:d.now,sleep=d.sleep)
            with patch.object(capture,'Capture',operation):
                status=capture.main(['--manifest',str(path),'--output',str(root/'failed'),
                    '--hardware-image',str(root/'hardware.sof'),'--port','MOCK',
                    '--clock-history-attested'],opener=lambda *_:d)
            self.assertEqual(status,3)
            report=json.loads((root/'failed/failure.json').read_text())
            self.assertTrue(report['transport_poisoned'])
            self.assertEqual(report['indices_bytes'],4096)
            self.assertEqual((root/'failed/indices.partial').read_bytes(),d.indices[:4096])
            self.assertFalse((root/'failed/capture.json').exists())
            self.assertFalse((root/'failed/indices.bin').exists())
            self.assertFalse((root/'failed/palette.bin').exists())
            self.assertEqual(d.requests[-1][0],wire.CMD_BULK_READ)
            self.assertTrue(d.closed)

if __name__=='__main__':unittest.main()
