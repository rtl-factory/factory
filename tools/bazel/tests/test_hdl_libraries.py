"""Tests of the HDL library resolver (wiki/tools/bazel/spec.md, Libraries)."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hdl_libraries as h  # noqa: E402

LIBS = {
    "pkg": {"srcs": ["pkg.sv"]},
    "macros": {"headers": ["macros.vh"], "deps": ["asserts"]},
    "asserts": {"headers": ["asserts.vh"], "deps": ["macros"]},
    "ram": {"variants": {"default": {"srcs": ["ram_portable.sv"], "deps": ["pkg"]},
                         "intel": {"srcs": ["ram_intel.sv"]}}},
    "core": {"srcs": ["core.sv"], "deps": ["macros", "pkg", "ram"]},
    "tb": {"srcs": ["tb.sv"], "deps": ["core", {"if": ["FAST"], "deps": ["fast"]},
                                       {"unless": ["FAST"], "deps": ["slow"]}]},
    "fast": {"srcs": ["fast.sv"]},
    "slow": {"srcs": ["slow.sv"]},
}


def test_every_library_comes_after_the_ones_it_needs():
    sources, headers = h.resolve(LIBS, ["core"])
    assert sources == ["pkg.sv", "ram_portable.sv", "core.sv"]
    assert sorted(headers) == ["asserts.vh", "macros.vh"]


def test_a_flavor_picks_the_variant_and_its_own_needs():
    assert h.resolve(LIBS, ["core"], flavors=["intel"])[0] == ["pkg.sv", "ram_intel.sv", "core.sv"]


def test_defines_pick_the_conditional_needs():
    assert "fast.sv" in h.resolve(LIBS, ["tb"], defines=["FAST=1"])[0]
    assert "slow.sv" in h.resolve(LIBS, ["tb"])[0]
    assert "fast.sv" not in h.resolve(LIBS, ["tb"])[0]


def test_a_target_names_its_top_by_default_and_never_a_source_list():
    assert h.resolve_target({"top": "core"}, LIBS)["sources"][-1] == "core.sv"
    with pytest.raises(ValueError, match="name libraries"):
        h.resolve_target({"top": "core", "sources": ["core.sv"]}, LIBS)


def test_an_unknown_library_or_a_cycle_of_modules_fails():
    with pytest.raises(ValueError, match="unknown library nope"):
        h.resolve({"a": {"srcs": ["a.sv"], "deps": ["nope"]}}, ["a"])
    with pytest.raises(ValueError, match="cycle"):
        h.resolve({"a": {"srcs": ["a.sv"], "deps": ["b"]}, "b": {"srcs": ["b.sv"], "deps": ["a"]}}, ["a"])


def test_derive_finds_instances_packages_includes_variants_and_ifdefs(tmp_path):
    rtl = tmp_path / "src" / "p" / "rtl"
    dv = tmp_path / "src" / "p" / "dv"
    rtl.mkdir(parents=True)
    dv.mkdir(parents=True)
    (rtl / "p_pkg.sv").write_text("package p_pkg;\n    typedef logic t_bit;\nendpackage\n")
    (rtl / "p_ram.sv").write_text("module p_ram;\nendmodule\n")
    (rtl / "p_ram_intel.sv").write_text("module p_ram;\n    altsyncram u_ram (.clock0());\nendmodule\n")
    (rtl / "p_top.sv").write_text('`include "src/p/rtl/m.vh"\nmodule p_top;\n    import p_pkg::*;\n'
                                  "    p_ram u_ram ();\nendmodule\n")
    (rtl / "m.vh").write_text("`ifndef M_VH\n`define M_VH\n`endif\n")
    (dv / "altsyncram.sv").write_text("module altsyncram (input clock0);\nendmodule\n")
    (dv / "p_tb.sv").write_text("module p_tb;\n`ifdef BIG\n    p_top u_top ();\n`endif\nendmodule\n")
    libs = h.derive(tmp_path)["p"]
    assert libs["p_top"]["deps"] == ["m", "p_pkg", "p_ram"]
    assert set(libs["p_ram"]["variants"]) == {"default", "intel"}
    design_code_never_needs_a_dv_stub = "deps" not in libs["p_ram"]["variants"]["intel"]
    assert design_code_never_needs_a_dv_stub
    assert libs["p_tb"]["deps"] == [{"if": ["BIG"], "deps": ["p_top"]}]


def _tree(tmp_path, files):
    for name, text in files.items():
        path = tmp_path / "src" / "p" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return tmp_path


def test_instances_split_across_lines_are_found(tmp_path):
    root = _tree(tmp_path, {
        "rtl/a.sv": "module a;\nendmodule\n",
        "rtl/b.sv": "module b #(parameter W = 1);\nendmodule\n",
        "rtl/c.sv": "module c;\nendmodule\n",
        "rtl/top.sv": "module top;\n    a\n        u_a ();\n    b\n        #(.W(2))\n        u_b ();\n"
                      "    g: if (1) begin c u_c (); end\nendmodule\n"})
    assert h.derive(root)["p"]["top"]["deps"] == ["a", "b", "c"]


def test_elsif_and_else_branches_exclude_the_earlier_ones():
    refs = dict(h._references("`ifdef A\nfoo u ();\n`elsif B\nbar u ();\n`else\nbaz u ();\n`endif\n"))
    assert refs["foo"] == (("if", "A"),)
    assert refs["bar"] == (("unless", "A"), ("if", "B"))
    assert refs["baz"] == (("unless", "A"), ("unless", "B"))


def test_an_include_guard_of_any_name_is_not_a_condition():
    assert h._references("`ifndef FOO_SVH\n`define FOO_SVH\nfoo u ();\n`endif\n") == [("foo", ())]


def test_a_variant_is_named_by_the_module_its_file_is_named_after(tmp_path):
    root = _tree(tmp_path, {
        "rtl/ram_portable.sv": "module ram;\nendmodule\nmodule ram_enabled;\nendmodule\n",
        "rtl/ram_intel.sv": "module ram;\nendmodule\nmodule ram_enabled;\nendmodule\n"})
    assert set(h.derive(root)["p"]["ram"]["variants"]) == {"default", "intel"}


def test_two_files_that_would_be_one_variant_fail(tmp_path):
    root = _tree(tmp_path, {"rtl/foo.sv": "module foo;\nendmodule\n", "dv/foo.sv": "module foo;\nendmodule\n"})
    with pytest.raises(ValueError, match="would both be library foo variant default"):
        h.derive(root)


def test_a_formal_target_without_a_library_fails_clearly(tmp_path):
    fv = tmp_path / "src" / "p" / "fv" / "x"
    fv.mkdir(parents=True)
    (fv / "t.sby").write_text("# level: L0\n[tasks]\nprove\n")
    (tmp_path / "src" / "p" / "cfg").mkdir()
    (tmp_path / "src" / "p" / "cfg" / "libraries.json").write_text(h.render({}))
    with pytest.raises(ValueError, match="needs one '# library"):
        h.registry(tmp_path)


def test_the_libraries_match_the_hdl():
    assert h.stale() == [], "run python3 tools/bazel/hdl_libraries.py update"
