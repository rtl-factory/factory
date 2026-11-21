---
name: Bug
about: Something is wrong and must be fixed
title: ""
labels: ["type:bug", "status:triage"]
---

<!-- Template: wiki/bkm/workflow/templates.md. Labels: wiki/bkm/workflow/labels.md. Add at least one area:, any block:, one priority:, one size: and one complexity: label. -->

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
