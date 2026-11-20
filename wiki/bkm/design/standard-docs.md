---
status: draft
---

# Standard documents

The documents each discipline keeps, where each one lives, and what goes in it. The layout rules they follow are in [repo-layout.md](../workflow/repo-layout.md), and the decision behind them is ADR 0013.

## Rules

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
- Write the HAS and a block's first MAS in the block's first slice with its code, not ahead of it; until then the interface sketch lives in the PRD's Blocks section ([planning.md](planning.md)). Change each document in the same pull request as the code it describes ([flow.md](../workflow/flow.md)). Why: a document ahead of the code misleads like one that lags it.
- Write PRDs and the specs of scripts, software and tools per [planning.md](planning.md); DV plans per [verification.md](../dv/verification.md); FV plans per [formal.md](../fv/formal.md); board specs per [fpga.md](../fpga/fpga.md). This BKM says what each document holds; those say how the work behind it is done. Why: one method per kind of work.
- Give each discipline folder of a project a `README.md` landing page listing its documents, per [wiki-site.md](../workflow/wiki-site.md). Why: the site's navigation follows the folders.

## Template: HAS (`rtl/has.md`)

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

## Template: MAS (`rtl/<block>/mas.md`)

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

## Template: DV plan (`dv/<block>/dv-plan.md`)

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

## Template: FV plan (`fv/<block>/fv-plan.md`)

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

## Template: board spec (`fpga/<board>/board-spec.md`)

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

## Exit criteria

- Every block, board, script, program and tool has its standard documents at the mirrored path, written from these templates, and they match the code at the pull request's head.
