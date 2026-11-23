"""Wiki reference check: finds cited wiki paths, resolves them from the
repository root, and fails on each one that does not exist."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import wiki_refs  # noqa: E402

# Why: a literal missing path in this file would fail the check it tests.
W = "wiki"


def tree(tmp_path, files):
    for name, text in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return sorted(files)


def run(tmp_path, monkeypatch, capsys, files, argv=()):
    tracked = tree(tmp_path, files)
    monkeypatch.setattr(wiki_refs, "ROOT", tmp_path)
    monkeypatch.setattr(wiki_refs, "tracked_files", lambda root: tracked)
    status = wiki_refs.main([str(tmp_path / a) for a in argv])
    return status, capsys.readouterr().out


def test_references_strip_punctuation_and_skip_urls_and_longer_paths():
    text = (
        f"// see {W}/src/a/mas.md.\n"
        f"# ({W}/src/b/spec.md#parts), and {W}/src/c/:\n"
        f"url https://github.com/o/r/blob/abc/{W}/src/d.md\n"
        f"docs/{W}/e.md and x{W}/f.md\n"
        f"[link](../../{W}/src/g.md)\n"
    )
    assert wiki_refs.references(text) == [
        (1, f"{W}/src/a/mas.md"),
        (2, f"{W}/src/b/spec.md"),
        (2, f"{W}/src/c/"),
        (5, f"../../{W}/src/g.md"),
    ]


def test_target_drops_relative_prefix():
    assert wiki_refs.target(f"../../{W}/src/g.md") == f"{W}/src/g.md"
    assert wiki_refs.target(f"./{W}/tools/") == f"{W}/tools"


def test_placeholder_matches_any_tracked_file(tmp_path):
    tracked = [f"{W}/src/p/fpga/de10_lite/board-spec.md"]
    assert wiki_refs.exists(f"{W}/src/p/fpga/<board>/board-spec.md", tmp_path, tracked)
    assert wiki_refs.exists(f"{W}/src/p/fpga/" + "{board}", tmp_path, tracked)
    assert wiki_refs.exists(f"{W}/**", tmp_path, tracked)
    assert not wiki_refs.exists(f"{W}/src/q/<board>/board-spec.md", tmp_path, tracked)


def test_scope_skips_wiki_pages_and_third_party():
    assert wiki_refs.scanned("src/p/rtl/a.sv")
    assert wiki_refs.scanned(".github/workflows/l0.yml")
    assert not wiki_refs.scanned(f"{W}/src/p/README.md")
    assert not wiki_refs.scanned("src/p/sw/third_party/x.c")


def test_existing_file_and_folder_pass(tmp_path, monkeypatch, capsys):
    status, out = run(tmp_path, monkeypatch, capsys, {
        f"{W}/src/p/rtl/a/mas.md": "# a\n",
        "src/p/rtl/a/a.sv": f"// Block a ({W}/src/p/rtl/a/mas.md); see {W}/src/p/rtl.\n",
    })
    assert status == 0, out
    assert "every cited wiki path exists" in out


def test_missing_path_fails_with_file_and_line(tmp_path, monkeypatch, capsys):
    status, out = run(tmp_path, monkeypatch, capsys, {
        f"{W}/src/p/rtl/has.md": "# p\n",
        "src/p/rtl/a/a.sv": f"module a;\n// Ref: {W}/src/p/rtl/a/mas.md\nendmodule\n",
        "src/p/dv/test_a.py": f'"""Checks {W}/src/p/rtl/has.md."""\n',
    })
    assert status == 1
    assert f"src/p/rtl/a/a.sv:2: {W}/src/p/rtl/a/mas.md does not exist" in out
    assert "test_a.py" not in out


def test_wiki_pages_are_not_checked(tmp_path, monkeypatch, capsys):
    status, out = run(tmp_path, monkeypatch, capsys, {
        f"{W}/src/p/README.md": f"See {W}/src/p/gone.md.\n",
    })
    assert status == 0, out


def test_named_files_only(tmp_path, monkeypatch, capsys):
    status, out = run(tmp_path, monkeypatch, capsys, {
        "src/p/a.py": f"# {W}/gone.md\n",
        "src/p/b.py": "x = 1\n",
    }, argv=["src/p/b.py"])
    assert status == 0, out
    assert "1 files checked" in out


def test_binary_file_is_skipped(tmp_path, monkeypatch, capsys):
    tracked = tree(tmp_path, {"src/p/a.py": "x = 1\n"})
    (tmp_path / "src/p/img.png").write_bytes(b"\x89PNG\xff\xfe" + f"{W}/gone.md".encode())
    tracked.append("src/p/img.png")
    monkeypatch.setattr(wiki_refs, "ROOT", tmp_path)
    monkeypatch.setattr(wiki_refs, "tracked_files", lambda root: tracked)
    assert wiki_refs.main([]) == 0
