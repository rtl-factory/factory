# Lint

Repository-wide lint checks that know no design. It holds the comment lint, which keeps every Python and SystemVerilog comment to the keep list in [code comments](../../bkm/code-comments.md), and the wiki reference check, which fails on a `wiki/` path cited outside the wiki that does not exist. Code: `tools/lint/`.

- [Requirements](prd.md)
- [Spec](spec.md)
