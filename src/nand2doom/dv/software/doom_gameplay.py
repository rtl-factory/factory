"""Passive, symbol-correlated evidence for the pinned Doom single-player loop."""
from __future__ import annotations

from pathlib import Path
import json
import subprocess

from src.nand2doom.scripts.sw_build.n2d_image import _tool

COMPLETION = 0x20000004
MAKE = 0xD00D39AE
BREAK = 0xD00D38AE
TURN = 0xD00D4001
# Freedoom E1M1 starts at zero; initial slow right turns apply -320 << 16.
EXPECTED_ANGLES = tuple(((-320 * (1 << 16) * count) & 0xFFFFFFFF) >> 20
                        for count in (1, 2))
COUNTERS = ("gametic", "maketic", "ticdup")
FRAME_WIDTH, FRAME_HEIGHT = 320, 200


def frame_statistics(frame: bytes, width: int = FRAME_WIDTH) -> tuple[int, int, int]:
    """Distinct palette indices, non-black (nonzero index) pixels and rows
    containing at least one non-black pixel of one indexed framebuffer image."""
    assert width > 0 and len(frame) % width == 0, len(frame)
    rows = {index // width for index, value in enumerate(frame) if value}
    return len(set(frame)), sum(1 for value in frame if value), len(rows)


def parse_symbols(text: str, names: tuple[str, ...] = COUNTERS) -> dict[str, int]:
    symbols = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) != 4 or fields[3] not in names:
            continue
        address, size, kind, name = fields
        assert name not in symbols, f"duplicate Doom symbol {name}"
        address, size = int(address, 16), int(size, 16)
        assert size == 4 and kind.lower() == "b", line
        assert address % 4 == 0 and 0 <= address < 0x04000000, line
        symbols[name] = address
    assert set(symbols) == set(names), f"missing Doom counter symbols: {symbols}"
    assert len(set(symbols.values())) == len(names), symbols
    return symbols


def elf_symbols(elf: Path, names: tuple[str, ...] = COUNTERS) -> dict[str, int]:
    result = subprocess.run(
        [_tool("N2D_RISCV_NM", "riscv64-unknown-elf-nm"),
         "-S", "--defined-only", str(elf)],
        check=True, capture_output=True, text=True, timeout=30,
    )
    return parse_symbols(result.stdout, names)


def parse_call_symbols(text: str) -> dict[str, int]:
    """Unique RV32IM entry PCs and the typed upstream single-tic BSS flag."""
    symbols = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) != 4 or fields[3] not in ("G_Ticker", "D_Display", "singletics"):
            continue
        address, size, kind, name = fields
        assert name not in symbols, f"duplicate call symbol {name}"
        address, size = int(address, 16), int(size, 16)
        assert address % 4 == 0 and 0 <= address < 0x04000000, line
        if name == "singletics":
            assert size == 4 and kind.lower() == "b", line
        else:
            assert size >= 4 and kind.lower() == "t", line
        symbols[name] = address
    assert set(symbols) == {"G_Ticker", "D_Display", "singletics"}, symbols
    assert len(set(symbols.values())) == 3, symbols
    return symbols


def elf_call_symbols(elf: Path) -> dict[str, int]:
    result = subprocess.run(
        [_tool("N2D_RISCV_NM", "riscv64-unknown-elf-nm"),
         "-S", "--defined-only", str(elf)],
        check=True, capture_output=True, text=True, timeout=30,
    )
    return parse_call_symbols(result.stdout)


class GameplayTrace:
    """Observe real retirements; never infer initial counter values as zero.

    BuildNewTic polls release before constructing command maketic. With no next
    stimulus yet, that slot is neutral. gametic > that snapshot, with ticdup=1,
    proves all earlier slots and the post-break neutral slot were applied.
    """

    def __init__(self, symbols: dict[str, int]):
        self.symbols = {address: name for name, address in symbols.items()}
        self.counters: dict[str, int] = {}
        self.words = {name: 0 for name in COUNTERS}
        self.known = {name: 0 for name in COUNTERS}
        self.counter_events: list[dict[str, int | str]] = []
        self.last_order = -1
        self.last_store: dict[str, int] | None = None
        self.failure: str | None = None
        self.events: dict[str, list[dict[str, int]]] = {
            key: [] for key in ("make", "break", "turn", "angle")
        }
        self.angles: list[int] = []
        self.frames: list[dict[str, int]] = []
        self.phase = -1
        self.phase_start = 0
        self.barriers: list[int | None] = []

    def begin_phase(self, phase: int, tick: int) -> None:
        assert phase == self.phase + 1 and phase in (0, 1)
        assert set(self.counters) == set(COUNTERS), "unobserved initial counters"
        assert self.counters["ticdup"] == 1, "Doom ticdup must be one"
        if phase:
            assert self.barriers[0] is not None, "physical commands not drained"
            assert self.settled_frame(0) is not None, "physical frame not settled"
            assert self.angles == list(EXPECTED_ANGLES[:1])
        self.phase, self.phase_start = phase, tick
        self.barriers.append(None)

    def retirement(self, order: int, tick: int, address: int, data: int, mask: int,
                   *, valid: bool = True, memory_valid: bool = True,
                   write: bool = True, trap: bool = False) -> None:
        if not (valid and memory_valid and write and not trap):
            return
        self.last_store = {"order": order, "tick": tick, "address": address,
                           "data": data, "mask": mask}
        assert order > self.last_order, f"duplicate/out-of-order store {order}"
        self.last_order = order
        base = address & ~3
        if base in self.symbols:
            name = self.symbols[base]
            assert 0 < mask <= 15
            if self.known[name] == 15:
                assert address == base and mask == 15, f"partial post-init {name} store"
            # Architectural data/mask already occupy their addressed lanes.
            for lane in range(4):
                if mask & (1 << lane):
                    byte_mask = 0xFF << (8 * lane)
                    self.words[name] = ((self.words[name] & ~byte_mask)
                                        | (data & byte_mask))
            self.known[name] |= mask
            if self.known[name] != 15:
                return
            value = self.words[name]
            assert value < 0x80000000, f"negative/wrapped {name}: {value}"
            if name not in self.counters:
                assert value == 0, f"missed BSS initialization for {name}"
            else:
                delta = value - self.counters[name]
                assert delta in (0, 1), f"{name} reset/decreased/jumped"
            if name == "ticdup":
                assert value in (0, 1), f"Doom ticdup must be one, observed {value}"
            self.counters[name] = value
            if "gametic" in self.counters and "maketic" in self.counters:
                assert self.counters["gametic"] <= self.counters["maketic"]
            self.counter_events.append({"order": order, "tick": tick,
                                        "name": name, "value": value})
            self._advance_barrier(tick)
            return
        if address != COMPLETION or self.phase < 0:
            return
        assert mask == 0xF, f"partial completion store mask={mask:x}"
        kind = {MAKE: "make", BREAK: "break", TURN: "turn"}.get(data)
        if data & 0xFFFFF000 == 0xD00D5000:
            kind = "angle"
        if kind is None:
            return
        event = {"order": order, "tick": tick, "marker": data, "phase": self.phase}
        assert tick > self.phase_start, f"{kind} precedes phase stimulus"
        assert len(self.events[kind]) == self.phase, f"extra or out-of-phase {kind}"
        if kind != "make":
            assert len(self.events["make"]) == self.phase + 1, "missing phase make"
            assert tick > self.events["make"][-1]["tick"]
        if kind in ("break", "angle"):
            assert len(self.events["turn"]) == self.phase + 1, "missing phase turn"
            assert tick > self.events["turn"][-1]["tick"]
        if kind == "break":
            assert set(self.counters) == set(COUNTERS), "unobserved initial counters"
            assert self.counters["gametic"] <= self.counters["maketic"]
            event["neutral_slot"] = self.counters["maketic"]
        elif kind == "angle":
            self.angles.append(data & 0xFFF)
            assert self.angles == list(EXPECTED_ANGLES[:self.phase + 1]), self.angles
        self.events[kind].append(event)

    def _advance_barrier(self, tick: int) -> None:
        if self.phase < 0 or len(self.events["break"]) <= self.phase:
            return
        slot = self.events["break"][self.phase]["neutral_slot"]
        if (self.counters.get("ticdup") == 1
                and self.counters.get("gametic", -1) > slot
                and self.counters.get("maketic", -1) > slot
                and self.barriers[self.phase] is None):
            self.barriers[self.phase] = tick

    def frame(self, start: int, end: int, crc: int) -> None:
        assert start < end
        self.frames.append({"start": start, "end": end, "crc": crc})

    def settled_frame(self, phase: int) -> dict[str, int] | None:
        if (len(self.events["angle"]) <= phase
                or len(self.events["break"]) <= phase
                or self.barriers[phase] is None):
            return None
        threshold = max(self.events["angle"][phase]["tick"],
                        self.events["break"][phase]["tick"], self.barriers[phase])
        return next((frame for frame in self.frames if frame["start"] > threshold), None)

    def check_final(self) -> None:
        assert self.phase == 1 and self.settled_frame(1) is not None
        assert self.angles == list(EXPECTED_ANGLES)
        assert all(len(events) == 2 for events in self.events.values())

    def evidence(self) -> dict[str, object]:
        result = {"symbols": self.symbols, "counters": self.counters,
                "known_bytes": self.known, "last_store": self.last_store,
                "failure": self.failure,
                "counter_events": self.counter_events,
                "events": self.events, "angles": self.angles,
                "barriers": self.barriers, "frames": self.frames}
        if hasattr(self, "input_witness"):
            result["uart_input_witness"] = self.input_witness.evidence()
        return result

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.evidence(), indent=2) + "\n")
