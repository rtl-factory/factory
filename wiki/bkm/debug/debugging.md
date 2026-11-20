---
status: draft
upstream: mattpocock/skills skills/engineering/diagnosing-bugs/SKILL.md @ abeeaf95 (MIT, see ../LICENSE-mattpocock-skills), cursor/plugins pstack/skills/poteto-mode/playbooks/{bug-fix,investigation,runtime-forensics,trace-forensics,perf-issue}.md @ aaeb0ea (MIT, see ../LICENSE-pstack)
---

# Debugging

How to take a hard failure (a failing simulation, a formal counterexample, a lint or CDC finding, a synthesis or timing surprise, a flaky test, a broken tool or flow) from first sight to a root-cause fix with a regression check. Follow [fix-root-causes.md](../principles/fix-root-causes.md) throughout; this BKM is the loop that applies it.

## Rules

- Debug on an issue. A failure outside your issue's scope gets its own issue per [labels.md](../workflow/labels.md); a failure of L1–L4 on `main` gets one labelled `priority:P0` ([ci-levels.md](../workflow/ci-levels.md)). Why: every change starts with an issue, and a fix smuggled into unrelated work is never reviewed as one.
- You must have a red command before you form a hypothesis. Why: without a pass/fail signal on this failure, reading RTL builds theories nobody can check.
- Change one variable per run, and tie every probe to a prediction. Why: two changes in one run leave you unable to say which one moved the result.
- Read actual state (the waveform, the counterexample trace, the report line), never infer it from the source. Why: the source says what should happen; the dump says what did.
- Every line of the fix must trace to evidence from a run. A change that "might help" is a hypothesis; revert what a refuted hypothesis motivated. Why: speculative fixes hide the real fault and stay forever.
- Treat a waiver, a constraint relaxation, a retry, a larger timeout or a reset of the stimulus as a fix only when the state it handles is legitimate. Why: these are the hardware forms of the guards [fix-root-causes.md](../principles/fix-root-causes.md) warns about.
- When two fixes that share one premise have failed, stop and follow [attack-the-premise.md](../principles/attack-the-premise.md). Why: a third fix on a false premise fails the same way.
- Read large artifacts (VCD/FST dumps, full logs, timing reports) through a script or a query that returns the reduced finding, never whole into context. Why: [guard-the-context-window.md](../principles/guard-the-context-window.md).
- Tag every piece of temporary instrumentation with `DEBUG-<issue>` (a `$display`, a probe signal, a dump scope, a script). Why: cleanup becomes one grep, and untagged probes survive into review.
- Post the ranked hypotheses and the final diagnosis as comments on the issue; do not wait for an answer to continue. Why: the first mate or a captain may rule one out at a glance, and GitHub keeps the trail if the session dies ([never-block-on-the-human.md](../principles/never-block-on-the-human.md)).

## Steps

1. **Build the red command.** Find one command, runnable unattended, that goes red on this failure's exact symptom: the failing assertion or scoreboard mismatch, the counterexample property, the lint/CDC rule ID, the timing path, the tool's error. Record everything that pins it: commit, seed, tool and version, command line, plusargs, defines. Ways to build one, cheapest first:
   - rerun the failing test with its seed and dumping on;
   - replay a formal counterexample in simulation (export the trace as stimulus or a testbench), so the trace runs in the same flow as the fix;
   - run the lint, CDC or synthesis step alone on the block and grep the finding;
   - bisect: `git bisect run` the red command between a known-good and a known-bad commit;
   - differential: the same stimulus through two commits, two simulators, RTL against gate level, or two tool versions, diffing the outputs.
   If you cannot build one, stop and report to the first mate what you tried and what you need (a seed, a dump, a machine, a licence). Do not hypothesise without it.
2. **Tighten it.** Make it fast (smallest testbench, shortest stimulus, dump only the scopes you need), sharp (assert the specific symptom, not "simulation finished"), and deterministic (fixed seed, fixed tool version, no wall-clock or file-order dependence). For a flaky failure, aim for a higher reproduction rate, not a clean repro: sweep seeds in parallel, stress the stimulus, narrow the timing window, until it fails often enough to debug against.
3. **Reproduce and minimise.** Run it and confirm it shows the failure that was reported, not a neighbour. Then cut one thing at a time (stimulus cycles, transactions, enabled features, parameters, blocks in the hierarchy, the formal depth) and rerun after each cut. Done when removing anything left makes it go green.
4. **Hypothesise.** Write three to five ranked, falsifiable hypotheses: "If X is the cause, then changing Y makes it pass, and changing Z makes it worse." Usual hardware suspects: reset or X-propagation, an off-by-one in a counter or pointer, a missing handshake or backpressure case, a clock-domain crossing, a blocking/non-blocking or delta-cycle race, a wrong constraint or assumption (formal and synthesis), a testbench or reference-model bug. Post the list on the issue.
5. **Instrument and eliminate.** Take the split that rules out the most remaining hypotheses. Read the waveform at the cycle the symptom first appears, then walk backward to the first signal that holds a wrong value; that is where the system first went wrong. For a counterexample, read the trace cycle by cycle against the spec. For a perf, area or timing surprise, measure before changing anything and follow [explain-the-number.md](../principles/explain-the-number.md): name the path, the inferred structure or the removed logic in the report that sets the number. Confirm the surviving mechanism with a run before you fix.
6. **Fix at the root.** Fix the first wrong decision, in the block or in the testbench, wherever it is, and search for every other place with the same fault. If the fix changes behaviour the spec describes, change the spec in the same pull request ([flow.md](../workflow/flow.md) step 5).
7. **Add the regression check.** Turn the minimised repro into a check that exercises the real failure through the block's interface ([test-behavior-not-implementation.md](../principles/test-behavior-not-implementation.md)): a directed test, a pinned seed, a formal property or a cover, a lint rule turned on. Watch it fail without the fix, then pass with it. Put it at the lowest regression level whose runtime fits ([ci-levels.md](../workflow/ci-levels.md)). Commit the failing check before the fix ([sequence-verifiable-units.md](../principles/sequence-verifiable-units.md)). If no check can reach the failure as it really happens, say so on the issue; that gap is a finding.
8. **Leave a cheap signal.** If the deciding fact took a deep dig, add the always-on signal that would have shown it at first look: an assertion on the broken invariant, a clearer error message, a report line that CI prints. If it belongs to a BKM, a skill or `.github/`, open an issue for it instead.
9. **Verify and clean up.** Rerun the original, unminimised red command and see it pass ([prove-it-works.md](../principles/prove-it-works.md)); an "inconclusive" run or a run on a different flow is not a pass. Grep `DEBUG-<issue>` and remove every hit, and delete throwaway harnesses.

For a read-only diagnosis (the issue asks why, not for a fix), stop after step 5 and post the diagnosis: the artifact and command, the reduced finding, the source location, and whether a run confirmed the mechanism or it is the strongest hypothesis the artifact supports.

## Exit criteria

- The original red command passes, and its failing and passing output is in the pull request.
- A regression check at the right level fails without the fix and passes with it, or the issue records why none can reach the failure.
- The pull request states the root cause and which hypothesis survived.
- No `DEBUG-<issue>` instrumentation or throwaway harness remains.
