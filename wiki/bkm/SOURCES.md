# BKM sources

Where our BKMs come from. Four agent-skill packs overlap heavily, so we take each idea once: one BKM per topic, drawing on every pack that covers it. Each BKM's front matter names the upstream files it uses.

| Pack | Author | License |
|---|---|---|
| [Skills for Real Engineers](https://github.com/mattpocock/skills) | Matt Pocock | MIT, `LICENSE-mattpocock-skills` |
| [tstack](https://github.com/t3dotgg/tstack) | Theo Browne | MIT, `LICENSE-tstack` |
| [pstack](https://github.com/cursor/plugins/tree/main/pstack) | Lauren Tan (`poteto`) | MIT, `LICENSE-pstack` |
| [firstmate](https://github.com/kunchenguid/firstmate) | Kun Chen | MIT, `LICENSE-firstmate` |

## Adopted

| BKM | From |
|---|---|
| `design/grilling.md`, `design/domain-modeling.md`, `design/glossary-format.md`, `design/adr-format.md` | Skills for Real Engineers: `grilling`, `grill-me`, `grill-with-docs`, `domain-modeling` |
| `writing/writing-for-agents.md`, `writing/skill-mechanics.md` | Skills for Real Engineers: `writing-for-agents` |
| `workflow/first-mate.md` | firstmate: roles, principles, crewmate brief |
| `principles/*.md` (23 files) | pstack: every `principle-*` skill except `principle-experience-first` |

## Topics in progress

Each topic is one issue that produces one BKM from all the sources listed.

| Topic | Issue | BKM | Sources |
|---|---|---|---|
| Planning and specs | #32 | `design/planning.md` | SRE `to-spec`, SRE `wayfinder`, SRE `loop-me`, SRE `to-questionnaire`, SRE `prototype`, pstack `architect`, pstack `sharpen`, pstack `in-your-own-words`, pstack `figure-it-out`, pstack `multi-phase-plan`, pstack `prototype` |
| Splitting work into issues and epics | #33 | `workflow/flow.md (issue section)` | SRE `to-tickets`, SRE `triage`, SRE `setup-matt-pocock-skills` |
| Orchestration and parallel crewmates | #34 | `workflow/first-mate.md` | SRE `chief-of-staff`, SRE `implement`, SRE `implement-spec`, SRE `handoff`, SRE `claude-handoff`, tstack `takeover`, tstack `modernize`, pstack `swarm`, pstack `arena`, pstack `worker`, pstack `autonomous-run`, pstack `autopilot-full`, pstack `autopilot-stack`, pstack `pause-safely`, pstack `session-pickup`, pstack `worktree-cleanup`, firstmate `AGENTS`, SRE `software-factory` |
| Opening pull requests and writing GitHub comments | #35 | `workflow/pull-requests.md` | SRE `pr`, tstack `file-pr`, tstack `leaving-pr-comments`, pstack `opening-a-pr`, pstack `technical-writing`, pstack `unslop` |
| Review loop | #36 | `workflow/review.md` | SRE `code-review`, tstack `council`, tstack `sol-loop`, pstack `interrogate`, pstack `blast-radius`, pstack `no-comments`, pstack `bugbot-triage` |
| CI monitoring and landing | #37 | `workflow/landing.md` | tstack `babysit-pr`, tstack `full-send`, tstack `full-stop`, pstack `babysit`, pstack `shipping`, SRE `git-guardrails-claude-code`, SRE `wizard` |
| Debugging | #38 | `debug/debugging.md` | SRE `diagnosing-bugs`, pstack `bug-fix`, pstack `investigation`, pstack `runtime-forensics`, pstack `trace-forensics` |
| Verification, testing and measurement | #39 | `dv/ and fv/` | SRE `tdd`, pstack `tdd`, pstack `create-verification-skill`, pstack `maintain-verification-skill`, pstack `create-formal-verification-skill`, pstack `maintain-formal-verification-skill`, pstack `statechart`, pstack `benchmark-checklist`, pstack `perf-issue`, pstack `eval`, pstack `hillclimb` |
| Architecture and design of blocks | #40 | `arch/` | SRE `codebase-design`, SRE `improve-codebase-architecture`, pstack `how`, pstack `why`, pstack `refactoring` |
| Learning from mistakes | #41 | `writing/improving-bkms.md` | SRE `retro`, pstack `reflect`, pstack `correct`, pstack `automate-me` |
| Reporting to and teaching the captain | #42 | `workflow/reporting.md` | SRE `wait-what`, SRE `teach`, SRE `research`, pstack `bro`, pstack `ketchup`, pstack `recall`, pstack `show-me-your-work`, pstack `teach` |

SRE = Skills for Real Engineers.

## Rejected

| Source | Why |
|---|---|
| SRE `migrate-to-shoehorn`, `setup-ts-deep-modules`, `scaffold-exercises`, `setup-pre-commit` | TypeScript, course material or Husky; software-only |
| SRE `writing-beats`, `writing-fragments`, `writing-shape` | article writing |
| SRE `ask-matt` | a router over upstream skills; replaced by the `bkm-router` skill |
| pstack `typescript-best-practices`, `make-bot-ui`, `principle-experience-first`, `poteto-mode/playbooks/visual-parity.md` | TypeScript, UI or product UX |
| pstack `setup-pstack`, `poteto-help`, `poteto-mode`, `ketchup-video` | pack setup, help and personal style; the useful parts are covered by the topics above |
