---
status: draft
---

# Layout and documents

Where code and its documents live, which standard documents each discipline keeps, and how the wiki is published.

## Repository layout

Where code and its documents live, and how to choose the folder for something new. The decisions behind it are ADR 0010, ADR 0011, ADR 0012 and ADR 0013; what each document holds is in [standard documents](#standard-documents).

### Rules

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
- Every tool, script and piece of software must have a PRD and a spec at its mirrored wiki path, written per [planning and specs](planning.md#planning-and-specs), and every design, block, testbench, formal setup and board top its standard document per [standard documents](#standard-documents); they change with the code per [flow](flow.md#flow). Why: code without written requirements cannot be reviewed against anything.
- Make it a **tool** when any project could use it unchanged (the simulation runner, the toolchain, the wiki site, the tracker). Make it a **script** of a project when it is bound to that project, or to a block of the `common` project: it speaks a block's protocol, reads one project's config, or encodes one board's facts. Why: code lives next to what forces it to change.
- When unsure, ask: if this project or block were deleted, would it still be worth keeping? If not, it is a script of that project. Why: it is the deletion test (glossary) applied to tools, and it gives the same answer every time.
- Code shared by several projects but tied to a shared block is a script of `common`, not a tool. Why: sharing makes it common; only independence from every design makes it a tool.
- Name the spec path in the header of the tool's or script's entry file. Why: a reader of the code finds the documents without knowing this rule.
- When the scope changes (a script every project now uses, so a tool), move the code and its documents together in one pull request through its own issue. Why: the mirror breaks if only one side moves.

#### Example: the JTAG keyboard host

The JTAG keyboard host sends key scan codes to a board over JTAG. It is the host side of the `common` JTAG keyboard block (`src/common/rtl/jtag/`) and speaks that block's own command and status format, so it changes whenever the block does, and it would be useless if the block were deleted. It is therefore a script of `common`: code in `src/common/scripts/jtag/`, documents in `wiki/src/common/scripts/jtag/`. The simulation runner, by contrast, runs any project's targets and knows no design, so it is a tool: code in `tools/sim/`, documents in `wiki/tools/sim/`.

### Steps: add a tool or a script

1. Decide tool or script (Rules), and say which in the issue.
2. Create the code folder, with its tests in `tests/` inside it.
3. Write `prd.md` and `spec.md` in the mirrored wiki folder per [planning and specs](planning.md#planning-and-specs), with a `README.md` landing page per [wiki site](#wiki-site).
4. Add it to the table of its section's landing page: `wiki/tools/README.md` for a tool, the project's `wiki/src/<project>/scripts/README.md` for a script.

### Exit criteria

- The code and its documents are at mirrored paths, with the standard file names, and no PRD, spec, HAS, MAS or plan sits in the source tree.

## Standard documents

The documents each discipline keeps, where each one lives, and what goes in it. The layout rules they follow are in [repository layout](#repository-layout), and the decision behind them is ADR 0013.

### Rules

- Give every piece of code the standard documents of its discipline, at the wiki path that mirrors its code, with the file names below. Why: a reader, an agent or a check finds a project's HAS or a block's DV plan by its path alone, in every project.

  | Discipline | Document | File | One per |
  |---|---|---|---|
  | `rtl` | HAS: high-level architecture specification | `wiki/src/<project>/rtl/has.md` | project |
  | `rtl` | MAS: micro-architecture specification | `wiki/src/<project>/rtl/<block>/mas.md` | block |
  | `dv` | DV plan: verification plan | `wiki/src/<project>/dv/<block>/dv-plan.md` | block or testbench |
  | `fv` | FV plan: formal verification plan | `wiki/src/<project>/fv/<block>/fv-plan.md` | formally checked block |
  | `fpga` | Board spec | `wiki/src/<project>/fpga/<board>/board-spec.md` | board top |
  | `scripts` | PRD and spec | `wiki/src/<project>/scripts/<script>/prd.md`, `spec.md` | script |
  | `sw` | PRD and spec | `wiki/src/<project>/sw/<name>/prd.md`, `spec.md` | program, runtime or image |

  A tool keeps a PRD and a spec too, at `wiki/tools/<tool>/`.
- Write the HAS for the whole design and a MAS for each block: the HAS says what the blocks are and how they fit together, and each MAS says how one block is built behind its interface. Never repeat a block's internals in the HAS or the system view in a MAS; link instead. Why: each fact then has one home, and a block's change touches one MAS and, only when its interface changes, the HAS.
- A block's interface is written once, in its MAS. The HAS names each seam and links to the MAS that owns it. Why: the interface is what neighbours and tests depend on, so two copies of it drift into two contracts.
- The HAS is the project's architecture document: block decomposition, the seams between blocks, the memory map, clocks and resets, and the trade-offs behind them. There is no separate architecture discipline or document (ADR 0013). Why: an architecture written apart from the RTL it shapes is a second spec that nobody keeps in step.
- The `common` project has no system, so it has no HAS; each of its blocks has a MAS, and its scripts a PRD and a spec. Why: a HAS describes how blocks fit into one design, and `common` is a shelf of parts.
- Write each document from its template below. Leave out a section that does not apply and say so in one line, rather than deleting it silently. Why: a missing section that says "none" is a decision; a missing section that says nothing is a question.
- Write the HAS and a block's first MAS in the block's first slice with its code, not ahead of it; until then the interface sketch lives in the PRD's Blocks section ([planning and specs](planning.md#planning-and-specs)). Change each document in the same pull request as the code it describes ([flow](flow.md#flow)). Why: a document ahead of the code misleads like one that lags it.
- Write PRDs and the specs of scripts, software and tools per [planning and specs](planning.md#planning-and-specs); DV plans per [dv.md](dv.md); FV plans per [fv.md](fv.md); board specs per [fpga.md](fpga.md). This BKM says what each document holds; those say how the work behind it is done. Why: one method per kind of work.
- Give each discipline folder of a project a `README.md` landing page listing its documents, per [wiki site](#wiki-site). Why: the site's navigation follows the folders.

### Template: HAS (`rtl/has.md`)

```md
# <project> HAS

## Purpose
What the design is, who uses it, and the requirements it answers (link the PRD when there is one).

## Block diagram
The blocks and the seams between them, as a diagram and a table: block, what it does, its MAS.

## Seams
One row per seam: the two blocks, the protocol in one line, the MAS that owns the interface.

## Memory and register map
Address ranges and what answers each one.

## Clocks and resets
Each clock domain and its frequency, each reset and its sequence, every crossing and the block that synchronizes it.

## Top-level behaviour
Boot, the main data flows walked end to end, error and recovery behaviour.

## Decisions
Each architectural trade-off, the options rejected, and why; link the ADR when there is one.

## Verification strategy
Where each top-level requirement is checked: which DV plan, FV plan or board test (link them).
```

### Template: MAS (`rtl/<block>/mas.md`)

```md
# <block> MAS

## Purpose
What the block does for the design, in two or three sentences, and the HAS section it serves.

## Interface
Parameters; ports with direction, width, clock domain and meaning; the protocol and handshake; ordering, latency and throughput; reset and error behaviour; registers.

## Microarchitecture
The data path and control path, each FSM with its states and transitions, pipelines and their stages, storage, and one transaction walked cycle by cycle.

## Clocks and resets
The domains the block uses and the crossings it owns.

## Design choices
The candidates compared (design it twice, per the rtl architecture BKM) and why this one won.

## Verification
Links to the block's DV plan and FV plan, and anything they must cover that is specific to this microarchitecture.
```

### Template: DV plan (`dv/<block>/dv-plan.md`)

```md
# <block> DV plan

## Scope
What is verified here (the block or testbench, the MAS or HAS it checks against) and what is verified elsewhere.

## Testbench
Drivers, monitors, reference model and scoreboard, and where they come from (the spec, never the RTL).

## Features
| ID | Spec section | Stimulus | Checker | Coverage point | Test | Level |
|---|---|---|---|---|---|---|

## Coverage
Functional and code coverage targets, current numbers, and every hole with its reason.

## Status
Open gaps, each with its issue.
```

### Template: FV plan (`fv/<block>/fv-plan.md`)

```md
# <block> FV plan

## Scope
The promises checked formally, why simulation is unlikely to reach their breaking cases, and the tool kind used.

## Properties
| Property | Harm if broken | Spec line | Assumptions | Result (proof, bound, cover) | Command | Sync commit |
|---|---|---|---|---|---|---|

## Assumptions
Each assumption, the spec line that justifies it, and where it is checked on the driving side.

## Covered files
The RTL files the properties describe, with their bounds.
```

### Template: board spec (`fpga/<board>/board-spec.md`)

```md
# <board> board spec

## Board
The board, its FPGA device, and what the design uses on it (memories, displays, buttons, cables).

## Top level
The board top, the design blocks it wraps, and every vendor primitive with the model that stands in for it in simulation.

## Pins and I/O
Each pin group, its I/O standard, and where the pin assignment lives (the board's config).

## Clocks and constraints
Each clock source, any PLL and its outputs, and the timing constraints (link the SDC).

## Budget
Resource use and timing slack at every corner from the last passing build, with its commit.

## Build and program
The build command, the gates it must pass, how the programming file reaches the board host, and how the board is observed.
```

### Exit criteria

- Every block, board, script, program and tool has its standard documents at the mirrored path, written from these templates, and they match the code at the pull request's head.

## Wiki site

How the `wiki/` folder is published as a GitHub Pages site, and how to keep it building.

### Rules

- Write wiki pages as plain Markdown under `wiki/`; the site is generated from them by MkDocs with the Material theme (`mkdocs.yml`). Why: the files stay readable on GitHub and in an editor; the site is only a view.
- Put each document at the wiki path that mirrors the code it describes, per [repository layout](#repository-layout); the site shows it like any other page. Why: one rule places every document, and the site needs nothing special to show it.
- Give every folder under `wiki/` a `README.md`; it is the folder's landing page. Why: a folder without one has no page to land on.
- Link between wiki pages with relative `.md` links. Link to files outside `wiki/` with full GitHub URLs. Why: relative links work both on GitHub and on the site; links out of `wiki/` break on the site.
- Set a folder's title and page order in its `.nav.yml`; without one, pages are listed alphabetically. Why: folder names like `prd` or `dv` are not readable titles.
- Pin the build dependencies in `tools/pages/requirements.txt`, and change them only through an issue. Why: an unpinned theme update can break the site without any wiki change.
- Keep `mkdocs build --strict` passing. Why: a warning is almost always a broken link.

### Steps

1. Preview locally: `pip install -r tools/pages/requirements.txt`, then `mkdocs serve` and open `http://127.0.0.1:8000`.
2. Before opening a pull request that touches `wiki/`, run `mkdocs build --strict` (as CI does) and fix every warning.
3. The `pages` workflow builds and deploys the site; when it runs is in [CI levels](flow.md#ci-levels).

### Exit criteria

- `mkdocs build` passes without warnings, and the deployed site shows the change after merge.
