"""Independent synthetic public-retirement vectors; no runtime timing claim."""
from types import SimpleNamespace

from .doom_gameplay import GameplayTrace, MAKE, BREAK, TURN, COMPLETION

# Why: raw ABI fixtures are written independently of the ledger's state machine
# and SEAMS table, so a shared mistake cannot pass both.
OFFSETS = {'entry': 0, 'input_call': 48, 'post_input': 52, 'process_call': 60,
           'slot': 176, 'command_call': 188, 'publish': 256, 'return': 288}
KEY_OFFSETS = {'status': 8, 'zero': 20, 'empty_return': 24, 'data': 32,
               'key_marker': 128, 'key_return': 132}


def abi_fixture():
    pc = {n: 0x2544 + v for n, v in OFFSETS.items()}
    pc.update({n: 0x2d7a4 + v for n, v in KEY_OFFSETS.items()})
    pc.update(event_return=0x2d078, start_tail=0x2d25c, key_call=0x2d018, turn=0x2d868)
    words = {'entry': 0xfd010113, 'input_call': 0xce8080e7, 'post_input': 0xa884a783,
             'process_call': 0x780e7, 'slot': 0xaa442583, 'command_call': 0x780e7,
             'publish': 0xaa942223, 'return': 0x8067, 'status': 0x7a783,
             'zero': 0x513, 'empty_return': 0x8067, 'data': 0x7a783,
             'key_marker': 0xf72223, 'key_return': 0x8067, 'event_return': 0x8067,
             'start_tail': 0xd9030067, 'key_call': 0x790080e7, 'turn': 0xf72223}
    symbols = {'BuildNewTic': 0x2544, 'I_StartTic': 0x2d258, 'I_GetEvent': 0x2cfe8,
               'DG_GetKey': 0x2d7a4, 'NetUpdate': 0x2668, 'D_ProcessEvents': 0x2bc0,
               'G_BuildTiccmd': 0x6004, 'loop_interface': 0x5ca88,
               'gametic': 0x5ca9c, 'maketic': 0x5caa4, 'ticdup': 0x5ca90}
    a = SimpleNamespace(pc=pc, instructions={pc[n]: words[n] for n in pc},
                        symbols=symbols, key_return_target=0x2d01c)
    a.evidence = lambda: {'synthetic_ABI': True}
    return a


def initialized_trace(a, slot=7):
    t = GameplayTrace({n: a.symbols[n] for n in ('gametic', 'maketic', 'ticdup')})
    def store(address, value, mask=15):
        t.retirement(t.last_order + 1, t.last_order + 2, address, value, mask)
    for name in ('gametic', 'maketic', 'ticdup'):
        for lane in range(4):
            store(a.symbols[name] + lane, 0xffffffff ^ (0xff << (8 * lane)), 1 << lane)
    store(a.symbols['ticdup'], 1)
    for value in range(1, slot):
        store(a.symbols['maketic'], value)
    t.begin_phase(0, t.last_order + 1)
    for marker in (MAKE, TURN, BREAK, 0xd00d5fec):
        store(COMPLETION, marker)
    store(a.symbols['maketic'], slot)
    for value in range(1, slot + 1):
        store(a.symbols['gametic'], value)
    t.frame(t.last_order + 3, t.last_order + 4, 123)
    t.begin_phase(1, t.last_order + 5)
    return t


def rows(a, slot=7, neutral_before=0):
    result = []
    def emit(label, **changes):
        pc = a.pc[label]
        r = dict(valid=True, trap=False, pc=pc, instruction=a.instructions[pc],
                 next_pc=pc + 4, memory_valid=False, memory_write=False,
                 address=0, read_mask=0, write_mask=0, read_data=0,
                 write_data=0, rd_write=False, rd=0, rd_data=0, seam=label)
        r.update(changes)
        result.append(r)
    def call(label, target):
        emit(label, next_pc=target, rd_write=True, rd=1, rd_data=a.pc[label] + 4)
    def load(label, address, value, rd=15):
        emit(label, memory_valid=True, address=address, read_mask=15,
             read_data=value, rd_write=True, rd=rd, rd_data=value)
    def store(label, address, value):
        emit(label, memory_valid=True, memory_write=True, address=address,
             write_mask=15, write_data=value)
    for kind in [None] * neutral_before + ['make', 'break']:
        emit('entry', rd_write=True, rd=2, rd_data=0x1000)
        call('input_call', a.symbols['I_StartTic'])
        emit('start_tail', next_pc=a.symbols['I_GetEvent'])
        call('key_call', a.symbols['DG_GetKey'])
        if kind:
            load('status', 0x20002004, 0x1009)
            load('data', 0x20002000, 0x80000274 if kind == 'make' else 0x80000374)
            store('key_marker', COMPLETION, MAKE if kind == 'make' else BREAK)
            emit('key_return', next_pc=a.key_return_target)
        if kind != 'break':
            if kind:
                call('key_call', a.symbols['DG_GetKey'])
            load('status', 0x20002004, 0x1002)
            emit('zero', rd_write=True, rd=10, rd_data=0)
            emit('empty_return', next_pc=a.key_return_target)
        # Why: an actual BREAK keyup exits I_GetEvent directly, so this vector
        # has no EMPTY/zero steps.
        emit('event_return', next_pc=a.pc['post_input'])
        load('post_input', a.symbols['loop_interface'], 0x7000)
        call('process_call', a.symbols['D_ProcessEvents'])
        load('slot', a.symbols['maketic'], slot, 11)
        call('command_call', a.symbols['G_BuildTiccmd'])
        if kind == 'make':
            store('turn', COMPLETION, TURN)
        store('publish', a.symbols['maketic'], slot + 1)
        emit('return', next_pc=a.symbols['NetUpdate'] + 0x88)
        slot += 1
    return result
