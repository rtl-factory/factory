"""ELF-bound architectural UART stimulus witness; no device sample-margin claim."""
from __future__ import annotations

import hashlib
import struct
from pathlib import Path

from src.nand2doom.dv.software.doom_gameplay import BREAK, COMPLETION, MAKE, TURN

# Why: only the supported production Os RV32IM ABI is matched; relocated global
# immediates are read from the actual image, and both instruction identity and
# runtime address are checked, so a rebuilt image cannot silently move a seam.
# The offsets are those of the rv32_gcc pin in cfg/toolchain.json; a pin bump
# re-derives them from the new image's disassembly and updates SEAMS_RV32_GCC,
# which an L0 test compares with the pin.
SEAMS_RV32_GCC = "15.2.0-1"
SEAMS = {
    "entry": ("BuildNewTic", 0, 0xfd010113, 0xffffffff),
    "input_call": ("BuildNewTic", 48, 0x000080e7, 0xfffff),
    "post_input": ("BuildNewTic", 52, 0x0004a783, 0xfffff),
    "process_call": ("BuildNewTic", 60, 0x000780e7, 0xffffffff),
    "slot": ("BuildNewTic", 176, 0x00042583, 0xfffff),
    "command_call": ("BuildNewTic", 188, 0x000780e7, 0xffffffff),
    "publish": ("BuildNewTic", 256, 0x00942023, 0x01fff07f),
    "return": ("BuildNewTic", 284, 0x00008067, 0xffffffff),
    "status": ("DG_GetKey", 4, 0x0047a783, 0xffffffff),
    "zero": ("DG_GetKey", 16, 0x00000513, 0xffffffff),
    "empty_return": ("DG_GetKey", 20, 0x00008067, 0xffffffff),
    "data": ("DG_GetKey", 28, 0x0007a783, 0xffffffff),
    "key_marker": ("DG_GetKey", 124, 0x00f72223, 0xffffffff),
    "key_return": ("DG_GetKey", 128, 0x00008067, 0xffffffff),
    "event_return": ("I_GetEvent", 144, 0x00008067, 0xffffffff),
    "turn": ("N2D_RecordTiccmd", 48, 0x00f72223, 0xffffffff),
}


class InputABI:
    """Resolve unique actual symbols and bind executable ELF bytes to flat bin."""

    def __init__(self, elf: Path, binary: Path):
        data, raw = elf.read_bytes(), binary.read_bytes()
        assert data[:6] == b'\x7fELF\x01\x01' and struct.unpack_from('<H', data, 18)[0] == 243
        self.elf_sha256 = hashlib.sha256(data).hexdigest()
        self.bin_sha256 = hashlib.sha256(raw).hexdigest()
        phoff = struct.unpack_from('<I', data, 28)[0]
        phsize, phcount = struct.unpack_from('<HH', data, 42)
        segments = [struct.unpack_from('<IIIIIIII', data, phoff + i * phsize)
                    for i in range(phcount)]
        shoff = struct.unpack_from('<I', data, 32)[0]
        shsize, shcount = struct.unpack_from('<HH', data, 46)
        sections = [struct.unpack_from('<IIIIIIIIII', data, shoff + i * shsize)
                    for i in range(shcount)]
        names = {row[0] for row in SEAMS.values()} | {
            'I_StartTic', 'D_ProcessEvents', 'G_BuildTiccmd', 'NetUpdate',
            'loop_interface', 'gametic', 'maketic', 'ticdup'}
        symbols = {}
        for section in sections:
            if section[1] != 2:
                continue
            strings = sections[section[6]]
            table = data[strings[4]:strings[4] + strings[5]]
            assert section[9] == 16
            for pos in range(section[4], section[4] + section[5], 16):
                name, value, size, info, _, index = struct.unpack_from('<IIIBBH', data, pos)
                name = table[name:].split(b'\0', 1)[0].decode()
                if name in names:
                    assert name not in symbols and index != 0, f'duplicate/undefined {name}'
                    assert value % 4 == 0 and 0 <= value < 0x04000000
                    assert size >= 4 and (info & 15) == (1 if name in {
                        'loop_interface', 'gametic', 'maketic', 'ticdup'} else 2), name
                    if name in {'loop_interface', 'gametic', 'maketic', 'ticdup'}:
                        assert size == 4, f'non-word global {name}'
                    symbols[name] = (value, size)
        assert set(symbols) == names, f'missing input ABI symbols: {names - symbols.keys()}'
        self.symbols = {n: v[0] for n, v in symbols.items()}

        def word(pc):
            matches = [p for p in segments if p[0] == 1 and p[2] <= pc <= p[2] + p[4] - 4]
            assert len(matches) == 1 and matches[0][6] & 1, 'executable instruction mapping'
            p = matches[0]
            value = int.from_bytes(data[p[1] + pc - p[2]:p[1] + pc - p[2] + 4], 'little')
            assert value == int.from_bytes(raw[pc:pc + 4], 'little'), 'ELF/bin instruction mismatch'
            return value

        self.pc, self.instructions = {}, {}
        for label, (name, offset, expected, mask) in SEAMS.items():
            assert offset + 4 <= symbols[name][1], f'truncated {name}'
            pc = symbols[name][0] + offset
            instruction = word(pc)
            assert instruction & mask == expected & mask, f'unsupported input ABI {label}'
            self.pc[label], self.instructions[pc] = pc, instruction
        # Why: AUIPC/JALR pairs bind the caller and tailjump, because
        # I_StartTic has no return of its own to bind. The DG_GetKey call
        # returns to I_GetEvent+0x34.
        def pair(pc, destination, first, second):
            hi, lo = word(pc), word(pc + 4)
            assert hi & 0xfff == first and lo & 0xfffff == second
            immediate = lo >> 20
            if immediate & 0x800:
                immediate -= 0x1000
            assert (pc + (hi & 0xfffff000) + immediate) & 0xfffffffe == destination
            self.instructions[pc], self.instructions[pc + 4] = hi, lo
        pair(self.pc['input_call'] - 4, self.symbols['I_StartTic'], 0x097, 0x080e7)
        pair(self.symbols['I_StartTic'], self.symbols['I_GetEvent'], 0x317, 0x30067)
        pair(self.symbols['I_GetEvent'] + 0x2c, self.symbols['DG_GetKey'], 0x097, 0x080e7)
        self.pc['start_tail'] = self.symbols['I_StartTic'] + 4
        self.pc['key_call'] = self.symbols['I_GetEvent'] + 0x30
        self.key_return_target = self.symbols['I_GetEvent'] + 0x34

    def evidence(self):
        return {'elf_sha256': self.elf_sha256, 'bin_sha256': self.bin_sha256,
                'symbols': self.symbols, 'seam_pcs': self.pc,
                'instructions': {str(pc): word for pc, word in self.instructions.items()}}


class InputWitness:
    """Runtime ledger using GameplayTrace's complete initialized counter history.

    Neutral pre-MAKE invocations are retained, not rebased to synthetic zero.
    A MAKE outside a witnessed invocation, or an aborted MAKE/BREAK build fails.
    The ready callback is latched exactly once after the MAKE empty-return chain.
    """

    def __init__(self, abi: InputABI, trace, ready, boundary):
        self.abi, self.trace, self.ready, self.boundary = abi, trace, ready, boundary
        self.armed = False
        self.completed = False
        self.order = -1
        self.invocation = None
        self.history = []
        self.make = None
        self.empty_tick = None
        self.ready_tick = None
        self.make_ack_tick = None
        self.break_start_tick = None
        self.break_ack_tick = None
        self.records = []
        self.labels = {value: key for key, value in abi.pc.items()}
        self.boundary_tick = None
        self.make_start_tick = None

    def arm(self, tick):
        assert not self.armed and self.trace.phase == 1
        assert set(self.trace.counters) == {'gametic', 'maketic', 'ticdup'}
        assert all(v == 15 for v in self.trace.known.values())
        assert self.trace.counters['ticdup'] == 1
        assert self.trace.settled_frame(0) is not None and self.trace.barriers[0] is not None
        self.armed = True
        self.arm_tick = tick
        self.arm_counters = dict(self.trace.counters)

    def ack_make(self, tick):
        assert self.make_start_tick is not None and self.make_ack_tick is None
        assert tick >= self.make_start_tick
        self.make_ack_tick = tick

    def start_make(self, tick):
        assert self.boundary_tick is not None and tick > self.boundary_tick
        assert self.make_start_tick is None
        self.make_start_tick = tick

    def start_break(self, tick):
        assert self.ready_tick is not None and self.make_ack_tick is not None
        assert tick > max(self.ready_tick, self.make_ack_tick), 'writable edge after witness/ACK'
        assert self.break_start_tick is None
        self.break_start_tick = tick

    def accept(self, r):
        if not self.armed or self.completed or not r['valid']:
            return
        assert r['order'] > self.order, 'duplicate/out-of-order input retirement'
        self.order = r['order']
        assert not r['trap'], 'input construction trapped'
        a = self.abi
        pc = r['pc']
        label = self.labels.get(pc)
        # Why: memory writes are checked before the PC label, so no unqualified
        # construction or publication can hide behind a wrong PC.
        if r['memory_valid'] and r['memory_write']:
            if r['address'] == COMPLETION:
                marker = r['write_data']
                assert marker != 0xd00d37ee and marker & 0xfffff000 != 0xd00de000, 'runtime failure'
            if r['address'] == a.symbols['maketic']:
                assert label == 'publish', 'maketic publication outside bound invocation'
            if r['address'] == COMPLETION and r['write_data'] in (MAKE, BREAK, TURN):
                assert label == ('turn' if r['write_data'] == TURN else 'key_marker'), 'wrong marker PC'
        if label is None:
            if self.invocation and self.invocation['state'] == 'slot':
                assert not (r['rd_write'] and r['rd'] == 11), 'slot argument overwritten'
            if self.invocation and self.invocation['state'] in ('zero', 'empty-return', 'event-return'):
                assert not (r['rd_write'] and r['rd'] == 10), 'empty return value overwritten'
            return
        assert r['instruction'] == a.instructions[pc], 'input instruction identity'
        self.records.append(dict(r, seam=label))
        if label == 'entry':
            assert not r['memory_valid'] and not r['memory_write']
            assert r['read_mask'] == r['write_mask'] == 0 and r['next_pc'] == pc + 4
            assert r['rd_write'] and r['rd'] == 2, 'entry stack register'
            assert self.invocation is None, 'overlapping BuildNewTic invocation'
            self.invocation = {'index': len(self.history), 'entry_tick': r['tick'],
                               'slot': self.trace.counters['maketic'], 'state': 'entry',
                               'role': None, 'turns': 0}
            if self.make is not None:
                assert len(self.history) == self.make['index'] + 1, 'intervening construction'
            if self.boundary_tick is None:
                self.boundary_tick = r['tick']
                self.boundary()
            return
        i = self.invocation
        if self.boundary_tick is None:
            # Why: monitoring can begin mid-construction, but MAKE is not
            # injected until a newly retired entry has synchronized this ledger,
            # so every partial seam (including slot/publication) is retained
            # without claiming a complete invocation. All counter stores still
            # pass GameplayTrace.
            assert self.make_start_tick is None
            if label in ('key_marker', 'turn'):
                raise AssertionError('input marker before clean boundary')
            if label == 'slot':
                assert r['memory_valid'] and not r['memory_write'] and r['read_mask'] == 15
                assert r['write_mask'] == 0 and r['address'] == a.symbols['maketic']
                assert r['rd_write'] and r['rd'] == 11
                assert r['rd_data'] == r['read_data'] == self.trace.counters['maketic']
                assert r['next_pc'] == pc + 4
            elif label == 'publish':
                assert r['memory_valid'] and r['memory_write'] and r['write_mask'] == 15
                assert r['read_mask'] == 0 and r['address'] == a.symbols['maketic']
                assert r['write_data'] == self.trace.counters['maketic'] and not r['rd_write']
                assert r['next_pc'] == pc + 4
            self.records[-1]['pre_boundary'] = True
            return
        assert i is not None, f'{label} before witnessed invocation entry'

        def control(next_pc, rd=None, value=None):
            assert not r['memory_valid'] and not r['memory_write']
            assert r['read_mask'] == r['write_mask'] == 0 and r['next_pc'] == next_pc
            assert r['rd_write'] == (rd is not None)
            if rd is not None:
                assert r['rd'] == rd and r['rd_data'] == value

        def load(address, rd):
            assert r['memory_valid'] and not r['memory_write'] and r['read_mask'] == 15
            assert r['write_mask'] == 0 and r['address'] == address
            assert r['rd_write'] and r['rd'] == rd and r['rd_data'] == r['read_data']
            assert r['next_pc'] == pc + 4

        def store(address, value):
            assert r['memory_valid'] and r['memory_write'] and r['write_mask'] == 15
            assert r['read_mask'] == 0 and r['address'] == address and r['write_data'] == value
            assert not r['rd_write'] and r['next_pc'] == pc + 4

        state = i['state']
        if label == 'input_call':
            control(a.symbols['I_StartTic'], 1, pc + 4)
            assert state == 'entry'
            i['state'] = 'input-call'
        elif label == 'start_tail':
            control(a.symbols['I_GetEvent'])
            assert state == 'input-call'
            i['state'] = 'poll'
        elif label == 'key_call':
            control(a.symbols['DG_GetKey'], 1, a.key_return_target)
            assert state == 'poll'
            i['state'] = 'key-call'
        elif label == 'status':
            load(0x20002004, 15)
            assert state == 'key-call'
            status = r['read_data']
            count = (status >> 3) & 15
            assert status & ~0x1f7f == 0 and status & 0xf00 == 0, 'keyboard status reserved/error bits'
            assert status & 0x1000 and count <= 8, 'keyboard disabled/count overflow'
            assert (status & 7) == ((1 if count else 2) | (4 if count == 8 else 0)), 'keyboard status/count mismatch'
            if count:
                i['state'] = 'data'
            else:
                i['state'] = 'empty'
                i['empty_tick'] = r['tick']
        elif label == 'data':
            load(0x20002000, 15)
            assert state == 'data'
            expected = 0x80000274 if self.make is None else 0x80000374
            assert r['read_data'] == expected, 'unexpected DATA code/reserved bits'
            assert i['role'] is None, 'multiple input events in one invocation'
            i['state'] = 'consumed'
        elif label == 'key_marker':
            marker = MAKE if self.make is None else BREAK
            store(COMPLETION, marker)
            assert state == 'consumed'
            assert self.trace.counters['maketic'] == i['slot']
            if marker == MAKE:
                assert self.make_start_tick is not None, 'MAKE before injection'
                self.make = i
                i['role'] = 'make'
            else:
                assert self.break_start_tick is not None, 'BREAK before release initiation'
                assert i['index'] == self.make['index'] + 1 and i['slot'] == self.make['slot'] + 1
                assert self.trace.events['break'][1]['neutral_slot'] == i['slot']
                i['role'] = 'break'
            i['state'] = 'key-return'
        elif label == 'key_return':
            control(a.key_return_target)
            assert state == 'key-return'
            i['state'] = 'break-return' if i['role'] == 'break' else 'poll'
        elif label == 'zero':
            control(pc + 4, 10, 0)
            assert state == 'empty'
            i['state'] = 'zero'
        elif label == 'empty_return':
            control(a.key_return_target)
            assert state == 'zero'
            i['state'] = 'empty-return'
        elif label == 'event_return':
            control(a.pc['post_input'])
            # Why: I_GetEvent exits directly after a keyup D_PostEvent, so only
            # the MAKE path must drain DG_GetKey to EMPTY before its return.
            assert state == ('break-return' if i['role'] == 'break' else 'empty-return')
            i['state'] = 'event-return'
        elif label == 'post_input':
            load(a.symbols['loop_interface'], 15)
            assert state == 'event-return'
            if self.make is not None and i is not self.make:
                assert i['role'] == 'break', 'next invocation did not consume BREAK'
            i['state'] = 'post-input'
            if i['role'] == 'make':
                assert self.ready_tick is None
                self.empty_tick, self.ready_tick = i['empty_tick'], r['tick']
                self.ready()  # Why: safe to set before anyone awaits, because a cocotb Event is latched even before await registration.
        elif label == 'process_call':
            control(a.symbols['D_ProcessEvents'], 1, pc + 4)
            assert state == 'post-input'
            i['state'] = 'processed'
        elif label == 'slot':
            load(a.symbols['maketic'], 11)
            assert state == 'processed'
            assert r['read_data'] == i['slot'] == self.trace.counters['maketic']
            i['state'] = 'slot'
        elif label == 'command_call':
            control(a.symbols['G_BuildTiccmd'], 1, pc + 4)
            assert state == 'slot'
            i['state'] = 'building'
        elif label == 'turn':
            store(COMPLETION, TURN)
            assert state == 'building' and i['role'] == 'make' and i['turns'] == 0
            i['turns'] += 1
        elif label == 'publish':
            store(a.symbols['maketic'], i['slot'] + 1)
            assert state == 'building' and i['turns'] == (1 if i['role'] == 'make' else 0)
            assert self.trace.counters['maketic'] == i['slot'] + 1, 'same-cycle counter dispatch'
            i['state'] = 'published'
        elif label == 'return':
            control(a.symbols['NetUpdate'] + 0x88)
            assert state == 'published' or (i['role'] is None and self.make is None), 'aborted input construction'
            i['aborted'] = state != 'published'
            self.history.append(dict(i, return_tick=r['tick']))
            self.invocation = None
            if i['role'] == 'break':
                self.completed = True

    def check_final(self):
        assert self.completed and self.make is not None
        assert self.make_ack_tick is not None and self.break_ack_tick is not None
        assert self.break_start_tick > max(self.ready_tick, self.make_ack_tick)
        assert self.break_ack_tick >= self.break_start_tick
        assert self.history[-2]['role'] == 'make' and self.history[-1]['role'] == 'break'
        assert self.history[-1]['slot'] == self.history[-2]['slot'] + 1

    def evidence(self):
        return {'abi': self.abi.evidence(), 'armed': self.armed,
                'completed': self.completed, 'history': self.history,
                'current_invocation': self.invocation, 'records': self.records,
                'arm_tick': getattr(self, 'arm_tick', None),
                'arm_counters': getattr(self, 'arm_counters', None),
                'boundary_tick': self.boundary_tick, 'make_start_tick': self.make_start_tick,
                'empty_tick': self.empty_tick, 'chain_complete_tick': self.ready_tick,
                'make_ack_tick': self.make_ack_tick, 'break_start_tick': self.break_start_tick,
                'break_ack_tick': self.break_ack_tick,
                'device_sampling_margin_claimed': False}
