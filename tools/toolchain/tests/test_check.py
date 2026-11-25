"""Readiness check: passes only on the pinned Verilator, Bazelisk, OSS CAD Suite release and Quartus with both families."""
import json
from pathlib import Path
import sys

import pytest

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


VERILATOR = json.loads((check.DEFAULT_CONFIG).read_text())["verilator"]
SUITE_DIR = "/opt/oss-cad-suite"
PINNED = f'Verilator {VERILATOR["version"]} devel rev {VERILATOR["revision"]} (mod)\n'


def verilator_runner(version=PINNED):
    return lambda _command: (0, version)


def never(_name):
    return None


def run_check(monkeypatch, *, version=PINNED, is_file=lambda _p: True, read=lambda _p: "OBJCACHE ?= \n"):
    monkeypatch.delenv("VERILATOR", raising=False)
    monkeypatch.delenv("OSS_CAD_SUITE", raising=False)
    return check.check_verilator(VERILATOR, SUITE_DIR, runner=verilator_runner(version), which=never,
                                 is_file=is_file, read=read)


def test_verilator_pass_on_pinned_version_from_the_suite(monkeypatch):
    r = run_check(monkeypatch)
    assert r["status"] == "PASS" and r["path"] == f"{SUITE_DIR}/bin/verilator"
    assert r["found"] == f'{VERILATOR["version"]} rev {VERILATOR["revision"]}'


def test_verilator_ignores_path(monkeypatch):
    monkeypatch.delenv("VERILATOR", raising=False)
    monkeypatch.delenv("OSS_CAD_SUITE", raising=False)
    found = check.find_verilator(SUITE_DIR, which=lambda _n: "/usr/bin/verilator", is_file=lambda _p: False)
    assert found is None


def test_verilator_explicit_override(monkeypatch):
    monkeypatch.setenv("VERILATOR", "/x/verilator")
    assert check.find_verilator(SUITE_DIR, which=lambda n: n, is_file=lambda _p: False) == "/x/verilator"


def test_verilator_fail_on_other_version(monkeypatch):
    r = run_check(monkeypatch, version="Verilator 5.052 2026-09-05 rev v5.052\n")
    assert r["status"] == "FAIL" and r["found"] == "5.052 rev v5.052" and "OSS CAD Suite" in r["fix"]


def test_verilator_fail_on_other_revision(monkeypatch):
    r = run_check(monkeypatch, version=f'Verilator {VERILATOR["version"]} devel rev v5.052-1-gabc\n')
    assert r["status"] == "FAIL"


def test_verilator_fail_without_coverage_tool(monkeypatch):
    r = run_check(monkeypatch, is_file=lambda p: p.endswith("/bin/verilator"))
    assert r["status"] == "FAIL" and "verilator_coverage" in r["found"]


def test_verilator_fail_on_baked_in_ccache(monkeypatch):
    r = run_check(monkeypatch, read=lambda _p: "VERILATOR_ROOT ?= x\nOBJCACHE ?= ccache\n")
    assert r["status"] == "FAIL" and "ccache" in r["found"]


def test_verilator_pass_on_empty_objcache(monkeypatch):
    r = run_check(monkeypatch, read=lambda _p: "OBJCACHE ?= \nCXX ?= g++\n")
    assert r["status"] == "PASS"


def test_verilator_fail_when_missing(monkeypatch):
    r = run_check(monkeypatch, is_file=lambda _p: False)
    assert r["status"] == "FAIL" and r["fix"]


def test_verilator_fail_without_makefile(monkeypatch):
    r = run_check(monkeypatch, read=lambda _p: None)
    assert r["status"] == "FAIL" and "verilated.mk" in r["found"]


def test_verilator_fail_when_lint_smoke_test_fails(monkeypatch):
    def runner(command):
        return (0, PINNED) if "--version" in command else (1, "error while loading shared libraries")
    monkeypatch.delenv("VERILATOR", raising=False)
    monkeypatch.delenv("OSS_CAD_SUITE", raising=False)
    r = check.check_verilator(VERILATOR, SUITE_DIR, runner=runner, which=never, is_file=lambda _p: True,
                              read=lambda _p: "OBJCACHE ?= \n")
    assert r["status"] == "FAIL" and "smoke" in r["found"]


def fake_suite(root):
    """A stand-in suite whose verilator prints the pinned version and lints anything."""
    (root / "bin").mkdir(parents=True)
    (root / "share" / "verilator" / "include").mkdir(parents=True)
    (root / "share" / "verilator" / "include" / "verilated.mk").write_text("OBJCACHE ?= \n")
    for tool, body in [("verilator", f"echo '{PINNED.strip()}'"), ("verilator_coverage", "true"),
                       ("verilator_bin", "true")]:
        (root / "bin" / tool).write_text(f"#!/bin/sh\n{body}\n")
        (root / "bin" / tool).chmod(0o755)
    (root / "bin" / "python3").write_text("#!/bin/sh\n")


def test_pinned_verilator_dir_links_only_verilator_tools(monkeypatch, tmp_path):
    fake_suite(tmp_path / "suite")
    monkeypatch.delenv("VERILATOR", raising=False)
    monkeypatch.setenv("OSS_CAD_SUITE", str(tmp_path / "suite"))
    folder = check.pinned_verilator_dir(links=tmp_path / "links")
    assert folder == tmp_path / "links" / VERILATOR["revision"]
    assert sorted(p.name for p in folder.iterdir()) == ["verilator", "verilator_bin", "verilator_coverage"]
    assert (folder / "verilator").resolve() == (tmp_path / "suite" / "bin" / "verilator").resolve()
    assert check.pinned_verilator_dir(links=tmp_path / "links") == folder


def test_pinned_verilator_dir_refuses_a_missing_suite(monkeypatch, tmp_path):
    monkeypatch.delenv("VERILATOR", raising=False)
    monkeypatch.setenv("OSS_CAD_SUITE", str(tmp_path / "nowhere"))
    with pytest.raises(SystemExit, match="pinned Verilator not ready"):
        check.pinned_verilator_dir(links=tmp_path / "links")


BAZELISK = json.loads((check.DEFAULT_CONFIG).read_text())["bazelisk"]


def pinned_bazelisk(tmp_path):
    (tmp_path / "bazelisk").write_text("")
    (tmp_path / "bazel").symlink_to(tmp_path / "bazelisk")
    pin = dict(BAZELISK, install_dir=str(tmp_path))
    return pin, lambda name: str(tmp_path / "bazel") if name.endswith("bazel") else None


def test_bazel_pass_on_pinned_bazelisk(tmp_path):
    pin, which = pinned_bazelisk(tmp_path)
    assert check.check_bazel(pin, which=which, digest=lambda _p: BAZELISK["sha256"])["status"] == "PASS"


def test_bazel_fail_on_changed_bazelisk(tmp_path):
    pin, which = pinned_bazelisk(tmp_path)
    r = check.check_bazel(pin, which=which, digest=lambda _p: "0" * 64)
    assert r["status"] == "FAIL" and "SHA-256" in r["found"]


def test_bazel_warn_on_other_bazel(tmp_path):
    r = check.check_bazel(dict(BAZELISK, install_dir=str(tmp_path)), which=lambda _n: "/usr/local/bin/bazel")
    assert r["status"] == "WARN" and r["path"] == "/usr/local/bin/bazel"


def test_bazel_fail_when_missing(tmp_path):
    r = check.check_bazel(dict(BAZELISK, install_dir=str(tmp_path)), which=lambda _n: None)
    assert r["status"] == "FAIL" and r["fix"]
