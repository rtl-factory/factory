---
status: draft
---

# GitHub settings

The repository settings that enforce the workflow. A repo admin applies them; agents cannot.

## Ruleset for `main`

Settings → Rules → Rulesets → New branch ruleset, target the default branch, enforcement **Active**, bypass list: **Repository admin**, set to *For pull requests only*:

- **Restrict deletions** and **Block force pushes**.
- **Require a pull request before merging**:
  - Required approvals: 1
  - **Require review from Code Owners**
  - Dismiss stale approvals when new commits are pushed
- **Require status checks to pass**: add `issue-link` (and the L1 job once it exists). Require branches to be up to date.

Why: every change to `main` goes through a pull request, passes L1, is approved by a code owner, and closes an issue. The admin bypass exists only for `full-send` issues; when to use it is set in [issues-and-branches.md](issues-and-branches.md), not by GitHub.

## Code owners

`.github/CODEOWNERS` names who must approve: the repo admins, for now. GitHub never counts a pull request author's own approval, so a captain's pull request needs another code owner.

## Labels

Create the label `full-send`: the first mate may merge this issue's pull request into `main` without a captain's approval.
