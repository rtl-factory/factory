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


SUITE = json.loads((check.DEFAULT_CONFIG).read_text())["oss_cad_suite"]


def suite_runner(version=f'Yosys {SUITE["yosys"]} (git sha1 63eecf3dd, Release)\n', plugin_ok=True):
    def runner(command):
        if command[-1] == "-V":
            return 0, version
        return (0, "") if plugin_ok else (1, "ERROR: Can't load module `slang'")
    return runner


def stamp(value=SUITE["version"].replace("-", "")):
    return lambda _path: value


def test_suite_pass_on_pinned_release():
    r = check.check_oss_cad_suite(SUITE, runner=suite_runner(), is_file=lambda _p: True, read=stamp())
    assert r["status"] == "PASS"


def test_suite_fail_when_missing():
    r = check.check_oss_cad_suite(SUITE, runner=suite_runner(), is_file=lambda _p: False, read=stamp(None))
    assert r["status"] == "FAIL" and "not installed" in r["found"] and r["fix"]


def test_suite_fail_on_other_release():
    r = check.check_oss_cad_suite(SUITE, runner=suite_runner(), is_file=lambda _p: True, read=stamp("20250101"))
    assert r["status"] == "FAIL" and "20250101" in r["found"]


def test_suite_fail_without_sby():
    r = check.check_oss_cad_suite(SUITE, runner=suite_runner(), is_file=lambda p: not p.endswith("/sby"),
                                  read=stamp())
    assert r["status"] == "FAIL" and "sby" in r["found"]


def test_suite_fail_without_slang():
    r = check.check_oss_cad_suite(SUITE, runner=suite_runner(plugin_ok=False), is_file=lambda _p: True,
                                  read=stamp())
    assert r["status"] == "FAIL" and "slang" in r["found"]


def test_tool_selection():
    config = json.loads((check.DEFAULT_CONFIG).read_text())
    results = check.check(config, runner=suite_runner(), which=lambda _n: None, tools=("quartus",))
    assert [r["tool"] for r in results] == ["quartus"]
