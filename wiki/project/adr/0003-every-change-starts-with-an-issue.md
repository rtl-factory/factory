# Every change starts with a GitHub issue

No change reaches the repo without a GitHub issue behind it, and every pull request into `main` must close one (enforced by CI and the `main` ruleset). A human assigning the issue to an agent is what starts the work; no extra label is needed. This makes the issue tracker the factory's single human interface and audit trail: humans decide by writing and assigning issues; agents execute them. The cost is overhead on trivial changes, which we accept.
