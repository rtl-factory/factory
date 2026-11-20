"""Strict x16 SDRAM pin model shared by the n2d controller pin-level tests.

The model follows the memory MAS (wiki/src/nand2doom/rtl/memory/mas.md), not the controller RTL: mode register
burst length eight, sequential, CAS latency two; byte address bits [25:13]
select the row, [10:1] the x16 column and [12:11] XOR [14:13] the bank. Callers decode
one command per device edge and pass the controller's DQ/DQM pins.
The due table is a controller-edge abstraction: a command sampled at edge N
returns beat k before controller edge N+1+k. Board tests instead drive DQ
after the actual inverted device edge n+CL-1 plus tAC. Every command
spacing (tRCD, tRP, tRC, tRAS, tRRD, tWR, tRFC, tMRD) must meet the
IS42S16320D-7 minimum rounded up to whole 25 MHz edges (`MIN_EDGES`), a WRITE
must follow the last read beat by one idle edge, bursts must be
uninterrupted, and violations raise AssertionError with the edge number.
"""
from __future__ import annotations

NOP, ACTIVE, READ, WRITE = (1, 1, 1), (0, 1, 1), (1, 0, 1), (1, 0, 0)
PRECHARGE, REFRESH, MRS = (0, 1, 0), (0, 0, 1), (0, 0, 0)
MODE_VALUE = 0x023
BURST = 8
CLOCK_NS = 40
# IS42S16320D datasheet (ISSI Rev. B 05/12/2015, AC electrical
# characteristics), -7 grade minimums in ns. tRFC (REF to REF or ACT) is tRC;
# tWR is tDPL (input data to precharge).
TIMING_NS = {"tRCD": 15, "tRP": 15, "tRC": 60, "tRAS": 37, "tRRD": 14,
             "tWR": 14, "tRFC": 60, "tMRD": 14}
MIN_EDGES = {name: -(-ns // CLOCK_NS) for name, ns in TIMING_NS.items()}
# A write may drive DQ only after one idle edge following the last read beat.
TURNAROUND_EDGES = 2
FAR = -100


def split(address: int) -> tuple[int, int, int]:
    """Byte address to (bank, row, x16 column)."""
    row = (address >> 13) & 0x1FFF
    return ((address >> 11) ^ row) & 0x3, row, (address >> 1) & 0x3FF


def join(bank: int, row: int, column: int) -> int:
    """(bank, row, x16 column) to the byte address of that halfword."""
    return (row << 13) | (((bank ^ row) & 0x3) << 11) | (column << 1)


class SdramPinModel:
    def __init__(self) -> None:
        self.words: dict[int, int] = {}  # halfword byte address -> value
        self.rows: dict[int, int] = {}   # open bank -> row
        self.due: dict[int, int] = {}    # controller sample edge -> read beat
        self.writes: list[tuple[int, int, int]] = []
        self.refresh: list[int] = []
        self.commands: list[tuple[int, tuple[int, int, int]]] = []
        self.mode_set = False
        self.activates = 0
        self.row_hits = 0
        self.precharges = 0
        self.bursts = 0
        self.last_active = {bank: FAR for bank in range(4)}
        self.last_precharge = {bank: FAR for bank in range(4)}
        self.last_refresh = self.last_mode = self.last_write = FAR
        self.last_read_beat = FAR
        self.fresh = set()  # banks activated since their last column command
        self.write_burst: tuple[int, int, int, int] | None = None  # bank,row,column,beat
        self.read_burst_end = FAR

    def _beat_address(self, bank: int, row: int, column: int, beat: int) -> int:
        return join(bank, row, (column & ~7) | ((column + beat) & 7))

    def _write(self, now: int, address: int, data: int, dqm: int) -> None:
        old = self.words.get(address, 0)
        if not dqm & 1:
            old = (old & 0xFF00) | (data & 0x00FF)
        if not dqm & 2:
            old = (old & 0x00FF) | (data & 0xFF00)
        self.words[address] = old
        self.writes.append((now, address, old))
        self.last_write = now

    def edge(self, now: int, cmd: tuple[int, int, int], bank: int, address: int,
             dq: int = 0, dqm: int = 0, dq_oe: int | None = None) -> list[int] | None:
        """Apply one device edge. Returns the eight read beats for a READ."""
        if cmd != NOP:
            self.commands.append((now, cmd))
        if self.write_burst is not None:
            wbank, wrow, wcolumn, beat = self.write_burst
            assert cmd == NOP, f"command {cmd} interrupts a write burst at edge {now}"
            assert dq_oe in (None, 1), f"write beat without DQ drive at edge {now}"
            self._write(now, self._beat_address(wbank, wrow, wcolumn, beat), dq, dqm)
            self.write_burst = None if beat == BURST - 1 else (wbank, wrow, wcolumn, beat + 1)
            return None
        if now < self.read_burst_end:
            assert cmd == NOP, f"command {cmd} interrupts a read burst at edge {now}"
        if dq_oe and cmd != WRITE:
            raise AssertionError(f"DQ driven outside a write burst at edge {now}")
        if cmd == ACTIVE:
            assert bank not in self.rows, f"ACTIVE to open bank {bank} at edge {now}"
            assert now - self.last_precharge[bank] >= MIN_EDGES["tRP"], f"tRP violation at edge {now}"
            assert now - self.last_active[bank] >= MIN_EDGES["tRC"], f"tRC violation at edge {now}"
            assert now - max(self.last_active.values()) >= MIN_EDGES["tRRD"], \
                f"tRRD violation at edge {now}"
            assert now - self.last_refresh >= MIN_EDGES["tRFC"], f"tRFC violation at edge {now}"
            assert now - self.last_mode >= MIN_EDGES["tMRD"], f"tMRD violation at edge {now}"
            self.rows[bank] = address
            self.last_active[bank] = now
            self.activates += 1
            self.fresh.add(bank)
        elif cmd in (READ, WRITE):
            assert self.mode_set, f"column command before MRS at edge {now}"
            assert bank in self.rows, f"column command without open row at edge {now}"
            assert now - self.last_active[bank] >= MIN_EDGES["tRCD"], f"tRCD violation at edge {now}"
            assert not address & (1 << 10), f"auto-precharge used at edge {now}"
            assert address & 7 == 0, f"burst does not start a line at edge {now}"
            self.bursts += 1
            if bank in self.fresh:
                self.fresh.discard(bank)
            else:
                self.row_hits += 1
            row, column = self.rows[bank], address & 0x3FF
            if cmd == READ:
                beats = [self.words.get(self._beat_address(bank, row, column, beat), 0)
                         for beat in range(BURST)]
                for beat, value in enumerate(beats):
                    self.due[now + 1 + beat] = value
                self.read_burst_end = now + BURST
                self.last_read_beat = now + BURST
                return beats
            assert dq_oe in (None, 1), f"WRITE without DQ drive at edge {now}"
            assert now - self.last_read_beat >= TURNAROUND_EDGES, f"DQ turnaround violation at edge {now}"
            self._write(now, self._beat_address(bank, row, column, 0), dq, dqm)
            self.write_burst = (bank, row, column, 1)
        elif cmd == PRECHARGE:
            assert now - self.last_write >= MIN_EDGES["tWR"], f"tWR violation at edge {now}"
            closing = range(4) if address & (1 << 10) else (bank,)
            for target in closing:
                if target in self.rows:
                    assert now - self.last_active[target] >= MIN_EDGES["tRAS"], \
                        f"tRAS violation at edge {now}"
                    del self.rows[target]
                    self.fresh.discard(target)
                self.last_precharge[target] = now
            self.precharges += 1
        elif cmd == REFRESH:
            assert not self.rows, f"REFRESH with an open row at edge {now}"
            assert now - max(self.last_precharge.values()) >= MIN_EDGES["tRP"], \
                f"refresh tRP violation at edge {now}"
            assert now - self.last_refresh >= MIN_EDGES["tRFC"], f"tRFC violation at edge {now}"
            self.refresh.append(now)
            self.last_refresh = now
        elif cmd == MRS:
            assert not self.rows, f"MRS with an open row at edge {now}"
            assert bank == 0 and address == MODE_VALUE, \
                f"MRS {address:#x} is not burst-eight sequential CAS-two"
            assert now - self.last_refresh >= MIN_EDGES["tRFC"], f"MRS tRFC violation at edge {now}"
            self.mode_set = True
            self.last_mode = now
        else:
            assert cmd == NOP, f"unknown command {cmd} at edge {now}"
        return None

    def word(self, address: int) -> int:
        """Little-endian 32-bit word at a byte address."""
        return self.words.get(address, 0) | self.words.get(address + 2, 0) << 16
