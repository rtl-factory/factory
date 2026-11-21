#!/usr/bin/env python3
"""One bounded coherent N2D1 snapshot. No serial dependency before live execution.

Spec: wiki/src/nand2doom/scripts/host/spec.md#coherent-frame-capture.
"""
from __future__ import annotations

import sys
sys.dont_write_bytecode = True

import argparse
import hashlib
import json
import math
from pathlib import Path
import struct
import time

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from src.nand2doom.scripts.host import n2d_uart as uart  # noqa: E402
from src.nand2doom.scripts.host import n2d_uart_debug_host as wire  # noqa: E402
from src.nand2doom.scripts.sw_build.n2d_doom import _confined_output  # noqa: E402

MAGIC, ABI, WORDS, HEADER_WORDS = 0x4332444E, 1, 280, 24
IDLE, PREPARING, READY, ERROR = range(4)
FRAMEBUFFER = 0x10000000
LIMIT, CLEANUP, SHORT_HALT = 120.0, 15.0, 5.0
FRAME_BLOCKS = (4096,) * 15 + (2560,)
PALETTE_BLOCKS = (1024,)
PROTOCOL = 'N2B2 CRC32 bulk read'


class CaptureError(Exception):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


def plan_output(repository, requested):
    root = repository.resolve() / 'workdir'
    if root.is_symlink():
        raise ValueError('output workdir must not be a symbolic link')
    output = _confined_output(repository, requested)
    for ancestor in (output, *output.parents):
        if ancestor.exists() and not ancestor.is_dir():
            raise ValueError(f'output contains non-directory component: {ancestor}')
        if ancestor == root:
            break
    if output.exists():
        raise ValueError('output must be fresh')
    return output


def validate_layout(path):
    raw = path.read_bytes()
    manifest = json.loads(raw, object_pairs_hook=uart.unique_object)
    image = uart.validate_manifest(path)
    layout = manifest.get('capture', {})
    if (type(layout.get('abi')) is not int or layout.get('abi') != ABI
            or type(layout.get('words')) is not int or layout.get('words') != WORDS):
        raise ValueError('unsupported capture ABI')
    address = uart.integer(layout.get('address'), 'mailbox', 0, 0x700000-WORDS*4)
    if address % 32:
        raise ValueError('mailbox must be 32-byte aligned')
    identity = bytes.fromhex(layout['identity'])
    if len(identity) != 32 or digest(json.dumps(layout['identity_inputs'],sort_keys=True,
            separators=(',', ':')).encode()) != identity.hex():
        raise ValueError('invalid build identity recipe')
    elf = layout['elf']
    name = elf['path']
    if not isinstance(name,str) or Path(name).name != name or '\\' in name or ':' in name:
        raise ValueError('ELF must be a local portable filename')
    elf_path = path.resolve().parent / name
    if not elf_path.resolve().is_relative_to(path.resolve().parent):
        raise ValueError('ELF escapes manifest directory')
    if digest(elf_path.read_bytes()) != elf['sha256']:
        raise ValueError('ELF digest mismatch')
    identity_address = uart.integer(layout['identity_address'], 'identity address')
    binary = next(s.data for s in image.segments if s.address == 0)
    if identity_address % 4 or binary[identity_address:identity_address+32] != identity:
        raise ValueError('binary build identity mismatch')
    if address < len(binary):
        raise ValueError('mailbox overlaps initialized binary')
    return dict(layout, manifest_sha256=digest(raw), segments=manifest['segments'])


class Capture:
    def __init__(self, client, layout, *, clock=time.monotonic, sleep=time.sleep):
        self.client, self.layout, self.clock, self.sleep = client, layout, clock, sleep
        self.events = []
        self.start = clock()
        self.deadline = self.start + LIMIT
        self.halted_at = None
        self.owned = False
        self.safe_resume = False
        self.sequence = None
        self.previous_sequence = None
        self.accepted = False
        self.snapshot = None
        self.dumping = False
        self.partial_indices = bytearray()
        self.partial_palette = bytearray()

    def command(self, command, *, cleanup=False, **kwargs):
        now = self.clock()
        end = self.deadline if cleanup else self.deadline-CLEANUP
        if now + self.client.timeout > end:
            raise CaptureError('capture budget exhausted; incomplete snapshot')
        if self.halted_at is not None and not self.dumping and now+self.client.timeout-self.halted_at > SHORT_HALT:
            raise CaptureError('short halt budget exhausted')
        # Why: the write timeout stays fixed because a Windows handle must never be
        # reconfigured mid-packet; the full transaction is reserved before any command.
        if command == wire.CMD_CAPS:
            result = self.client.capabilities()
        elif command == wire.CMD_BULK_READ:
            result = self.client.bulk_read(kwargs['address'], kwargs['length'])
        else:
            result = self.client.command(command, **kwargs)
        self.events.append(dict(seconds=self.clock()-self.start, command=command,
                                address=kwargs.get('address'), count=kwargs.get('count'),
                                length=kwargs.get('length')))
        if command == wire.CMD_HALT:
            if self.halted_at is None:
                self.halted_at = now
        elif command == wire.CMD_RESUME:
            self.halted_at = None
        if self.clock() > end:
            raise CaptureError('command exceeded capture budget')
        return result

    def header(self, *, cleanup=False):
        words=[]
        for i in range(0, HEADER_WORDS, 8):
            words.extend(self.command(wire.CMD_PEEK,address=self.layout['address']+i*4,
                                      count=8,cleanup=cleanup))
        if words[:3] != [MAGIC,ABI,WORDS] or words[12:20] != list(struct.unpack('<8I',bytes.fromhex(self.layout['identity']))) or any(words[20:24]):
            self.safe_resume=False
            raise CaptureError('uninitialized, reset or wrong-image mailbox')
        if words[4] not in (IDLE,PREPARING,READY,ERROR) or words[11] not in (0,1):
            raise CaptureError('invalid mailbox state')
        return words

    def request(self, sequence, *, cleanup=False):
        address=self.layout['address']+12
        self.command(wire.CMD_POKE,address=address,words=(sequence,),byte_enable=15,cleanup=cleanup)
        if self.command(wire.CMD_PEEK,address=address,count=1,cleanup=cleanup) != (sequence,):
            raise CaptureError('request write verification failed')

    def resume(self, *, cleanup=False):
        self.command(wire.CMD_RESUME,cleanup=cleanup)

    def poll(self, predicate, until, *, cleanup=False):
        while self.clock() < until:
            self.sleep(0.05)
            self.command(wire.CMD_HALT,cleanup=cleanup)
            header=self.header(cleanup=cleanup)
            if predicate(header):
                return header
            self.resume(cleanup=cleanup)
        raise CaptureError('firmware progress deadline exceeded')

    def check_ownership(self, header, *, released=False):
        allowed = (0,) if released else (0, self.sequence)
        valid_last = (self.sequence,) if self.accepted else (self.previous_sequence, self.sequence)
        if (header[3] not in allowed or header[6] not in valid_last
                or (self.snapshot is not None and not header[11])):
            self.safe_resume=False
            raise CaptureError('release ownership changed or reset')
        if header[6] == self.sequence:
            self.accepted=True

    def release(self):
        if self.halted_at is None:
            self.command(wire.CMD_HALT,cleanup=True)
        self.dumping=True  # Why: release commands may then spend the remaining whole-operation reserve
        before=self.header(cleanup=True)
        self.check_ownership(before)
        if self.snapshot is not None and before != self.snapshot:
            self.safe_resume=False
            raise CaptureError('snapshot changed before release')
        generation=before[9]
        self.request(0,cleanup=True)
        self.resume(cleanup=True)
        self.dumping=False
        def acknowledged(h):
            # Why: ownership is checked on every poll, before poll() may resume again.
            # Generation equality detects sampling because numeric order is not valid
            # across u32 wrap; accepted IDs, unlike generation, never wrap.
            self.check_ownership(h, released=True)
            return h[4] in (IDLE,ERROR) and h[5]==0 and h[9]!=generation
        header=self.poll(acknowledged,self.deadline-1,cleanup=True)
        self.resume(cleanup=True)
        self.owned=False
        return header

    def run(self, *, continuity_attested=False):
        if not continuity_attested:
            raise CaptureError('unknown prior clock continuity: operator history attestation required')
        try:
            ping=self.command(wire.CMD_PING)
            if ping[1]:
                raise CaptureError('unknown initial halt age: no automatic resume')
            capabilities=self.command(wire.CMD_CAPS)
            self.command(wire.CMD_HALT)
            initial=self.header()
            if initial[3] or initial[4]!=IDLE or initial[5]:
                raise CaptureError('outstanding/error mailbox: explicit operator reconciliation required')
            self.safe_resume=True
            if initial[6]==0xffffffff:
                raise CaptureError('capture sequence exhausted; no automatic reinitialization')
            # Why: resuming and waiting for a new generation first gives the request a real
            # service witness, not a stale header.
            self.resume()
            baseline=self.poll(lambda h: h[9]!=initial[9],self.start+15)
            if baseline[3] or baseline[4]!=IDLE or baseline[5] or baseline[6]!=initial[6]:
                self.safe_resume=False
                raise CaptureError('mailbox changed during baseline service')
            self.sequence=baseline[6]+1
            self.previous_sequence=baseline[6]
            self.owned=True  # Why: set before request(), so cleanup still runs if the write readback fails
            self.request(self.sequence)
            self.resume()

            def ready(h):
                self.check_ownership(h)
                if h[3]!=self.sequence:
                    self.safe_resume=False
                    raise CaptureError('request cancelled, replaced or reset')
                if h[4]==ERROR:
                    raise CaptureError('request cancelled, replaced, reset or firmware error')
                if h[5] and (h[5]!=self.sequence or h[4]!=READY):
                    raise CaptureError('stale or partial READY publication')
                return h[4]==READY and h[5]==self.sequence and h[9]!=baseline[9]

            header=self.poll(ready,self.start+30)
            if header[6]!=self.sequence or header[10] or not header[11] or header[8]&2:
                raise CaptureError('READY lacks committed palette proof')
            self.dumping=True
            for base,blocks,output in ((FRAMEBUFFER,FRAME_BLOCKS,self.partial_indices),
                    (self.layout['address']+HEADER_WORDS*4,PALETTE_BLOCKS,self.partial_palette)):
                offset=0
                for length in blocks:
                    data=self.command(wire.CMD_BULK_READ,address=base+offset,length=length)
                    # Why: the client returns only a complete validated block; a device error's
                    # completed prefix is diagnostic, never image data.
                    output.extend(data)
                    offset+=length
            if self.header()!=header:
                raise CaptureError('header changed during maintained halt')
            if any(value>0xfff for value in struct.unpack('<256I',self.partial_palette)):
                raise CaptureError('palette contains non-RGB444 bits')
            self.snapshot=header
            acknowledged=self.release()
            if acknowledged[4]!=IDLE or acknowledged[10]:
                raise CaptureError('firmware error at release acknowledgement')
            return dict(sequence=self.sequence,frame=header[7],video=header[8],
                payload_protocol=PROTOCOL,capabilities=list(capabilities),
                framebuffer_block_lengths=list(FRAME_BLOCKS),
                palette_block_lengths=list(PALETTE_BLOCKS),payload_exchanges=17,
                generation_before=baseline[9],generation_ready=header[9],
                generation_after=acknowledged[9],elapsed_seconds=self.clock()-self.start,
                clock_continuity='conditional on operator history attestation; service observed before/after',
                clock_assumptions='No prior lost period; sample age <=10s at entry; exclusive running endpoint',
                method='completed gameplay display; settled submitted palette; two raster advances; maintained Halt',
                limitations='Sampled gameplay; real elapsed time advances during halt; not physical sync evidence')
        except Exception:
            # Why: a poisoned transaction has unknown effects, so cleanup is not guessed.
            if not self.client.failed and self.safe_resume and self.clock()+5 < self.deadline:
                try:
                    if self.owned:
                        self.dumping=True
                        self.release()
                    elif self.halted_at is not None:
                        self.dumping=True
                        self.resume(cleanup=True)
                except Exception as error:
                    self.events.append(dict(cleanup_error=str(error)))
            raise


def parser():
    result=argparse.ArgumentParser(description=__doc__)
    result.add_argument('--manifest',type=Path,required=True)
    result.add_argument('--output',type=Path,required=True)
    result.add_argument('--hardware-image',type=Path,required=True,help='local validated SOF for provenance only')
    result.add_argument('--port')
    result.add_argument('--timeout',type=float,default=0.5)
    uart.add_baud_option(result)
    result.add_argument('--clock-history-attested',action='store_true',
                        help='operator attests no prior lost period and current sample age <=10s')
    result.add_argument('--dry-run','--plan',action='store_true')
    return result


def main(argv=None, *, opener=uart.open_serial):
    args=parser().parse_args(argv)
    try:
        if not math.isfinite(args.timeout) or not .01<=args.timeout<=1:
            raise ValueError('timeout must be finite 0.01..1 seconds')
        layout=validate_layout(args.manifest)
        output=plan_output(Path(__file__).resolve().parents[4],args.output)
        hardware_hash=digest(args.hardware_image.read_bytes())
        if args.dry_run:
            print(json.dumps(dict(mailbox=layout['address'],bursts=17,budget_seconds=LIMIT,
                                  payload_protocol=PROTOCOL,capability_required=[wire.BULK_IDENTITY,4096,3],
                                  framebuffer_block_lengths=list(FRAME_BLOCKS),
                                  palette_block_lengths=list(PALETTE_BLOCKS),
                                  output=str(output),hardware_sha256=hardware_hash),sort_keys=True))
            return 0
        if not args.port or not args.clock_history_attested:
            raise ValueError('live capture requires explicit port and clock history attestation')
        output.mkdir(parents=True,exist_ok=False)
    except (ValueError,OSError,KeyError,TypeError,StopIteration) as error:
        print(f'capture plan failed: {error}')
        return 2
    operation=None
    try:
        with opener(args.port,args.timeout,args.baud) as transport:
            operation=Capture(uart.Client(transport,args.timeout),layout)
            report=operation.run(continuity_attested=args.clock_history_attested)
        indices,palette=bytes(operation.partial_indices),bytes(operation.partial_palette)
        # Why: the framebuffer window always reads 320x200; half-width (DE10-Lite)
        # hardware returns each stored even-x pixel for both columns.
        report.update(schema_version=1,status='success',width=320,height=200,
            horizontal_pairs_equal=all(indices[i]==indices[i+1] for i in range(0,len(indices),2)),
            indices_sha256=digest(indices),palette_sha256=digest(palette),
            layout=layout,hardware_sha256=hardware_hash,events=operation.events,
            timestamp_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()))
        (output/'indices.bin').write_bytes(indices)
        (output/'palette.bin').write_bytes(palette)
        (output/'capture.json').write_text(json.dumps(report,indent=2)+'\n')
        return 0
    except (Exception,KeyboardInterrupt) as error:
        report=dict(status='failed',error=str(error),clock_continuity='unknown; no silent recovery')
        if operation is not None:
            report.update(events=operation.events,elapsed_seconds=operation.clock()-operation.start,
                          maintained_halt=operation.halted_at is not None,
                          transport_poisoned=operation.client.failed,
                          indices_bytes=len(operation.partial_indices),
                          palette_bytes=len(operation.partial_palette))
            if report['elapsed_seconds']+10>=171.79869184:
                report['clock_continuity']='lost-clock-continuity'
            (output/'indices.partial').write_bytes(operation.partial_indices)
            (output/'palette.partial').write_bytes(operation.partial_palette)
        (output/'failure.json').write_text(json.dumps(report,indent=2)+'\n')
        print(f'capture failed: {error}')
        return 3

if __name__=='__main__':
    raise SystemExit(main())
