# Lint: spec

How the comment lint works ([requirements](prd.md)). Which comments to write is in [code comments](../../bkm/writing/code-comments.md).

## Parts

| Part | Role |
|---|---|
| `tools/lint/comment_lint.py` | The check; standard library only |
| `tools/lint/comment_baseline.json` | Frozen violation count per file, sorted by path |
| `tools/lint/tests/` | Unit tests, run by the `l0` unit-test step |
| `.github/workflows/l0.yml` step `Comment lint` | Runs the check first in L0, against the target branch's baseline on pull requests |

## Usage

```
python3 tools/lint/comment_lint.py                    # check every tracked file in scope against the baseline
python3 tools/lint/comment_lint.py <files>            # check only these files against the baseline
python3 tools/lint/comment_lint.py --all [<files>]    # list every violation, ignoring the baseline
python3 tools/lint/comment_lint.py --update-baseline  # rewrite the baseline to today's counts (whole tree only)
python3 tools/lint/comment_lint.py --base-baseline <file>  # also fail if the baseline is higher than <file>'s
```

## Behaviour

- **Scope:** `git ls-files` under `src/` and `tools/` with suffix `.py`, `.sv`, `.svh`, `.v` or `.vh`, skipping any path with a `third_party` folder.
- **Finding comments:** Python uses the standard tokenizer, so `#` inside a string is not a comment. SystemVerilog uses a small lexer that skips string literals and reads `//` line comments and `/* */` block comments; a block comment counts as one comment at its first line.
- **Accepted:** a comment before the file's first line of code (the header); in SystemVerilog, a comment block directly above a `module`, `interface`, `package`, `class`, `program`, `function`, `task`, `checker`, `typedef`, `localparam`, `parameter` or `` `define `` declaration, any comment from a `module` or `interface` keyword to the first `;` outside parentheses (the end of its port list, or the same line for `module x;`), and any comment inside a `typedef enum`, `struct` or `union` body; a comment whose text starts with `Why:`, `Ref:` or `TODO(#<n>):`; a comment starting with `SPDX-License-Identifier:` or `Copyright`; a tool directive (Python: shebang, coding line, `noqa`, `type:`, `pragma`, `pylint:`, `fmt:`, `isort:`, `mypy:`; SystemVerilog: `verilator`, `synthesis`, `synopsys`, `pragma`, `altera`, `lint_off`, `lint_on`, `translate_off`, `translate_on`, `spyglass`, `cadence`, `coverage`, `verilog_lint:`); an empty comment. A `/* */` comment is judged by its first non-empty line, with a leading `*` stripped.
- **Blocks:** comment-only lines directly under an accepted own-line comment are accepted with it; comment-only lines directly under a violation are part of that one violation. A blank line or a line of code ends a block. A trailing comment after code stands alone.
- **Baseline:** for each file, more violations than its baseline entry (zero when absent) fails and lists every violation in the file; fewer fails with the instruction to run `--update-baseline`; an entry for a file no longer in scope also fails as stale. With `--base-baseline`, an entry above its value in the base file fails, and so does a higher total; a new entry may only carry counts moved from entries that dropped, as in a rename. The `l0` step passes the target branch's baseline on pull requests, so a pull request cannot raise the baseline. Exit status is 1 on any failure, 0 otherwise.
