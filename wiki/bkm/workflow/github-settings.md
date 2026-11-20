---
status: draft
---

# GitHub settings

The repository settings that enforce the flow. A repo admin applies them; agents cannot.

## Ruleset for `main`

Settings → Rules → Rulesets → New branch ruleset, target the default branch, enforcement **Active**, no bypass list:

- **Restrict deletions** and **Block force pushes**.
- **Require a pull request before merging**:
  - Required approvals: **0**. Why: agents act under their captain's account and GitHub never counts an author's approval; approval is a step in the flow instead (ADR 0007).
  - Allowed merge methods: **Squash** and **Merge**, not Rebase. Why: issue branches squash, epic branches merge-commit (ADR 0008).
- **Require status checks to pass**: `issue-link`, and the L1 job once it exists. Require branches to be up to date.

## Ruleset for epic branches

Target `epic/*`, enforcement **Active**: require a pull request, allowed merge method **Squash** only, require `issue-link` and the L0 job once it exists.

## Code owners

`.github/CODEOWNERS` lists the repo admins, so GitHub requests their review on every pull request. Their review is not required by the ruleset.

## Labels

Create the label `full-send`: the first mate may merge this issue's pull request into `main` without the captain's approval.
