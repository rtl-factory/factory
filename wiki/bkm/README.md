# BKMs

Best Known Methods: how work is done in the factory. Agents follow them; captains tune them.

The list of every BKM and when to follow it lives in the `bkm-router` skill (`.claude/skills/bkm-router/SKILL.md`), so agents always have it in context. Add a row there whenever you add a BKM.

| Directory | Holds |
|---|---|
| `workflow/` | how work moves: the flow, the first mate, CI levels, GitHub settings |
| `design/` | deciding what to build: grilling, domain modeling, glossary and ADR formats |
| `writing/` | writing BKMs, skills and anything else an agent reads |

Planned, created with their first BKM: `arch/`, `rtl/`, `dv/`, `fv/`, `syn/`.

Some BKMs are adapted from outside sources under the MIT license; their front matter names the upstream file and commit. See `LICENSE-mattpocock-skills`.
