"""Formal runner: target discovery, header parsing and selection."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fv  # noqa: E402

SBY = """# A formal target
# level: L0
# timeout: 120
# library: t_fv
[tasks]
prove
cover

[options]
prove: mode prove
"""


def make(root: Path, project: str, block: str, name: str, text: str = SBY) -> Path:
    path = root / "src" / project / "fv" / block / f"{name}.sby"
    path.parent.mkdir(parents=True)
    path.write_text(text)
    # Why: every target proves the one library t_fv; it is declared once per tree.
    if not list(root.glob("src/*/cfg/libraries.json")):
        libraries = root / "src" / project / "cfg" / "libraries.json"
        libraries.parent.mkdir(parents=True)
        libraries.write_text('{"libraries": {"t_fv": {"srcs": ["t_fv.sv"], "deps": ["m"]}, "m": {"headers": ["m.vh"]}}}')
    return path


def test_header_and_tasks(tmp_path):
    target = fv.parse_target(make(tmp_path, "p", "b", "t"))
    assert target["level"] == "L0" and target["timeout_seconds"] == 120
    assert target["tasks"] == ["prove", "cover"]


def test_default_timeout_is_ten_minutes(tmp_path):
    target = fv.parse_target(make(tmp_path, "p", "b", "t", "# level: L1\n# library: t_fv\n[options]\nmode prove\n"))
    assert target["timeout_seconds"] == 600 and target["tasks"] == [""]


def test_timeout_over_ten_minutes_is_an_error(tmp_path):
    with pytest.raises(ValueError):
        fv.parse_target(make(tmp_path, "p", "b", "t", "# level: L0\n# timeout: 7200\n# library: t_fv\n"))


def test_missing_level_is_an_error(tmp_path):
    with pytest.raises(ValueError):
        fv.parse_target(make(tmp_path, "p", "b", "t", "[options]\nmode prove\n"))


def test_discovery_and_selection(tmp_path):
    make(tmp_path, "alpha", "memory", "a_l0")
    make(tmp_path, "beta", "io", "b_l2", SBY.replace("L0", "L2"))
    make(tmp_path, "beta", "core", "b_manual", SBY.replace("L0", "manual"))
    targets = fv.load_targets(tmp_path)
    assert targets["a_l0"]["project"] == "alpha"
    assert fv.select(targets, [], None, "L0", False) == ["a_l0"]
    assert sorted(fv.select(targets, [], None, None, True)) == ["a_l0", "b_l2"]
    assert fv.select(targets, [], "beta", None, False) == ["b_l2"]
    assert fv.select(targets, ["b_manual"], None, None, False) == ["b_manual"]
    with pytest.raises(SystemExit):
        fv.select(targets, ["nope"], None, None, False)


def test_repository_targets_parse():
    targets = fv.load_targets()
    assert {"n2d_memory_arbiter", "n2d_ps2_keyboard"} <= set(targets)
    assert all(t["timeout_seconds"] <= 600 for t in targets.values())


def test_missing_library_is_an_error(tmp_path):
    with pytest.raises(ValueError, match="library"):
        fv.parse_target(make(tmp_path, "p", "b", "t", "# level: L0\n[options]\nmode prove\n"))


def test_render_fills_the_script_and_the_files_from_the_library(tmp_path):
    path = make(tmp_path, "p", "b", "t", SBY + "[script]\nread_slang -I . {sources} --top t_fv\n")
    target = fv.load_targets(tmp_path)["t"]
    text = fv.render(target, tmp_path)
    assert "read_slang -I . t_fv.sv --top t_fv" in text
    assert text.endswith(f"[files]\nm.vh {tmp_path / 'm.vh'}\nt_fv.sv {tmp_path / 't_fv.sv'}\n")
    path.write_text(SBY + "[script]\nread_slang t_fv.sv\n")
    with pytest.raises(ValueError, match="sources"):
        fv.render(fv.load_targets(tmp_path)["t"], tmp_path)
