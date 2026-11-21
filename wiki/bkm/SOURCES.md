# BKM sources

Where our BKMs come from. Four agent-skill packs, plus one captain's own project repository, overlap heavily, so we take each idea once: one BKM per topic, drawing on every pack that covers it. Each BKM file's front matter names the upstream files it uses.

| Pack | Author | License |
|---|---|---|
| [Skills for Real Engineers](https://github.com/mattpocock/skills) | Matt Pocock | MIT, [Licences](#licences) |
| [tstack](https://github.com/t3dotgg/tstack) | Theo Browne | MIT, [Licences](#licences) |
| [pstack](https://github.com/cursor/plugins/tree/main/pstack) | Lauren Tan (`poteto`) | MIT, [Licences](#licences) |
| [firstmate](https://github.com/kunchenguid/firstmate) | Kun Chen | MIT, [Licences](#licences) |
| nand2anything (private) | Amichai Ben-David, a captain of this factory | No licence file; adapted at the owner's direction (issue #61) |

## Adopted

Which topic sources each BKM used, and why the others were rejected, is recorded in a comment on its topic issue.

| BKM | From |
|---|---|
| `planning.md` Grilling, Domain modeling, Glossary format, ADR format | Skills for Real Engineers: `grilling`, `grill-me`, `grill-with-docs`, `domain-modeling` |
| `writing.md` Writing for agents, Skill mechanics | Skills for Real Engineers: `writing-for-agents` |
| `first-mate.md` Running the crew | firstmate: roles, principles, crewmate brief; topic #34 for orchestration, sources in its front matter |
| `fpga.md`; the install method in `wiki/tools/toolchain/spec.md` | nand2anything: host routing, bitstream handoff, Quartus build gates, native pinned Quartus install |
| `principles.md` (23 principles) | pstack: every `principle-*` skill except `principle-experience-first` |
| `issues.md` Issues and epics | topic #33; sources used in its front matter |
| `pull-requests.md` Review | topic #36; sources used in its front matter |
| `pull-requests.md` Writing and landing | topics #35 and #37; sources used in its front matter |
| `first-mate.md` Reporting to the captain | topic #42; sources used in its front matter |
| `debug.md` | topic #38; sources used in its front matter |
| `writing.md` Learning from mistakes | topic #41; sources used in its front matter |
| `dv.md`, `fv.md` | topic #39; sources used in their front matter |
| `rtl.md` Architecture of blocks | topic #40; sources used in its front matter |
| `planning.md` Planning and specs | topic #32; sources used in its front matter |
| `code-comments.md`; the comment lint in `tools/lint/` | pstack: `no-comments` and its `comment-sicko` agent, turned into a lint at its author's suggestion; Matt Pocock's comment-review prompt (2026-11-20) for the cleanup steps; issue #105 |

## Rejected

| Source | Why |
|---|---|
| SRE `migrate-to-shoehorn`, `setup-ts-deep-modules`, `scaffold-exercises`, `setup-pre-commit` | TypeScript, course material or Husky; software-only |
| SRE `writing-beats`, `writing-fragments`, `writing-shape` | article writing |
| SRE `ask-matt` | a router over upstream skills; replaced by the `bkm-router` skill |
| pstack `typescript-best-practices`, `make-bot-ui`, `principle-experience-first`, `poteto-mode/playbooks/visual-parity.md` | TypeScript, UI or product UX |
| pstack `setup-pstack`, `poteto-help`, `poteto-mode`, `ketchup-video` | pack setup, help and personal style; the useful parts are covered by the BKMs above |

SRE = Skills for Real Engineers.

## Background reading

Used for the first-mate BKM's terms; not adopted as method.

- [Orchestration topology is the new bottleneck in agentic coding](https://dev.to/terminalblog/orchestration-topology-is-the-new-bottleneck-in-agentic-coding-487g)
- [Orchestration patterns](https://daily.dev/agentic-ai-hub/orchestration-patterns/)
- [Git worktrees for parallel AI agent execution](https://augmentcode.com/guides/git-worktrees-parallel-ai-agent-execution)

## Licences

The four packs are MIT licensed. Their licence text is identical apart from the copyright line, so it is given once:

```text
MIT License

Copyright (c) 2026 Matt Pocock (Skills for Real Engineers)
Copyright (c) 2026 Theo Browne (tstack)
Copyright (c) 2026 Lauren Tan (pstack)
Copyright (c) 2026 Kun Chen (firstmate)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
