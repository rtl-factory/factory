---
status: draft
---

# GitHub settings

The repository settings that enforce the flow. A repo admin applies them; agents cannot.

## Steps

1. **Ruleset for `main`.** Settings → Rules → Rulesets → New branch ruleset; target the default branch; enforcement **Active**; no bypass list:
   - **Restrict deletions** and **Block force pushes**.
   - **Require a pull request before merging**, with required approvals **0** and allowed merge methods **Squash** and **Merge** (not Rebase). Why: approval is a flow step, not a GitHub review (ADR 0007); merge methods follow ADR 0008.
   - **Require status checks to pass**: `issue-link`, and the L1 job once it exists. Require branches to be up to date.
2. **Ruleset for epic branches.** Target `epic/*`; enforcement **Active**; require a pull request, allowed merge method **Squash** only, and require `issue-link` and the L0 job once it exists.
3. **Labels.** Run the `label-sync` workflow once (Actions → label-sync → Run workflow). It reruns on every change to `.github/labels.json` merged into `main`.
4. **Wiki site.** Settings → Pages → Build and deployment → Source: **GitHub Actions**. Then run the `pages` workflow once (Actions → pages → Run workflow); it reruns on every merge to `main` that touches the wiki. The site is at `https://rtl-factory.github.io/factory/`.
5. **Dashboard.** Create a GitHub Project for the repo with views per [labels.md](labels.md).

`.github/CODEOWNERS` lists the repo admins so GitHub requests their review; their review is not required.

## Exit criteria

- Both rulesets are active, the labels exist, the wiki site is live, and the Project board shows open issues.
