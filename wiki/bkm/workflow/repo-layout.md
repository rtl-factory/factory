---
status: draft
---

# Repository layout

Where code and its documents live, and how to choose the folder for something new. The decision behind it is ADR 0010.

## Rules

- Put code in the source tree and its documents in `wiki/`, at the path that mirrors the code: the documents for code in `<path>/` live in `wiki/<path>/`. Why: every document is in one place and on the site, and its path names the code it describes.

  | What | Code | Documents |
  |---|---|---|
  | Block of a project | `src/<project>/<type>/<block>/` (type: `arch`, `rtl`, `dv`, `fv`, `fpga`) | `wiki/src/<project>/<type>/<block>/`: spec, verification plan, formal plan |
  | Project tool | `src/<project>/tools/<tool>/` | `wiki/src/<project>/tools/<tool>/`: `prd.md`, `spec.md` |
  | Repo tool | `tools/<tool>/` | `wiki/tools/<tool>/`: `prd.md`, `spec.md` |

- Never put a PRD, spec or plan in the source tree. A code folder may keep a short `README.md` that says what is in it and points to its documents in the wiki, and third-party notices stay with the files they cover. Why: a requirements or design document beside the code is one the site never shows, or a second copy that drifts from the wiki one.
- Every tool must have a PRD and a spec at its mirrored wiki path, written per [planning.md](../design/planning.md); they change with the code per [flow.md](flow.md). Why: a tool without written requirements cannot be reviewed against anything.
- Make a tool a **repo tool** when any project could use it unchanged (the simulation runner, the toolchain, the wiki site, the tracker). Make it a **project tool** when it is bound to one project, or to a block of the `common` project: it speaks a block's protocol, reads one project's config, or encodes one board's facts. Why: a tool lives next to what forces it to change.
- When unsure, ask: if this project or block were deleted, would the tool still be worth keeping? If not, it is a project tool of that project. Why: it is the deletion test (glossary) applied to tools, and it gives the same answer every time.
- A tool shared by several projects but tied to a shared block is a project tool of `common`, not a repo tool. Why: sharing makes it common; only independence from every design makes it a repo tool.
- Name the tool's spec path in the header of its entry file. Why: a reader of the code finds the documents without knowing this rule.
- When a tool's scope changes (a project tool every project now uses), move its code and its documents together in one pull request through its own issue. Why: the mirror breaks if only one side moves.

### Example: the JTAG keyboard host

The JTAG keyboard host sends key scan codes to a board over JTAG. It is the host side of the `common` JTAG keyboard block (`src/common/rtl/jtag/`) and speaks that block's own command and status format, so it changes whenever the block does, and it would be useless if the block were deleted. It is therefore a project tool of `common`: code in `src/common/tools/jtag/`, documents in `wiki/src/common/tools/jtag/`. The simulation runner, by contrast, runs any project's targets and knows no design, so it is a repo tool: code in `tools/sim/`, documents in `wiki/tools/sim/`.

## Steps: add a tool

1. Decide repo tool or project tool (Rules), and say which in the issue.
2. Create the code folder, with its tests in `tests/` inside it.
3. Write `prd.md` and `spec.md` in the mirrored wiki folder per [planning.md](../design/planning.md), with a `README.md` landing page per [wiki-site.md](wiki-site.md).
4. Add the tool to the table of its section's landing page: `wiki/tools/README.md` for a repo tool, the project's `wiki/src/<project>/README.md` for a project tool.

## Exit criteria

- The code and its documents are at mirrored paths, and no PRD, spec or plan sits in the source tree.
