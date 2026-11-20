---
status: draft
---

# First mate on different platforms

Examples of how the roles in [first-mate.md](first-mate.md) map onto agent platforms. The roles and rules stay in that BKM; this file only gives hints, and it is not a complete list.

## Rules

- Follow [first-mate.md](first-mate.md) on every platform; where a platform allows more (crewmates that spawn agents, a captain talking straight to a crewmate), the BKM wins. Why: the factory is platform-agnostic; the platform is only the transport.
- Add a row only from the platform's official docs, with the date you checked and a link. Why: platforms change fast, and an undated row rots silently.
- When a row turns out wrong, fix or delete it in its own pull request. Why: a stale hint is worse than none.

## Mapping

| Platform | First mate | Crewmate | How dispatch works | Last verified |
|---|---|---|---|---|
| Claude Code projects ([docs](https://code.claude.com/docs/en/claude-projects)) | The project conversation (the coordinator) | A thread: its own cloud session, context, branch and pull request | Tell the coordinator the work; it starts one thread per task, or proposes them. Threads can run subagents too: keep those to helpers inside one issue. | 2026-11-20 |
| Claude Code CLI ([subagents](https://code.claude.com/docs/en/subagents), [agent teams](https://code.claude.com/docs/en/agent-teams)) | The main session (`/first-mate`) | A subagent; `isolation: worktree` gives it its own worktree | The main session spawns subagents, in the background by default. Subagents may nest up to three levels unless you stop them. Agent teams (experimental) map lead to first mate and teammates to crewmates, but teammates also talk to each other and to you. | 2026-11-20 |
| Claude app, Cowork ([overview](https://claude.com/docs/cowork/overview), [getting started](https://support.claude.com/en/articles/13345190-get-started-with-cowork)) | The task's main Claude | A sub-agent with a fresh context | Claude decides when to split work into parallel sub-agents; you can ask for it. No per-sub-agent branch or worktree is documented, so it suits research and documents more than repo changes. | 2026-11-20 |
| herdr ([README](https://github.com/herdrdev/herdr)) | An agent in one pane, of any supported harness | An agent in another pane, of any harness | The first mate drives herdr's CLI or socket API to spawn panes, prompt them and wait until one is blocked. herdr owns terminals, not git: create each crewmate's worktree yourself. | 2026-11-20 |
| T3 Code ([README](https://github.com/pingdotgg/t3code), [outside agents](https://github.com/pingdotgg/t3code/blob/main/docs/user/outside-agents.md)) | An agent connected to T3 Code's MCP server (for example Claude Code in a terminal) | A thread, on any supported provider, each in its own worktree | The first mate starts and messages threads through the MCP server; a human can also start threads in the app. | 2026-11-20 |

Not yet verified (official pages were unreachable when this was written; add a row once checked): Meta's Muse Code, reported to run async background agents, and xAI's Grok Build, reported to run parallel subagents in their own worktrees.
