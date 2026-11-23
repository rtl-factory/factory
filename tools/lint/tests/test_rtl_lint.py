"""RTL lint: flags the RTL coding rules a script can check, honours inline
Verilator waivers, and keeps both baselines shrink-only."""
import json
from pathlib import Path
import shutil
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import baseline  # noqa: E402
import rtl_lint  # noqa: E402

CLEAN = """\
`include "src/common/rtl/pkg/macros.vh"

module n2x_counter #(
    parameter int unsigned WIDTH = 8
) (
    input  logic             clk_i,
    input  logic             reset_i,
    input  n2x_pkg::t_cmd    cmd_i,
    output logic [WIDTH-1:0] count_o,
    inout  tri               pin_io
);
    import n2x_pkg::*;

    typedef enum logic [1:0] {
        COUNT_IDLE = 2'd0,
        COUNT_RUN  = 2'd1
    } t_count_state;
    typedef struct packed {
        logic       valid;
        logic [3:0] data;
    } t_beat;
    typedef logic [WIDTH-1:0] t_count [2];

    logic [WIDTH-1:0] count_d, count_q;
    // Why: a string and a comment may hold rule words: always_ff, reg, #5, "initial".
    localparam string NAME = "reg always_ff #1 initial";
    /* verilator lint_off UNUSEDSIGNAL */
    logic spare;
    /* verilator lint_on UNUSEDSIGNAL */

    always_comb begin
        count_d = count_q + WIDTH'(1);
    end
    `DFF_RST(count_q, count_d, clk_i, reset_i)
    n2x_child #(.WIDTH(WIDTH)) u_child (.clk_i(clk_i));
    assign count_o = count_q;
endmodule
"""


def rules(tmp_path, text, name="src/n2x/rtl/blk/n2x_counter.sv"):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return [(v.line, v.rule) for v in rtl_lint.violations(path, name)]


def test_a_block_that_follows_the_rules_passes(tmp_path):
    assert rules(tmp_path, CLEAN) == []


@pytest.mark.parametrize("line, rule", [
    ("    always_ff @(posedge clk_i) count_q <= count_d;", "always-ff"),
    ("    always @(*) count_d = count_q;", "always-at"),
    ("    always@(posedge clk_i) count_q = count_d;", "always-at"),
    ("    reg spare_q;", "reg"),
    ("    always_latch if (clk_i) count_d = count_q;", "always-latch"),
    ("    initial count_d = '0;", "initial"),
    ("    task automatic poke; endtask", "task"),
    ("    always_comb casex (count_q) default: count_d = '0; endcase", "casex"),
    ("    assign #5 count_o = count_q;", "delay"),
    ("    always_comb begin #1; end", "delay"),
    ("    typedef struct { logic a; } t_loose;", "struct-packed"),
    ("    typedef union packed { logic a; } t_either;", "no-union-interface"),
    ("    typedef enum {A_ONE, A_TWO} t_implicit;", "enum-width"),
    ("    typedef logic [3:0] nibble_t;", "typedef-name"),
    ("\tassign count_o = count_q;", "tab"),
    ('`include "macros.vh"', "include-path"),
    ("    /* verilator lint_off WIDTHTRUNC */", "waiver-scope"),
])
def test_each_rule_flags_its_construct(tmp_path, line, rule):
    text = CLEAN.replace("    assign count_o = count_q;\n", "    assign count_o = count_q;\n" + line + "\n")
    assert (text.splitlines().index(line) + 1, rule) in rules(tmp_path, text)


def test_ports_need_a_direction_suffix(tmp_path):
    text = CLEAN.replace("output logic [WIDTH-1:0] count_o,", "output logic [WIDTH-1:0] count,")
    assert rules(tmp_path, text) == [(9, "port-suffix")]


def test_non_ansi_and_unpacked_ports_are_named_by_their_last_word(tmp_path):
    text = "module n2x_pins (a_i, b);\n    input logic a_i;\n    output logic b;\nendmodule\n"
    assert rules(tmp_path, text) == [(1, "port-suffix")]
    text = "module n2x_arr (\n    input logic [1:0] lanes_i [4],\n    output logic x = 1'b0\n);\nendmodule\n"
    assert rules(tmp_path, text) == [(3, "port-suffix")]


def test_imports_belong_in_the_module_body(tmp_path):
    text = CLEAN.replace("module n2x_counter #(", "module n2x_counter\n    import n2x_pkg::*;\n#(")
    assert (4, "header-import") in rules(tmp_path, text)
    assert (1, "header-import") in rules(tmp_path, "import n2x_pkg::*;\n" + CLEAN)
    package = "package n2x_pkg;\n    import n2a_types_pkg::*;\n    typedef logic t_bit;\nendpackage\n"
    assert rules(tmp_path, package, "src/n2x/rtl/blk/n2x_pkg.sv") == []


def test_ports_after_a_header_import_are_still_checked(tmp_path):
    text = "module n2x_m\n    import p::*;\n(\n    input logic a\n);\nendmodule\n"
    assert rules(tmp_path, text) == [(2, "header-import"), (4, "port-suffix")]


def test_an_enum_member_with_a_value_is_not_the_typedef_name(tmp_path):
    text = "package n2x_pkg;\n    typedef enum logic {\n        X_ON = 1'b1\n    } state_t;\nendpackage\n"
    assert rules(tmp_path, text, "src/n2x/rtl/blk/n2x_pkg.sv") == [(2, "typedef-name")]


def test_a_waiver_closed_for_every_rule_is_narrow(tmp_path):
    text = CLEAN.replace("/* verilator lint_on UNUSEDSIGNAL */", "/* verilator lint_on */")
    assert rules(tmp_path, text) == []


def test_the_shared_macros_may_hold_flops_and_initial_blocks(tmp_path):
    macros = "`define DFF(Q, D, CLK) \\\n    always_ff @(posedge (CLK)) Q <= D;\n`define INIT \\\n    initial $display(1);\n"
    assert rules(tmp_path, macros, "src/common/rtl/pkg/macros.vh") == []
    assert {r for _, r in rules(tmp_path, macros, "src/n2x/rtl/pkg/my_macros.vh")} == {"always-ff", "initial"}


def test_only_design_files_are_scanned():
    assert rtl_lint.scanned("src/n2x/rtl/blk/a.sv")
    assert rtl_lint.scanned("src/n2x/fpga/board/a.sv")
    assert not rtl_lint.scanned("src/n2x/dv/blk/tb.sv")
    assert not rtl_lint.scanned("src/n2x/rtl/third_party/a.sv")
    assert not rtl_lint.scanned("tools/x.sv")


def test_the_baseline_only_shrinks():
    assert baseline.check({"a": 2}, {"a": 2}, ["a"], "v", "fix", "upd") == []
    assert baseline.check({"a": 3}, {"a": 2}, ["a"], "v", "fix", "upd", {"a": ["  a:1"]})[1] == "  a:1"
    assert "run upd" in baseline.check({"a": 1}, {"a": 2}, ["a"], "v", "fix", "upd")[0]
    assert baseline.raised({"a": 3}, {"a": 2}, "v", "clean")[0].startswith("a: baseline raised")
    assert baseline.raised({"b": 2}, {"a": 2}, "v", "clean") == []


def test_the_base_branch_baseline_blocks_a_raise(tmp_path, capsys):
    data = json.loads(rtl_lint.BASELINE.read_text())
    if not data["rules"]:
        pytest.skip("no rule baseline to raise")
    path = next(iter(data["rules"]))
    data["rules"][path] -= 1
    base = tmp_path / "base.json"
    base.write_text(json.dumps(data))
    assert rtl_lint.main(["--base-baseline", str(base), path]) == 1
    assert f"{path}: baseline raised" in capsys.readouterr().out


@pytest.mark.skipif(not shutil.which("verilator"), reason="needs Verilator on PATH")
def test_verilator_fails_an_unwaived_warning_and_passes_a_waived_one(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "b.json").write_text(json.dumps({"rules": {}, "verilator": {}}))
    unused = "module n2x_top (\n    input logic a_i,\n    output logic y_o\n);\n    logic spare;\n" \
             "    assign spare = a_i;\n    assign y_o = 1'b0;\nendmodule\n"
    (tmp_path / "n2x_top.sv").write_text(unused)
    assert rtl_lint.main(["verilator", "n2x_top", "n2x_top.sv", "--baseline", "b.json"]) == 1
    assert "UNUSEDSIGNAL" in capsys.readouterr().out
    waived = unused.replace("    logic spare;\n", "    /* verilator lint_off UNUSEDSIGNAL */\n    logic spare;\n"
                                                 "    /* verilator lint_on UNUSEDSIGNAL */\n")
    (tmp_path / "n2x_top.sv").write_text(waived)
    assert rtl_lint.main(["verilator", "n2x_top", "n2x_top.sv", "--baseline", "b.json"]) == 0
    (tmp_path / "b.json").write_text(json.dumps({"rules": {}, "verilator": {"n2x_top": 1}}))
    (tmp_path / "n2x_top.sv").write_text(unused)
    assert rtl_lint.main(["verilator", "n2x_top", "n2x_top.sv", "--baseline", "b.json"]) == 0


@pytest.mark.skipif(not shutil.which("verilator"), reason="needs Verilator on PATH")
def test_verilator_errors_fail_whatever_the_baseline(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "b.json").write_text(json.dumps({"rules": {}, "verilator": {"n2x_top": 99}}))
    (tmp_path / "n2x_top.sv").write_text("module n2x_top;\n    n2x_missing u_missing ();\nendmodule\n")
    assert rtl_lint.main(["verilator", "n2x_top", "n2x_top.sv", "--baseline", "b.json"]) == 1

