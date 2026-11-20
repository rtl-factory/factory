---
status: draft
---

# Repository layout

Where code and its documents live, and how to choose the folder for something new. The decisions behind it are ADR 0010, ADR 0011, ADR 0012 and ADR 0013; what each document holds is in [standard-docs.md](../design/standard-docs.md).

## Rules

- Lay out a project's code by project, then discipline, then unit: `src/<project>/<discipline>/<name>/`. The disciplines are `rtl`, `dv`, `fv`, `fpga`, `scripts` and `sw`; a project may also have `cfg/` (simulation targets and board config), which is not a discipline. There is no architecture discipline: a project's architecture is its rtl HAS (ADR 0013). Why: the discipline says what kind of work the code is, and the path is the same in every project.
- Put code in the source tree and its documents in `wiki/`, at the path that mirrors the code: the documents for code in `<path>/` live in `wiki/<path>/`. Why: every document is in one place and on the site, and its path names the code it describes.

  | What | Code | Documents |
  |---|---|---|
  | Design | `src/<project>/rtl/` | `wiki/src/<project>/rtl/has.md` |
  | Block | `src/<project>/rtl/<block>/` | `wiki/src/<project>/rtl/<block>/mas.md` |
  | Testbench | `src/<project>/dv/<block>/` | `wiki/src/<project>/dv/<block>/dv-plan.md` |
  | Formal setup | `src/<project>/fv/<block>/` | `wiki/src/<project>/fv/<block>/fv-plan.md` |
  | Board top | `src/<project>/fpga/<board>/` | `wiki/src/<project>/fpga/<board>/board-spec.md` |
  | Software | `src/<project>/sw/<name>/` | `wiki/src/<project>/sw/<name>/`: `prd.md`, `spec.md` |
  | Script | `src/<project>/scripts/<script>/` | `wiki/src/<project>/scripts/<script>/`: `prd.md`, `spec.md` |
  | Tool | `tools/<tool>/` | `wiki/tools/<tool>/`: `prd.md`, `spec.md` |

- Say **tool** only for code in the top-level `tools/`, and **script** for code in a project's `scripts/` discipline. Never call a script a tool. Why: one word per layer means "where does this live" has one answer.
- Put code and data that run on the design's own CPU in `sw/`: C and assembly sources, firmware and runtimes, linker scripts and the target build setup, and ROM or data images built for the design. Code that runs on the host to build, load or drive them (a compiler driver, an assembler, a loader, a board or Quartus driver) is a script, even when it exists only for that software. Why: the two run on different machines and change for different reasons; the CPU a file runs on decides its discipline.
- Never put a PRD, spec, HAS, MAS or plan in the source tree. A code folder may keep a short `README.md` that says what is in it and points to its documents in the wiki, and third-party notices stay with the files they cover. Why: a requirements or design document beside the code is one the site never shows, or a second copy that drifts from the wiki one.
- Every tool, script and piece of software must have a PRD and a spec at its mirrored wiki path, written per [planning.md](../design/planning.md), and every design, block, testbench, formal setup and board top its standard document per [standard-docs.md](../design/standard-docs.md); they change with the code per [flow.md](flow.md). Why: code without written requirements cannot be reviewed against anything.
- Make it a **tool** when any project could use it unchanged (the simulation runner, the toolchain, the wiki site, the tracker). Make it a **script** of a project when it is bound to that project, or to a block of the `common` project: it speaks a block's protocol, reads one project's config, or encodes one board's facts. Why: code lives next to what forces it to change.
- When unsure, ask: if this project or block were deleted, would it still be worth keeping? If not, it is a script of that project. Why: it is the deletion test (glossary) applied to tools, and it gives the same answer every time.
- Code shared by several projects but tied to a shared block is a script of `common`, not a tool. Why: sharing makes it common; only independence from every design makes it a tool.
- Name the spec path in the header of the tool's or script's entry file. Why: a reader of the code finds the documents without knowing this rule.
- When the scope changes (a script every project now uses, so a tool), move the code and its documents together in one pull request through its own issue. Why: the mirror breaks if only one side moves.

### Example: the JTAG keyboard host

The JTAG keyboard host sends key scan codes to a board over JTAG. It is the host side of the `common` JTAG keyboard block (`src/common/rtl/jtag/`) and speaks that block's own command and status format, so it changes whenever the block does, and it would be useless if the block were deleted. It is therefore a script of `common`: code in `src/common/scripts/jtag/`, documents in `wiki/src/common/scripts/jtag/`. The simulation runner, by contrast, runs any project's targets and knows no design, so it is a tool: code in `tools/sim/`, documents in `wiki/tools/sim/`.

## Steps: add a tool or a script

1. Decide tool or script (Rules), and say which in the issue.
2. Create the code folder, with its tests in `tests/` inside it.
3. Write `prd.md` and `spec.md` in the mirrored wiki folder per [planning.md](../design/planning.md), with a `README.md` landing page per [wiki-site.md](wiki-site.md).
4. Add it to the table of its section's landing page: `wiki/tools/README.md` for a tool, the project's `wiki/src/<project>/scripts/README.md` for a script.

## Exit criteria

- The code and its documents are at mirrored paths, with the standard file names, and no PRD, spec, HAS, MAS or plan sits in the source tree.
