# BKMs

Best Known Methods: how work is done in the factory. Agents follow them; captains tune them.

The list of every BKM and when to follow it lives in the `bkm-router` skill (`.claude/skills/bkm-router/SKILL.md`), so agents always have it in context. Add a row there whenever you add a BKM.

| Directory | Holds |
|---|---|
| `workflow/` | how work moves: the flow, issues, review, landing, reporting, the first mate, labels, CI levels, GitHub settings, the wiki site |
| `design/` | deciding what to build: planning a PRD or spec, grilling, domain modeling, glossary and ADR formats |
| `writing/` | writing BKMs, skills and anything else an agent reads; retros |
| `debug/` | debugging hard failures |
| `arch/` | designing blocks and their interfaces, refactoring, explaining how a block works |
| `dv/` | simulation-based verification |
| `fv/` | formal verification |
| `fpga/` | building, gating and programming FPGA images |
| `principles/` | engineering principles, one per file |

Planned, created with their first BKM: `rtl/`.

Many BKMs are adapted from outside packs under the MIT license; their front matter names the upstream file and commit. [SOURCES.md](SOURCES.md) maps which pack feeds which BKM.
