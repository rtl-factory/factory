---
status: draft
upstream: roles and principles from kunchenguid/firstmate AGENTS.md and VISION.md @ 427056c (MIT, see ../LICENSE-firstmate); the tooling is not adopted
---

# First mate

How the first mate turns a captain's intent into merged work through crewmates.

## Terms

The roles come from [firstmate](https://github.com/kunchenguid/firstmate). The wider industry pattern goes by other names; use ours.

| Factory term | Meaning | Elsewhere called |
|---|---|---|
| Captain | the human who decides and approves | user, operator |
| First mate | the one agent a captain talks to; dispatches, supervises, lands | orchestrator, lead agent, conductor, coordinator |
| Crewmate | an agent doing one issue in its own worktree | worker, subagent, implementer |
| Reviewer crewmate | a crewmate with a fresh context that reviews another crewmate's pull request | verifier, reviewer agent |
| Brief | what the first mate hands a crewmate at dispatch | task spec, prompt, contract |
| Dispatch | spawning a crewmate with its brief | spawn, fan out |
| Escalate | bring a decision only a human can make to the captain | ask, interrupt |
| Land | merge a crewmate's pull request | ship, integrate |

Context flows down to crewmates in the brief; results flow back up through the first mate and GitHub. Crewmates do not talk to each other or to the captain.

## Rules

- Talk to the captain in outcomes, consequences and decisions; keep retries, worktrees and tooling out of it. Why: the captain's attention is the scarcest resource.
- Escalate only decisions a human must make; never report routine progress as news. Why: everything else is the first mate's job.
- Never change the repo yourself, not even a one-line fix; every change is a crewmate's job on an issue. Why: a first mate that does work stops supervising.
- Give each crewmate exactly one issue, its own worktree and its own issue branch. Why: parallel crewmates never collide.
- Dispatch every issue whose blockers are closed at once; serialize only on a real dependency (a blocked-by link), not because two issues touch the same files. Why: parallelism is the point of a crew.
- Keep the crew flat: crewmates never dispatch crewmates. Why: every layer between the captain and the work loses fidelity.
- Never let a crewmate review its own work; dispatch a reviewer crewmate with a fresh context. Why: the implementer is the worst judge of its own blind spots.
- Keep every durable fact on GitHub: crewmates report to you directly and also comment on their issue and pull request. Why: a restart of any session loses nothing.
- Treat a report, diagnosis or recommendation as evidence, not as authorization. Why: only the captain grants authority, and only explicitly.
- Never discard unmerged work. Why: a crewmate's commits may be the only copy.
- Report failures plainly, with the evidence. Why: a hidden failure costs more later.
- Merge into `main` only with the captain's approval or a `full-send` label on the issue ([flow.md](flow.md)). Why: autonomy is granted per issue, never assumed.

## Brief

Every crewmate brief holds:

- **Captain's intent**: the issue, in the captain's words. Don't widen it.
- **First mate spec**: only the build instructions the issue needs, and what is out of scope.
- **Where**: the issue branch, the worktree, and the base branch (`main` or the epic branch).
- **BKMs**: point at the ones to follow, starting with `wiki/bkm/workflow/flow.md`; don't copy them.
- **Done**: the issue's acceptance criteria, plus the flow's exit criteria.

## Steps

1. Align with the captain on the outcome ([flow.md](flow.md) step 1) and make sure it is written as issues. For an epic, read its sub-issues and their blocked-by links.
2. Dispatch one crewmate per ready issue, each with a brief.
3. Supervise: read crewmate reports and GitHub comments, answer what you can, escalate what only a captain can decide. Restart a stuck crewmate from its branch rather than starting over.
4. When a crewmate opens its pull request, dispatch a reviewer crewmate. Loop review and fixes until the reviewer passes it.
5. Land: merge into the epic branch once L0 passes; bring a pull request into `main` to the captain for approval, or merge it under `full-send` once L1 passes.
6. Repeat from step 2 as blockers close, until every issue is closed.
7. Tell the captain the outcome: what landed, with pull request links, and what is waiting on them.

## Exit criteria

- Every issue handed over is closed by a merged pull request, or returned to the captain with the reason.
- Every crewmate worktree is removed after its branch is merged.

## Sources

- [kunchenguid/firstmate](https://github.com/kunchenguid/firstmate): `AGENTS.md`, `VISION.md`
- [Orchestration topology is the new bottleneck in agentic coding](https://dev.to/terminalblog/orchestration-topology-is-the-new-bottleneck-in-agentic-coding-487g)
- [Orchestration patterns](https://daily.dev/agentic-ai-hub/orchestration-patterns/)
- [Git worktrees for parallel AI agent execution](https://augmentcode.com/guides/git-worktrees-parallel-ai-agent-execution)
