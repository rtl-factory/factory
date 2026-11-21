---
status: draft
upstream: cursor/plugins pstack/skills/no-comments/SKILL.md, pstack/agents/comment-sicko.md @ d42aff0 (MIT, see SOURCES.md); Matt Pocock, "prompt of the day", 2026-11-20 (review every code comment in a codebase)
---

# Code comments

How to write, review and clean up comments in Python and SystemVerilog. A comment earns its place only when it says what the code cannot; the comment lint (`tools/lint/comment_lint.py`, spec in `wiki/tools/lint/spec.md`) enforces the keep list in L0.

## Keep list

A comment survives only as one of these. The lint checks the form; review checks that the content is true.

| Kind | Form | Holds |
|---|---|---|
| Header | the file's leading comment block; in SystemVerilog also the block directly above a `module`, `interface`, `package`, `class`, `program`, `function`, `task`, `checker`, `typedef`, `localparam`, `parameter` or `` `define ``, and comments inside a module or interface port and parameter list or a `typedef` enum or struct body | what the unit, type, constant or macro is, its contract, its ports and fields; the summary a reader needs before the code |
| Why | `Why: ...` | intent or a constraint the code cannot show: why this order, this width, this edge, this workaround |
| Ref | `Ref: ...` | a fact owned by something we cannot reshape: a protocol, a datasheet timing, a vendor primitive, a spec section, with its source |
| Todo | `TODO(#<issue>): ...` | open work, tracked by an issue |
| License | `SPDX-License-Identifier`, `Copyright` | legal notices |
| Directive | tool pragmas: `noqa`, `type:`, `pragma`, `verilator`, `synthesis`, `translate_off`, `verilog_lint:` and the like (full list in the lint spec) | instructions to a tool |

A tag covers the comment lines directly under it, so a long `Why:` needs the tag once. Python docstrings are not comments: they document the API and are not linted.

## Rules

- Before writing a comment, try to make it unnecessary: a better name, a named constant, a typed struct or enum, an assertion, a formal property, a test. Write the comment only for what is left. Why: code and checks cannot drift from the behaviour; prose can.
- Never write narration (what the next line does), section banners, commented-out code, change history or a bare `TODO`. Why: they repeat the code or git, and go stale first.
- In RTL, put protocol, timing and clock-domain facts in a `Ref:` comment naming their source, and design intent in a `Why:` comment. Why: these are the facts a reader cannot recover from the RTL alone.
- A surprise in our own code is not a reason to keep a comment; it is a reason to reshape the code. Delete the comment and open an issue, or fix it in scope, so the behaviour is obvious without prose. Why: an explained surprise is still a surprise for the next reader (pstack's `MUST KILL` flag).
- A comment that claims a constraint ("do not remove", "must stay in this order", "IMPORTANT") must be proven true on a live path, then encoded as the cheapest check in scope (an assertion, a test, a lint) and deleted. If it cannot be encoded, keep it as `Why:` and say what would break. Why: nothing fails when an agent ignores prose; a check fails every time ([Encode Lessons in Structure](principles.md#encode-lessons-in-structure)).
- Review comments with a fresh context, not the author's. The reviewer crewmate reads every comment in the diff against this keep list and flags any whose content is false, stale or narration under a tag. Why: an author defends its own comments (pstack `/no-comments`).
- Never add a tag to an existing comment just to pass the lint. Read it, then delete it, encode it, or rewrite it as a true `Why:` or `Ref:`. Why: a tag on narration defeats the check and teaches the next agent to copy it.
- Shrink the baseline whenever you touch a file that has one: clean its comments and run `python3 tools/lint/comment_lint.py --update-baseline`. Never raise a file's baseline; CI fails a pull request that does. Why: the baseline freezes old violations so new ones fail; it only works if it moves one way.

## Steps: cleaning up the comments of an area

Use for a cleanup issue, or Matt Pocock's whole-codebase review, scoped to one area.

1. List the area's violations: `python3 tools/lint/comment_lint.py --all <files>`. Also read the comments the lint accepts (headers and tagged comments); the lint checks only their form.
2. For each comment, decide: delete (narration, banners, dead code, history), encode (a name, constant, type, assertion or test makes it obvious), or keep (rewrite as a true `Why:`, `Ref:` or `TODO(#<issue>):`, or move it into the header).
3. For each surprise or constraint in our own code that the comment explained, fix it in scope or open an issue for it.
4. Run the lint, the area's L0 tests and simulations, then `--update-baseline`, and commit the smaller baseline with the cleanup.

## Exit criteria

- The lint passes with the area's baseline lower than before, every kept comment is a true header, `Why:`, `Ref:`, `TODO(#<issue>):`, license or directive, and every surprise the cleanup found is fixed or has an issue.
