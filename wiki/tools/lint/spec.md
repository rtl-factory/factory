# Lint: spec

How the comment lint and the wiki reference check work ([requirements](prd.md)). Which comments to write is in [code comments](../../bkm/code-comments.md).

## Parts

| Part | Role |
|---|---|
| `tools/lint/comment_lint.py` | The check; standard library only |
| `tools/lint/comment_baseline.json` | Frozen violation count per file, sorted by path |
| `tools/lint/tests/` | Unit tests, the Bazel test `//tools:lint.unit` |
| `//tools:comment_lint` (`tools/BUILD.bazel`) | Runs the check in L0 through Bazel, against `workdir/lint/base-baseline.json`, which the `l0` job writes from the target branch on pull requests |
| `tools/lint/wiki_refs.py` | The wiki reference check; standard library only |
| `//tools:wiki_refs` (`tools/BUILD.bazel`) | Runs the wiki reference check in L0 through Bazel |

## Usage

```
python3 tools/lint/comment_lint.py                    # check every tracked file in scope against the baseline
python3 tools/lint/comment_lint.py <files>            # check only these files against the baseline
python3 tools/lint/comment_lint.py --all [<files>]    # list every violation, ignoring the baseline
python3 tools/lint/comment_lint.py --update-baseline  # rewrite the baseline to today's counts (whole tree only)
python3 tools/lint/comment_lint.py --base-baseline <file>  # also fail if the baseline is higher than <file>'s (relative to the checkout; skipped if missing)
```

## Behaviour

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
