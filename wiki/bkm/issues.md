---
status: draft
upstream: mattpocock/skills skills/engineering/to-tickets/SKILL.md @ abeeaf95, mattpocock/skills skills/engineering/triage/SKILL.md @ abeeaf95, mattpocock/skills skills/engineering/setup-matt-pocock-skills/issue-tracker-github.md @ abeeaf95 (MIT, see SOURCES.md); cursor/plugins pstack/skills/poteto-mode/playbooks/multi-phase-plan.md @ aaeb0ea (MIT, see SOURCES.md)
---

# Issues

How work is written down: splitting and triaging issues, their labels, and the issue and pull request templates.

## Issues and epics

How to turn an aligned plan, spec or conversation into well-formed issues, when they become an epic, and how to triage an issue until it is `status:ready`. The steps of a change and the epic branch mechanics are in [flow](flow.md#flow); label names and moves are in [labels](#labels).

### Rules

- Cut work into vertical slices: each issue delivers one narrow, complete behaviour that ends in a state a reviewer crewmate can check, per [Sequence work into verifiable units](principles.md#sequence-work-into-verifiable-units). For a block, a slice is typically spec + RTL + DV test for one feature of that block. Why: a horizontal slice ("all the RTL", "all the tests") cannot be verified until its siblings land.
- Size each issue for one crewmate in one fresh context. Why: a crewmate that runs out of context lands half a change.
- Put prefactoring (a refactor that makes the feature easy) in its own issue, first, blocking the feature. Why: the refactor then lands green on its own and the feature diff stays small.
- Write what to build as behaviour, in glossary terms: what the block does on its interfaces, not a list of files or signals to edit. Name the block; leave file paths and code out unless a prototype produced a snippet (an FSM, a register map) that encodes a decision better than prose. Why: paths and code go stale; behaviour is what review checks.
- Write each acceptance criterion as a check with a pass predicate: a named test or seed passing, a formal property proven (or a counterexample gone), lint and CDC clean, a synthesis report meeting a stated number. For a timing, area or power criterion, name the metric, the baseline measured on `main` first and the number that fails ([Explain the Number](principles.md#explain-the-number)). Why: "works" is not checkable; a predicate is.
- Add a "blocked by" link only where one issue truly gates another, using GitHub's native issue dependencies (`gh issue edit <n> --add-blocked-by <m>`). Open blockers first so links point at real numbers. Why: the first mate dispatches every ready issue whose blockers are closed, so a false edge serializes work for nothing.
- Open an epic only when the issues cannot reach `main` one by one: each would break `main` until the others land (ADR 0004). Being related, large or ordered is not enough; link those issues instead. Why: an epic branch drifts from `main` and delays real regression.
- For a wide mechanical change (rename a shared interface, retype a bus) prefer expand–contract over an epic: an issue that adds the new form beside the old, migrate issues per block each blocked by it, and a contract issue that removes the old form, blocked by every migrate issue. Use an epic only when even the batches cannot stay green alone. Why: expand–contract keeps every step landable on `main`.
- Make an issue a sub-issue only of an epic. Why: every parent issue is an epic (glossary).
- Label every issue you open per [labels](#labels); an epic carries `type:epic`, and each sub-issue carries its own labels. Why: the first mate reads kind and status from labels.
- An issue **must** pass the ready test below before it moves to `status:ready`. Why: a crewmate is dispatched only on `status:ready` ([flow](flow.md#flow)).

### Ready test

An issue is ready when all hold:

- It uses its template from [templates](#templates) with Spec references filled, and its What to build (Expected behaviour for a bug, What to change for a BKM) is stated as behaviour, with no decision left that only a captain can make.
- Every acceptance criterion is a check with a pass predicate.
- Its labels are complete per [labels](#labels), and its "blocked by" links are set.
- It fits one crewmate, one issue branch and one pull request: its size is not `size:xlarge`.
- For a bug, the failure is reproduced: a failing test, seed, waveform or counterexample is named in the issue.

### Steps: splitting a plan into issues

1. Read the source in full: the alignment notes, spec or issue, with its comments. Read the glossary and the ADRs for the area.
2. Look at the current blocks and specs for prefactoring that would make the slices easier.
3. Draft the slices as a numbered list, each with a title, its blockers and what it delivers. Decide whether the set is an epic (Rules).
4. If the split came out of an alignment session, show the list to the captain and adjust granularity and blockers until they agree. Within an already-aligned goal, go on and report the split afterwards ([Never Block on the Human](principles.md#never-block-on-the-human)).
5. Open the issues blockers first, each with its template from [templates](#templates). For an epic, open the parent with `type:epic` and attach each issue as a sub-issue (`gh issue edit <epic> --add-sub-issue <n>`).
6. If the source was an existing issue and the set is not an epic, narrow that issue to one slice and link the others with "relates to".
7. Triage each new issue (next steps).

### Steps: triaging an issue

1. Read the issue, its comments and labels, and any earlier triage notes so you do not repeat a question already answered.
2. Search open and closed issues and the repo, by domain concept rather than wording, for the same request or an existing implementation. Note where you looked.
3. Choose one outcome:
   - **Duplicate**: comment with the link to the original and close it as duplicate.
   - **Already done**: comment with where it lives (pull request or block) and close it as completed.
   - **Bug**: reproduce it at L0 or in simulation before going further; if you cannot, it needs info.
   - **Needs info**: comment with triage notes, "What is established" and "What is still needed", each question specific; keep `status:triage` and escalate to the captain if only they can answer. Grill ([grilling](planning.md#grilling)) when the request itself is unclear.
   - **Reject**: escalate with your reason; close as not planned only on a captain's word, giving the reason in the closing comment. Why: rejecting changes the goal, which is the captain's call.
   - **Clear**: fix labels and links, split it if it fails the size rule, then move it to `status:ready`.

### Exit criteria

- Every issue from the source is open, written with its template, labelled, linked by its real blockers, and either `status:ready` or waiting in `status:triage` with its open questions written on it.
- An epic exists only where ADR 0004 requires one, with every issue attached as a sub-issue.

## Labels

Which labels issues and pull requests carry, and when agents set, change and check them.

### Kinds

`.github/labels.json` is the single source for label names, colours and descriptions; the `label-sync` workflow creates them on GitHub. Names are `kind:value`.

| Kind | Per issue |
|---|---|
| `type:` | exactly one |
| `area:` | at least one |
| `block:` | one per block the issue touches; none for factory work |
| `priority:` | exactly one |
| `size:` | exactly one |
| `complexity:` | exactly one |
| `status:` | exactly one while open; none once closed |
| `authority:` | optional |

### Rules

- Label every issue when you open it: one `type:`, at least one `area:`, `block:` labels for the blocks it touches, one `priority:`, one `size:`, one `complexity:` and `status:triage`. Why: an unlabelled issue is invisible on every dashboard.
- Set `size:` from the change one pull request will make: `minimal` (a one-line or wording fix), `small` (one block, document or tool, under about 100 changed lines), `medium` (one block or tool with its spec and tests, up to about 400 lines), `large` (several blocks or documents, still one crewmate and one pull request), `xlarge` (more than one crewmate or pull request). An `xlarge` issue must be split or made an epic before `status:ready`; an epic carries the size of the whole. Why: the first mate plans and batches by size, and an issue too big for one crewmate lands half done.
- Set `complexity:` from how much is unknown: `low` (mechanical; the method and the answer are known), `medium` (a known method with design choices inside existing patterns), `high` (new design, unknown risk or open questions; grill or research first and expect captain decisions). Re-set both when triage or the work shows they were wrong. Why: complexity, not size, says how much review and alignment the work needs.
- Move `status:` as the work moves: `ready` once the issue is clear, `in-progress` at dispatch, `in-review` when the pull request opens, `blocked` while waiting. The `status-clear` workflow removes it when the issue closes and sets `status:triage` when it reopens; never set one on a closed issue. Why: dashboards and the first mate read status from labels, and a closed issue left `in-progress` reads as live work.
- The first mate must check an issue's labels before dispatching on it, and fix them or escalate to the captain rather than dispatch blind. Why: labels tell the first mate what kind of work it is handing out.
- Give a pull request the same `type:`, `area:` and `block:` labels as its issue. Why: dashboards count pull requests too.
- Only a captain may add or remove `authority:needs-captain`. Why: it is the captain's control over merge authority; what it does is in [Merging](flow.md#merging).
- Add a `block:` label to `.github/labels.json` in the same pull request that creates the block. Why: the label set stays in one place.
- Never delete a label on GitHub as part of other work; remove it from `.github/labels.json` and delete it by hand. Why: deleting a label strips it from every issue.

### Dashboard

A GitHub Project board for the repo shows the work, with views filtered and grouped by `status:`, `area:`, `block:` and `priority:` labels. The tracker tool will publish generated status, regression and metric pages on the wiki site; it is not built yet ([its PRD](../tools/tracker/prd.md)).

### Exit criteria

- Every open issue has one `type:`, at least one `area:`, one `priority:`, one `size:`, one `complexity:` and one `status:` label, and no closed issue has a `status:` label.

## Templates

The four issue templates and the one pull request template, which one to use, and what each section must hold. Every issue and every pull request uses one of them, whoever opens it and however: the web UI, `gh`, or the API.

### Rules

- Open every issue with one of the four issue templates below, chosen by its `type:` label: **Enhancement** (`type:feature`, `type:spec`, `type:infra`, `type:research`), **Bug** (`type:bug`), **BKM** (`type:bkm`) or **Epic** (`type:epic`). Why: the first mate, the ready test ([issues and epics](#issues-and-epics)) and the reviewer read these headings; an issue in another shape has to be rewritten before anyone can dispatch it.
- Open every pull request with the pull request template below. Why: the reviewer and the captain judge the outcome and its evidence in a fixed place.
- Copy the headings exactly and keep their order. Drop none; write "None" under a heading that has nothing, and say why when it is not obvious. Add other sections (Notes, Findings) after Blocked by when an issue needs them, or before `Closes` in a pull request. Why: an agent finds a section by its heading, and an empty heading shows it was thought about.
- Start with **Spec references**: the wiki files the work follows or changes, as repo paths. Name the BKMs that govern it, and the PRD, HAS, MAS, DV or FV plan, board spec, spec or ADR it builds on. Why: the crewmate reads these before anything else, and the reviewer checks the change against them.
- Write **Scope** and **Out of scope** as short lists of what this issue changes and what it leaves alone, naming the nearest thing someone might expect it to cover. Why: out of scope is where review loops grow a change one cheap fix at a time.
- Write **Blast radius** as what else the change can affect: other blocks and interfaces, the other project, CI, timing or area, the agents that follow a changed BKM. Say how hard it is to revert. Why: it tells the crewmate what to re-run and the reviewer where to look.
- Write acceptance criteria and blockers per [issues and epics](#issues-and-epics), and labels, including `size:` and `complexity:`, per [labels](#labels). Why: those rules live there.
- Keep the files in `.github/ISSUE_TEMPLATE/` and `.github/pull_request_template.md` identical to the bodies below, apart from their front matter and one leading comment that points here, and change both in the same pull request. Why: humans file through the web UI and agents write the body by hand, and both must produce the same shape.
- A planning issue ([planning and specs](planning.md#planning-and-specs)) is the one exception: it starts with Spec references and then uses the planning body. Why: it tracks decisions, not a change to build.

### Enhancement

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

### Bug

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

### BKM

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

### Epic

Only when the issues cannot reach `main` one by one ([Issues and epics: Rules](#rules), ADR 0004).

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

### Pull request

How to title it, label it and carry it to landable is in [writing and landing](pull-requests.md#writing-and-landing).

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

- Evidence is a claim the reviewer can check without re-running it ([Prove It Works](principles.md#prove-it-works)). Why: a claim without evidence gets re-run.
- `Closes #<n>` names every issue the pull request closes. Why: the `issue-link` check fails without it.
- The last line says the work is agent-written. Why: readers must know.

### Exit criteria

- The issue or pull request has every heading of its template, in order, with Spec references filled, and carries its labels per [labels](#labels).
