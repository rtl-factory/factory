---
name: Epic
about: Parent issue whose sub-issues land together through an epic branch
title: ""
labels: ["type:epic", "status:triage"]
---

<!-- Template: wiki/bkm/workflow/templates.md. Labels: wiki/bkm/workflow/labels.md. Add at least one area:, any block:, one priority:, one size: and one complexity: label. -->

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
