# Lint

Repository-wide lint checks. The comment lint keeps every Python and SystemVerilog comment to the keep list in [code comments](../../bkm/code-comments.md); the wiki reference check fails on a `wiki/` path cited outside the wiki that does not exist; the RTL lint checks every design file against the RTL coding rules a script can check and runs Verilator `--lint-only -Wall` on every project top and common block ([RTL coding](../../bkm/rtl.md#rtl-coding)). Code: `tools/lint/`.

- [Requirements](prd.md)
- [Spec](spec.md)
