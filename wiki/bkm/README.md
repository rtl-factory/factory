# BKMs

Best Known Methods: how work is done in the factory. Agents follow them; captains tune them.

One file per area. The list of every BKM section and when to follow it lives in the `bkm-router` skill (`.claude/skills/bkm-router/SKILL.md`), so agents always have it in context. Add a row there whenever you add a BKM.

| Area | File | Holds |
|---|---|---|
| Workflow | [flow.md](flow.md) | the flow from issue to `main`, who may merge, epics, CI levels, GitHub settings |
| | [issues.md](issues.md) | splitting and triaging issues, labels, the issue and pull request templates |
| | [pull-requests.md](pull-requests.md) | writing a pull request and GitHub comments, getting it landable, landing, review |
| | [first-mate.md](first-mate.md) | running the crew, the first mate on each agent platform, reporting to a captain |
| | [layout.md](layout.md) | the repository layout, the standard documents of each discipline, the wiki site |
| Design | [planning.md](planning.md) | planning a PRD, grilling, domain modeling, glossary and ADR formats |
| Writing | [writing.md](writing.md) | writing BKMs, skills and anything else an agent reads; retros |
| | [code-comments.md](code-comments.md) | which comments to keep in Python and SystemVerilog, and the comment lint |
| Hardware | [rtl.md](rtl.md) | designing blocks and their seams, refactoring, explaining a block; RTL coding rules |
| | [dv.md](dv.md) | simulation-based verification |
| | [fv.md](fv.md) | formal verification |
| | [fpga.md](fpga.md) | building, gating and programming FPGA images |
| | [debug.md](debug.md) | debugging hard failures |
| Principles | [principles.md](principles.md) | engineering principles, each with its trigger |

Many BKMs are adapted from outside packs under the MIT license; their front matter names the upstream files and commit. [SOURCES.md](SOURCES.md) maps which pack feeds which BKM and holds the licences.
