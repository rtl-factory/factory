---
status: draft
upstream: mattpocock/skills skills/engineering/code-review/SKILL.md @ abeeaf95 (MIT, see ../LICENSE-mattpocock-skills), t3dotgg/tstack skills/sol-loop/SKILL.md, skills/council/SKILL.md, skills/leaving-pr-comments/SKILL.md @ 11e37a7 (MIT, see ../LICENSE-tstack), cursor/plugins pstack/skills/interrogate/SKILL.md, pstack/skills/blast-radius/SKILL.md, pstack/skills/poteto-mode/references/bugbot-triage.md @ aaeb0ea (MIT, see ../LICENSE-pstack)
---

# Review

How a reviewer crewmate reviews a pull request and decides whether to pass it ([flow.md](flow.md) step 7).

## Rules

- You must not review a pull request you, or a crewmate sharing your context, authored. Why: the author is the worst judge of its own blind spots ([first-mate.md](first-mate.md)).
- Review read-only: do not commit, push or edit the issue branch; keep repros in your own scratch worktree. Why: the author fixes; a reviewer that edits stops being a fresh pair of eyes.
- Review on three axes and report them separately: **Spec** (the issue's acceptance criteria and the block's spec), **Standards** (BKMs and principles whose triggers match), **Breakage** (what the change breaks beyond the files it touches). Why: a change can pass one axis and fail another, and merging them lets one mask the other.
- Post a finding as blocker or should only when you proved it by running something; what you only reasoned about is a nit or a question. Why: a review that sounds right reads the same whether or not it is true.
- Give every finding a severity, a `file:line`, the concrete failure (input, seed or trace, what happened, what the issue or spec requires) and the smallest fix. Why: the author must be able to reproduce it without asking you.
- Use these severities. **blocker**: the intended case is wrong, an acceptance criterion is unmet, or code and spec disagree. **should**: a real failure a caller, testbench or downstream block will hit within the issue's intent. **nit**: everything else (rare paths, style, small cost, extra compatibility). Why: only blockers and shoulds hold up approval, so the label must mean the same to everyone.
- Flag anything the issue did not ask for as scope creep, and hold fixes to the smallest change. Why: repeated review rounds grow a pull request unless someone fights it.
- Skip what lint, CI or a regression level already enforces. Why: a human-style comment on a machine-checked rule is noise.

## Steps

1. **Frame.** Read the issue, its acceptance criteria, the pull request description and commits, and the spec under `wiki/src/<project>/<discipline>/<block>/` for each block touched. Restate the intent in one or two sentences. Pin one base: `git merge-base HEAD origin/<base branch>`, and use the same `git diff <base>` every round. If the intent or acceptance is unclear, report that to the first mate instead of guessing.
2. **Run the checks.** Run L0 ([ci-levels.md](ci-levels.md)) and every check that covers the touched blocks: lint, simulation, formal, synthesis. Note which pass, fail, or already failed on the base.
3. **Spec.** List acceptance criteria and spec statements that are missing or partial, behaviour nobody asked for, and behaviour that looks implemented but wrong. Quote the issue or spec line for each. Check that code and spec changed together and still agree.
4. **Standards.** Check the change against the BKMs and principles whose triggers match (`bkm-router` skill). Cite the BKM and its rule. A constraint written only as a comment ("do not change", "must stay one-hot") is a should: it belongs in an assertion, lint rule or test ([encode-lessons-in-structure.md](../principles/encode-lessons-in-structure.md)).
5. **Breakage.** Find what the change breaks where grep stops: other blocks that instantiate it, parameter overrides, interface and handshake consumers, register maps, reset and clock-domain behaviour, testbenches, formal properties, synthesis constraints, and files generated from it. Name the one fact the change is safe because of, and prove it (step 6). List risks you checked and cleared separately, each with the `file:line` or the empty search that cleared it.
6. **Prove.** For each candidate blocker or should, and for the safety fact, run something against the real code: a failing simulation with its seed and the signal and time in the waveform, a formal counterexample, the lint or CDC warning, the synthesis report line (inferred latch, timing violation, logic optimized away). Rerun it once before posting. Drop what you could not reproduce, or post it as a nit or a question.
7. **Second opinions.** When the first mate's brief asks for them, the first mate dispatches reviewer crewmates on other models, each posting its own review; crewmates do not dispatch crewmates. If a named model is unavailable, say so; never substitute another. Treat every bot or second-opinion comment on the pull request as a candidate finding: verify it against the current head, rerunning the cheap check it names, before acting on it.
8. **Post.** Post one GitHub pull request review with `gh` or the GitHub tools: inline comments at `file:line`, and a body with a `VERDICT: PASS` or `VERDICT: CHANGES` line, the intent, the check results, one section per axis, the safety fact (proven or marked unproven), and a ledger of finding ids (R1, R2, ...) with severity and state. Submit it as a comment review, not an approve or request-changes review. Start the body with a note naming your model and the issue, and lead each comment with its conclusion. Check it for secrets before posting, and confirm GitHub returned the review URL. Why: approval is a flow step, not a GitHub review, and crewmates may share one GitHub identity.
9. **Report** the verdict and the review URL to the first mate.

## Re-review

- Review only the commits since your last review and the open ledger ids; mark each id `resolved`, `declined: <reason>` or `still open: <reason>`. Why: a full re-review every round restarts the argument and invites scope creep.
- Accept an author's disagreement backed by evidence (`file:line`, a test, a run); answer one without evidence by pointing at your proof. Why: findings stand on runs, not on who argues longer.
- When one finding is still disputed after two exchanges, or a blocker or should is still open after three rounds, stop and report both sides in two or three lines each to the first mate, which decides or escalates. Why: a deadlock between agents is a decision for a human, not another round.
- Do not re-raise a declined nit. Why: nits never hold up approval.

## Exit criteria

- You pass the pull request (`VERDICT: PASS`) only when no blocker or should is open, the checks you ran are green or their failure predates the change, and your review covers the current head. A timeout, an error or silence from any reviewer is not a pass.
- What happens after your pass is in [flow.md](flow.md) Merging.
