"""Targets as read-only objects, the shape these tests were written against."""
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "tools/sim"))
import sim  # noqa: E402

DEFAULTS = {"kind": "cocotb", "parameters": {}, "defines": [], "test_module": None,
            "testcase": None, "default_seed": None, "max_cycles": None}


def frozen(value):
    if isinstance(value, dict):
        return tuple(sorted(value.items()))
    return tuple(value) if isinstance(value, list) else value


def load_targets():
    return {name: SimpleNamespace(**{k: frozen(v) for k, v in {**DEFAULTS, **target}.items()}, name=name)
            for name, target in sim.load_targets(ROOT).items()}
