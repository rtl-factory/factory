# BKM sources

Where our BKMs come from. Four agent-skill packs overlap heavily, so we take each idea once: one BKM per topic, drawing on every pack that covers it. Each BKM's front matter names the upstream files it uses.

| Pack | Author | License |
|---|---|---|
| [Skills for Real Engineers](https://github.com/mattpocock/skills) | Matt Pocock | MIT, `LICENSE-mattpocock-skills` |
| [tstack](https://github.com/t3dotgg/tstack) | Theo Browne | MIT, `LICENSE-tstack` |
| [pstack](https://github.com/cursor/plugins/tree/main/pstack) | Lauren Tan (`poteto`) | MIT, `LICENSE-pstack` |
| [firstmate](https://github.com/kunchenguid/firstmate) | Kun Chen | MIT, `LICENSE-firstmate` |
| nand2anything (private) | Amichai Ben-David, a captain of this factory | No licence file; adapted at the owner's direction (issue #61) |

## Adopted

Which topic sources each BKM used, and why the others were rejected, is recorded in a comment on its topic issue.

| BKM | From |
|---|---|
| `design/grilling.md`, `design/domain-modeling.md`, `design/glossary-format.md`, `design/adr-format.md` | Skills for Real Engineers: `grilling`, `grill-me`, `grill-with-docs`, `domain-modeling` |
| `writing/writing-for-agents.md`, `writing/skill-mechanics.md` | Skills for Real Engineers: `writing-for-agents` |
| `workflow/first-mate.md` | firstmate: roles, principles, crewmate brief; topic #34 for orchestration, sources in its front matter |
| `fpga/fpga.md`; the install method in `tools/toolchain/spec.md` | nand2anything: host routing, bitstream handoff, Quartus build gates, native pinned Quartus install |
| `principles/*.md` (23 files) | pstack: every `principle-*` skill except `principle-experience-first` |
| `workflow/issues.md` | topic #33; sources used in its front matter |
| `workflow/review.md` | topic #36; sources used in its front matter |
| `workflow/landing.md` | topics #35 and #37; sources used in its front matter |
| `workflow/reporting.md` | topic #42; sources used in its front matter |
| `debug/debugging.md` | topic #38; sources used in its front matter |
| `writing/improving-bkms.md` | topic #41; sources used in its front matter |
| `dv/verification.md`, `fv/formal.md` | topic #39; sources used in their front matter |
| `arch/architecture.md` | topic #40; sources used in its front matter |
| `design/planning.md` | topic #32; sources used in its front matter |

SRE = Skills for Real Engineers.

## Rejected

| Source | Why |
|---|---|
| SRE `migrate-to-shoehorn`, `setup-ts-deep-modules`, `scaffold-exercises`, `setup-pre-commit` | TypeScript, course material or Husky; software-only |
| SRE `writing-beats`, `writing-fragments`, `writing-shape` | article writing |
| SRE `ask-matt` | a router over upstream skills; replaced by the `bkm-router` skill |
| pstack `typescript-best-practices`, `make-bot-ui`, `principle-experience-first`, `poteto-mode/playbooks/visual-parity.md` | TypeScript, UI or product UX |
| pstack `setup-pstack`, `poteto-help`, `poteto-mode`, `ketchup-video` | pack setup, help and personal style; the useful parts are covered by the BKMs above |

## Background reading

Used for the first-mate BKM's terms; not adopted as method.

- [Orchestration topology is the new bottleneck in agentic coding](https://dev.to/terminalblog/orchestration-topology-is-the-new-bottleneck-in-agentic-coding-487g)
- [Orchestration patterns](https://daily.dev/agentic-ai-hub/orchestration-patterns/)
- [Git worktrees for parallel AI agent execution](https://augmentcode.com/guides/git-worktrees-parallel-ai-agent-execution)
