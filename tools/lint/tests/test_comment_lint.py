"""Comment lint: keeps headers, licenses, directives and tagged comments;
flags everything else; the baseline may only shrink."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import comment_lint  # noqa: E402


def lint(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text)
    return [v.line for v in comment_lint.violations(path, name)]


def test_python_header_license_directives_and_tags_pass(tmp_path):
    source = (
        "#!/usr/bin/env python3\n"
        "# Leading header block is the file's description.\n"
        '"""Docstring."""\n'
        "import os  # noqa: F401\n"
        "# SPDX-License-Identifier: MIT\n"
        "# Why: the board resets on DTR, so the port opens with it low\n"
        "# and stays that way for the whole session.\n"
        "x = 1  # Ref: UART 8N1, one start and one stop bit\n"
        "# TODO(#12): drop once the loader speaks the bulk protocol\n"
        "y = 2  # type: ignore[index]\n"
    )
    assert lint(tmp_path, "a.py", source) == []


def test_python_narration_fails_once_per_block(tmp_path):
    source = (
        '"""Doc."""\n'
        "# set x\n"
        "# to one\n"
        "x = 1  # one\n"
        "\n"
        "# TODO: no issue number\n"
        "y = 2\n"
    )
    assert lint(tmp_path, "b.py", source) == [2, 4, 6]


def test_python_hash_in_string_is_not_a_comment(tmp_path):
    assert lint(tmp_path, "c.py", 'x = "# not a comment"\ny = f"{x} // nor this"\n') == []


def test_sv_header_and_api_docs_pass(tmp_path):
    source = (
        "// File header: what this block does.\n"
        "`default_nettype none\n"
        "// Doc block directly above the module is its contract.\n"
        "module m #(\n"
        "    parameter int W = 8  // data width\n"
        ") (\n"
        "    input  logic clk,    // 50 MHz\n"
        "    output logic q\n"
        ");\n"
        "  /* verilator lint_off UNUSED */\n"
        "  // Ref: SDRAM tRCD is 3 cycles at 50 MHz\n"
        "  logic a; // synthesis keep\n"
        "  // Returns the parity of v.\n"
        "  function automatic logic parity(input logic [7:0] v);\n"
        "    return ^v;\n"
        "  endfunction\n"
        "  initial $display(\"// not a comment\");\n"
        "endmodule\n"
    )
    assert lint(tmp_path, "m.sv", source) == []


def test_sv_narration_and_block_comments_fail(tmp_path):
    source = (
        "module m (input logic clk);\n"
        "  // ---- registers ----\n"
        "  logic a;\n"
        "  /* increment\n"
        "     the counter */\n"
        "  always_ff @(posedge clk) a <= ~a; // toggle\n"
        "  /* Why: the core samples on the falling edge\n"
        "     of the divided clock. */\n"
        "endmodule\n"
    )
    assert lint(tmp_path, "n.sv", source) == [2, 4, 6]


def test_baseline_only_shrinks(tmp_path):
    found = [comment_lint.Violation("f.py", 3, "# x"), comment_lint.Violation("f.py", 5, "# y")]
    assert comment_lint.check(found, {"f.py": 2}, ["f.py"]) == []
    over = comment_lint.check(found, {"f.py": 1}, ["f.py"])
    assert over[0].startswith("f.py: 2 comment violations, baseline allows 1")
    assert "  f.py:3: # x" in over and "  f.py:5: # y" in over
    stale = comment_lint.check(found, {"f.py": 3}, ["f.py"])
    assert "--update-baseline" in stale[0]
    assert "--update-baseline" in comment_lint.check([], {"gone.py": 1}, ["gone.py"])[0]


def test_scope_skips_third_party_and_other_suffixes():
    assert comment_lint.scanned("src/p/rtl/a/b.sv")
    assert comment_lint.scanned("tools/sim/sim.py")
    assert not comment_lint.scanned("src/p/sw/third_party/x.py")
    assert not comment_lint.scanned("src/p/sw/main.c")
    assert not comment_lint.scanned("wiki/x.py")
