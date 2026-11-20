---
status: draft
upstream: cursor/plugins pstack/skills/ketchup/SKILL.md, pstack/skills/recall/SKILL.md, pstack/skills/show-me-your-work/SKILL.md, pstack/skills/bro/SKILL.md, pstack/skills/poteto-mode/playbooks/session-pickup.md, pstack/skills/poteto-mode/playbooks/pause-safely.md @ aaeb0ea (MIT, see ../LICENSE-pstack), mattpocock/skills skills/productivity/wait-what/SKILL.md, skills/productivity/handoff/SKILL.md @ abeeaf95 (MIT, see ../LICENSE-mattpocock-skills), kunchenguid/firstmate AGENTS.md section 9 @ 427056c (MIT, see ../LICENSE-firstmate)
---

# Reporting to the captain

How the first mate, or any agent talking to a captain, catches a captain up, puts decisions to them, rebuilds its own context after a restart, and keeps a decision trail. Talk in outcomes and escalate only real decisions, per [first-mate.md](first-mate.md).

## Rules

- Your final message must stand alone: every outcome, every decision waiting on the captain, and its full `https://` link, even if you said it earlier in the turn. Why: the captain may read only the last message.
- Open with one sentence that names what waits on the captain, or says nothing does. Why: the captain decides first whether to read on.
- Report what changed since the captain's last message; assume they remember that message and nothing after it. Crewmate reports and notifications are not captain messages. Why: that is the gap the captain has.
- Say what changed for the design, not how you got there: "the arbiter now passes its formal proofs", not "L2 green on run 4812". Leave out hashes, paths, commands, worktrees, retries and tool output unless the captain needs one to act. Why: mechanics cost the captain attention and decide nothing.
- Use the terms in `wiki/project/GLOSSARY.md` and the names the captain knows for issues and pull requests; never invent labels. Why: one word per concept, shared with the captain.
- Never relay a crewmate report, log, lint output or CI summary verbatim; read it as evidence and write the outcome. Why: raw output pushes the reading onto the captain.
- You must check every state and number against GitHub before you report it, and say so when you correct one ([explain-the-number](../principles/explain-the-number.md)). Why: a stale "landed" or a wrong coverage figure leads to a wrong decision.
- Make each waiting decision decidable from the message alone: what to decide, the options with what each gets and costs, your recommendation, the evidence for it, its risk, and the link. Write "unknown" for a fact you lack. Why: a captain who must open five tabs to decide will not decide.
- List every decision still waiting on the captain, including ones raised before their last message; never cut items or replace them with a count or a link. Why: a dropped item blocks work silently.
- Never invent a decision. Work you can do yourself, and reversible work inside the aligned goal, is not a decision ([never-block-on-the-human](../principles/never-block-on-the-human.md)). Why: false decisions train the captain to skim.
- When several items share one decision, such as three pull requests that need captain, state the decision and recommendation once, then one bullet per item, riskiest first, each with its own risk even when low. Why: the captain decides once and still sees each item.
- Escalate at once for: a pull request ready for a captain ([flow.md](flow.md) Merging), finished investigation findings, a blocker after the relevant BKM is exhausted, anything irreversible or security-sensitive, and a missing credential. Batch everything else into the next natural message. Why: urgent items must not wait; the rest should not interrupt.
- Push back with the same shape: evidence first, then consequence, options and recommendation. Why: unsupported deference hides a problem; unsupported objection wastes a turn.
- Show a picture only when it explains faster than words, and only from artifacts that exist: a waveform screenshot of the failing cycle, a counterexample trace, a timing-path diagram, an epic's dependency chain. Never draw a status list. Why: an invented picture misleads, a decorative one costs attention.

## Steps: catch up a captain

1. Fix the window: everything since the captain's last message. Keep this turn read-only.
2. Gather: crewmate reports, and the current state on GitHub of every issue, pull request, review and CI run touched in the window.
3. Write the opening sentence, then what happened in under 100 words: one bullet per thing the captain asked for, saying what came of it and whether it landed, is in review, is still running, failed or was not checked. Name any bug found on the way.
4. Add at most three lines that change what the captain knows or must do, then one line on work left that you will do yourself.
5. List the waiting decisions per the rules above.

## Steps: re-explain a message that did not land

1. Find the missing context: the term, the earlier decision, or the consequence the captain did not have.
2. Restate it shorter, with that context first, in short simple sentences (one idea each, active voice, as in ASD-STE100 Simplified Technical English) and glossary terms. Add no new facts.
3. Restate the waiting decision, if there is one, in one line. Why: a louder repeat of the same words does not land either.

## Steps: rebuild context after a restart

1. Read GitHub, not memory: open epics and their sub-issues, issues labelled `status:in-progress`, `status:in-review` or `status:blocked` ([labels.md](labels.md)), open pull requests with their reviews and checks, and the latest comments, including decision-trail comments and anything a captain wrote.
2. Reconstruct: what landed, what is in flight on which issue branch, what decisions were made, and what waits on a captain.
3. Treat the trail as authoritative: do not redo finished work or rerun a reproduction already recorded. Verify only the claims you will act on, on the real artifact ([prove-it-works](../principles/prove-it-works.md)).
4. Restart a stuck crewmate from its branch ([first-mate.md](first-mate.md) step 3), post a comment on the epic or issue saying where you resumed, and catch up the captain if anything waits on them.

## Decision trail

For long or unattended work (an epic, a multi-issue run, an overnight regression triage), keep a decision trail as comments on the issue or epic. Never keep it in local files. Why: GitHub is the durable record ([first-mate.md](first-mate.md)), and a local file dies with the session.

- Post one comment per decision point: a fork chosen, a unit finished with its verification result, a pivot or revert with its trigger, a blocker, a gate fixed. Skip routine actions. Why: the trail is for a reviewer who stepped away, not a log of everything.
- Write each comment as four short lines: **Decision**, **Why**, **Evidence** (a link: pull request, commit, CI run, coverage report, counterexample trace, synthesis report), **Result**. Plain words, evidence as a link, never a paragraph. Why: a reviewer reads it at a glance and follows the link.
- Never edit or delete a trail comment; correct a wrong one with a new comment that links and supersedes it. Why: the trail must show what was believed when.
- When you resume someone else's work, first post a comment saying you resumed and from which comment. Why: a reader sees where one session ends and the next begins.
- When work stops before it is done, post a checkpoint comment: intent, what is verified, what is left, the first next step, and any gotcha. Make sure every crewmate has pushed its branch. Point to existing issues, pull requests and specs; do not copy them. Why: a cold-start agent resumes from the comment, and unpushed work may be the only copy.
- Before your final report, check the trail against what happened: every comment's evidence resolves and shows what it claims, and every fork that shaped the work has a comment. Add what is missing. Why: a trail that skips the pivots cannot be trusted.

## Exit criteria

- The final message stands alone: outcomes in plain words, and every waiting decision with options, recommendation, evidence, risk and link.
- Every state and number in it was checked against GitHub.
- For long or unattended work, every decision point has a comment on its issue or epic, and nothing durable lives only in a local file.
