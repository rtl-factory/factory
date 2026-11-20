# Epics and epic branches are opt-in

By default an issue branch merges straight into `main`. Only when several issues cannot reach `main` one at a time (each would break `main` until the others land) do we open an epic: its issue branches merge into an epic branch, and the epic branch merges into `main` through one pull request once healthy. Related issues that can land independently are linked, not grouped under an epic. We rejected always-on epic branches because long-lived shared branches drift from `main` and delay real regression.
