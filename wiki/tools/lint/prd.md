# Lint: requirements

## Problem

Comments in Python and SystemVerilog drift from the code they describe: narration repeats the code, banners and commented-out code pile up, and claimed constraints go unchecked. A BKM alone does not stop this, because nothing fails when an agent skips prose ([learning from mistakes](../../bkm/writing.md#learning-from-mistakes) step 5). The codebase already holds thousands of comments, so a check that failed on all of them at once could not land.

## Requirements

1. Every comment in a tracked Python or SystemVerilog file under `src/` and `tools/` is checked against the keep list in [code comments](../../bkm/code-comments.md): header, `Why:`, `Ref:`, `TODO(#<issue>):`, license, tool directive.
2. A new comment outside the keep list fails L0, naming the file, the line and the fix.
3. Existing violations do not fail L0, but their count per file can only go down.
4. Third-party code is not checked.
5. The check runs in seconds with the Python standard library only, so it runs before every push.

## Out of scope

- Judging whether a kept comment is true: the reviewer crewmate does that ([review](../../bkm/pull-requests.md#review)).
- Docstrings, C and assembly sources, YAML and Markdown.

## Acceptance

| Requirement | Check |
|---|---|
| 1, 4 | Unit tests for each keep class, strings holding comment markers, SystemVerilog block comments, and the third-party skip |
| 2 | A unit test and a manual run: one untagged comment added to a scanned file fails with its line named |
| 3 | Unit tests: over the baseline fails, under the baseline fails until the baseline is updated |
| 5 | The `l0` step runs before any toolchain setup and finishes in seconds |
