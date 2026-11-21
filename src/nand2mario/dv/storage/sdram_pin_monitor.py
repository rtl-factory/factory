"""Independent Python monitor of the SDRAM pins (storage HAS), written from the
device contract and the IS42S16320D command set, never from the controller or
the SystemVerilog device model.

It decodes the command presented in every controller clock, tracks the open
row of each bank, records the clock of every command class, derives refresh
gaps and the initialization sequence, and checks the invariants that are
cheap to state here: a legal mode register, no column command without an
open row, no refresh with an open row, DQ driven only with a WRITE. Timing
minimums belong to the device model; this monitor owns the numbers a test
pins (command clocks, gaps, counts).
"""
NOP, ACTIVATE, READ, WRITE, PRECHARGE, REFRESH, MRS = (
    (1, 1, 1), (0, 1, 1), (1, 0, 1), (1, 0, 0), (0, 1, 0), (0, 0, 1), (0, 0, 0))
NAMES = {NOP: 'NOP', ACTIVATE: 'ACTIVATE', READ: 'READ', WRITE: 'WRITE',
         PRECHARGE: 'PRECHARGE', REFRESH: 'REFRESH', MRS: 'MRS'}
MODE_BL1_SEQ_CL2 = 0x020


def split(address):
    """Device byte address -> (bank, row, column, lane) per the HAS mapping."""
    return (address >> 24) & 3, (address >> 11) & 0x1FFF, (address >> 1) & 0x3FF, address & 1


def join(bank, row, column, lane=0):
    return (bank << 24) | (row << 11) | (column << 1) | lane


class SdramPinMonitor:
    def __init__(self):
        self.open_row = {}
        self.mode_set = False
        self.commands = []
        self.refresh_clocks = []
        self.activates = self.reads = self.writes = self.precharges = 0
        self.first_command = None
        self.last_clock = None

    def edge(self, clock, cke, cs_n, ras_n, cas_n, we_n, bank, address, dq_oe):
        """One controller clock: the command the controller presented."""
        self.last_clock = clock
        if not cke or cs_n:
            assert not dq_oe, f'clock {clock}: DQ driven while deselected'
            return NOP
        command = (ras_n, cas_n, we_n)
        assert command in NAMES, f'clock {clock}: illegal command {command}'
        assert dq_oe == (command == WRITE), f'clock {clock}: DQ drive {dq_oe} with {NAMES[command]}'
        if command == NOP:
            return command
        if self.first_command is None:
            self.first_command = clock
        self.commands.append((clock, NAMES[command], bank, address))
        if command == MRS:
            assert address == MODE_BL1_SEQ_CL2 and bank == 0, f'clock {clock}: mode register {address:#05x}'
            assert not self.open_row, 'mode register set with an open row'
            self.mode_set = True
        elif command == ACTIVATE:
            assert bank not in self.open_row, f'clock {clock}: ACTIVATE to open bank {bank}'
            self.open_row[bank] = address
            self.activates += 1
        elif command in (READ, WRITE):
            assert self.mode_set, 'column command before the mode register set'
            assert bank in self.open_row, f'clock {clock}: column command on closed bank {bank}'
            assert not (address >> 10) & 1, 'auto-precharge is not part of the contract'
            if command == READ:
                self.reads += 1
            else:
                self.writes += 1
        elif command == PRECHARGE:
            if (address >> 10) & 1:
                self.open_row.clear()
            else:
                self.open_row.pop(bank, None)
            self.precharges += 1
        elif command == REFRESH:
            assert not self.open_row, f'clock {clock}: REFRESH with open rows {self.open_row}'
            self.refresh_clocks.append(clock)
        return command

    def refresh_gaps(self):
        return [right - left for left, right in zip(self.refresh_clocks, self.refresh_clocks[1:])]

    def init_sequence(self):
        """Command names up to and including the mode register set."""
        names = []
        for _, name, _, _ in self.commands:
            names.append(name)
            if name == 'MRS':
                break
        return names
