# Lint: spec

How the comment lint, the wiki reference check and the RTL lint work ([requirements](prd.md)). Which comments to write is in [code comments](../../bkm/code-comments.md); which RTL to write is in [RTL coding](../../bkm/rtl.md#rtl-coding).

## Parts

| Part | Role |
|---|---|
| `tools/lint/comment_lint.py` | The check; standard library only |
| `tools/lint/comment_baseline.json` | Frozen violation count per file, sorted by path |
| `tools/lint/tests/` | Unit tests, the Bazel test `//tools:lint.unit` |
| `//tools:comment_lint` (`tools/BUILD.bazel`) | Runs the check in L0 through Bazel, against `workdir/lint/base-baseline.json`, which the `l0` job writes from the target branch on pull requests |
| `tools/lint/wiki_refs.py` | The wiki reference check; standard library only |
| `//tools:wiki_refs` (`tools/BUILD.bazel`) | Runs the wiki reference check in L0 through Bazel |
| `tools/lint/baseline.py` | The shrink-only baseline checks the comment lint and the RTL lint share |
| `tools/lint/rtl_lint.py` | The RTL lint: the rule checker, and the Verilator lint of one top; standard library only |
| `tools/lint/rtl_baseline.json` | Frozen counts: `rules` per design file, `verilator` per lint top |
| `tools/lint/tests/test_rtl_lint.py` | Its unit tests, part of `//tools:lint.unit` |
| `//tools:rtl_lint` (`tools/BUILD.bazel`) | Runs the rule checker in L0, against `workdir/lint/base-rtl-baseline.json`, which the `l0` job writes from the target branch |
| `//src/<project>:<top>.lint` (`hdl_project()`) | One L0 test per lint top: Verilator on the top's files |

## Comment lint: usage

```
python3 tools/lint/comment_lint.py                    # check every tracked file in scope against the baseline
python3 tools/lint/comment_lint.py <files>            # check only these files against the baseline
python3 tools/lint/comment_lint.py --all [<files>]    # list every violation, ignoring the baseline
python3 tools/lint/comment_lint.py --update-baseline  # rewrite the baseline to today's counts (whole tree only)
python3 tools/lint/comment_lint.py --base-baseline <file>  # also fail if the baseline is higher than <file>'s (relative to the checkout; skipped if missing)
```

## Comment lint: behaviour

- **Scope:** `git ls-files` under `src/` and `tools/` with suffix `.py`, `.sv`, `.svh`, `.v` or `.vh`, skipping any path with a `third_party` folder.
- **Finding comments:** Python uses the standard tokenizer, so `#` inside a string is not a comment. SystemVerilog uses a small lexer that skips string literals and reads `//` line comments and `/* */` block comments; a block comment counts as one comment at its first line.
- **Accepted:** a comment before the file's first line of code (the header); in SystemVerilog, a comment block directly above a `module`, `interface`, `package`, `class`, `program`, `function`, `task`, `checker`, `typedef`, `localparam`, `parameter` or `` `define `` declaration, any comment from a `module` or `interface` keyword to the first `;` outside parentheses (the end of its port list, or the same line for `module x;`), ignoring `import` lines in the header, and any comment inside a `typedef enum`, `struct` or `union` body; a comment whose text starts with `Why:`, `Ref:` or `TODO(#<n>):`; a comment starting with `SPDX-License-Identifier:` or `Copyright`; a tool directive (Python: shebang, coding line, `noqa`, `type:`, `pragma`, `pylint:`, `fmt:`, `isort:`, `mypy:`; SystemVerilog: `verilator`, `synthesis`, `synopsys`, `pragma`, `altera`, `lint_off`, `lint_on`, `translate_off`, `translate_on`, `spyglass`, `cadence`, `coverage`, `verilog_lint:`); an empty comment. A `/* */` comment is judged by its first non-empty line, with a leading `*` stripped.
- **Blocks:** comment-only lines directly under an accepted own-line comment are accepted with it; comment-only lines directly under a violation are part of that one violation. A blank line or a line of code ends a block. A trailing comment after code stands alone.
- **Baseline:** for each file, more violations than its baseline entry (zero when absent) fails and lists every violation in the file; fewer fails with the instruction to run `--update-baseline`; an entry for a file no longer in scope also fails as stale. With `--base-baseline`, an entry above its value in the base file fails, and so does a higher total; new entries may together hold no more than the entries that disappeared, as in a rename, so a new file brings no violations. The `l0` step passes the target branch's baseline on pull requests, so a pull request cannot raise the baseline. Exit status is 1 on any failure, 0 otherwise.

## Wiki reference check

Code cites the documents it implements by path (`wiki/src/<project>/rtl/<block>/mas.md`); a citation of a document that does not exist sends the reader nowhere and hides a missing spec.

```
python3 tools/lint/wiki_refs.py           # check every tracked file outside wiki/
python3 tools/lint/wiki_refs.py <files>   # check only these files
```

- **Scope:** every file `git ls-files` lists, except files under `wiki/` (links between wiki pages are the site build's to check), any path with a `third_party` folder, and files that are not UTF-8 text.
- **Citations:** a `wiki/` path that starts a word, optionally after `./` or `../` segments, up to the first character that cannot be in a path; trailing `.`, `,`, `:` and `;` are dropped, and so is a `#` anchor. A path inside a longer one, such as a URL (`.../blob/<sha>/wiki/...`), names another repository and is not checked.
- **Resolution:** leading `./` and `../` segments are dropped and the rest is read from the repository root, so `../../wiki/x.md` and `wiki/x.md` name the same document. A file or a folder satisfies a citation. A placeholder (`<board>`, `{name}`, `*`) matches any characters, and the citation passes when some tracked path matches it.
- **Result:** each missing citation prints as `<file>:<line>: <path> does not exist`; any missing citation exits 1, none exits 0. Anchors are not checked.
- **Fix:** port the document where the code needs one, or cite the document that holds the content now. A document that lives only in another repository is cited by a URL pinned to a commit.

## RTL lint

### Usage

```
python3 tools/lint/rtl_lint.py                         # check every tracked design file against the baseline
python3 tools/lint/rtl_lint.py <files>                 # check only these files against the baseline
python3 tools/lint/rtl_lint.py --all [<files>]         # list every rule violation, ignoring the baseline
python3 tools/lint/rtl_lint.py --update-baseline       # rewrite both sections of the baseline (Verilator on PATH)
python3 tools/lint/rtl_lint.py verilator <top> <files> # lint one top, as its Bazel test does
python3 tools/bazel/hdl_libraries.py lint-tops         # list the lint tops and their files
```

Regenerate the baseline with `--update-baseline` from the checkout root after a change that fixes violations; it scans every design file and lints every top (seconds), and it refuses to write when a top has a Verilator error. A change may never raise it: the `l0` job passes the target branch's baseline, and a higher entry, a higher total, or new entries holding more than the entries that disappeared fail `//tools:rtl_lint`.

### Rules

- **Scope:** `git ls-files` under `src/<project>/rtl/` and `src/<project>/fpga/` with suffix `.sv`, `.svh`, `.v` or `.vh`, skipping any path with a `third_party` folder. Testbenches (`dv/`) are not scanned.
- **Reading:** comments and string literals are blanked before a rule matches, keeping lines, so a rule word in a comment or a string never fires.
- **Rules**, each named in its finding:

| Rule | Fails on | Rule in [RTL coding](../../bkm/rtl.md#rtl-coding) |
|---|---|---|
| `always-ff` | `always_ff` outside the shared macros (`macros.vh`, `assertion_macros.vh`) | sequential logic |
| `always-at`, `reg`, `always-latch` | `always @`, `reg`, `always_latch` | sequential logic |
| `delay` | a `#` not followed by `(` or another `#` | sequential logic |
| `initial`, `task` | `initial` or `task` outside the shared macros | sequential and combinational logic |
| `casex` | `casex` | combinational logic |
| `struct-packed`, `no-union-interface` | a `struct` that is not `packed`; `union`, `interface`, `modport` | structs |
| `enum-width` | an `enum` not on `logic` | enums |
| `typedef-name` | a typedef whose name is not `t_<name>` | files and names |
| `port-suffix` | a module port whose name does not end in `_i`, `_o` or `_io` (ANSI and non-ANSI headers) | files and names |
| `header-import` | an `import` in a module header or at file scope | packages |
| `include-path` | an `` `include `` whose path does not start at `src/` | formatting |
| `tab` | a tab anywhere in the file | formatting |
| `waiver-scope` | a `verilator lint_off <RULE>` with no later `lint_on <RULE>` (or bare `lint_on`) in the file | assertions and checks |

- **Baseline:** the same as the comment lint's, per file, under `rules`; an entry for a file no longer in scope fails as stale.

### Verilator lint

- **Tops:** `tools/bazel/hdl_libraries.py lint_tops()` derives them from the libraries: every module of `rtl/` or `fpga/` that no other design library needs (each project's tops, such as its board tops), and every module of `src/common/rtl/` and `src/common/fpga/` (each common block, at its default parameters and default variant). A project's `targets.json` may hold a `lint` object, `{"<top>": {"libraries": [...], "flavors": [...]}}`, that adds what a top needs beyond its own library, such as a vendor primitive's stand-in (`sld_virtual_jtag_stub`) and the project's composition waivers, or names one more top. Each top's files are its libraries resolved exactly as for a simulation target.
- **Run:** `verilator --lint-only -Wall --assert -Wno-fatal --top-module <top> <sources>` in the working folder (the test's runfiles under Bazel), so root-relative includes resolve. Each `%Warning-` line counts; any `%Error`, or a non-zero exit, fails whatever the baseline holds.
- **Waivers:** Verilator's own inline comments, `/* verilator lint_off <RULE> */ ... /* verilator lint_on <RULE> */`, on the narrowest span; the rule checker's `waiver-scope` fails one left open.
- **Baseline:** per top, under `verilator`: more warnings than the entry fails and lists them; fewer fails until the baseline is regenerated; `//tools:rtl_lint` fails an entry for a top that is no longer linted.
- **Bazel:** `hdl_project()` makes `//src/<project>:<top>.lint` for each top from `@hdl_registry`'s `LINT_TARGETS`, tagged `L0` and `lint`. It runs sandboxed and cached, with only the top's sources and headers, `rtl_lint.py`, `baseline.py`, the baseline and `@verilator` as inputs, so it reruns only when one of them changes.
