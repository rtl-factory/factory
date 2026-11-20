"""Readiness check: passes only on the pinned Quartus with both families."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check  # noqa: E402

PIN = json.loads((check.DEFAULT_CONFIG).read_text())["quartus"]
VERSION = "Quartus Prime Shell\nVersion 25.1std.0 Build 1129 10/21/2025 SC Lite Edition\n"
FAMILIES = "{Cyclone IV E} {Cyclone IV GX} {MAX 10}\n"


def fake(version=VERSION, families=FAMILIES):
    def runner(command):
        return (0, version) if command[-1] == "--version" else (0, families)
    return runner


def which(_name):
    return "/opt/quartus/bin/quartus_sh"


def test_pass_on_pinned_version_and_families():
    assert check.check_quartus(PIN, runner=fake(), which=which)["status"] == "PASS"


def test_fail_on_other_version():
    r = check.check_quartus(PIN, runner=fake(version="Version 24.1std.0 Build 1077\n"), which=which)
    assert r["status"] == "FAIL" and "24.1std.0 Build 1077" in r["found"]


def test_fail_without_cyclone_iv():
    r = check.check_quartus(PIN, runner=fake(families="{MAX 10}\n"), which=which)
    assert r["status"] == "FAIL" and "Cyclone IV E" in r["found"]


def test_fail_when_missing(monkeypatch, tmp_path):
    monkeypatch.delenv("QUARTUS_SH", raising=False)
    pin = dict(PIN, install_dir=str(tmp_path))
    r = check.check_quartus(pin, runner=fake(), which=lambda _n: None)
    assert r["status"] == "FAIL" and r["fix"]
