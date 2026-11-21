from __future__ import annotations

from dataclasses import dataclass

LINE_BYTES = 16
SET_COUNT = 512


def read_word(memory: bytearray, address: int) -> int:
    base = address & ~3
    return sum(memory[base + lane] << (8 * lane) for lane in range(4))


def read_line(memory: bytearray, address: int) -> int:
    base = address & ~(LINE_BYTES - 1)
    return sum(memory[base + lane] << (8 * lane) for lane in range(LINE_BYTES))


def write_line(memory: bytearray, address: int, value: int) -> None:
    base = address & ~(LINE_BYTES - 1)
    for lane in range(LINE_BYTES):
        memory[base + lane] = (value >> (8 * lane)) & 0xFF


@dataclass
class Entry:
    valid: bool = False
    dirty: bool = False
    tag: int = 0
    data: int = 0


class CacheModel:
    """Obvious transaction-level oracle for one 512-set cache of 2 or 4 ways.

    Replacement is invalid-first (lowest way), otherwise tree pseudo-LRU:
    per set, heap node n (root 1, children 2n and 2n+1) holds one bit naming
    the subtree of the next victim; a hit or install points every node on
    its way's path at the other subtree. Two ways reduce to one victim bit.
    """

    def __init__(self, memory: bytearray, *, writable: bool, ways: int = 2) -> None:
        assert ways in (2, 4)
        self.memory = memory
        self.writable = writable
        self.ways = ways
        self.levels = ways.bit_length() - 1
        self.entries = [[Entry() for _ in range(ways)] for _ in range(SET_COUNT)]
        self.plru = [[0] * ways for _ in range(SET_COUNT)]
        self.hits = 0
        self.misses = 0
        self.line_transactions: list[tuple[bool, int, int]] = []

    @staticmethod
    def _fields(address: int) -> tuple[int, int, int]:
        return (address >> 4) & 0x1FF, address >> 13, (address >> 2) & 3

    def access(
        self,
        address: int,
        *,
        write: bool = False,
        write_data: int = 0,
        byte_enable: int = 0,
    ) -> int:
        assert 0 <= address < len(self.memory)
        assert self.writable or not write
        set_index, tag, word = self._fields(address)
        ways = self.entries[set_index]
        hit = next((way for way, entry in enumerate(ways) if entry.valid and entry.tag == tag), None)
        if hit is None:
            self.misses += 1
            hit = next((way for way, entry in enumerate(ways) if not entry.valid),
                       self.plru_victim(set_index))
            victim = ways[hit]
            if victim.valid and victim.dirty:
                victim_address = ((victim.tag << 9) | set_index) << 4
                self.line_transactions.append((True, victim_address, victim.data))
                write_line(self.memory, victim_address, victim.data)
            line_address = address & ~0xF
            self.line_transactions.append((False, line_address, 0))
            ways[hit] = Entry(True, False, tag, read_line(self.memory, line_address))
        else:
            self.hits += 1
        self._touch(set_index, hit)
        entry = ways[hit]
        shift = word * 32
        result = (entry.data >> shift) & 0xFFFF_FFFF
        if write:
            for lane in range(4):
                if byte_enable & (1 << lane):
                    byte_shift = shift + lane * 8
                    entry.data = (entry.data & ~(0xFF << byte_shift)) | (
                        ((write_data >> (lane * 8)) & 0xFF) << byte_shift
                    )
            entry.dirty = True
            return 0
        return result

    def plru_victim(self, set_index: int) -> int:
        bits, node, way = self.plru[set_index], 1, 0
        for _ in range(self.levels):
            way = (way << 1) | bits[node]
            node = 2 * node + bits[node]
        return way

    def _touch(self, set_index: int, way: int) -> None:
        bits, node = self.plru[set_index], 1
        for level in range(self.levels - 1, -1, -1):
            branch = (way >> level) & 1
            bits[node] = 1 - branch
            node = 2 * node + branch

    def maintain(self) -> None:
        for set_index, ways in enumerate(self.entries):
            for entry in ways:
                if entry.valid and entry.dirty:
                    address = ((entry.tag << 9) | set_index) << 4
                    self.line_transactions.append((True, address, entry.data))
                    write_line(self.memory, address, entry.data)
                entry.valid = False
                entry.dirty = False
        # Why: replacement state can be kept because invalid-first refills of a
        # set rewrite every node before the tree is consulted again.
