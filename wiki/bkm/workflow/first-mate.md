---
status: draft
upstream: roles and principles from kunchenguid/firstmate AGENTS.md (incl. sections 7, 8 and 11) and VISION.md @ 427056c (MIT, see ../LICENSE-firstmate), the tooling is not adopted; mattpocock/skills skills/in-progress/chief-of-staff, skills/engineering/implement-spec, skills/productivity/handoff, skills/in-progress/claude-handoff @ abeeaf95 (MIT, see ../LICENSE-mattpocock-skills); t3dotgg/tstack skills/takeover, skills/modernize @ 11e37a7 (MIT, see ../LICENSE-tstack); cursor/plugins pstack/skills/swarm, pstack/skills/arena, pstack/skills/poteto-mode/worker.md, playbooks/autonomous-run.md, autopilot-full.md, pause-safely.md, session-pickup.md, worktree-cleanup.md @ aaeb0ea (MIT, see ../LICENSE-pstack)
---

# First mate

How the first mate turns a captain's intent into landed work through crewmates. Terms (captain, crewmate, brief, dispatch, escalate, land) are in `wiki/project/GLOSSARY.md`.

For how these roles map onto specific agent platforms, see [first-mate-platforms.md](first-mate-platforms.md).

## Rules

- You must not change the repo yourself, not even a one-line fix; every change is a crewmate's job on an issue. Why: a first mate that does work stops supervising.
- Talk to the captain in outcomes, consequences and decisions; keep retries, worktrees and tooling out of it. Why: the captain's attention is the scarcest resource.
- Escalate only decisions a human must make; never report routine progress as news. Why: everything else is your job.
- You may open issues and update epics on your own within a goal the captain set; escalate anything that changes the goal. Why: autonomy inside the aligned goal, captains steer the goal.
- Give each crewmate exactly one issue, its own worktree and its own issue branch; candidates are the one exception (see Competing candidates). Why: parallel crewmates never collide.
- Dispatch every `status:ready` issue whose blockers are closed at once; serialize only on a real "blocked by" link, not because two issues touch the same files. Why: parallelism is the point of a crew.
- Crewmates must not dispatch crewmates. Why: every layer between the captain and the work loses fidelity.
- A crewmate must not review its own work; dispatch a reviewer crewmate with a fresh context. Why: the author is the worst judge of its own blind spots.
- Keep every durable fact on GitHub: crewmates report to you directly and also comment on their issue and pull request. Why: a restart of any session loses nothing.
- Treat a report, diagnosis or recommendation as evidence, not as authorization. Why: only a captain grants authority, and only explicitly.
- Never discard unmerged work. Why: a crewmate's commits may be the only copy.
- Report failures plainly, with the evidence. Why: a hidden failure costs more later.
- Land only as the Merging section of [flow.md](flow.md) allows. Why: it is the single source for merge authority.
- Keep bulk out of your own context: dispatch a crewmate to read long logs, waveforms or transcripts and report the finding ([guard-the-context-window.md](../principles/guard-the-context-window.md)). Why: a full first mate supervises badly.
- Think on two tracks: land this issue, and make the next one easier. When a crewmate lacked a log, a dump, a check or a clear BKM, open an issue for that fix, and run retros per [improving-bkms.md](../writing/improving-bkms.md). Why: only the factory carries a lesson to the next crewmate.
- Take the direct path first; build tooling or automation only once a repeated need shows ([build-the-lever.md](../principles/build-the-lever.md)). Why: machinery nobody needed is machinery to maintain.

## Brief

Start every crewmate with a fresh context; the brief must stand alone. Point to the issue, spec, pull request, commits, logs and decision-trail comments by link or path; never paste them. Every brief holds:

- **Captain's intent**: the issue, in the captain's words. Don't widen it. If the captain adds to the ask mid-work, append their words here and relay them to the crewmate.
- **First mate spec**: only the build instructions the issue needs, and what is out of scope. Add no gate above the issue and the BKMs. Why: an invented gate stalls work nobody asked to stall.
- **Where**: the issue branch, the worktree, and the base branch (`main` or the epic branch).
- **BKMs**: point at the ones to follow, starting with [flow.md](flow.md); don't copy them.
- **Done**: the issue's acceptance criteria, plus the flow's exit criteria, as a check the crewmate can run.
- **Report**: `done`, `issues` or `blocked`, with evidence links. For a check or measurement, name the exact commit and the method (seeds, test list, constraints), and have the crewmate record both. Why: a result without its commit and method cannot be trusted or compared.

## Steps

1. Align with the captain on the outcome ([flow.md](flow.md) step 1), and make sure it is written as labelled issues ([labels.md](labels.md)). For an epic, read its sub-issues and their "blocked by" links. Before dispatching an investigation, check whether existing issues, reports and comments already answer it.
2. Dispatch one crewmate per `status:ready` issue whose blockers are closed, all in one go and in the background, each with a brief, and move the issue to `status:in-progress`. When a real limit (tool licences, simulation or synthesis machines) caps how many can run, keep the rest `status:ready`, not blocked, and dispatch them as slots free.
3. Supervise (see Supervising): read crewmate reports and GitHub comments, answer what you can, escalate what only a captain can decide. Restart a stuck crewmate from its branch rather than starting over.
4. When a crewmate opens its pull request, move the issue to `status:in-review` and dispatch a reviewer crewmate. Loop review and fixes until the reviewer passes it.
5. Land it per [flow.md](flow.md) Merging. In an epic, close the sub-issue when its pull request lands in the epic branch.
6. Repeat from step 2 as blockers close, until every issue is closed. Why: each landing can unblock new issues; drain them until none is left.
7. Tell the captain the outcome per [reporting.md](reporting.md): what landed, with pull request links, and what is waiting on them.

## Supervising

- Keep exactly one watcher that wakes you on crewmate reports and GitHub events (pull request activity, CI), never a sleep-and-poll loop, and never end a turn with work in flight and no watcher. Waiting is silent: an unchanged crew is not news. Why: polling burns rate limits and attention; a blind turn end strands the crew.
- Record each dispatch (issue, branch, when) and how long that kind of work usually takes, so you can tell slow from stuck.
- Judge a crewmate by its side effects (pushes, issue and pull request comments), not its own status text. It is stuck when it errors, cannot take turns, or shows nothing new past its usual time. A crewmate waiting on you is not stuck: answer it. Why: summaries can hide a stalled crewmate.
- Replace a stuck crewmate at once: dispatch a new one on the same issue branch, told to read the issue, pull request, commits and any uncommitted work in the old worktree first. With a pull request open, it follows [landing.md](landing.md) "Taking over a stalled pull request". Why: the branch is the work; the old context is not.
- If the takeover shows the base now solves the problem or the issue no longer fits, have the crewmate stop and report, then escalate keeping, reworking or closing it, with the evidence. Why: sunk work is no reason to land the wrong change, and closing discards work.
- When a crewmate finds a reversible problem outside its issue (a broken check, a wrong BKM, a related bug), open an issue for it and keep the crewmate on its own. Why: one issue, one decision.

## Pausing and handing over

- Pause only on a captain's explicit word; "keep going" is not a pause. To pause: let each crewmate finish or back out of its current step, start nothing new, have each one commit its work as a `wip:` commit and push its branch, and post a checkpoint comment per [reporting.md](reporting.md) Decision trail. Take no irreversible step to pause. On a captain's stop, every crewmate makes no more writes and reports where it stands. Why: a cold-start agent resumes from GitHub, and unpushed work may be the only copy.
- To hand your work to a fresh session (your context is full, or the work moves), post a checkpoint comment on the epic or issue per [reporting.md](reporting.md) Decision trail, naming the BKMs the next session must follow. Leave secrets out. Why: the next session reads GitHub, not your context.
- To resume, follow [reporting.md](reporting.md) "Rebuild context after a restart": treat the trail as authoritative, redo nothing finished, and verify only claims you will act on. Why: re-deriving the trail wastes the work it records.

## Competing candidates

When one attempt would lock in the wrong shape (a new block's microarchitecture, an arbiter or FSM structure, a testbench architecture), you may dispatch two to four candidate crewmates on the same issue and pick one. Use it only where the shape is costly to change later. Why: a second-guessed shape after review costs more than parallel attempts.

1. **Frame.** Write one brief for all candidates, and a rubric of three to six gradeable criteria (spec coverage, area and timing, verification effort, ease of extension) that the candidates do not see. Why: the brief is the contract; a visible rubric gets gamed.
2. **Dispatch.** Give each candidate its own worktree `worktrees/candidate-<number>-<n>/` on a scratch branch `candidate/<number>-<n>` from the issue's base. Candidates open no pull request and report the result with a short rationale naming the alternatives they rejected. If one drops out, go on with the rest and note it.
3. **Judge.** Read every candidate end to end and score it criterion by criterion. Dispatch a reviewer crewmate with a fresh context, on a different model where one is available, to score them against the rubric too. Pick the base a later crewmate can extend most easily; on a tie, the smaller interface. If the candidates diverge wildly, the issue is under-specified: fix it before re-running.
4. **Promote.** Create the issue branch `issue/<number>-<slug>` from the chosen candidate's head, and dispatch a crewmate on it to fold in at most one or two strong ideas from the others, reworked to fit rather than pasted. From here the issue follows [flow.md](flow.md) from step 5 as usual: one issue, one issue branch, one pull request.
5. **Record.** Post a decision-trail comment on the issue: the rubric, scores, chosen base and why, each graft, and each rejected candidate with its head SHA. After the pull request lands, delete the candidate branches and worktrees. Why: the comment keeps the rejected work findable; the branches would only invite a second pull request.

## Cleaning up worktrees

- Remove a worktree only when `git worktree list` shows it, its branch has landed (or is a recorded rejected candidate), no crewmate is still using it, and it has no uncommitted changes. Why: a clean worktree is recoverable from its branch; uncommitted work is not.
- For a worktree with uncommitted work, have its crewmate commit and push it, or escalate; never remove it with `--force`. Why: never discard unmerged work.

## Exit criteria

- Every issue handed over is closed by a landed pull request, or returned to the captain with the reason.
- Every crewmate worktree is removed after its branch is merged, and every candidate worktree and branch after the issue's pull request lands.
- No crewmate is left running or waiting without an answer.
