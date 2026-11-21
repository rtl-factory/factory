---
status: draft
---

# Issue and pull request templates

The four issue templates and the one pull request template, which one to use, and what each section must hold. Every issue and every pull request uses one of them, whoever opens it and however: the web UI, `gh`, or the API.

## Rules

- Open every issue with one of the four issue templates below, chosen by its `type:` label: **Enhancement** (`type:feature`, `type:spec`, `type:infra`, `type:research`), **Bug** (`type:bug`), **BKM** (`type:bkm`) or **Epic** (`type:epic`). Why: the first mate, the ready test ([issues.md](issues.md)) and the reviewer read these headings; an issue in another shape has to be rewritten before anyone can dispatch it.
- Open every pull request with the pull request template below. Why: the reviewer and the captain judge the outcome and its evidence in a fixed place.
- Copy the headings exactly and keep their order. Drop none; write "None" under a heading that has nothing, and say why when it is not obvious. Add other sections (Notes, Findings) after Blocked by when the issue needs them. Why: an agent finds a section by its heading, and an empty heading shows it was thought about.
- Start with **Spec references**: the wiki files the work follows or changes, as repo paths. Name the BKMs that govern it, and the PRD, HAS, MAS, DV or FV plan, board spec, spec or ADR it builds on. Why: the crewmate reads these before anything else, and the reviewer checks the change against them.
- Write **Scope** and **Out of scope** as short lists of what this issue changes and what it leaves alone, naming the nearest thing someone might expect it to cover. Why: out of scope is where review loops grow a change one cheap fix at a time.
- Write **Blast radius** as what else the change can affect: other blocks and interfaces, the other project, CI, timing or area, the agents that follow a changed BKM. Say how hard it is to revert. Why: it tells the crewmate what to re-run and the reviewer where to look.
- Write acceptance criteria and blockers per [issues.md](issues.md), and labels, including `size:` and `complexity:`, per [labels.md](labels.md). Why: those rules live there.
- Keep the files in `.github/ISSUE_TEMPLATE/` and `.github/pull_request_template.md` identical to the bodies below, and change both in the same pull request. Why: humans file through the web UI and agents write the body by hand, and both must produce the same shape.
- A planning issue ([planning.md](../design/planning.md)) is the one exception: it starts with Spec references and then uses the planning body. Why: it tracks decisions, not a change to build.

## Enhancement

```markdown
## Spec references
- <wiki/... path>

## What to build
<the behaviour this issue makes work, on the block's interfaces, in glossary terms>

## Scope
- <what this changes>

## Out of scope
- <what it leaves alone>

## Blast radius
<what else it can affect, and how hard it is to revert>

## Acceptance criteria
- [ ] <check with a pass predicate>

## Blocked by
<#n, or "None">
```

## Bug

```markdown
## Spec references
- <wiki/... path that states the expected behaviour>

## Observed behaviour
<what happens, with the log line, assertion, waveform or counterexample>

## Expected behaviour
<what should happen, and which spec says so>

## Reproduce
<the exact command, test, seed or board step that shows it, and the commit it was seen on>

## Scope
- <what the fix changes>

## Out of scope
- <related problems this fix leaves alone>

## Blast radius
<what else the fix can affect, and how hard it is to revert>

## Acceptance criteria
- [ ] <the reproducing test or seed passes, plus any other check>

## Blocked by
<#n, or "None">
```

## BKM

For a change to a BKM, principle, skill, `AGENTS.md`, the glossary or `.github/` settings.

```markdown
## Spec references
- <the BKMs, skills or ADRs this changes or touches>

## Evidence
<what an agent did, or would do, wrong under today's wording, with links to the pull request, transcript or issue>

## What to change
<the rule or step as it should read, or what agents should do differently>

## Scope
- <the files this changes>

## Out of scope
- <related rules this leaves alone>

## Blast radius
<which agents, flows and other BKMs change behaviour, and how hard it is to revert>

## Acceptance criteria
- [ ] <check with a pass predicate, such as the rule present, the router row added, contradicting text gone>

## Blocked by
<#n, or "None">
```

## Epic

Only when the issues cannot reach `main` one by one ([issues.md](issues.md) Rules, ADR 0004).

```markdown
## Spec references
- <wiki/... path>

## What to build
<the behaviour the whole epic makes work>

## Why an epic
<which sub-issue would break main until the others land, and why expand–contract does not fit>

## Sub-issues
- [ ] #<n> <title>

## Scope
- <what the epic changes>

## Out of scope
- <what it leaves alone>

## Blast radius
<what else it can affect, and how hard the epic branch is to revert>

## Acceptance criteria
- [ ] Every sub-issue is closed and the epic branch merges to main with L0 green.
- [ ] <any check across sub-issues>

## Blocked by
<#n, or "None">
```

## Pull request

Keep it under about 30 lines. How to title it, label it and carry it to landable is in [landing.md](landing.md).

```markdown
## Spec references
- <the issue's spec references, plus any the change touched>

## Problem and outcome
<two or three sentences in the issue's words: what was wrong or missing, and what is true now; no inventory of edits>

## Evidence
<the regression level run and its result, with numbers: tests and seeds passed, properties proven, lint warnings, synthesis area and timing against the base; before and after for a fix; what was not run>

## Blast radius
<what else could break, and whether it is easy to revert>

Closes #<n>

<one line naming the agent and harness that made the change>
```

- Evidence is a claim the reviewer can check without re-running it ([prove-it-works](../principles/prove-it-works.md)). Why: a claim without evidence gets re-run.
- `Closes #<n>` names every issue the pull request closes. Why: the `issue-link` check fails without it.
- The last line says the work is agent-written. Why: readers must know.

## Exit criteria

- The issue or pull request has every heading of its template, in order, with Spec references filled, and carries its labels per [labels.md](labels.md).
