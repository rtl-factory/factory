---
status: draft
---

# GitHub settings

The repository settings that enforce the workflow. A repo admin applies them; agents cannot.

## Ruleset for `main`

Settings → Rules → Rulesets → New branch ruleset, target the default branch, enforcement **Active**, no bypass list:

- **Restrict deletions** and **Block force pushes**.
- **Require a pull request before merging**:
  - Required approvals: 1
  - **Require review from Code Owners**
  - Dismiss stale approvals when new commits are pushed
- **Require status checks to pass**: add `issue-link` (and the L1 job once it exists). Require branches to be up to date.

Why: every change to `main` goes through a pull request, passes L1, is approved by a code owner, and closes an issue.

## Code owners

`.github/CODEOWNERS` names who must approve. It is itself protected by the code-owner rule.
