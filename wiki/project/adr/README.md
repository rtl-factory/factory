# Decisions

Architecture decision records: one per decision that is hard to reverse, surprising without context, and the result of a real trade-off ([ADR format](../../bkm/design/adr-format.md)).

| ADR | Decision |
|---|---|
| [0001](0001-vendor-agent-skills.md) | Adopt upstream agent skills as BKMs, one BKM per topic |
| [0002](0002-bkms-steer-agents.md) | BKMs steer the agents; skills are entry points |
| [0003](0003-every-change-starts-with-an-issue.md) | Every change starts with a GitHub issue |
| [0004](0004-epics-are-opt-in.md) | Epics and epic branches are opt-in |
| [0005](0005-merge-fast-recover-fast.md) | Merge fast, recover fast: cumulative regression levels |
| [0006](0006-first-mate-orchestrates-crewmates.md) | A first mate orchestrates crewmates; captains talk only to the first mate |
| [0007](0007-merge-authority.md) | Full send by default; captains hold back what they choose and the factory's controls |
| [0008](0008-squash-issues-merge-epics.md) | Squash issue branches; merge-commit epic branches into main |
| [0009](0009-linux-toolchain.md) | Linux-first toolchain: Verilator and cocotb, SymbiYosys, Quartus Prime Lite, installed natively at pinned versions |
| [0010](0010-docs-mirror-code-in-wiki.md) | Documents live in the wiki, mirroring the code tree; tools are repo tools or project tools |
| [0011](0011-disciplines-and-scripts.md) | Disciplines, not types; code bound to a project is a script, and only `tools/` holds tools |
| [0012](0012-software-discipline.md) | Software is a discipline: what runs on the design's CPU is `sw`, what runs on the host is a script |
