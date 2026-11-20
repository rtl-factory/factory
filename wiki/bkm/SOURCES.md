# BKM sources

Where our BKMs come from. Four agent-skill packs overlap heavily, so we take each idea once: one BKM per topic, drawing on every pack that covers it. Each BKM's front matter names the upstream files it uses.

| Pack | Author | License |
|---|---|---|
| [Skills for Real Engineers](https://github.com/mattpocock/skills) | Matt Pocock | MIT, `LICENSE-mattpocock-skills` |
| [tstack](https://github.com/t3dotgg/tstack) | Theo Browne | MIT, `LICENSE-tstack` |
| [pstack](https://github.com/cursor/plugins/tree/main/pstack) | Lauren Tan (`poteto`) | MIT, `LICENSE-pstack` |
| [firstmate](https://github.com/kunchenguid/firstmate) | Kun Chen | MIT, `LICENSE-firstmate` |

## Adopted

Which topic sources each BKM used, and why the others were rejected, is recorded in a comment on its topic issue.

| BKM | From |
|---|---|
| `design/grilling.md`, `design/domain-modeling.md`, `design/glossary-format.md`, `design/adr-format.md` | Skills for Real Engineers: `grilling`, `grill-me`, `grill-with-docs`, `domain-modeling` |
| `writing/writing-for-agents.md`, `writing/skill-mechanics.md` | Skills for Real Engineers: `writing-for-agents` |
| `workflow/first-mate.md` | firstmate: roles, principles, crewmate brief |
| `principles/*.md` (23 files) | pstack: every `principle-*` skill except `principle-experience-first` |
| `workflow/issues.md` | topic #33; sources used in its front matter |
| `workflow/review.md` | topic #36; sources used in its front matter |
| `workflow/landing.md` | topics #35 and #37; sources used in its front matter |
| `workflow/reporting.md` | topic #42; sources used in its front matter |
| `debug/debugging.md` | topic #38; sources used in its front matter |
| `writing/improving-bkms.md` | topic #41; sources used in its front matter |
| `dv/verification.md`, `fv/formal.md` | topic #39; sources used in their front matter |

## Topics in progress

Each topic is one issue that produces one BKM from all the sources listed.

| Topic | Issue | BKM | Sources |
|---|---|---|---|
| Planning and specs | #32 | `design/planning.md` | SRE `to-spec`, SRE `wayfinder`, SRE `loop-me`, SRE `to-questionnaire`, SRE `prototype`, pstack `architect`, pstack `sharpen`, pstack `in-your-own-words`, pstack `figure-it-out`, pstack `multi-phase-plan`, pstack `prototype` |
| Orchestration and parallel crewmates | #34 | `workflow/first-mate.md` | SRE `chief-of-staff`, SRE `implement`, SRE `implement-spec`, SRE `handoff`, SRE `claude-handoff`, tstack `takeover`, tstack `modernize`, pstack `swarm`, pstack `arena`, pstack `worker`, pstack `autonomous-run`, pstack `autopilot-full`, pstack `autopilot-stack`, pstack `pause-safely`, pstack `session-pickup`, pstack `worktree-cleanup`, firstmate `AGENTS`, SRE `software-factory` |
| Architecture and design of blocks | #40 | `arch/` | SRE `codebase-design`, SRE `improve-codebase-architecture`, pstack `how`, pstack `why`, pstack `refactoring` |

SRE = Skills for Real Engineers.

## Rejected

| Source | Why |
|---|---|
| SRE `migrate-to-shoehorn`, `setup-ts-deep-modules`, `scaffold-exercises`, `setup-pre-commit` | TypeScript, course material or Husky; software-only |
| SRE `writing-beats`, `writing-fragments`, `writing-shape` | article writing |
| SRE `ask-matt` | a router over upstream skills; replaced by the `bkm-router` skill |
| pstack `typescript-best-practices`, `make-bot-ui`, `principle-experience-first`, `poteto-mode/playbooks/visual-parity.md` | TypeScript, UI or product UX |
| pstack `setup-pstack`, `poteto-help`, `poteto-mode`, `ketchup-video` | pack setup, help and personal style; the useful parts are covered by the topics above |

## Background reading

Used for the first-mate BKM's terms; not adopted as method.

- [Orchestration topology is the new bottleneck in agentic coding](https://dev.to/terminalblog/orchestration-topology-is-the-new-bottleneck-in-agentic-coding-487g)
- [Orchestration patterns](https://daily.dev/agentic-ai-hub/orchestration-patterns/)
- [Git worktrees for parallel AI agent execution](https://augmentcode.com/guides/git-worktrees-parallel-ai-agent-execution)
